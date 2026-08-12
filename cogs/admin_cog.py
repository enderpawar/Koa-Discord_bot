"""Administrator interface for runtime bot settings."""
from __future__ import annotations

import logging
import os
import discord
from discord import app_commands
from discord.ext import commands

from cogs.admin_key_store import AdminKeyStore
from cogs.config_store import ConfigStore
from cogs.public_url import cached_url, resolve_url
from cogs.web_admin_cog import _web_enabled
from cogs.ui import BRAND_COLOR, INFO_COLOR, notice_embed

log = logging.getLogger(__name__)


def _key_embed(guild: discord.Guild, key: str, web_url: str | None) -> discord.Embed:
    """서버 주인에게 DM 으로 보낼 로그인 키 안내.

    키 평문이 이 메시지에만 남으므로, 분실 시 재발급 방법을 함께 적는다.
    """
    embed = discord.Embed(
        title="코아 웹 대시보드 로그인 키",
        description=(
            f"**{guild.name}** 서버의 설정을 웹에서 관리할 수 있는 키입니다.\n"
            "이 키로는 **이 서버만** 보이고 설정할 수 있습니다."
        ),
        color=BRAND_COLOR,
    )
    embed.add_field(name="로그인 키", value=f"||`{key}`||", inline=False)
    if web_url:
        embed.add_field(name="접속 주소", value=web_url, inline=False)
    else:
        embed.add_field(
            name="접속 주소",
            value="아직 공개 주소가 설정되지 않았습니다. 봇 운영자에게 문의하세요.",
            inline=False,
        )
    embed.add_field(
        name="주의",
        value=(
            "• 이 키를 아는 사람은 누구나 이 서버 설정을 바꿀 수 있습니다. 공유하지 마세요.\n"
            "• 키는 다시 볼 수 없습니다. 잃어버렸거나 유출됐다면 서버에서 "
            "`/관리자 키재발급` 을 실행하세요. 이전 키는 즉시 무효가 됩니다."
        ),
        inline=False,
    )
    embed.set_footer(text="코아 · 서버별 대시보드 키")
    return embed


def _web_dashboard_url() -> str | None:
    # 공개 주소는 ADMIN_WEB_PUBLIC_URL(고정) 또는 cloudflared 임시 터널에서
    # 온다. 호스팅 업체가 주입하는 변수를 추측하지 않으므로 어디에 배포하든
    # 동작이 같다. 네트워크를 타는 해석은 resolve_url() 이 하고, 여기서는
    # 마지막으로 확인된 값만 읽는다 (동기 경로).
    public_url = cached_url()
    if public_url:
        return public_url
    # 어드민이 켜져 있는지로 판단한다. ADMIN_WEB_TOKEN 은 운영자 마스터 키라
    # 없는 구성이 정상이므로, 그 유무로 게이트하면 서버별 키만 쓰는 배포에서
    # 안내 링크가 통째로 사라진다.
    if not _web_enabled():
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


def _dashboard_embed(web_url: str | None = None) -> discord.Embed:
    web_url = web_url if web_url is not None else _web_dashboard_url()
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
            else "`ADMIN_WEB_PUBLIC_URL` 설정이 필요합니다. 봇 운영자에게 문의하세요."
        ),
        inline=False,
    )
    embed.add_field(
        name="로그인 키",
        value=(
            "코아를 초대할 때 서버 소유자에게 DM으로 보낸 키로 로그인합니다.\n"
            "키를 잃어버렸거나 유출됐다면 `/관리자 키재발급` 을 실행하세요."
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
    embed.set_footer(text="키 하나는 그 서버 하나만 열 수 있습니다.")
    return embed


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


class AdminPanelView(discord.ui.View):
    def __init__(self, web_url: str | None = None) -> None:
        super().__init__(timeout=300)
        web_url = web_url if web_url is not None else _web_dashboard_url()
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
        self.keys = AdminKeyStore()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """초대되면 서버 주인에게 대시보드 키를 DM 한다."""
        await self._deliver_key(guild, guild.owner, reason="join")

    async def _deliver_key(
        self,
        guild: discord.Guild,
        target: discord.Member | discord.User | None,
        *,
        reason: str,
    ) -> bool:
        """키를 발급해 DM 한다. 성공 여부를 돌려준다.

        DM 이 막혀 있어도 서버 채널로는 절대 보내지 않는다 — 키가 공개된다.
        """
        if target is None:
            log.warning("no key recipient for guild_id=%s (%s)", guild.id, reason)
            return False

        key = await self.keys.issue(guild.id, issued_to=target.id)
        # 재발급이면 그 서버 범위의 기존 세션도 끊는다.
        web_cog = self.bot.get_cog("WebAdminCog")
        if web_cog is not None and hasattr(web_cog, "revoke_sessions_for_guild"):
            await web_cog.revoke_sessions_for_guild(guild.id)

        try:
            await target.send(embed=_key_embed(guild, key, await resolve_url()))
        except discord.Forbidden:
            log.warning(
                "dashboard key DM blocked for guild_id=%s user_id=%s (%s)",
                guild.id,
                target.id,
                reason,
            )
            return False
        except discord.HTTPException:
            log.exception("dashboard key DM failed for guild_id=%s (%s)", guild.id, reason)
            return False
        log.info("dashboard key delivered: guild_id=%s (%s)", guild.id, reason)
        return True

    @admin.command(
        name="키재발급",
        description="이 서버의 웹 대시보드 로그인 키를 새로 발급해 DM으로 받습니다",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reissue_key(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        delivered = await self._deliver_key(guild, interaction.user, reason="reissue")
        if delivered:
            await interaction.followup.send(
                embed=notice_embed(
                    "키를 DM으로 보냈습니다",
                    "이전 키와 로그인 세션은 모두 무효가 되었습니다. DM을 확인하세요.",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=notice_embed(
                "DM을 보낼 수 없습니다",
                "새 키는 발급되었지만 DM이 차단되어 전달하지 못했습니다.\n"
                "개인정보 보호 설정에서 서버 멤버의 DM을 허용한 뒤 다시 실행하세요.",
                tone="warn",
            ),
            ephemeral=True,
        )

    @admin.command(name="대시보드", description="웹 관리자 대시보드 링크를 엽니다")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return
        web_url = await resolve_url()
        await interaction.response.send_message(
            embed=_dashboard_embed(web_url),
            view=AdminPanelView(web_url),
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
