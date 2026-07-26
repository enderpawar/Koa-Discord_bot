"""Administrator interface for runtime bot settings."""
from __future__ import annotations

import logging
import os
import discord
from discord import app_commands
from discord.ext import commands

from cogs.config_store import ConfigStore
from cogs.ui import BRAND_COLOR, INFO_COLOR, notice_embed

log = logging.getLogger(__name__)


def _web_dashboard_url() -> str | None:
    public_url = os.getenv("ADMIN_WEB_PUBLIC_URL", "").strip()
    if public_url:
        return public_url.rstrip("/")
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        return f"https://{railway_domain}".rstrip("/")
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip("/")
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


def _dashboard_embed() -> discord.Embed:
    web_url = _web_dashboard_url()
    embed = discord.Embed(
        title="관리자 대시보드",
        description="서버 운영 설정은 웹 관리자 UI에서 관리합니다.",
        color=BRAND_COLOR if web_url else INFO_COLOR,
    )
    embed.add_field(
        name="접속",
        value=(
            "아래 `웹 대시보드 열기` 버튼을 누르세요."
            if web_url
            else "`ADMIN_WEB_TOKEN` 과 `ADMIN_WEB_PUBLIC_URL` 설정이 필요합니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="관리 항목",
        value=(
            "TTS 입력 채널, 음성 출력 채널, 일일 리더보드 채널, 발송 시각, 자동 발송 여부, 즉시 발송"
        ),
        inline=False,
    )
    embed.set_footer(text="웹 UI 로그인에는 ADMIN_WEB_TOKEN 값이 필요합니다.")
    return embed


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        web_url = _web_dashboard_url()
        if web_url:
            self.add_item(
                discord.ui.Button(
                    label="웹 대시보드 열기",
                    style=discord.ButtonStyle.link,
                    url=web_url,
                    row=0,
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


class AdminCog(commands.Cog):
    admin = app_commands.Group(
        name="관리자",
        description="봇 관리자 설정",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()

    @admin.command(name="대시보드", description="웹 관리자 대시보드 링크를 엽니다")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=_dashboard_embed(),
            view=AdminPanelView(),
            ephemeral=True,
        )

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
