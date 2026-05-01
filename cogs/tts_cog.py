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

import discord
from discord import app_commands
from discord.ext import commands

from cogs.audio_queue import AudioQueue, AudioRequest
from cogs.config_store import ConfigStore
from cogs.preprocess import clean_message
from cogs.tts_engine import DEFAULT_VOICE, close_session, start_keepalive, warm_up

log = logging.getLogger(__name__)

VOICE_CHOICES = [
    app_commands.Choice(name="여성-차분 (SunHi)", value="ko-KR-SunHiNeural"),
    app_commands.Choice(name="남성-자연 (InJoon)", value="ko-KR-InJoonNeural"),
    app_commands.Choice(name="남성-무게감 (BongJin)", value="ko-KR-BongJinNeural"),
    app_commands.Choice(name="남성-친근 (GookMin)", value="ko-KR-GookMinNeural"),
]


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self.queue = AudioQueue()
        self._warmup_task = asyncio.create_task(
            self._warm_start(), name="tts-warm-start"
        )

    async def cog_unload(self) -> None:
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
            f"TTS 채널을 {channel.mention} 으로 설정했습니다.", ephemeral=True
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
            f"음성 채널을 {channel.mention} 으로 설정했습니다.", ephemeral=True
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
            f"보이스를 **{voice.name}** 으로 설정했습니다.", ephemeral=True
        )

    @app_commands.command(name="join", description="설정된 음성 채널로 입장")
    async def join(self, interaction: discord.Interaction) -> None:
        cfg = await self.store.get(interaction.guild_id)
        ch_id = cfg.get("voice_channel_id")
        channel = interaction.guild.get_channel(ch_id) if ch_id else None
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "먼저 `/setvc` 로 음성 채널을 지정하세요.", ephemeral=True
            )
            return
        try:
            vc = interaction.guild.voice_client
            if vc is not None and vc.is_connected():
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
            else:
                await channel.connect(reconnect=True, self_deaf=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "음성 채널 접속 권한이 없습니다.", ephemeral=True
            )
            return
        except Exception:
            log.exception("join failed: guild_id=%s", interaction.guild_id)
            await interaction.response.send_message(
                "입장 중 오류가 발생했습니다.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{channel.mention} 에 입장했습니다.", ephemeral=True
        )

    @app_commands.command(name="leave", description="음성 채널에서 퇴장")
    async def leave(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            await interaction.response.send_message(
                "음성 채널에 없습니다.", ephemeral=True
            )
            return
        try:
            await vc.disconnect()
        except Exception:
            log.exception("leave failed: guild_id=%s", interaction.guild_id)
        await interaction.response.send_message("퇴장했습니다.", ephemeral=True)

    @app_commands.command(name="status", description="현재 설정 확인")
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = await self.store.get(interaction.guild_id)
        tts_ch = cfg.get("tts_channel_id")
        vc_ch = cfg.get("voice_channel_id")
        voice = cfg.get("voice", DEFAULT_VOICE)
        msg = (
            f"📝 TTS 채널: {f'<#{tts_ch}>' if tts_ch else '미설정'}\n"
            f"🔊 음성 채널: {f'<#{vc_ch}>' if vc_ch else '미설정'}\n"
            f"🎙 보이스: `{voice}`"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    # ---------- 권한/오류 안내 ----------

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await self._safe_send(
                interaction, "이 명령은 `채널 관리` 권한이 필요합니다.", ephemeral=True
            )
            return
        log.exception(
            "slash command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        await self._safe_send(
            interaction, "명령 처리 중 오류가 발생했습니다.", ephemeral=True
        )

    @staticmethod
    async def _safe_send(
        interaction: discord.Interaction, content: str, *, ephemeral: bool = True
    ) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
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
        cfg = await self.store.get(message.guild.id)
        tts_channel_id = cfg.get("tts_channel_id")
        voice_channel_id = cfg.get("voice_channel_id")
        if not tts_channel_id or not voice_channel_id:
            return
        if message.channel.id != tts_channel_id:
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
        cfg = await self.store.get(member.guild.id)
        watched_id = cfg.get("voice_channel_id")
        if not watched_id:
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
