"""User activity rank commands and collectors."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.rank_store import RankStore

log = logging.getLogger(__name__)


def _format_duration(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _format_score(score: int) -> str:
    weighted_seconds = max(0, int(score)) / 100
    hours, rem = divmod(int(weighted_seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _rank_icon(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}`")


class RankCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = RankStore()
        self._startup_synced = False
        self._reset_task.start()

    async def cog_unload(self) -> None:
        self._reset_task.cancel()

    @tasks.loop(minutes=1)
    async def _reset_task(self) -> None:
        await self.store.ensure_week()

    @_reset_task.before_loop
    async def _before_reset_task(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_synced:
            return
        self._startup_synced = True
        try:
            await self.store.ensure_week()
            for guild in self.bot.guilds:
                for channel in guild.voice_channels:
                    for member in channel.members:
                        if not member.bot:
                            await self.store.start_voice(guild.id, member.id, channel.id)
            log.info("rank startup voice sessions synced")
        except Exception:
            log.exception("rank startup sync failed")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        try:
            await self.store.ensure_week()
            await self.store.record_message(message.guild.id, message.author.id)
        except Exception:
            log.exception("rank message tracking failed: guild_id=%s", message.guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild is None:
            return
        if before.channel == after.channel:
            return

        try:
            await self.store.ensure_week()
            if before.channel is not None:
                await self.store.stop_voice(member.guild.id, member.id)
            if after.channel is not None:
                await self.store.start_voice(member.guild.id, member.id, after.channel.id)
        except Exception:
            log.exception("rank voice tracking failed: guild_id=%s", member.guild.id)

    @app_commands.command(name="leaderboard", description="이번 주 서버 활동 순위 확인")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        await self.store.ensure_week()
        rows = await self.store.leaderboard(interaction.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message("아직 집계된 활동 내역이 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(
            title="이번 주 활동 리더보드",
            description="서버 활동 점수 기준 TOP 10\n`음성 70% + 채팅 30%`",
            color=discord.Color.gold(),
        )
        for index, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"<@{row['user_id']}>"
            embed.add_field(
                name=f"{_rank_icon(index)} {name}",
                value=(
                    f"점수 **{_format_score(row['score'])}**\n"
                    f"음성 `{_format_duration(row['voice_seconds'])}` · "
                    f"채팅 `{_format_duration(row['chat_seconds'])}` · "
                    f"메시지 `{row['message_count']}개`"
                ),
                inline=False,
            )
        embed.set_footer(text="매주 금요일 00:00(KST) 초기화")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rank", description="멤버별 활동 내역 확인")
    async def rank(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        target = member or interaction.user
        await self.store.ensure_week()
        stats = await self.store.user_stats(interaction.guild_id, target.id)
        message = (
            f"**{target.display_name} 활동 내역**\n"
            f"활동 점수: {_format_score(stats['score'])} (음성 70% + 채팅 30%)\n"
            f"누적 활동: {_format_duration(stats['total_seconds'])}\n"
            f"음성 시간: {_format_duration(stats['voice_seconds'])}\n"
            f"채팅 시간: {_format_duration(stats['chat_seconds'])}\n"
            f"메시지: {stats['message_count']}개\n"
            "매주 금요일 00:00(KST)에 초기화됩니다."
        )
        await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankCog(bot))
