"""Phase 6 + 7 — 슬래시 명령 + 이벤트 핸들러.

본 cog 는 봇의 사용자 인터페이스 전체를 담당한다.
- Phase 6: `/settts /setvc /setvoice /join /leave /status` 6개 슬래시 명령
- Phase 7: `on_message` (TTS 합성), `on_voice_state_update` (입/퇴장 알림)

Rule 01 (봇 루프 방지): on_message / on_voice_state_update 첫 줄에 봇 가드.
Rule 02 (guild 격리): 모든 처리는 interaction.guild_id / message.guild.id 기준.
Rule 03 (복원력): 핸들러는 `log.exception` 으로 잡고 사용자에겐 무응답 또는 ephemeral 안내.
Rule 04 (시크릿/권한): 민감 명령(settts/setvc/setvoice) 에 manage_channels 권한 체크.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.audio_queue import AudioQueue, AudioRequest
from cogs.config_store import ConfigStore
from cogs.preprocess import clean_message
from cogs.tts_engine import DEFAULT_VOICE, close_session, start_keepalive, warm_up
from cogs.ui import BRAND_COLOR, channel_ref, notice_embed

log = logging.getLogger(__name__)

VOICE_CHOICES = [
    app_commands.Choice(name="여성-차분 (SunHi)", value="ko-KR-SunHiNeural"),
    app_commands.Choice(name="여성-또렷 (JiMin)", value="ko-KR-JiMinNeural"),
    app_commands.Choice(name="여성-부드러움 (SeoHyeon)", value="ko-KR-SeoHyeonNeural"),
    app_commands.Choice(name="여성-편안함 (SoonBok)", value="ko-KR-SoonBokNeural"),
    app_commands.Choice(name="여성-경쾌 (YuJin)", value="ko-KR-YuJinNeural"),
    app_commands.Choice(name="남성-자연 (InJoon)", value="ko-KR-InJoonNeural"),
    app_commands.Choice(name="남성-무게감 (BongJin)", value="ko-KR-BongJinNeural"),
    app_commands.Choice(name="남성-친근 (GookMin)", value="ko-KR-GookMinNeural"),
    app_commands.Choice(name="남성-담백 (Hyunsu)", value="ko-KR-HyunsuNeural"),
    app_commands.Choice(
        name="남성-다국어 (Hyunsu Multi)",
        value="ko-KR-HyunsuMultilingualNeural",
    ),
]

PANEL_COOLDOWN_SEC = 300  # unused — kept for reference


def _voice_label(voice: str) -> str:
    for choice in VOICE_CHOICES:
        if choice.value == voice:
            return choice.name
    return voice


def _has_human_members(
    channel: discord.VoiceChannel,
    *,
    excluding_member_id: int | None = None,
) -> bool:
    return any(
        not candidate.bot
        and (
            excluding_member_id is None
            or candidate.id != excluding_member_id
        )
        for candidate in channel.members
    )


def _tts_status_embed(cfg: dict) -> discord.Embed:
    tts_ch = cfg.get("tts_channel_id")
    vc_ch = cfg.get("voice_channel_id")
    voice = cfg.get("voice", DEFAULT_VOICE)
    ready = bool(tts_ch and vc_ch)

    embed = discord.Embed(
        title="TTS 상태",
        description="메시지를 읽을 채널과 음성을 출력할 채널 설정입니다.",
        color=BRAND_COLOR if ready else discord.Color.dark_grey(),
    )
    embed.add_field(name="입력 채널", value=channel_ref(tts_ch), inline=True)
    embed.add_field(name="음성 채널", value=channel_ref(vc_ch), inline=True)
    embed.add_field(name="보이스", value=f"`{_voice_label(voice)}`", inline=False)
    embed.add_field(
        name="상태",
        value="재생 준비됨" if ready else "채널 설정 필요",
        inline=False,
    )
    return embed


def _voice_panel_embed(channel: discord.VoiceChannel) -> discord.Embed:
    embed = discord.Embed(
        title="TTS 빠른 설정",
        description=f"{channel.mention} 채널 채팅을 TTS 입력으로 사용할 수 있습니다.",
        color=BRAND_COLOR,
    )
    embed.add_field(
        name="동작",
        value="버튼을 눌러 이 음성 채널의 채팅 읽기를 켜거나 끕니다.",
        inline=False,
    )
    return embed


class TTSControlView(discord.ui.View):
    def __init__(self, cog: "TTSCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="TTS 켜기",
        emoji="🔊",
        style=discord.ButtonStyle.secondary,
        custom_id="nothing_tts:enable",
    )
    async def enable_tts(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.enable_from_panel(interaction)

    @discord.ui.button(
        label="TTS 끄기",
        emoji="🔇",
        style=discord.ButtonStyle.secondary,
        custom_id="nothing_tts:disable",
    )
    async def disable_tts(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.disable_from_panel(interaction)


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self.queue = AudioQueue()
        self._panel_view = TTSControlView(self)
        self._panel_sent: set[int] = set()  # channel IDs that already received the panel
        self._panel_connect_tasks: dict[int, asyncio.Task] = {}
        self._warmup_task = asyncio.create_task(
            self._warm_start(), name="tts-warm-start"
        )
        self.bot.add_view(self._panel_view)

    async def cog_unload(self) -> None:
        panel_tasks = list(self._panel_connect_tasks.values())
        for task in panel_tasks:
            task.cancel()
        for task in panel_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._panel_connect_tasks.clear()
        self._warmup_task.cancel()
        try:
            await self._warmup_task
        except asyncio.CancelledError:
            pass
        await self.queue.shutdown()
        await close_session()

    async def _warm_start(self) -> None:
        if not os.getenv("AZURE_SPEECH_KEY") or not os.getenv("AZURE_SPEECH_REGION"):
            return
        try:
            await warm_up()
            start_keepalive()
            log.info("tts azure connection warmed")
        except Exception:
            log.debug("tts warm-up failed", exc_info=True)

    # ---------- Phase 6: Slash Commands ----------

    async def enable_from_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        voice_state = getattr(interaction.user, "voice", None)
        channel = voice_state.channel if voice_state else None
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=notice_embed(
                    "음성 채널 필요",
                    "먼저 이 음성 채널에 입장한 뒤 눌러주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        # 패널이 눌린 채널과 실제 음성 채널이 다르면 오작동을 줄인다.
        if interaction.channel and interaction.channel.id != channel.id:
            await interaction.response.send_message(
                embed=notice_embed(
                    "채널 확인 필요",
                    f"{channel.mention} 채널 채팅에서 다시 눌러주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.store.set(
            interaction.guild.id,
            tts_channel_id=channel.id,
            voice_channel_id=channel.id,
        )
        await interaction.edit_original_response(
            embed=notice_embed(
                "TTS 연결 준비 중",
                "음성 연결을 백그라운드에서 준비합니다. 지금 입력한 문장은 "
                "큐에 보관했다가 연결이 안정되면 재생합니다.",
                tone="info",
            ),
        )
        self._start_panel_connect(interaction, channel)

    def _start_panel_connect(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        guild_id = interaction.guild.id
        current = self._panel_connect_tasks.get(guild_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._connect_from_panel(interaction, channel),
            name=f"tts-panel-connect-{guild_id}",
        )
        self._panel_connect_tasks[guild_id] = task

    async def _connect_from_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        guild_id = interaction.guild.id
        try:
            await self.queue.ensure_voice(interaction.guild, channel.id)
        except discord.Forbidden:
            embed = notice_embed(
                "권한 부족",
                "음성 채널 접속 권한이 없습니다.",
                tone="error",
            )
        except Exception:
            log.exception("panel join failed: guild_id=%s", interaction.guild.id)
            embed = notice_embed(
                "입장 실패",
                "TTS 입장 중 오류가 발생했습니다.",
                tone="error",
            )
        else:
            embed = notice_embed(
                "TTS 활성화",
                f"{channel.mention} 채널 채팅을 TTS 입력으로 사용합니다.",
                tone="ok",
            )
        try:
            await interaction.edit_original_response(embed=embed)
        except discord.HTTPException:
            log.debug(
                "panel connection result response expired: guild_id=%s",
                guild_id,
            )
        finally:
            current = asyncio.current_task()
            if self._panel_connect_tasks.get(guild_id) is current:
                self._panel_connect_tasks.pop(guild_id, None)

    async def disable_from_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        connect_task = self._panel_connect_tasks.pop(interaction.guild.id, None)
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            try:
                await connect_task
            except asyncio.CancelledError:
                pass

        cfg = await self.store.get(interaction.guild.id)
        channel_id = interaction.channel.id if interaction.channel else None
        updates: dict[str, int | None] = {}
        if cfg.get("tts_channel_id") == channel_id:
            updates["tts_channel_id"] = 0
        if cfg.get("voice_channel_id") == channel_id:
            updates["voice_channel_id"] = 0
        if updates:
            await self.store.set(interaction.guild.id, **updates)

        await self.queue.disconnect_voice(interaction.guild)
        await interaction.edit_original_response(
            embed=notice_embed("TTS 비활성화", "이 채널의 TTS를 껐습니다.", tone="ok"),
        )

    @app_commands.command(name="settts", description="TTS 로 읽을 텍스트 채널 설정")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def settts(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self.store.set(interaction.guild_id, tts_channel_id=channel.id)
        log.info(
            "settts: guild_id=%s channel_id=%s by user_id=%s",
            interaction.guild_id, channel.id, interaction.user.id,
        )
        await interaction.response.send_message(
            embed=notice_embed(
                "TTS 입력 채널 설정",
                f"이제 {channel.mention} 메시지를 읽습니다.",
                tone="ok",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="setvc", description="봇이 음성을 출력할 채널 설정")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def setvc(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel
    ) -> None:
        await self.store.set(interaction.guild_id, voice_channel_id=channel.id)
        log.info(
            "setvc: guild_id=%s channel_id=%s by user_id=%s",
            interaction.guild_id, channel.id, interaction.user.id,
        )
        await interaction.response.send_message(
            embed=notice_embed(
                "음성 출력 채널 설정",
                f"봇 음성을 {channel.mention}에서 재생합니다.",
                tone="ok",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="setvoice", description="TTS 보이스 변경")
    @app_commands.choices(voice=VOICE_CHOICES)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def setvoice(
        self, interaction: discord.Interaction, voice: app_commands.Choice[str]
    ) -> None:
        await self.store.set(interaction.guild_id, voice=voice.value)
        log.info(
            "setvoice: guild_id=%s voice=%s by user_id=%s",
            interaction.guild_id, voice.value, interaction.user.id,
        )
        await interaction.response.send_message(
            embed=notice_embed(
                "보이스 변경",
                f"보이스를 `{voice.name}` 으로 설정했습니다.",
                tone="ok",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="join", description="설정된 음성 채널로 입장")
    async def join(self, interaction: discord.Interaction) -> None:
        cfg = await self.store.get(interaction.guild_id)
        ch_id = cfg.get("voice_channel_id")
        channel = interaction.guild.get_channel(ch_id) if ch_id else None
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=notice_embed(
                    "음성 채널 미설정",
                    "먼저 `/setvc` 로 음성 채널을 지정하세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await self.queue.ensure_voice(interaction.guild, channel.id)
        except discord.Forbidden:
            await interaction.edit_original_response(
                embed=notice_embed("권한 부족", "음성 채널 접속 권한이 없습니다.", tone="error"),
            )
            return
        except Exception:
            log.exception("join failed: guild_id=%s", interaction.guild_id)
            await interaction.edit_original_response(
                embed=notice_embed("입장 실패", "입장 중 오류가 발생했습니다.", tone="error"),
            )
            return
        await interaction.edit_original_response(
            embed=notice_embed("음성 채널 입장", f"{channel.mention} 에 입장했습니다.", tone="ok"),
        )

    @app_commands.command(name="leave", description="음성 채널에서 퇴장")
    async def leave(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            await interaction.response.send_message(
                embed=notice_embed("퇴장 불가", "현재 음성 채널에 없습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        connect_task = self._panel_connect_tasks.pop(interaction.guild.id, None)
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            try:
                await connect_task
            except asyncio.CancelledError:
                pass
        try:
            await self.queue.disconnect_voice(interaction.guild)
        except Exception:
            log.exception("leave failed: guild_id=%s", interaction.guild_id)
        await interaction.edit_original_response(
            embed=notice_embed("음성 채널 퇴장", "퇴장했습니다.", tone="ok"),
        )

    @app_commands.command(name="status", description="현재 설정 확인")
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = await self.store.get(interaction.guild_id)
        await interaction.response.send_message(embed=_tts_status_embed(cfg), ephemeral=True)

    # ---------- 권한/오류 안내 ----------

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await self._safe_send(
                interaction,
                "권한 필요",
                "이 명령은 `채널 관리` 권한이 필요합니다.",
                ephemeral=True,
                tone="error",
            )
            return
        log.exception(
            "slash command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        await self._safe_send(
            interaction,
            "처리 실패",
            "명령 처리 중 오류가 발생했습니다.",
            ephemeral=True,
            tone="error",
        )

    @staticmethod
    async def _safe_send(
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        ephemeral: bool = True,
        tone: str = "info",
    ) -> None:
        embed = notice_embed(title, description, tone=tone)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        except discord.HTTPException:
            log.exception("failed to send interaction response")

    # ---------- Phase 7: Event Listeners ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Rule 01: 봇/webhook 메시지 즉시 차단
        if message.author.bot or message.webhook_id:
            return
        if message.guild is None:
            return
        try:
            await self._handle_tts_message(message)
        except Exception:
            log.exception("on_message failed: guild_id=%s", message.guild.id)

    async def _handle_tts_message(self, message: discord.Message) -> None:
        # Fast-path: 무관 채널 메시지는 await 없이 즉시 reject. ConfigStore 는
        # path-singleton 이라 web_admin 등 다른 cog 가 set() 한 결과가 즉시
        # 반영되므로 stale cache 문제는 in-process 에서 발생하지 않는다.
        cached = self.store.get_cached_sync(message.guild.id)
        tts_channel_id = cached.get("tts_channel_id")
        if not tts_channel_id or message.channel.id != tts_channel_id:
            return

        cfg = await self.store.get(message.guild.id)
        tts_channel_id = cfg.get("tts_channel_id")
        voice_channel_id = cfg.get("voice_channel_id")
        if not tts_channel_id or not voice_channel_id:
            return
        if message.channel.id != tts_channel_id:
            return

        voice_channel = message.guild.get_channel(voice_channel_id)
        if (
            not isinstance(voice_channel, discord.VoiceChannel)
            or not _has_human_members(voice_channel)
        ):
            return

        text = clean_message(message)
        if not text:
            return

        await self.queue.enqueue(
            message.guild,
            AudioRequest(
                text=text,
                voice=cfg.get("voice", DEFAULT_VOICE),
                voice_channel_id=voice_channel_id,
            ),
        )

    async def _send_voice_panel(self, channel: discord.VoiceChannel) -> None:
        if channel.id in self._panel_sent:
            return
        self._panel_sent.add(channel.id)

        try:
            await channel.send(
                embed=_voice_panel_embed(channel),
                view=self._panel_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            log.debug("voice panel send forbidden: channel_id=%s", channel.id)
        except discord.HTTPException:
            log.exception("voice panel send failed: channel_id=%s", channel.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Rule 01: 봇 자기 자신의 입/퇴장 이벤트로 알림 생성 금지
        if member.bot:
            return
        if before.channel == after.channel:
            return  # mute/deafen/server-mute 등 무관 이벤트

        try:
            await self._handle_voice_state(member, before, after)
        except Exception:
            log.exception(
                "on_voice_state_update failed: guild_id=%s",
                member.guild.id if member.guild else None,
            )

    async def _handle_voice_state(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Fast-path: watched 채널과 무관한 음성 이벤트는 패널만 보내고 종료.
        # ConfigStore singleton 덕분에 cached 값이 항상 최신이다.
        cached = self.store.get_cached_sync(member.guild.id)
        watched_id_cached = cached.get("voice_channel_id")
        joined_or_left_watched = (
            (after.channel and after.channel.id == watched_id_cached)
            or (before.channel and before.channel.id == watched_id_cached)
        )
        if not watched_id_cached or not joined_or_left_watched:
            if after.channel and isinstance(after.channel, discord.VoiceChannel):
                await self._send_voice_panel(after.channel)
            return

        cfg = await self.store.get(member.guild.id)
        watched_id = cfg.get("voice_channel_id")
        if not watched_id:
            if after.channel and isinstance(after.channel, discord.VoiceChannel):
                await self._send_voice_panel(after.channel)
            return

        await self._announce_voice_state(member, before, after, cfg, watched_id)

    async def _announce_voice_state(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
        cfg: dict,
        watched_id: int,
    ) -> None:
        if after.channel and isinstance(after.channel, discord.VoiceChannel):
            await self._send_voice_panel(after.channel)

        # 봇이 이미 watched 채널에 접속해 있을 때만 입/퇴장 안내. 안내 enqueue 가
        # _ensure_voice → connect() 를 트리거해 사용자가 호출하지 않은 봇 자동 입장
        # 으로 이어지는 것을 막는다.
        vc = member.guild.voice_client
        if vc is None or not vc.is_connected() or vc.channel.id != watched_id:
            return

        left_connected_channel = (
            before.channel is not None
            and before.channel.id == vc.channel.id
            and (
                after.channel is None
                or after.channel.id != vc.channel.id
            )
        )
        if left_connected_channel and not _has_human_members(
            vc.channel,
            excluding_member_id=member.id,
        ):
            log.info(
                "voice channel empty, disconnecting: guild_id=%s channel_id=%s",
                member.guild.id,
                vc.channel.id,
            )
            await self.queue.disconnect_voice(member.guild)
            return

        announcements: list[str] = []
        if after.channel and after.channel.id == watched_id and (
            before.channel is None or before.channel.id != watched_id
        ):
            announcements.append(f"{member.display_name}님 입장")
        if before.channel and before.channel.id == watched_id and (
            after.channel is None or after.channel.id != watched_id
        ):
            announcements.append(f"{member.display_name}님 퇴장")

        voice = cfg.get("voice", DEFAULT_VOICE)
        for text in announcements:
            await self.queue.enqueue(
                member.guild,
                AudioRequest(text=text, voice=voice, voice_channel_id=watched_id),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TTSCog(bot))
