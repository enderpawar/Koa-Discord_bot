"""Administrator interface for runtime bot settings."""
from __future__ import annotations

import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from cogs.config_store import ConfigStore
from cogs.rank_cog import DEFAULT_LEADERBOARD_POST_TIME

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _configured(value: str | None, *, secret: bool = False) -> str:
    if not value:
        return "미설정"
    return "설정됨" if secret else f"`{value}`"


def _settings_message(cfg: dict) -> str:
    daily_enabled = "켜짐" if cfg.get("leaderboard_daily_enabled", False) else "꺼짐"
    post_time = cfg.get("leaderboard_post_time", DEFAULT_LEADERBOARD_POST_TIME)
    leaderboard_channel_id = cfg.get("leaderboard_channel_id")
    tts_channel_id = cfg.get("tts_channel_id")
    voice_channel_id = cfg.get("voice_channel_id")
    last_post_date = cfg.get("leaderboard_last_post_date")

    return (
        "**관리자 설정 패널**\n"
        f"TTS 채널: {f'<#{tts_channel_id}>' if tts_channel_id else '미설정'}\n"
        f"음성 채널: {f'<#{voice_channel_id}>' if voice_channel_id else '미설정'}\n"
        f"일일 리더보드: `{daily_enabled}`\n"
        f"리더보드 채널: {f'<#{leaderboard_channel_id}>' if leaderboard_channel_id else '미설정'}\n"
        f"리더보드 발송 시각: `{post_time}` KST\n"
        f"마지막 자동 발송일: `{last_post_date or '없음'}`"
    )


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


class LeaderboardTimeModal(discord.ui.Modal, title="리더보드 발송 시각"):
    post_time = discord.ui.TextInput(
        label="KST 기준 HH:MM",
        placeholder="23:59",
        default=DEFAULT_LEADERBOARD_POST_TIME,
        min_length=5,
        max_length=5,
    )

    def __init__(self, cog: "AdminCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        value = str(self.post_time.value).strip()
        if not _TIME_RE.match(value):
            await interaction.response.send_message(
                "`23:59` 같은 HH:MM 형식으로 입력하세요.",
                ephemeral=True,
            )
            return

        await self.cog.store.set(interaction.guild_id, leaderboard_post_time=value)
        await interaction.response.send_message(
            f"리더보드 발송 시각을 `{value}` KST로 저장했습니다.",
            ephemeral=True,
        )


class LeaderboardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog: "AdminCog", view_ref: "AdminPanelView") -> None:
        super().__init__(
            placeholder="리더보드 발송 채널 선택",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.cog = cog
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("텍스트 채널만 선택할 수 있습니다.", ephemeral=True)
            return
        await self.cog.store.set(interaction.guild_id, leaderboard_channel_id=channel.id)
        await self.view_ref.refresh_panel(interaction)


class AdminPanelView(discord.ui.View):
    def __init__(self, cog: "AdminCog") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(LeaderboardChannelSelect(cog, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not _is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    async def refresh_panel(self, interaction: discord.Interaction) -> None:
        cfg = await self.cog.store.get(interaction.guild_id)
        await interaction.response.edit_message(content=_settings_message(cfg), view=self)

    @discord.ui.button(label="자동발송 켜기/끄기", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_daily(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        cfg = await self.cog.store.get(interaction.guild_id)
        next_enabled = not bool(cfg.get("leaderboard_daily_enabled", False))
        if next_enabled and not cfg.get("leaderboard_channel_id"):
            await interaction.response.send_message(
                "먼저 리더보드 발송 채널을 선택하세요.",
                ephemeral=True,
            )
            return
        await self.cog.store.set(interaction.guild_id, leaderboard_daily_enabled=next_enabled)
        await self.refresh_panel(interaction)

    @discord.ui.button(label="발송시각", style=discord.ButtonStyle.secondary, row=1)
    async def set_time(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(LeaderboardTimeModal(self.cog))

    @discord.ui.button(label="즉시발송", style=discord.ButtonStyle.primary, row=1)
    async def post_now(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.cog.post_configured_leaderboard(interaction)
        await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="새로고침", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.refresh_panel(interaction)


class AdminCog(commands.Cog):
    admin = app_commands.Group(
        name="admin",
        description="봇 관리자 설정",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()

    async def _guild_config(self, interaction: discord.Interaction) -> dict:
        if interaction.guild_id is None:
            raise ValueError("guild only")
        return await self.store.get(interaction.guild_id)

    @admin.command(name="status", description="현재 서버 봇 설정 확인")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        cfg = await self._guild_config(interaction)
        await interaction.response.send_message(_settings_message(cfg), ephemeral=True)

    @admin.command(name="panel", description="버튼/선택 메뉴로 관리자 설정 편집")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        cfg = await self._guild_config(interaction)
        await interaction.response.send_message(
            _settings_message(cfg),
            view=AdminPanelView(self),
            ephemeral=True,
        )

    @admin.command(name="leaderboard", description="일일 리더보드 자동 발송 설정")
    @app_commands.describe(
        enabled="매일 자동 발송 여부",
        channel="리더보드를 발송할 텍스트 채널",
        post_time="KST 기준 HH:MM 형식. 예: 23:59",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        channel: discord.TextChannel | None = None,
        post_time: str = DEFAULT_LEADERBOARD_POST_TIME,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        if not _TIME_RE.match(post_time):
            await interaction.response.send_message(
                "`post_time`은 `23:59` 같은 HH:MM 형식이어야 합니다.",
                ephemeral=True,
            )
            return

        cfg = await self.store.get(interaction.guild_id)
        channel_id = channel.id if channel else cfg.get("leaderboard_channel_id")
        if enabled and not channel_id:
            await interaction.response.send_message(
                "자동 발송을 켜려면 리더보드 채널을 지정해야 합니다.",
                ephemeral=True,
            )
            return

        await self.store.set(
            interaction.guild_id,
            leaderboard_daily_enabled=enabled,
            leaderboard_channel_id=channel_id,
            leaderboard_post_time=post_time,
        )
        state = "켜짐" if enabled else "꺼짐"
        await interaction.response.send_message(
            f"일일 리더보드 자동 발송: `{state}`\n"
            f"채널: {f'<#{channel_id}>' if channel_id else '미설정'}\n"
            f"시각: `{post_time}` KST",
            ephemeral=True,
        )

    @admin.command(name="post_leaderboard", description="설정된 채널로 리더보드 즉시 발송")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.post_configured_leaderboard(interaction)
        await interaction.followup.send(result, ephemeral=True)

    async def post_configured_leaderboard(self, interaction: discord.Interaction) -> str:
        if interaction.guild is None or interaction.guild_id is None:
            return "서버에서만 사용할 수 있습니다."

        cfg = await self.store.get(interaction.guild_id)
        channel_id = cfg.get("leaderboard_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return "먼저 `/admin leaderboard` 또는 `/admin panel`로 리더보드 채널을 설정하세요."

        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "_leaderboard_embed"):
            return "랭킹 기능을 찾을 수 없습니다."

        await rank_cog.store.ensure_week()
        embed = await rank_cog._leaderboard_embed(interaction.guild, limit=10)
        if embed is None:
            return "아직 집계된 활동 내역이 없습니다."

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return f"{channel.mention}에 리더보드를 발송했습니다."

    @admin.command(name="env", description="봇 런타임 환경변수 상태 확인")
    @app_commands.checks.has_permissions(administrator=True)
    async def env(self, interaction: discord.Interaction) -> None:
        message = (
            "**환경변수 상태**\n"
            f"DISCORD_TOKEN: {_configured(os.getenv('DISCORD_TOKEN'), secret=True)}\n"
            f"AZURE_SPEECH_KEY: {_configured(os.getenv('AZURE_SPEECH_KEY'), secret=True)}\n"
            f"AZURE_SPEECH_REGION: {_configured(os.getenv('AZURE_SPEECH_REGION'))}\n"
            f"CONFIG_PATH: {_configured(os.getenv('CONFIG_PATH') or 'config.json')}\n"
            f"RANK_PATH: {_configured(os.getenv('RANK_PATH') or 'rank_stats.json')}\n"
            f"TEST_GUILD_ID: {_configured(os.getenv('TEST_GUILD_ID'))}"
        )
        await interaction.response.send_message(message, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await self._safe_send(interaction, "이 명령은 `관리자` 권한이 필요합니다.")
            return
        log.exception(
            "admin command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        await self._safe_send(interaction, "관리자 명령 처리 중 오류가 발생했습니다.")

    @staticmethod
    async def _safe_send(interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            log.exception("failed to send admin interaction response")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
