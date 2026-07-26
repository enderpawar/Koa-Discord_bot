"""User activity rank commands and collectors."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.config_store import ConfigStore
from cogs.rank_store import RANK_FLUSH_INTERVAL_SEC, RankStore
from cogs.ui import BRAND_COLOR, notice_embed

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_LEADERBOARD_POST_TIME = "00:00"


def _format_duration(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _format_score(score: int) -> str:
    return f"{max(0, int(score)) / 100:.2f}점"


def _rank_icon(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}`")


def _leaderboard_post_is_due(
    now: datetime,
    *,
    post_time: str,
    last_post_date: str | None,
) -> bool:
    """설정 시각 이후 오늘 아직 발송하지 않았으면 True."""
    try:
        hour, minute = (int(part) for part in post_time.split(":", 1))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return False
    except (AttributeError, TypeError, ValueError):
        return False

    today = now.date().isoformat()
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return last_post_date != today and now >= scheduled


def _rank_stats_embed(display_name: str, stats: dict[str, int]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{display_name} 활동 내역",
        description="이번 주 서버 활동 기준 개인 통계입니다.",
        color=BRAND_COLOR,
    )
    embed.add_field(name="활동 점수", value=f"**{_format_score(stats['score'])}**", inline=False)
    embed.add_field(
        name="활동 지표",
        value=(
            f"음성 시간: `{_format_duration(stats['voice_seconds'])}`\n"
            f"메시지: `{stats['message_count']}개`"
        ),
        inline=False,
    )
    embed.add_field(
        name="점수 기준",
        value="서버 내 최고 음성 시간과 최고 메시지 수를 각각 100%로 환산해 `음성 70% + 메시지 30%`로 계산합니다.",
        inline=False,
    )
    embed.set_footer(text="매주 금요일 00:00(KST) 초기화")
    return embed


class RankCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = RankStore()
        self.config = ConfigStore()
        self._startup_synced = False
        self._reset_task.start()
        self._leaderboard_task.start()
        # 핫 경로(record_message 등)는 인메모리만 갱신하므로, 대기 중 변경을
        # 주기적으로 디스크에 내려쓴다. interval<=0 이면 store 가 즉시 기록하므로 불필요.
        if RANK_FLUSH_INTERVAL_SEC > 0:
            self._flush_loop.start()

    async def cog_unload(self) -> None:
        self._reset_task.cancel()
        self._leaderboard_task.cancel()
        if self._flush_loop.is_running():
            self._flush_loop.cancel()
        # 종료 시 마지막 대기 변경을 유실하지 않도록 1회 flush.
        try:
            await self.store.flush()
        except Exception:
            log.exception("rank flush on unload failed")

    @tasks.loop(minutes=1)
    async def _reset_task(self) -> None:
        await self.store.ensure_week()

    @_reset_task.before_loop
    async def _before_reset_task(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=max(1.0, RANK_FLUSH_INTERVAL_SEC))
    async def _flush_loop(self) -> None:
        try:
            await self.store.flush()
        except Exception:
            log.exception("rank periodic flush failed")

    @_flush_loop.before_loop
    async def _before_flush_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def _leaderboard_task(self) -> None:
        now = datetime.now(KST)
        for guild in self.bot.guilds:
            try:
                await self._post_scheduled_leaderboard(guild, now)
            except Exception:
                log.exception("leaderboard scheduler failed: guild_id=%s", guild.id)

    async def _post_scheduled_leaderboard(
        self,
        guild: discord.Guild,
        now: datetime,
    ) -> None:
        cfg = await self.config.get(guild.id)
        if not cfg.get("leaderboard_daily_enabled", False):
            return

        post_time = cfg.get("leaderboard_post_time") or DEFAULT_LEADERBOARD_POST_TIME
        if not _leaderboard_post_is_due(
            now,
            post_time=post_time,
            last_post_date=cfg.get("leaderboard_last_post_date"),
        ):
            return

        channel_id = cfg.get("leaderboard_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            log.warning(
                "leaderboard channel missing: guild_id=%s channel_id=%s",
                guild.id,
                channel_id,
            )
            return

        await self.store.ensure_week()
        embed = await self._leaderboard_embed(guild, limit=10)
        if embed is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            log.warning(
                "leaderboard channel forbidden: guild_id=%s channel_id=%s",
                guild.id,
                channel.id,
            )
            return
        except discord.HTTPException:
            log.exception(
                "leaderboard scheduled post failed: guild_id=%s channel_id=%s",
                guild.id,
                channel.id,
            )
            return
        await self.config.set(
            guild.id,
            leaderboard_last_post_date=now.date().isoformat(),
        )

    @_leaderboard_task.before_loop
    async def _before_leaderboard_task(self) -> None:
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

    @app_commands.command(name="활동순위", description="이번 주 서버 활동 순위를 확인합니다")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        await self.store.ensure_week()
        embed = await self._leaderboard_embed(interaction.guild, limit=10)
        if embed is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "활동 내역 없음",
                    "아직 집계된 활동 내역이 없습니다.",
                    tone="info",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed)

    async def _leaderboard_embed(
        self, guild: discord.Guild, *, limit: int = 10
    ) -> discord.Embed | None:
        rows = await self.store.leaderboard(guild.id, limit=limit)
        if not rows:
            return None
        embed = discord.Embed(
            title="이번 주 활동 리더보드",
            description="서버 활동 점수 기준 TOP 10",
            color=discord.Color.gold(),
        )
        for index, row in enumerate(rows, start=1):
            member = guild.get_member(row["user_id"])
            name = member.display_name if member else f"<@{row['user_id']}>"
            embed.add_field(
                name=f"{_rank_icon(index)} {name}",
                value=(
                    f"점수: **{_format_score(row['score'])}**\n"
                    f"음성: `{_format_duration(row['voice_seconds'])}`\n"
                    f"메시지: `{row['message_count']}개`"
                ),
                inline=False,
            )
        embed.add_field(
            name="점수 기준",
            value="서버 내 최고 음성 시간과 최고 메시지 수를 각각 100%로 환산해 `음성 70% + 메시지 30%`로 계산합니다.",
            inline=False,
        )
        embed.set_footer(text="매주 금요일 00:00(KST) 초기화")
        return embed

    @app_commands.command(name="활동점수", description="나 또는 다른 멤버의 활동 점수를 확인합니다")
    @app_commands.rename(member="멤버")
    @app_commands.describe(member="조회할 멤버(생략하면 본인)")
    async def rank(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        target = member or interaction.user
        await self.store.ensure_week()
        stats = await self.store.user_stats(interaction.guild_id, target.id)
        await interaction.response.send_message(
            embed=_rank_stats_embed(target.display_name, stats),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankCog(bot))
