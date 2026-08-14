"""Exclusive music-mode slash commands for public YouTube URLs."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.audio_mode import AUDIO_MODE_MUSIC, get_audio_mode_coordinator
from cogs.music_player import (
    MusicError,
    MusicPlayer,
    MusicQueueFull,
    MusicTrack,
)
from cogs.tts_cog import TTSCog
from cogs.ui import BRAND_COLOR, notice_embed


log = logging.getLogger(__name__)


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "길이 정보 없음"
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _track_line(track: MusicTrack, index: int | None = None) -> str:
    prefix = f"`{index}` " if index is not None else ""
    title = track.title if len(track.title) <= 80 else track.title[:79] + "…"
    return f"{prefix}[{title}]({track.webpage_url}) · <@{track.requester_id}>"


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        tts_cog = bot.get_cog("TTSCog")
        if not isinstance(tts_cog, TTSCog):
            raise RuntimeError("MusicCog requires TTSCog to load first")
        self.tts_cog = tts_cog
        self.modes = get_audio_mode_coordinator(bot)
        self.store = self.modes.store
        self.player = MusicPlayer(bot, tts_cog.queue, self.modes)

    async def cog_unload(self) -> None:
        await self.player.shutdown()

    async def stop_for_mode_switch(self, guild: discord.Guild) -> bool:
        return await self.player.stop_guild(guild)

    async def stop_for_disconnect(self, guild: discord.Guild) -> bool:
        return await self.player.stop_guild(guild)

    async def _music_channel(
        self, interaction: discord.Interaction
    ) -> discord.VoiceChannel | None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있어요.", tone="warn"),
                ephemeral=True,
            )
            return None
        if await self.modes.get_mode(interaction.guild.id) != AUDIO_MODE_MUSIC:
            await interaction.response.send_message(
                embed=notice_embed(
                    "음악 재생 불가",
                    "TTS 모드에서는 음악을 재생할 수 없어요. "
                    "음성 패널에서 `음악 모드`로 전환해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return None

        voice_state = getattr(interaction.user, "voice", None)
        channel = voice_state.channel if voice_state else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=notice_embed(
                    "음성 채널 필요",
                    "먼저 봇과 함께 사용할 음성 채널에 입장해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return None

        configured = (await self.store.get(interaction.guild.id)).get("voice_channel_id")
        vc = interaction.guild.voice_client
        connected_channel = getattr(vc, "channel", None) if vc and vc.is_connected() else None
        expected_id = connected_channel.id if connected_channel is not None else configured
        if expected_id and expected_id != channel.id:
            await interaction.response.send_message(
                embed=notice_embed(
                    "다른 음성 채널",
                    f"봇이 접속한 <#{expected_id}> 채널에 입장한 후 실행해 주세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return None
        return channel

    @app_commands.command(name="재생", description="YouTube URL의 음악을 재생하거나 대기열에 추가합니다")
    @app_commands.rename(url="주소")
    @app_commands.describe(url="공개된 단일 YouTube 영상 URL")
    async def play(self, interaction: discord.Interaction, url: str) -> None:
        channel = await self._music_channel(interaction)
        if channel is None:
            return

        await interaction.response.defer(thinking=True)
        try:
            track = await self.player.extractor.extract(
                url,
                requester_id=interaction.user.id,
                request_channel_id=interaction.channel_id,
                voice_channel_id=channel.id,
            )
        except MusicError as exc:
            await interaction.edit_original_response(
                embed=notice_embed("YouTube 불러오기 실패", str(exc), tone="error")
            )
            return

        async with self.modes.lock_for(interaction.guild.id):
            if await self.modes.get_mode(interaction.guild.id) != AUDIO_MODE_MUSIC:
                await interaction.edit_original_response(
                    embed=notice_embed(
                        "모드가 변경됨",
                        "영상을 불러오는 동안 TTS 모드로 전환되어 요청을 취소했어요.",
                        tone="warn",
                    )
                )
                return
            try:
                position = await self.player.enqueue(interaction.guild, track)
            except MusicQueueFull as exc:
                await interaction.edit_original_response(
                    embed=notice_embed("대기열이 가득 찼어요", str(exc), tone="warn")
                )
                return

        title = "재생을 시작합니다" if position == 0 else "대기열에 추가했어요"
        embed = discord.Embed(
            title=title,
            description=f"[{track.title}]({track.webpage_url})",
            color=BRAND_COLOR,
        )
        embed.add_field(name="길이", value=_format_duration(track.duration), inline=True)
        if position:
            embed.add_field(name="대기 순서", value=f"{position}번", inline=True)
        embed.set_footer(text=f"요청자: {interaction.user.display_name}")
        await interaction.edit_original_response(embed=embed)
        log.info(
            "music enqueued: guild_id=%s user_id=%s title=%r position=%d",
            interaction.guild.id,
            interaction.user.id,
            track.title,
            position,
        )

    @app_commands.command(name="스킵", description="현재 음악을 넘기고 다음 곡을 재생합니다")
    async def skip(self, interaction: discord.Interaction) -> None:
        channel = await self._music_channel(interaction)
        if channel is None:
            return
        async with self.modes.lock_for(interaction.guild.id):
            track = await self.player.skip(interaction.guild)
        if track is None:
            await interaction.response.send_message(
                embed=notice_embed("스킵 불가", "현재 재생 중인 음악이 없어요.", tone="warn"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=notice_embed("스킵", f"`{track.title}`을(를) 넘겼어요.", tone="ok")
        )

    @app_commands.command(name="중지", description="현재 음악과 전체 대기열을 정리합니다")
    async def stop(self, interaction: discord.Interaction) -> None:
        channel = await self._music_channel(interaction)
        if channel is None:
            return
        async with self.modes.lock_for(interaction.guild.id):
            stopped = await self.player.stop_guild(interaction.guild)
        await interaction.response.send_message(
            embed=notice_embed(
                "음악 중지",
                "재생 중인 음악과 대기열을 모두 정리했어요."
                if stopped
                else "정리할 음악이 없어요.",
                tone="ok" if stopped else "info",
            )
        )

    @app_commands.command(name="재생목록", description="현재 음악과 남은 대기열을 확인합니다")
    async def playlist(self, interaction: discord.Interaction) -> None:
        channel = await self._music_channel(interaction)
        if channel is None:
            return
        current, pending = self.player.snapshot(interaction.guild.id)
        if current is None and not pending:
            await interaction.response.send_message(
                embed=notice_embed("재생목록", "현재 재생 중이거나 기다리는 음악이 없어요."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="재생목록", color=BRAND_COLOR)
        embed.add_field(
            name="현재 재생",
            value=_track_line(current) if current is not None else "재생 준비 중",
            inline=False,
        )
        if pending:
            embed.add_field(
                name=f"대기열 · {len(pending)}곡",
                value="\n".join(_track_line(track, i) for i, track in enumerate(pending, 1)),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
