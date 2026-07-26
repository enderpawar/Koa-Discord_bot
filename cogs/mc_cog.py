"""마인크래프트 서버 전원 제어 명령.

`/마크 켜기` / `/마크 끄기`는 실행 시마다 모달로 암호를 받는다. 슬래시 명령 옵션이
아니라 모달을 쓰는 이유는, 옵션값은 클라이언트 UI와 상호작용 페이로드에
평문으로 남지만 모달 입력은 채팅에 전혀 표시되지 않기 때문이다.

`/마크 상태`는 상태 조회일 뿐 아무것도 바꾸지 않으므로 암호를 받지 않는다.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.gcp_compute import GcpApiError, GcpComputeClient, GcpConfigError
from cogs.mc_ping import try_ping
from cogs.ui import BRAND_COLOR, notice_embed

log = logging.getLogger(__name__)

# 부팅 대기: 10초 간격으로 최대 5분. RUNBOOK 실측 기동 시간은 1~2분이다.
_BOOT_POLL_INTERVAL_SEC = 10
_BOOT_TIMEOUT_SEC = 300
# 종료 대기: ACPI 종료 + 디스크 정리까지.
_SHUTDOWN_POLL_INTERVAL_SEC = 10
_SHUTDOWN_TIMEOUT_SEC = 180

# 무차별 대입 방지. 암호가 4자리라 시도 횟수 제한이 사실상 유일한 방어선이다.
_MAX_FAILS = 5
_LOCKOUT_SEC = 600
_FAIL_WINDOW_SEC = 600


def _password() -> str:
    """제어 암호. 기본값을 두지 않는다 — 이 저장소는 공개라 소스에 박으면
    암호가 아니게 된다. 미설정이면 명령 자체가 '설정 필요'로 막힌다."""
    return os.getenv("MC_CONTROL_PASSWORD", "")


def _server_host() -> str:
    return os.getenv("MC_SERVER_HOST", "").strip()


def _server_port() -> int:
    raw = os.getenv("MC_SERVER_PORT", "25565").strip()
    try:
        return int(raw)
    except ValueError:
        log.warning("MC_SERVER_PORT 값이 숫자가 아님(%r) — 25565 사용", raw)
        return 25565


def _address_label() -> str:
    host, port = _server_host(), _server_port()
    if not host:
        return "미설정"
    return host if port == 25565 else f"{host}:{port}"


class AttemptLimiter:
    """사용자별 암호 실패 횟수 추적 (인메모리).

    봇 재시작 시 초기화된다. 재시작을 유도할 수 있는 사람은 이미 서버 운영자
    이므로 실질적인 우회 경로는 아니다.
    """

    def __init__(self, max_fails: int = _MAX_FAILS, lockout_sec: int = _LOCKOUT_SEC) -> None:
        self._max_fails = max_fails
        self._lockout_sec = lockout_sec
        self._fails: dict[int, list[float]] = {}
        self._locked_until: dict[int, float] = {}

    def locked_for(self, user_id: int) -> int:
        """남은 잠금 시간(초). 0이면 잠기지 않음."""
        until = self._locked_until.get(user_id, 0.0)
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._locked_until.pop(user_id, None)
            return 0
        return int(remaining) + 1

    def record_failure(self, user_id: int) -> int:
        """실패를 기록하고 남은 시도 횟수를 반환한다."""
        now = time.monotonic()
        recent = [t for t in self._fails.get(user_id, []) if now - t < _FAIL_WINDOW_SEC]
        recent.append(now)
        self._fails[user_id] = recent
        if len(recent) >= self._max_fails:
            self._locked_until[user_id] = now + self._lockout_sec
            self._fails.pop(user_id, None)
            return 0
        return self._max_fails - len(recent)

    def record_success(self, user_id: int) -> None:
        self._fails.pop(user_id, None)
        self._locked_until.pop(user_id, None)


class PasswordModal(discord.ui.Modal):
    """암호 입력 모달. 검증 통과 시 `handler(interaction)` 를 호출한다."""

    def __init__(self, *, title: str, limiter: AttemptLimiter, handler) -> None:
        super().__init__(title=title, timeout=120)
        self._limiter = limiter
        self._handler = handler
        self.password = discord.ui.TextInput(
            label="암호",
            placeholder="서버 제어 암호를 입력하세요",
            required=True,
            max_length=64,
        )
        self.add_item(self.password)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id

        locked = self._limiter.locked_for(user_id)
        if locked:
            await interaction.response.send_message(
                embed=notice_embed(
                    "잠시 차단됨",
                    f"암호를 여러 번 틀렸습니다. `{locked // 60}분 {locked % 60}초` 후 다시 시도하세요.",
                    tone="warn",
                ),
                ephemeral=True,
            )
            return

        if not hmac.compare_digest(self.password.value, _password()):
            remaining = self._limiter.record_failure(user_id)
            log.warning(
                "mc control password failure: user_id=%s guild_id=%s remaining=%s",
                user_id,
                interaction.guild_id,
                remaining,
            )
            detail = (
                f"남은 시도: `{remaining}회`"
                if remaining
                else f"시도 횟수를 초과해 `{_LOCKOUT_SEC // 60}분` 간 차단됩니다."
            )
            await interaction.response.send_message(
                embed=notice_embed("암호가 틀렸습니다", detail, tone="error"),
                ephemeral=True,
            )
            return

        self._limiter.record_success(user_id)
        await self._handler(interaction)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:  # pragma: no cover - discord 내부 경로
        log.exception("mc password modal error", exc_info=error)
        embed = notice_embed("처리 실패", "요청을 처리하지 못했습니다.", tone="error")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class ConfirmStopView(discord.ui.View):
    """접속자가 있을 때 한 번 더 확인받는 버튼. 호출자만 누를 수 있다."""

    def __init__(self, owner_id: int, handler) -> None:
        super().__init__(timeout=60)
        self._owner_id = owner_id
        self._handler = handler

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "명령을 실행한 사람만 누를 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="그래도 끄기", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()
        await self._handler(interaction)


class MCControlCog(commands.Cog):
    # Discord에 `default_member_permissions: null`로 등록한다. 빈 Permissions
    # 객체는 값이 0이어서 오히려 관리자만 사용할 수 있으므로 쓰면 안 된다.
    # on/off의 권한 경계는 Discord 역할이 아니라 아래 PasswordModal이다.
    mc = app_commands.Group(
        name="마크",
        description="서버 구성원 누구나 암호로 사용하는 마인크래프트 전원 제어",
        default_permissions=None,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gcp = GcpComputeClient()
        self.limiter = AttemptLimiter()
        # 두 사람이 동시에 켜기/끄기를 눌러 API가 교차 호출되는 걸 막는다.
        self._power_lock = asyncio.Lock()

    # ---- 공통 ------------------------------------------------------------

    def _missing_settings(self, *, needs_password: bool) -> list[str]:
        missing = self.gcp.missing_settings()
        if needs_password and not _password():
            missing.append("MC_CONTROL_PASSWORD")
        return missing

    @staticmethod
    def _config_error_embed(missing: list[str]) -> discord.Embed:
        return notice_embed(
            "설정이 필요합니다",
            "다음 환경변수가 없습니다: " + ", ".join(f"`{m}`" for m in missing),
            tone="error",
        )

    async def _guard(self, interaction: discord.Interaction, *, needs_password: bool) -> bool:
        """서버 전용 + 설정 확인. 통과하면 True."""
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=notice_embed("사용 불가", "서버에서만 사용할 수 있습니다.", tone="warn"),
                ephemeral=True,
            )
            return False
        missing = self._missing_settings(needs_password=needs_password)
        if missing:
            await interaction.response.send_message(
                embed=self._config_error_embed(missing), ephemeral=True
            )
            return False
        return True

    @staticmethod
    async def _edit(interaction: discord.Interaction, embed: discord.Embed) -> None:
        try:
            await interaction.edit_original_response(embed=embed, view=None)
        except discord.HTTPException:
            log.exception("failed to edit mc control response")

    # ---- /마크 켜기 ------------------------------------------------------

    @mc.command(name="켜기", description="마인크래프트 서버를 켭니다 (암호 필요)")
    async def power_on(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction, needs_password=True):
            return
        await interaction.response.send_modal(
            PasswordModal(title="서버 켜기 확인", limiter=self.limiter, handler=self._do_start)
        )

    async def _do_start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        log.info(
            "mc start requested: user_id=%s guild_id=%s",
            interaction.user.id,
            interaction.guild_id,
        )

        if self._power_lock.locked():
            # defer 로 이미 자리 잡은 응답을 고쳐 쓴다. followup.send 를 쓰면
            # "생각 중" 표시가 남은 채 메시지가 하나 더 붙는다.
            await self._edit(
                interaction,
                notice_embed(
                    "처리 중", "다른 전원 요청이 진행 중입니다. 잠시 후 다시 시도하세요.", tone="warn"
                ),
            )
            return

        async with self._power_lock:
            try:
                status = await self.gcp.get_status()
            except (GcpConfigError, GcpApiError) as exc:
                log.exception("mc start: status lookup failed")
                await self._edit(
                    interaction, notice_embed("상태 조회 실패", str(exc)[:400], tone="error")
                )
                return

            if status != "RUNNING":
                try:
                    await self.gcp.start()
                except (GcpConfigError, GcpApiError) as exc:
                    log.exception("mc start: start call failed")
                    await self._edit(
                        interaction, notice_embed("기동 실패", str(exc)[:400], tone="error")
                    )
                    return
            else:
                # VM은 이미 떠 있어도 자바가 아직 로딩 중일 수 있으므로 대기는 계속한다.
                ready = await try_ping(_server_host(), _server_port())
                if ready is not None:
                    await self._edit(interaction, self._ready_embed(ready.online, already=True))
                    return

            await self._edit(
                interaction,
                notice_embed(
                    "서버를 켜는 중",
                    "마인크래프트 기동을 기다리는 중입니다. 보통 1~2분 걸립니다.",
                    tone="info",
                ),
            )
            await self._wait_until_ready(interaction)

    async def _wait_until_ready(self, interaction: discord.Interaction) -> None:
        deadline = time.monotonic() + _BOOT_TIMEOUT_SEC
        host, port = _server_host(), _server_port()
        while time.monotonic() < deadline:
            await asyncio.sleep(_BOOT_POLL_INTERVAL_SEC)
            status = await try_ping(host, port)
            if status is not None:
                log.info("mc start: server ready (online=%s)", status.online)
                await self._edit(interaction, self._ready_embed(status.online))
                return
            elapsed = int(_BOOT_TIMEOUT_SEC - (deadline - time.monotonic()))
            await self._edit(
                interaction,
                notice_embed(
                    "서버를 켜는 중",
                    f"마인크래프트 기동을 기다리는 중입니다... `{elapsed}초` 경과",
                    tone="info",
                ),
            )

        log.warning("mc start: readiness timeout after %ss", _BOOT_TIMEOUT_SEC)
        await self._edit(
            interaction,
            notice_embed(
                "기동 확인 실패",
                f"`{_BOOT_TIMEOUT_SEC // 60}분` 안에 응답이 없었습니다. VM은 켜졌을 수 있으니 "
                "`/마크 상태`로 다시 확인해 주세요.",
                tone="warn",
            ),
        )

    @staticmethod
    def _ready_embed(online: int, *, already: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title="서버 준비 완료" if not already else "서버가 이미 켜져 있습니다",
            description="접속할 수 있습니다.",
            color=BRAND_COLOR,
        )
        embed.add_field(name="주소", value=f"`{_address_label()}`", inline=False)
        embed.add_field(name="접속자", value=f"{online}명", inline=False)
        return embed

    # ---- /마크 끄기 ------------------------------------------------------

    @mc.command(name="끄기", description="마인크래프트 서버를 끕니다 (암호 필요)")
    async def power_off(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction, needs_password=True):
            return
        await interaction.response.send_modal(
            PasswordModal(title="서버 끄기 확인", limiter=self.limiter, handler=self._do_stop)
        )

    async def _do_stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        log.info(
            "mc stop requested: user_id=%s guild_id=%s",
            interaction.user.id,
            interaction.guild_id,
        )

        try:
            status = await self.gcp.get_status()
        except (GcpConfigError, GcpApiError) as exc:
            log.exception("mc stop: status lookup failed")
            await self._edit(
                interaction, notice_embed("상태 조회 실패", str(exc)[:400], tone="error")
            )
            return

        if status == "TERMINATED":
            await self._edit(
                interaction, notice_embed("이미 꺼져 있습니다", "요금이 발생하지 않는 상태입니다.", tone="info")
            )
            return

        ping_result = await try_ping(_server_host(), _server_port())
        if ping_result is not None and ping_result.online > 0:
            await self._edit_with_view(
                interaction,
                notice_embed(
                    "접속자가 있습니다",
                    f"현재 `{ping_result.online}명` 이 접속 중입니다. 그래도 끄시겠습니까?",
                    tone="warn",
                ),
                ConfirmStopView(interaction.user.id, self._stop_confirmed),
            )
            return

        await self._stop_now(interaction)

    async def _stop_confirmed(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._stop_now(interaction)

    async def _stop_now(self, interaction: discord.Interaction) -> None:
        if self._power_lock.locked():
            await self._edit(
                interaction,
                notice_embed("처리 중", "다른 전원 요청이 진행 중입니다.", tone="warn"),
            )
            return

        async with self._power_lock:
            await self._edit(
                interaction,
                notice_embed(
                    "서버를 끄는 중",
                    "월드를 저장하고 종료합니다. 잠시 기다려 주세요.",
                    tone="info",
                ),
            )
            try:
                await self.gcp.stop()
            except (GcpConfigError, GcpApiError) as exc:
                log.exception("mc stop: stop call failed")
                await self._edit(
                    interaction, notice_embed("종료 실패", str(exc)[:400], tone="error")
                )
                return

            deadline = time.monotonic() + _SHUTDOWN_TIMEOUT_SEC
            while time.monotonic() < deadline:
                await asyncio.sleep(_SHUTDOWN_POLL_INTERVAL_SEC)
                try:
                    if await self.gcp.get_status() == "TERMINATED":
                        log.info("mc stop: instance terminated")
                        await self._edit(
                            interaction,
                            notice_embed(
                                "서버를 껐습니다",
                                "월드 저장 후 종료됐습니다. 이제 요금이 발생하지 않습니다.",
                                tone="ok",
                            ),
                        )
                        return
                except (GcpConfigError, GcpApiError):
                    log.exception("mc stop: status poll failed")
                    break

            await self._edit(
                interaction,
                notice_embed(
                    "종료 확인 실패",
                    "종료 명령은 보냈지만 완료를 확인하지 못했습니다. `/마크 상태`로 확인해 주세요.",
                    tone="warn",
                ),
            )

    @staticmethod
    async def _edit_with_view(
        interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View
    ) -> None:
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except discord.HTTPException:
            log.exception("failed to edit mc control response with view")

    # ---- /마크 상태 ------------------------------------------------------

    @mc.command(name="상태", description="마인크래프트 서버 상태를 확인합니다")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction, needs_password=False):
            return
        await interaction.response.defer(thinking=True)

        try:
            vm_status = await self.gcp.get_status()
        except (GcpConfigError, GcpApiError) as exc:
            log.exception("mc status: lookup failed")
            await self._edit(
                interaction, notice_embed("상태 조회 실패", str(exc)[:400], tone="error")
            )
            return

        if vm_status != "RUNNING":
            await self._edit(
                interaction,
                notice_embed(
                    "서버가 꺼져 있습니다",
                    f"VM 상태: `{vm_status}`\n`/마크 켜기`로 켤 수 있습니다.",
                    tone="info",
                ),
            )
            return

        ping_result = await try_ping(_server_host(), _server_port())
        if ping_result is None:
            await self._edit(
                interaction,
                notice_embed(
                    "기동 중",
                    "VM은 켜져 있으나 마인크래프트가 아직 응답하지 않습니다. 보통 1~2분 걸립니다.",
                    tone="warn",
                ),
            )
            return

        embed = discord.Embed(title="서버 상태", color=BRAND_COLOR)
        embed.add_field(name="상태", value="정상 가동 중", inline=False)
        embed.add_field(name="주소", value=f"`{_address_label()}`", inline=False)
        embed.add_field(
            name="접속자", value=f"{ping_result.online} / {ping_result.max_players}명", inline=False
        )
        embed.set_footer(text=f"버전 {ping_result.version}")
        await self._edit(interaction, embed)

    # ---- 오류 처리 --------------------------------------------------------

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.exception(
            "mc command error: guild_id=%s cmd=%s",
            interaction.guild_id,
            interaction.command.name if interaction.command else "?",
            exc_info=error,
        )
        embed = notice_embed("처리 실패", "명령 처리 중 오류가 발생했습니다.", tone="error")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception("failed to send mc error response")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MCControlCog(bot))
