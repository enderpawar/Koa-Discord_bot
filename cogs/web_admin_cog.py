"""Token-protected web dashboard for bot administration."""
from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import time
from html import escape
from pathlib import Path
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands

from cogs.admin_key_store import AdminKeyStore
from cogs.config_store import ConfigStore
from cogs.rank_cog import DEFAULT_LEADERBOARD_POST_TIME
from cogs.rank_store import weekly_reset_anchor

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# 쿠키에는 ADMIN_WEB_TOKEN 이 아니라 세션 ID 만 담는다. 쿠키가 새더라도 새는 것은
# 그 세션 하나이고, 로그아웃으로 즉시 끊을 수 있다. 이름이 'token' 이 아닌 이유다.
_COOKIE_NAME = "koa_admin_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60

# 무차별 대입 방어. IP 당 실패 횟수를 세고, 임계치를 넘으면 잠금 시간을 2배씩 늘린다.
# 30s → 1m → 2m → … → 15m. 공용 IP 뒤의 애먼 사용자가 오래 묶이지 않게 하면서
# 계속 두드리는 쪽은 몇 번 만에 상한에 닿게 한다.
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT_BASE_SECONDS = 30
_LOGIN_LOCKOUT_MAX_SECONDS = 15 * 60
# 서로 다른 출발지로 두드려 메모리를 밀어 올리는 것을 막는 상한.
_LOGIN_FAILURE_MAX_TRACKED = 4096

# CSRF 검사를 적용할 메서드.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 관리자 페이지 HTML 은 파이썬 문자열이 아니라 templates/*.html 에 둔다.
# 에디터 문법 강조와 diff 가 살아나고, 마크업을 고칠 때 cog 를 건드리지 않는다.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_LOGIN_TEMPLATE = "admin_login.html"
_DASHBOARD_TEMPLATE = "admin_dashboard.html"
_template_cache: dict[str, str] = {}


def _template_reload() -> bool:
    return (os.getenv("ADMIN_WEB_TEMPLATE_RELOAD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _apply_security_headers(response: web.StreamResponse, nonce: str) -> None:
    headers = response.headers
    # default-src 'none' 로 전부 막고 필요한 것만 연다. 인라인 style/script 는
    # nonce 가 있어야만 실행되므로 XSS 로 삽입된 <script> 는 죽는다.
    headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; "
        f"style-src 'nonce-{nonce}'; "
        f"script-src 'nonce-{nonce}'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'",
    )
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    # 어드민 URL 이 외부 사이트로 새어 나가지 않게 한다.
    headers.setdefault("Referrer-Policy", "no-referrer")
    # 설정값과 리더보드가 디스크 캐시나 뒤로가기 버튼에 남지 않게 한다.
    headers.setdefault("Cache-Control", "no-store")
    if _cookie_secure():
        # HTTPS 로 서비스할 때만. 평문 HTTP 에 붙이면 브라우저가 그 호스트를
        # https 로만 접근하려 해서 접속 자체가 끊긴다.
        headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )


def _template(name: str) -> str:
    """templates/<name> 을 읽어 돌려준다.

    기본은 프로세스 수명 동안 1회만 읽는다 (요청마다 디스크를 때리면 이벤트 루프가
    막힌다). ADMIN_WEB_TEMPLATE_RELOAD 를 켜면 매 요청 다시 읽어, 봇을 재시작하지
    않고 마크업 수정을 바로 확인할 수 있다.
    """
    if _template_reload():
        return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    if name not in _template_cache:
        _template_cache[name] = (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    return _template_cache[name]


def _web_enabled() -> bool:
    value = os.getenv("ADMIN_WEB_ENABLED")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(os.getenv("ADMIN_WEB_TOKEN"))


def _web_host() -> str:
    # 외부 공개는 항상 명시적으로만. 호스팅 환경을 추측해 0.0.0.0 으로 여는 분기를
    # 두지 않는다 — 토큰 하나로 보호되는 어드민이라 기본값은 로컬 바인딩이어야 한다.
    # 외부에서 접근하려면 ADMIN_WEB_HOST=0.0.0.0 을 직접 설정한다 (docs/deploy-oracle.md §6).
    return os.getenv("ADMIN_WEB_HOST") or "127.0.0.1"


def _cookie_secure() -> bool:
    """세션 쿠키에 Secure 를 붙일지.

    공개 주소가 https 면 자동으로 켠다. 평문 HTTP 로 서비스하는데 Secure 를 붙이면
    쿠키가 아예 저장되지 않아 로그인이 무한 반복되므로 기본값을 켜둘 수는 없다.
    """
    explicit = os.getenv("ADMIN_WEB_COOKIE_SECURE")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("ADMIN_WEB_PUBLIC_URL", "").strip().lower().startswith("https://")


def _web_port() -> int:
    raw = os.getenv("ADMIN_WEB_PORT") or os.getenv("PORT") or "8080"
    try:
        return int(raw)
    except ValueError:
        return 8080


def _clean_time(value: str | None) -> str:
    candidate = (value or DEFAULT_LEADERBOARD_POST_TIME).strip()
    if not _TIME_RE.match(candidate):
        raise ValueError("invalid post_time")
    return candidate


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _id(value: int) -> str:
    return str(value)


def _channel_payload(channel: discord.abc.GuildChannel) -> dict[str, str]:
    return {"id": _id(channel.id), "name": channel.name}


def _row_with_name(guild: discord.Guild, row: dict[str, Any]) -> dict[str, Any]:
    user_id = int(row.get("user_id", 0) or 0)
    member = guild.get_member(user_id) if user_id else None
    if member is not None:
        name = member.display_name
    elif user_id:
        name = f"ID {user_id}"
    else:
        name = "unknown"
    return {
        "user_id": _id(user_id),
        "name": name,
        "voice_seconds": int(row.get("voice_seconds", 0) or 0),
        "message_count": int(row.get("message_count", 0) or 0),
        "score": int(row.get("score", 0) or 0),
    }


def _allowed_guild_ids() -> set[int]:
    raw = os.getenv("ADMIN_WEB_GUILD_IDS") or os.getenv("TEST_GUILD_ID") or ""
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if value.isdigit():
            ids.add(int(value))
    return ids


class WebAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self.keys = AdminKeyStore()
        # 전역(운영자) 토큰. 비워 두면 서버별 키로만 로그인할 수 있다.
        self._token = os.getenv("ADMIN_WEB_TOKEN", "")
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # 세션 ID -> (범위 guild_id 또는 None, 만료 시각 monotonic).
        # 범위 None = 운영자 전역. 프로세스 메모리에만 두므로 재시작하면 전부
        # 무효가 되는데, 어드민 세션에서는 그게 올바른 기본값이다.
        self._sessions: dict[str, tuple[int | None, float]] = {}
        # 클라이언트 IP -> (연속 실패 횟수, 잠금 해제 시각)
        self._login_failures: dict[str, tuple[int, float]] = {}

    def _issue_session(self, scope: int | None) -> str:
        now = time.monotonic()
        self._sessions = {
            sid: value for sid, value in self._sessions.items() if value[1] > now
        }
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = (scope, now + _SESSION_TTL_SECONDS)
        return sid

    def _session_scope(self, sid: str) -> tuple[bool, int | None]:
        """(유효한가, 범위 guild_id). 범위 None 은 전역(운영자)."""
        record = self._sessions.get(sid)
        if record is None:
            return False, None
        scope, expiry = record
        if expiry <= time.monotonic():
            self._sessions.pop(sid, None)
            return False, None
        return True, scope

    def _session_valid(self, sid: str) -> bool:
        return self._session_scope(sid)[0]

    async def revoke_sessions_for_guild(self, guild_id: int) -> int:
        """해당 길드 범위의 활성 세션을 모두 끊는다. 키 재발급 시 호출."""
        doomed = [sid for sid, (scope, _) in self._sessions.items() if scope == guild_id]
        for sid in doomed:
            self._sessions.pop(sid, None)
        return len(doomed)

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        # X-Forwarded-For 는 신뢰하지 않는다. 헤더는 위조가 쉬워서, 그걸 키로 쓰면
        # 공격자가 매 요청 다른 값을 보내 잠금을 그냥 우회한다.
        return request.remote or "unknown"

    def _prune_login_failures(self, now: float) -> None:
        """만료 기록을 버리고, 그래도 넘치면 오래된 것부터 버린다.

        기록이 IP 별로 쌓이므로 정리하지 않으면 서로 다른 출발지에서 로그인을
        두드리는 것만으로 메모리를 계속 밀어 올릴 수 있다.
        """
        self._login_failures = {
            ip: record for ip, record in self._login_failures.items() if record[1] > now
        }
        overflow = len(self._login_failures) - _LOGIN_FAILURE_MAX_TRACKED
        if overflow > 0:
            oldest = sorted(self._login_failures.items(), key=lambda kv: kv[1][1])
            for ip, _ in oldest[:overflow]:
                self._login_failures.pop(ip, None)

    def _lockout_remaining(self, ip: str) -> int:
        record = self._login_failures.get(ip)
        if record is None:
            return 0
        failures, until = record
        if failures < _LOGIN_MAX_FAILURES:
            return 0
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._login_failures.pop(ip, None)
            return 0
        return int(remaining) + 1

    @staticmethod
    def _lockout_seconds(failures: int) -> float:
        """임계치를 넘긴 뒤로 잠금 시간을 2배씩 늘린다.

        평평한 15분 잠금은 공용 IP(회사망·NAT·모바일 캐리어) 뒤의 애먼 관리자가
        남의 실패 때문에 15분을 통째로 잃게 만든다. 첫 잠금을 30초로 짧게 두면
        그 피해는 금방 풀리고, 실제로 두드리는 쪽은 몇 번 만에 상한에 닿는다.
        """
        steps = max(0, failures - _LOGIN_MAX_FAILURES)
        return min(_LOGIN_LOCKOUT_BASE_SECONDS * (2**steps), _LOGIN_LOCKOUT_MAX_SECONDS)

    def _record_login_failure(self, ip: str) -> None:
        now = time.monotonic()
        failures = self._login_failures.get(ip, (0, 0.0))[0] + 1
        self._login_failures[ip] = (failures, now + self._lockout_seconds(failures))
        # 삽입한 뒤에 정리해야 상한을 실제로 지킨다. 방금 넣은 항목은 만료가
        # 가장 늦으므로 오래된 것부터 버리는 이 정리에서 살아남는다.
        self._prune_login_failures(now)
        if failures >= _LOGIN_MAX_FAILURES:
            log.warning(
                "web admin login locked out for %s after %d failures (%ds)",
                ip,
                failures,
                int(self._lockout_seconds(failures)),
            )

    async def cog_load(self) -> None:
        if not _web_enabled():
            log.info("web admin disabled")
            return
        # 전역 토큰이 없어도 서버별 키만으로 운영할 수 있다 (마스터 키 없는 구성).
        if not self._token:
            log.info("web admin running without a global token — per-guild keys only")
        else:
            log.warning(
                "ADMIN_WEB_TOKEN is set — this master key unlocks EVERY guild the bot "
                "is in and bypasses per-guild isolation. Unset it and use "
                "ADMIN_WEB_ENABLED=1 with per-guild keys unless you need it."
            )

        # 템플릿을 미리 읽어 둔다. 여기서 걸러야 첫 접속 때 500 을 보는 대신
        # 기동 로그에서 원인을 알 수 있다 (Rule 03: 봇 전체를 죽이지는 않는다).
        try:
            _template(_LOGIN_TEMPLATE)
            _template(_DASHBOARD_TEMPLATE)
        except OSError:
            log.exception("web admin templates not readable under %s", _TEMPLATE_DIR)
            return

        # 보안 헤더가 바깥, 인증이 안쪽. 인증 실패로 나가는 401/302 에도 헤더가 붙는다.
        app = web.Application(
            middlewares=[
                self._security_headers_middleware,
                self._csrf_middleware,
                self._auth_middleware,
            ]
        )
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/login", self._login),
                web.post("/login", self._login_submit),
                web.post("/logout", self._logout),
                web.get("/api/state", self._api_state),
                web.get("/api/leaderboard", self._api_leaderboard),
                web.get("/api/leaderboard-history", self._api_leaderboard_history),
                web.post("/api/config", self._api_config),
                web.post("/api/post-leaderboard", self._api_post_leaderboard),
                web.post("/api/clear-leaderboard", self._api_clear_leaderboard),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, _web_host(), _web_port())
        await self._site.start()
        log.info("web admin listening on http://%s:%s", _web_host(), _web_port())
        if _web_host() not in {"127.0.0.1", "localhost", "::1"} and not _cookie_secure():
            log.warning(
                "web admin bound to %s over plain HTTP. If this port is reachable "
                "from outside the host, keys and session cookies travel "
                "unencrypted — put it behind TLS and set "
                "ADMIN_WEB_PUBLIC_URL=https://... . Binding 0.0.0.0 inside a "
                "container is fine as long as the port is published to the host "
                "loopback only (see docker-compose.yml) and reached via SSH tunnel.",
                _web_host(),
            )

    async def cog_unload(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @web.middleware
    async def _security_headers_middleware(self, request: web.Request, handler):
        """모든 응답에 보안 헤더를 붙이고 요청별 CSP nonce 를 발급한다.

        인라인 <style>/<script> 를 nonce 로만 허용하므로, 마크업에 주입이 성공해도
        스크립트가 실행되지 않는다. 리다이렉트(HTTPException)도 응답이라 같이 감싼다.
        """
        nonce = secrets.token_urlsafe(16)
        request["csp_nonce"] = nonce
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            _apply_security_headers(exc, nonce)
            raise
        _apply_security_headers(response, nonce)
        return response

    @web.middleware
    async def _csrf_middleware(self, request: web.Request, handler):
        """상태를 바꾸는 요청이 다른 사이트에서 오면 거부한다.

        SameSite=Strict 쿠키가 이미 대부분을 막지만 두 가지 구멍이 남는다.
        (1) 로그인 POST 는 쿠키가 없어도 성립하므로, 공격자가 피해자를 자기
        서버 대시보드에 로그인시켜 놓을 수 있다(login CSRF).
        (2) SameSite 를 모르는 구형 브라우저.

        Sec-Fetch-Site 는 브라우저가 붙이는 값이라 스크립트로 위조할 수 없다.
        헤더가 아예 없으면(curl 등 비브라우저) 통과시킨다 — 그쪽은 쿠키를
        자동으로 실어 보내지 않으므로 CSRF 가 성립하지 않는다.
        """
        if request.method in _STATE_CHANGING_METHODS:
            fetch_site = request.headers.get("Sec-Fetch-Site")
            if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
                log.warning(
                    "cross-site %s to %s rejected (Sec-Fetch-Site: %s)",
                    request.method,
                    request.path,
                    fetch_site,
                )
                return web.json_response({"error": "cross-site request"}, status=403)
        return await handler(request)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path == "/login":
            return await handler(request)
        if self._authorized(request):
            return await handler(request)
        if request.path.startswith("/api/"):
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    def _authorized(self, request: web.Request) -> bool:
        """인증에 성공하면 request['scope'] 에 범위를 심는다.

        scope 가 None 이면 운영자(전역), 정수면 그 길드 하나로 한정된다.
        """
        sid = request.cookies.get(_COOKIE_NAME, "")
        if sid:
            valid, scope = self._session_scope(sid)
            if valid:
                request["scope"] = scope
                return True
        # 비브라우저 클라이언트(스크립트·모니터링)용 경로. 헤더로만 받는다.
        # 쿼리스트링(?token=)은 지원하지 않는다 — 접근 로그, Referer 헤더,
        # 브라우저 히스토리, 프록시 캐시에 토큰이 그대로 남기 때문이다.
        header = request.headers.get("Authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        supplied = bearer or request.headers.get("X-Admin-Token", "")
        if supplied and hmac.compare_digest(supplied, self._token):
            request["scope"] = None
            return True
        return False

    def _render(self, request: web.Request, name: str, *, error: str = "", status: int = 200) -> web.Response:
        html = _template(name).replace("{{NONCE}}", request.get("csp_nonce", ""))
        if error:
            html = html.replace("<!--ERROR-->", f'<p class="error" role="alert">{escape(error)}</p>')
        return web.Response(text=html, content_type="text/html", status=status)

    async def _login(self, request: web.Request) -> web.Response:
        return self._render(request, _LOGIN_TEMPLATE)

    async def _login_submit(self, request: web.Request) -> web.Response:
        ip = self._client_ip(request)
        locked = self._lockout_remaining(ip)
        if locked:
            wait = f"{locked}초" if locked < 60 else f"{locked // 60}분"
            return self._render(
                request,
                _LOGIN_TEMPLATE,
                error=f"시도 횟수를 초과했습니다. 약 {wait} 후 다시 시도하세요.",
                status=429,
            )

        data = await request.post()
        token = str(data.get("token", ""))

        # 1) 운영자 토큰 — 전역 범위. 2) 서버별 키 — 그 서버 하나로 한정.
        if self._token and hmac.compare_digest(token, self._token):
            scope: int | None = None
        else:
            scope = await self.keys.find_guild(token)
            if scope is None:
                self._record_login_failure(ip)
                return self._render(
                    request,
                    _LOGIN_TEMPLATE,
                    error="키가 올바르지 않습니다.",
                    status=401,
                )
            # 키는 살아 있는데 봇이 그 서버에서 쫓겨난 경우. 들여보내도 볼 게 없다.
            if self.bot.get_guild(scope) is None:
                self._record_login_failure(ip)
                return self._render(
                    request,
                    _LOGIN_TEMPLATE,
                    error="이 키의 서버에 코아가 없습니다. 다시 초대한 뒤 이용하세요.",
                    status=401,
                )

        self._login_failures.pop(ip, None)
        log.info("web admin login: scope=%s", scope if scope is not None else "operator")
        response = web.HTTPFound("/")
        response.set_cookie(
            _COOKIE_NAME,
            self._issue_session(scope),
            httponly=True,
            secure=_cookie_secure(),
            samesite="Strict",
            max_age=_SESSION_TTL_SECONDS,
        )
        raise response

    async def _logout(self, request: web.Request) -> web.Response:
        # 세션을 서버에서 지운다. 쿠키만 지우면 복사해 둔 쿠키가 계속 통한다.
        self._sessions.pop(request.cookies.get(_COOKIE_NAME, ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie(_COOKIE_NAME)
        return response

    async def _index(self, request: web.Request) -> web.Response:
        return self._render(request, _DASHBOARD_TEMPLATE)

    async def _api_state(self, request: web.Request) -> web.Response:
        guild_id = request.query.get("guild_id")
        guilds = self._visible_guilds(request)
        guild = self._guild(request, guild_id) if guild_id else (guilds[0] if guilds else None)
        if guild is None:
            return web.json_response(
                {
                    "guilds": [{"id": _id(item.id), "name": item.name} for item in guilds],
                    "selected": None,
                }
            )

        cfg = await self.store.get(guild.id)
        payload: dict[str, Any] = {
            "guilds": [{"id": _id(item.id), "name": item.name} for item in guilds],
            "selected": {
                "id": _id(guild.id),
                "name": guild.name,
                "config": cfg,
                "text_channels": [_channel_payload(ch) for ch in guild.text_channels],
                "voice_channels": [_channel_payload(ch) for ch in guild.voice_channels],
            },
        }
        # env 는 운영자에게만. 서버 주인에게는 남의 길드 ID(TEST_GUILD_ID)와 호스트
        # 파일시스템 경로가 자기 서버와 무관한 정보로 새어 나간다.
        if self._scope(request) is None:
            payload["env"] = {
                "host": _web_host(),
                "port": _web_port(),
                "config_path": os.getenv("CONFIG_PATH", "config.json"),
                "rank_path": os.getenv("RANK_PATH", "rank_stats.json"),
                "test_guild_id": os.getenv("TEST_GUILD_ID", ""),
            }
        return web.json_response(payload)

    async def _api_config(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(request, payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        fields: dict[str, Any] = {}
        try:
            if "tts_channel_id" in payload:
                fields["tts_channel_id"] = self._text_channel_id(guild, payload["tts_channel_id"])
            if "voice_channel_id" in payload:
                fields["voice_channel_id"] = self._voice_channel_id(guild, payload["voice_channel_id"])
            if "leaderboard_channel_id" in payload:
                fields["leaderboard_channel_id"] = self._text_channel_id(guild, payload["leaderboard_channel_id"])
            if "leaderboard_daily_enabled" in payload:
                fields["leaderboard_daily_enabled"] = _bool_value(payload["leaderboard_daily_enabled"])
            if "leaderboard_post_time" in payload:
                fields["leaderboard_post_time"] = _clean_time(payload["leaderboard_post_time"])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        await self.store.set(guild.id, **fields)
        return web.json_response({"ok": True, "config": await self.store.get(guild.id)})

    async def _api_leaderboard(self, request: web.Request) -> web.Response:
        guild = self._guild(request, request.query.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response(
                {"anchor": weekly_reset_anchor(), "rows": [], "available": False}
            )
        await rank_cog.store.ensure_week()
        rows = await rank_cog.store.leaderboard(guild.id, limit=10)
        return web.json_response(
            {
                "anchor": weekly_reset_anchor(),
                "rows": [_row_with_name(guild, row) for row in rows],
                "available": True,
            }
        )

    async def _api_leaderboard_history(self, request: web.Request) -> web.Response:
        guild = self._guild(request, request.query.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response({"weeks": [], "available": False})
        weeks = await rank_cog.store.list_history(guild.id)
        return web.json_response(
            {
                "weeks": [
                    {
                        "anchor": entry.get("anchor"),
                        "archived_at": entry.get("archived_at"),
                        "top": [_row_with_name(guild, row) for row in entry.get("top", [])],
                    }
                    for entry in weeks
                ],
                "available": True,
            }
        )

    async def _api_post_leaderboard(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(request, payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        cfg = await self.store.get(guild.id)
        channel = guild.get_channel(cfg.get("leaderboard_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "leaderboard channel is not configured"}, status=400)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "_leaderboard_embed"):
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        await rank_cog.store.ensure_week()
        embed = await rank_cog._leaderboard_embed(guild, limit=10)
        if embed is None:
            return web.json_response({"error": "no activity stats"}, status=400)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return web.json_response({"ok": True})

    async def _api_clear_leaderboard(self, request: web.Request) -> web.Response:
        payload = await request.json()
        guild = self._guild(request, payload.get("guild_id"))
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        if payload.get("confirm") != "CLEAR":
            return web.json_response({"error": "confirmation required"}, status=400)
        rank_cog = self.bot.get_cog("RankCog")
        if rank_cog is None or not hasattr(rank_cog, "store"):
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        result = await rank_cog.store.clear_guild(guild.id)
        return web.json_response({"ok": True, **result})

    @staticmethod
    def _scope(request: web.Request) -> int | None:
        return request.get("scope")

    def _guild(self, request: web.Request, guild_id: Any) -> discord.Guild | None:
        """요청이 건드려도 되는 길드만 돌려준다.

        세션 범위가 특정 길드면 그 길드 외에는 무조건 None 이다. 범위 밖 ID 를
        직접 넣어 보내는 요청이 여기서 막힌다 — 모든 API 핸들러의 유일한 관문.
        """
        try:
            target_id = int(guild_id)
        except (TypeError, ValueError):
            return None
        scope = self._scope(request)
        if scope is not None:
            if target_id != scope:
                return None
            return self.bot.get_guild(target_id)
        # 운영자 범위에서만 ADMIN_WEB_GUILD_IDS 허용 목록을 적용한다. 서버별 키는
        # 그 서버 주인이 직접 받은 것이므로 운영자 목록과 무관하게 동작해야 한다.
        allowed_ids = _allowed_guild_ids()
        if allowed_ids and target_id not in allowed_ids:
            return None
        return self.bot.get_guild(target_id)

    def _visible_guilds(self, request: web.Request) -> list[discord.Guild]:
        scope = self._scope(request)
        if scope is not None:
            guild = self.bot.get_guild(scope)
            return [guild] if guild is not None else []
        allowed_ids = _allowed_guild_ids()
        if not allowed_ids:
            return list(self.bot.guilds)
        return [guild for guild in self.bot.guilds if guild.id in allowed_ids]

    @staticmethod
    def _text_channel_id(guild: discord.Guild, value: Any) -> int:
        if value in (None, "", 0, "0"):
            return 0
        try:
            channel_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid text channel") from exc
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("invalid text channel")
        return channel.id

    @staticmethod
    def _voice_channel_id(guild: discord.Guild, value: Any) -> int:
        if value in (None, "", 0, "0"):
            return 0
        try:
            channel_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid voice channel") from exc
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            raise ValueError("invalid voice channel")
        return channel.id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebAdminCog(bot))
