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
from cogs.ui import BRAND_COLOR, INFO_COLOR, channel_ref, enabled_label, notice_embed

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _web_dashboard_url() -> str | None:
    public_url = os.getenv("ADMIN_WEB_PUBLIC_URL", "").strip()
    if public_url:
        return public_url.rstrip("/")
    if not os.getenv("ADMIN_WEB_TOKEN"):
        return None
    host = os.getenv("ADMIN_WEB_HOST", "127.0.0.1")
    port = os.getenv("ADMIN_WEB_PORT") or os.getenv("PORT") or "8080"
    if host == "0.0.0.0":
        return None
    return f"http://{host}:{port}"


def _configured(value: str | None, *, secret: bool = False) -> str:
    if not value:
        return "미설정"
    return "설정됨" if secret else f"`{value}`"


def _settings_message(cfg: dict) -> str:
    daily_enabled = enabled_label(bool(cfg.get("leaderboard_daily_enabled", False)))
    post_time = cfg.get("leaderboard_post_time", DEFAULT_LEADERBOARD_POST_TIME)
    leaderboard_channel_id = cfg.get("leaderboard_channel_id")
    tts_channel_id = cfg.get("tts_channel_id")
    voice_channel_id = cfg.get("voice_channel_id")
    last_post_date = cfg.get("leaderboard_last_post_date")

    return (
        "**관리자 설정 패널**\n"
        f"TTS 채널: {channel_ref(tts_channel_id)}\n"
        f"음성 채널: {channel_ref(voice_channel_id)}\n"
        f"일일 리더보드: `{daily_enabled}`\n"
        f"리더보드 채널: {channel_ref(leaderboard_channel_id)}\n"
        f"리더보드 발송 시각: `{post_time}` KST\n"
        f"마지막 자동 발송일: `{last_post_date or '없음'}`"
    )


def _settings_embed(cfg: dict) -> discord.Embed:
    daily_enabled = bool(cfg.get("leaderboard_daily_enabled", False))
    post_time = cfg.get("leaderboard_post_time", DEFAULT_LEADERBOARD_POST_TIME)
    leaderboard_channel_id = cfg.get("leaderboard_channel_id")
    tts_channel_id = cfg.get("tts_channel_id")
    voice_channel_id = cfg.get("voice_channel_id")
    last_post_date = cfg.get("leaderboard_last_post_date") or "없음"

    embed = discord.Embed(
        title="관리자 설정",
        description="서버 운영 설정을 확인하고 아래 컨트롤로 바로 변경합니다.",
        color=BRAND_COLOR if daily_enabled else INFO_COLOR,
    )
    embed.add_field(
        name="TTS",
        value=(
            f"입력 채널: {channel_ref(tts_channel_id)}\n"
            f"음성 채널: {channel_ref(voice_channel_id)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="일일 리더보드",
        value=(
            f"자동 발송: `{enabled_label(daily_enabled)}`\n"
            f"발송 채널: {channel_ref(leaderboard_channel_id)}\n"
            f"발송 시각: `{post_time}` KST\n"
            f"마지막 발송: `{last_post_date}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="작업",
        value=(
            "채널 선택 메뉴로 발송 채널을 지정합니다.\n"
            "`자동 발송 토글`로 매일 발송을 켜거나 끕니다.\n"
            "`발송 시각 변경`에서 `23:59` 형식으로 시간을 입력합니다."
        ),
        inline=False,
    )
    embed.set_footer(text="모든 시각은 KST 기준입니다.")
    return embed


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
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        value = str(self.post_time.value).strip()
        if not _TIME_RE.match(value):
            await interaction.response.send_message(
                embed=notice_embed(
                    "입력 형식 오류",
                    "`23:59` 같은 HH:MM 형식으로 입력하세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        await self.cog.store.set(interaction.guild_id, leaderboard_post_time=value)
        await interaction.response.send_message(
            embed=notice_embed(
                "발송 시각 저장",
                f"리더보드 발송 시각을 `{value}` KST로 저장했습니다.",
                tone="ok",
            ),
            ephemeral=True,
        )


class LeaderboardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog: "AdminCog", view_ref: "AdminPanelView") -> None:
        super().__init__(
            placeholder="일일 리더보드를 보낼 텍스트 채널 선택",
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
            await interaction.response.send_message(
                embed=notice_embed("채널 선택 오류", "텍스트 채널만 선택할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        await self.cog.store.set(interaction.guild_id, leaderboard_channel_id=channel.id)
        await self.view_ref.refresh_panel(interaction)


class AdminPanelView(discord.ui.View):
    def __init__(self, cog: "AdminCog") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(LeaderboardChannelSelect(cog, self))
        web_url = _web_dashboard_url()
        if web_url:
            self.add_item(
                discord.ui.Button(
                    label="웹 대시보드 열기",
                    style=discord.ButtonStyle.link,
                    url=web_url,
                    row=2,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return False
        if not _is_admin(interaction):
            await interaction.response.send_message(
                embed=notice_embed("권한 필요", "관리자만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return False
        return True

    async def refresh_panel(self, interaction: discord.Interaction) -> None:
        cfg = await self.cog.store.get(interaction.guild_id)
        await interaction.response.edit_message(
            content=None, embed=_settings_embed(cfg), view=self
        )

    @discord.ui.button(label="자동 발송 토글", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_daily(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        cfg = await self.cog.store.get(interaction.guild_id)
        next_enabled = not bool(cfg.get("leaderboard_daily_enabled", False))
        if next_enabled and not cfg.get("leaderboard_channel_id"):
            await interaction.response.send_message(
                embed=notice_embed(
                    "채널 필요",
                    "먼저 리더보드 발송 채널을 선택하세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return
        await self.cog.store.set(interaction.guild_id, leaderboard_daily_enabled=next_enabled)
        await self.refresh_panel(interaction)

    @discord.ui.button(label="발송 시각 변경", style=discord.ButtonStyle.secondary, row=1)
    async def set_time(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(LeaderboardTimeModal(self.cog))

    @discord.ui.button(label="리더보드 발송", style=discord.ButtonStyle.primary, row=1)
    async def post_now(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result, tone = await self.cog.post_configured_leaderboard(interaction)
        await interaction.followup.send(
            embed=notice_embed("리더보드 발송", result, tone=tone),
            ephemeral=True,
        )

    @discord.ui.button(label="상태 새로고침", style=discord.ButtonStyle.secondary, row=1)
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
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        cfg = await self._guild_config(interaction)
        await interaction.response.send_message(embed=_settings_embed(cfg), ephemeral=True)

    @admin.command(name="panel", description="버튼/선택 메뉴로 관리자 설정 편집")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        cfg = await self._guild_config(interaction)
        await interaction.response.send_message(
            embed=_settings_embed(cfg),
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
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        if not _TIME_RE.match(post_time):
            await interaction.response.send_message(
                embed=notice_embed(
                    "입력 형식 오류",
                    "`post_time`은 `23:59` 같은 HH:MM 형식이어야 합니다.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        cfg = await self.store.get(interaction.guild_id)
        channel_id = channel.id if channel else cfg.get("leaderboard_channel_id")
        if enabled and not channel_id:
            await interaction.response.send_message(
                embed=notice_embed(
                    "채널 필요",
                    "자동 발송을 켜려면 리더보드 채널을 지정해야 합니다.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        await self.store.set(
            interaction.guild_id,
            leaderboard_daily_enabled=enabled,
            leaderboard_channel_id=channel_id,
            leaderboard_post_time=post_time,
        )
        await interaction.response.send_message(
            embed=notice_embed(
                "리더보드 자동 발송 설정",
                f"자동 발송: `{enabled_label(enabled)}`\n"
                f"채널: {channel_ref(channel_id)}\n"
                f"시각: `{post_time}` KST",
                tone="ok",
            ),
            ephemeral=True,
        )

    @admin.command(name="post_leaderboard", description="설정된 채널로 리더보드 즉시 발송")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        result, tone = await self.post_configured_leaderboard(interaction)
        await interaction.followup.send(
            embed=notice_embed("리더보드 발송", result, tone=tone),
            ephemeral=True,
        )

    async def post_configured_leaderboard(
        self, interaction: discord.Interaction
    ) -> tuple[str, str]:
        if interaction.guild is None or interaction.guild_id is None:
            return "서버에서만 사용할 수 있습니다.", "warn"

        cfg = await self.store.get(interaction.guild_id)
        channel_id = cfg.get("leaderboard_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return (
                "먼저 `/admin leaderboard` 또는 `/admin panel`로 리더보드 채널을 설정하세요.",
                "warn",
            )

        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "_leaderboard_embed"):
            return "랭킹 기능을 찾을 수 없습니다.", "error"

        await rank_cog.store.ensure_week()
        embed = await rank_cog._leaderboard_embed(interaction.guild, limit=10)
        if embed is None:
            return "아직 집계된 활동 내역이 없습니다.", "info"

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return f"{channel.mention}에 리더보드를 발송했습니다.", "ok"

    @admin.command(name="env", description="봇 런타임 환경변수 상태 확인")
    @app_commands.checks.has_permissions(administrator=True)
    async def env(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="환경변수 상태",
            description="민감한 값은 내용 대신 설정 여부만 표시합니다.",
            color=INFO_COLOR,
        )
        embed.add_field(
            name="Secrets",
            value=(
                f"DISCORD_TOKEN: {_configured(os.getenv('DISCORD_TOKEN'), secret=True)}\n"
                f"AZURE_SPEECH_KEY: {_configured(os.getenv('AZURE_SPEECH_KEY'), secret=True)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Runtime",
            value=(
                f"AZURE_SPEECH_REGION: {_configured(os.getenv('AZURE_SPEECH_REGION'))}\n"
                f"CONFIG_PATH: {_configured(os.getenv('CONFIG_PATH') or 'config.json')}\n"
                f"RANK_PATH: {_configured(os.getenv('RANK_PATH') or 'rank_stats.json')}\n"
                f"TEST_GUILD_ID: {_configured(os.getenv('TEST_GUILD_ID'))}"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await self._safe_send(interaction, "권한 필요", "이 명령은 `관리자` 권한이 필요합니다.")
            return
        log.exception(
            "admin command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        await self._safe_send(interaction, "처리 실패", "관리자 명령 처리 중 오류가 발생했습니다.")

    @staticmethod
    async def _safe_send(
        interaction: discord.Interaction, title: str, description: str
    ) -> None:
        embed = notice_embed(title, description, tone="error")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception("failed to send admin interaction response")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
