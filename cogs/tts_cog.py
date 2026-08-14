"""Phase 6 + 7 — 슬래시 명령 + 이벤트 핸들러.

본 cog 는 봇의 사용자 인터페이스 전체를 담당한다.
- Phase 6: `/목소리 /입장 /퇴장 /상태` 슬래시 명령
- Phase 7: `on_message` (TTS 합성), `on_voice_state_update` (입/퇴장 알림)

읽을 채널은 사용자가 고르지 않는다. `/입장` 과 음성 패널의 `TTS 켜기` 가
`tts_channel_id` / `voice_channel_id` 를 현재 음성 채널 ID 로 함께 써 넣는다
(음성 채널 채팅의 ID 는 음성 채널 ID 와 같다). 두 값을 따로 고르는 명령이
있던 시절이 있었지만, 연결 경로가 항상 둘을 덮어쓰므로 분리된 조합은 단
한 문장도 재생되지 않았다 — 그래서 명령을 남기지 않는다.

Rule 01 (봇 루프 방지): on_message / on_voice_state_update 첫 줄에 봇 가드.
Rule 02 (guild 격리): 모든 처리는 interaction.guild_id / message.guild.id 기준.
Rule 03 (복원력): 핸들러는 `log.exception` 으로 잡고 사용자에겐 무응답 또는 ephemeral 안내.
Rule 04 (시크릿/권한): 민감 명령(목소리)에 manage_channels 권한 체크.
"""
from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from cogs.audio_mode import (
    AUDIO_MODE_MUSIC,
    AUDIO_MODE_TTS,
    get_audio_mode_coordinator,
    music_enabled,
    mode_from_config,
)
from cogs.audio_queue import AudioQueue, AudioRequest
from cogs.config_store import ConfigStore
from cogs.preprocess import clean_message, normalize_pronunciations
from cogs.tts_engine import DEFAULT_VOICE, close_session, start_keepalive, warm_up
from cogs.ui import BRAND_COLOR, channel_ref, notice_embed

log = logging.getLogger(__name__)

VOICE_CHOICES = [
    app_commands.Choice(name="여성 · 차분", value="ko-KR-SunHiNeural"),
    app_commands.Choice(name="여성 · 또렷", value="ko-KR-JiMinNeural"),
    app_commands.Choice(name="여성 · 부드러움", value="ko-KR-SeoHyeonNeural"),
    app_commands.Choice(name="여성 · 편안함", value="ko-KR-SoonBokNeural"),
    app_commands.Choice(name="여성 · 경쾌", value="ko-KR-YuJinNeural"),
    app_commands.Choice(name="남성 · 자연스러움", value="ko-KR-InJoonNeural"),
    app_commands.Choice(name="남성 · 무게감", value="ko-KR-BongJinNeural"),
    app_commands.Choice(name="남성 · 친근함", value="ko-KR-GookMinNeural"),
    app_commands.Choice(name="남성 · 담백", value="ko-KR-HyunsuNeural"),
    app_commands.Choice(
        name="남성 · 다국어",
        value="ko-KR-HyunsuMultilingualNeural",
    ),
]

PANEL_COOLDOWN_SEC = 300  # unused — kept for reference

# 입장 인사에 띄울 GIF. 매번 1.4MB 를 올리는 대신 공개 저장소의 raw URL 을 쓴다
# (Discord 가 CDN 으로 캐시하므로 두 번째부터는 즉시 뜬다).
# 저장소 이름이나 기본 브랜치가 바뀌면 이 주소도 같이 고쳐야 한다 — 그때는
# 이미지만 안 뜨고 인사 임베드 자체는 그대로 나간다.
JOIN_GIF_URL = (
    "https://raw.githubusercontent.com/enderpawar/Koa-Discord_bot/"
    "main/assets/koa-join.gif"
)


def _join_greeting_embed(channel: discord.VoiceChannel) -> discord.Embed:
    embed = discord.Embed(
        title="코아 왔어요",
        description=f"이제 {channel.mention} 채팅을 읽어드릴게요.",
        color=BRAND_COLOR,
    )
    embed.set_image(url=JOIN_GIF_URL)
    return embed


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


def _connected_voice_client(
    guild: discord.Guild,
    channel_id: int,
) -> discord.VoiceClient | None:
    """Return the guild voice client only when it is connected to channel_id."""
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        return None
    channel = getattr(vc, "channel", None)
    if channel is None or channel.id != channel_id:
        return None
    return vc


def _tts_status_embed(cfg: dict) -> discord.Embed:
    tts_ch = cfg.get("tts_channel_id")
    vc_ch = cfg.get("voice_channel_id")
    voice = cfg.get("voice", DEFAULT_VOICE)
    rules = normalize_pronunciations(cfg.get("pronunciations"))
    ready = bool(tts_ch and vc_ch)

    embed = discord.Embed(
        title="TTS 상태",
        description="`/입장` 으로 연결한 음성 채널과 그 채널 채팅을 읽습니다.",
        color=BRAND_COLOR if ready else discord.Color.dark_grey(),
    )
    embed.add_field(name="입력 채널", value=channel_ref(tts_ch), inline=True)
    embed.add_field(name="음성 채널", value=channel_ref(vc_ch), inline=True)
    embed.add_field(name="보이스", value=f"`{_voice_label(voice)}`", inline=False)
    embed.add_field(
        name="발음 사전",
        value=f"{len(rules)}개 규칙" if rules else "등록된 규칙 없음",
        inline=False,
    )
    embed.add_field(
        name="상태",
        value="재생 준비됨" if ready else "`/입장` 으로 음성 채널에 연결해 주세요",
        inline=False,
    )
    embed.add_field(
        name="오디오 모드",
        value="`음악`" if mode_from_config(cfg) == AUDIO_MODE_MUSIC else "`TTS`",
        inline=False,
    )
    return embed


def _voice_panel_embed(channel: discord.VoiceChannel) -> discord.Embed:
    enabled = music_enabled()
    embed = discord.Embed(
        title="음성 모드 빠른 설정",
        description=(
            f"{channel.mention}에서 TTS와 음악 중 하나만 선택해 사용합니다."
            if enabled
            else f"{channel.mention}에서 TTS 연결을 설정합니다."
        ),
        color=BRAND_COLOR,
    )
    embed.add_field(
        name="동작",
        value=(
            "모드를 바꾸면 반대쪽의 현재 재생과 대기열이 정리됩니다."
            if enabled
            else "음악 기능은 현재 운영에서 비활성화되어 있습니다."
        ),
        inline=False,
    )
    return embed


class TTSControlView(discord.ui.View):
    def __init__(self, cog: "TTSCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if not music_enabled():
            for item in self.children:
                if item.custom_id == "koa_music:enable":
                    item.disabled = True

    @discord.ui.button(
        label="TTS 모드",
        emoji="🔊",
        style=discord.ButtonStyle.secondary,
        custom_id="koa_tts:enable",
    )
    async def enable_tts(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.enable_from_panel(interaction)

    @discord.ui.button(
        label="음악 모드",
        emoji="🎵",
        style=discord.ButtonStyle.secondary,
        custom_id="koa_music:enable",
    )
    async def enable_music(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.enable_music_from_panel(interaction)

    @discord.ui.button(
        label="연결 끄기",
        emoji="🔇",
        style=discord.ButtonStyle.secondary,
        custom_id="koa_tts:disable",
    )
    async def disable_tts(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.disable_from_panel(interaction)


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self.modes = get_audio_mode_coordinator(bot)
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
        await interaction.edit_original_response(
            embed=notice_embed(
                "TTS 모드 전환 중",
                "음악을 정리하고 TTS 연결을 준비합니다.",
                tone="info",
            ),
        )
        self._start_panel_connect(interaction, channel)

    async def enable_music_from_panel(self, interaction: discord.Interaction) -> None:
        if not music_enabled():
            await interaction.response.send_message(
                embed=notice_embed(
                    "음악 기능 비활성화",
                    "현재 음악 재생 기능은 운영에서 꺼져 있습니다. TTS 모드를 이용해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        voice_state = getattr(interaction.user, "voice", None)
        channel = voice_state.channel if voice_state else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=notice_embed(
                    "음성 채널 필요",
                    "먼저 이 음성 채널에 입장한 뒤 눌러주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
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
        await interaction.edit_original_response(
            embed=notice_embed(
                "음악 모드 전환 중",
                "현재 TTS와 대기 문장을 정리하고 음악 연결을 준비합니다.",
                tone="info",
            )
        )
        self._start_music_panel_connect(interaction, channel)

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

    def _start_music_panel_connect(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        guild_id = interaction.guild.id
        current = self._panel_connect_tasks.get(guild_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._connect_music_from_panel(interaction, channel),
            name=f"music-panel-connect-{guild_id}",
        )
        self._panel_connect_tasks[guild_id] = task

    async def _stop_music(self, guild: discord.Guild) -> bool:
        music_cog = self.bot.get_cog("MusicCog")
        stop = getattr(music_cog, "stop_for_mode_switch", None)
        if stop is None:
            return False
        return bool(await stop(guild))

    async def _connect_from_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        guild_id = interaction.guild.id
        already_here = _connected_voice_client(interaction.guild, channel.id) is not None
        greet = False
        try:
            async with self.modes.lock_for(guild_id):
                previous_mode = await self.modes.get_mode(guild_id)
                if previous_mode == AUDIO_MODE_MUSIC:
                    await self._stop_music(interaction.guild)
                    await self.queue.disconnect_voice(interaction.guild)
                await self.store.set(
                    guild_id,
                    audio_mode=AUDIO_MODE_TTS,
                    tts_channel_id=channel.id,
                    voice_channel_id=channel.id,
                )
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
            greet = not already_here or previous_mode == AUDIO_MODE_MUSIC
        try:
            try:
                await interaction.edit_original_response(embed=embed)
            except discord.HTTPException:
                log.debug(
                    "panel connection result response expired: guild_id=%s",
                    guild_id,
                )
            if greet:
                await self._announce_join(channel)
        finally:
            current = asyncio.current_task()
            if self._panel_connect_tasks.get(guild_id) is current:
                self._panel_connect_tasks.pop(guild_id, None)

    async def _connect_music_from_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        guild_id = interaction.guild.id
        try:
            async with self.modes.lock_for(guild_id):
                previous_mode = await self.modes.get_mode(guild_id)
                already_here = (
                    _connected_voice_client(interaction.guild, channel.id) is not None
                )
                if previous_mode != AUDIO_MODE_MUSIC or not already_here:
                    # Persist music first so new chat messages cannot race into TTS.
                    await self.store.set(
                        guild_id,
                        audio_mode=AUDIO_MODE_MUSIC,
                        tts_channel_id=channel.id,
                        voice_channel_id=channel.id,
                    )
                    if previous_mode == AUDIO_MODE_MUSIC:
                        await self._stop_music(interaction.guild)
                    await self.queue.disconnect_voice(interaction.guild)
                    await self.queue.ensure_voice(interaction.guild, channel.id)
        except discord.Forbidden:
            embed = notice_embed("권한 부족", "음성 채널 접속 권한이 없습니다.", tone="error")
        except Exception:
            log.exception("music mode join failed: guild_id=%s", guild_id)
            embed = notice_embed(
                "음악 모드 전환 실패",
                "음악 연결 중 오류가 발생했습니다. 버튼을 다시 눌러주세요.",
                tone="error",
            )
        else:
            embed = notice_embed(
                "음악 모드 활성화",
                f"{channel.mention}에서 `/재생` 명령으로 YouTube URL을 재생할 수 있어요.",
                tone="ok",
            )
        try:
            await interaction.edit_original_response(embed=embed)
        except discord.HTTPException:
            log.debug("music panel response expired: guild_id=%s", guild_id)
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

        async with self.modes.lock_for(interaction.guild.id):
            cfg = await self.store.get(interaction.guild.id)
            channel_id = interaction.channel.id if interaction.channel else None
            updates: dict[str, int | None] = {}
            if cfg.get("tts_channel_id") == channel_id:
                updates["tts_channel_id"] = 0
            if cfg.get("voice_channel_id") == channel_id:
                updates["voice_channel_id"] = 0
            if updates:
                await self.store.set(interaction.guild.id, **updates)
            if mode_from_config(cfg) == AUDIO_MODE_MUSIC:
                await self._stop_music(interaction.guild)
            await self.queue.disconnect_voice(interaction.guild)
        await interaction.edit_original_response(
            embed=notice_embed("TTS 비활성화", "이 채널의 TTS를 껐습니다.", tone="ok"),
        )

    @app_commands.command(name="목소리", description="TTS 목소리를 변경합니다")
    @app_commands.rename(voice="종류")
    @app_commands.describe(voice="사용할 한국어 TTS 목소리")
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

    @app_commands.command(
        name="입장",
        description="현재 참여 중인 음성 채널로 봇을 부르고 채널 채팅 TTS를 켭니다",
    )
    async def join(self, interaction: discord.Interaction) -> None:
        if interaction.guild is not None and (
            await self.modes.get_mode(interaction.guild.id) == AUDIO_MODE_MUSIC
        ):
            await interaction.response.send_message(
                embed=notice_embed(
                    "TTS 재생 불가",
                    "음악 모드에서는 TTS 재생이 불가능해요. "
                    "음성 패널에서 `TTS 모드`로 전환해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        voice_state = getattr(interaction.user, "voice", None)
        channel = voice_state.channel if voice_state else None
        if interaction.guild is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=notice_embed(
                    "음성 채널 필요",
                    "먼저 사용할 음성 채널에 입장한 뒤 `/입장`을 실행하세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        # 이미 그 채널에 있으면 인사를 다시 하지 않는다. `/입장` 을 두 번 눌렀다고
        # 채팅에 GIF 가 두 번 뜨면 곤란하다.
        already_here = (
            _connected_voice_client(interaction.guild, channel.id) is not None
        )
        try:
            async with self.modes.lock_for(interaction.guild.id):
                if await self.modes.get_mode(interaction.guild.id) == AUDIO_MODE_MUSIC:
                    await interaction.edit_original_response(
                        embed=notice_embed(
                            "TTS 재생 불가",
                            "음악 모드에서는 TTS 재생이 불가능해요. "
                            "음성 패널에서 `TTS 모드`로 전환해 주세요.",
                            tone="warn",
                        )
                    )
                    return
                await self.queue.ensure_voice(interaction.guild, channel.id)
                # 음성 채널 채팅의 ID는 음성 채널 ID와 같다. 명령 실행 시 한 번만
                # 저장하면 on_message 핫패스는 기존의 메모리 dict 조회 + 정수 비교만 한다.
                await self.store.set(
                    interaction.guild.id,
                    audio_mode=AUDIO_MODE_TTS,
                    tts_channel_id=channel.id,
                    voice_channel_id=channel.id,
                )
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
        log.info(
            "join configured voice chat TTS: guild_id=%s channel_id=%s user_id=%s",
            interaction.guild.id,
            channel.id,
            interaction.user.id,
        )
        await interaction.edit_original_response(
            embed=notice_embed(
                "TTS 활성화",
                f"{channel.mention} 에 입장했습니다. 이제 이 음성 채널의 채팅을 읽습니다.",
                tone="ok",
            ),
        )
        if not already_here:
            await self._announce_join(channel)

    @app_commands.command(name="퇴장", description="봇을 음성 채널에서 퇴장시킵니다")
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
            async with self.modes.lock_for(interaction.guild.id):
                if await self.modes.get_mode(interaction.guild.id) == AUDIO_MODE_MUSIC:
                    await self._stop_music(interaction.guild)
                await self.queue.disconnect_voice(interaction.guild)
                await self.store.set(
                    interaction.guild.id,
                    tts_channel_id=0,
                    voice_channel_id=0,
                )
        except Exception:
            log.exception("leave failed: guild_id=%s", interaction.guild_id)
        await interaction.edit_original_response(
            embed=notice_embed("음성 채널 퇴장", "퇴장했습니다.", tone="ok"),
        )

    @app_commands.command(name="상태", description="현재 TTS 설정과 연결 상태를 확인합니다")
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
        if mode_from_config(cached) != AUDIO_MODE_TTS:
            return
        tts_channel_id = cached.get("tts_channel_id")
        if not tts_channel_id or message.channel.id != tts_channel_id:
            return
        voice_channel_id = cached.get("voice_channel_id")
        # 텍스트 입력이 AudioQueue의 자동 연결 경로를 열지 않도록, 비동기
        # config 조회보다 먼저 현재 voice 연결을 확인한다. 정상 연결 시에는
        # 메모리 속성 조회뿐이라 TTS 지연에 영향을 주지 않는다.
        if (
            not voice_channel_id
            or _connected_voice_client(message.guild, voice_channel_id) is None
        ):
            return

        cfg = await self.store.get(message.guild.id)
        if mode_from_config(cfg) != AUDIO_MODE_TTS:
            return
        tts_channel_id = cfg.get("tts_channel_id")
        voice_channel_id = cfg.get("voice_channel_id")
        if not tts_channel_id or not voice_channel_id:
            return
        if message.channel.id != tts_channel_id:
            return
        # config 조회 중 퇴장/이동한 경우에도 큐에 넣지 않는다.
        if _connected_voice_client(message.guild, voice_channel_id) is None:
            return

        voice_channel = message.guild.get_channel(voice_channel_id)
        if (
            not isinstance(voice_channel, discord.VoiceChannel)
            or not _has_human_members(voice_channel)
        ):
            return

        text = clean_message(message, pronunciations=cfg.get("pronunciations"))
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

    async def _announce_join(self, channel: discord.VoiceChannel) -> None:
        """음성 채널 채팅에 입장 인사를 남긴다.

        명령 응답은 실행한 사람에게만 보이는 ephemeral 이라, 채널에 있는 다른
        사람들은 봇이 들어온 걸 모른다. 인사는 따로 공개로 보낸다.

        실패해도 입장 자체는 이미 끝났으므로 조용히 넘어간다 (Rule 03).
        """
        try:
            await channel.send(
                embed=_join_greeting_embed(channel),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            log.debug("join greeting forbidden: channel_id=%s", channel.id)
        except discord.HTTPException:
            log.debug(
                "join greeting failed: channel_id=%s", channel.id, exc_info=True
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
            if mode_from_config(cfg) == AUDIO_MODE_MUSIC:
                await self._stop_music(member.guild)
            await self.queue.disconnect_voice(member.guild)
            return

        if mode_from_config(cfg) != AUDIO_MODE_TTS:
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
