"""Discord-authorized entry point for the web administration dashboard."""
from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from cogs.admin_login_store import (
    DEFAULT_LOGIN_GRANT_TTL_SECONDS,
    OneTimeLoginStore,
)
from cogs.public_url import cached_url, resolve_url
from cogs.ui import BRAND_COLOR, INFO_COLOR, notice_embed
from cogs.web_admin_cog import _web_enabled

log = logging.getLogger(__name__)


def _one_time_login_url(web_url: str | None, token: str) -> str | None:
    """토큰을 HTTP 요청에 실리지 않는 fragment에 담는다."""
    if not web_url or not token:
        return None
    return f"{web_url.rstrip('/')}/login#token={token}"


def _web_dashboard_url() -> str | None:
    # 공개 주소는 고정 ADMIN_WEB_PUBLIC_URL 또는 cloudflared metrics에서 온다.
    public_url = cached_url()
    if public_url:
        return public_url
    if not _web_enabled():
        return None
    host = os.getenv("ADMIN_WEB_HOST", "127.0.0.1")
    port = os.getenv("ADMIN_WEB_PORT") or os.getenv("PORT") or "8080"
    if host == "0.0.0.0":
        return None
    return f"http://{host}:{port}"


def _dashboard_embed(web_url: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="관리자 대시보드",
        description=(
            "Discord에서 관리자 권한을 확인해 만든 **일회용 로그인 링크**입니다.\n"
            "링크는 이 서버 하나에만 연결되고 한 번 사용하면 즉시 폐기됩니다."
        ),
        color=BRAND_COLOR if web_url else INFO_COLOR,
    )
    embed.add_field(
        name="접속",
        value=(
            "아래 `웹 대시보드 열기` 버튼을 누르세요."
            if web_url
            else "현재 공개 대시보드 주소를 확인할 수 없습니다. 잠시 뒤 다시 시도하세요."
        ),
        inline=False,
    )
    embed.add_field(
        name="보안",
        value="5분 안에 한 번만 사용할 수 있으며, 로그인 세션은 해당 서버에만 한정됩니다.",
        inline=False,
    )
    embed.add_field(
        name="관리 항목",
        value=(
            "TTS 입력 채널, 음성 출력 채널, 일일 리더보드 채널, "
            "발송 시각, 자동 발송 여부, 즉시 발송"
        ),
        inline=False,
    )
    embed.set_footer(text="다시 접속하려면 이 명령으로 새 링크를 발급하세요.")
    return embed


class AdminPanelView(discord.ui.View):
    def __init__(self, login_url: str | None = None) -> None:
        super().__init__(timeout=DEFAULT_LOGIN_GRANT_TTL_SECONDS)
        if login_url:
            self.add_item(
                discord.ui.Button(
                    label="웹 대시보드 열기",
                    style=discord.ButtonStyle.link,
                    url=login_url,
                    row=0,
                )
            )


class AdminCog(commands.Cog):
    admin = app_commands.Group(
        name="관리자",
        description="봇 관리자 설정",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.login_grants = OneTimeLoginStore()

    @admin.command(name="대시보드", description="5분짜리 일회용 웹 관리자 링크를 엽니다")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        web_url = await resolve_url()
        if not web_url:
            await interaction.followup.send(
                embed=_dashboard_embed(None),
                ephemeral=True,
            )
            return

        token = await self.login_grants.issue(interaction.guild_id, interaction.user.id)
        login_url = _one_time_login_url(web_url, token)
        log.info(
            "one-time dashboard login issued: guild_id=%s user_id=%s",
            interaction.guild_id,
            interaction.user.id,
        )
        await interaction.followup.send(
            embed=_dashboard_embed(login_url),
            view=AdminPanelView(login_url),
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
