"""Discord-authorized, guild-scoped web administration dashboard."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands
from yarl import URL

from cogs.admin_login_store import OneTimeLoginStore
from cogs.config_store import ConfigStore
from cogs.preprocess import (
    MAX_PRONUNCIATION_KEY,
    MAX_PRONUNCIATION_RULES,
    MAX_PRONUNCIATION_VALUE,
    normalize_pronunciations,
)
from cogs.rank_cog import DEFAULT_LEADERBOARD_POST_TIME
from cogs.rank_store import weekly_reset_anchor

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

_COOKIE_NAME = "koa_admin_session"
_HOST_COOKIE_NAME = "__Host-koa_admin_session"
_SESSION_IDLE_TTL_SECONDS = 15 * 60
_SESSION_ABSOLUTE_TTL_SECONDS = 30 * 60

# 남용 억제. IP 당 실패 횟수를 세고, 임계치를 넘으면 잠금 시간을 2배씩 늘린다.
# 30s → 1m → 2m → … → 15m.
#
# 이 잠금은 "유효한 링크를 든 관리자"를 절대 막지 않는다(_login_token 참고).
# Cloudflare Tunnel 뒤에서는 신뢰 프록시를 설정하지 않는 한 모든 사용자의
# request.remote 가 cloudflared 컨테이너 IP 하나로 같아지므로, 먼저 거부하는
# 구조였다면 아무나 잘못된 토큰을 반복 제출해 다른 서버 관리자까지 로그인하지
# 못하게 만들 수 있었다. 토큰이 256비트 난수 + 1회용이라 무차별 대입은 애초에
# 불가능하므로, 잠금은 차단이 아니라 남용 신호와 로깅 용도로만 남긴다.
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
    value = os.getenv("ADMIN_WEB_ENABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _web_host() -> str:
    # 외부 공개는 항상 명시적으로만. 호스팅 환경을 추측해 0.0.0.0 으로 여는 분기를
    # 두지 않는다. 기본값은 로컬 바인딩이어야 한다.
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


def _cookie_name() -> str:
    """HTTPS에서는 서브도메인이 덮어쓸 수 없는 __Host- 쿠키를 사용한다."""
    return _HOST_COOKIE_NAME if _cookie_secure() else _COOKIE_NAME


def _trusted_proxies() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """CF-Connecting-IP 를 믿어도 되는 직전 홉(hop) 목록. IP 또는 CIDR.

    비워 두면 어떤 전달 헤더도 신뢰하지 않는다. 헤더는 위조가 쉬워서, 신뢰 경로를
    확인하지 않고 키로 쓰면 공격자가 매 요청 다른 값을 보내 제한을 그냥 우회한다.
    docker compose 에서는 cloudflared 컨테이너 IP 가 유동적이므로 대역으로 적는다
    (예: ADMIN_WEB_TRUSTED_PROXIES=172.16.0.0/12).
    """
    networks = []
    for entry in os.getenv("ADMIN_WEB_TRUSTED_PROXIES", "").split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            log.warning("ignoring malformed ADMIN_WEB_TRUSTED_PROXIES entry: %s", candidate)
    return tuple(networks)


def _same_host(origin: str, host: str) -> bool:
    """Origin 헤더의 호스트가 요청 호스트와 같은지.

    스킴은 비교하지 않는다. 터널이 TLS 를 종단하고 봇에는 평문으로 넘기므로
    Origin 은 https, 요청은 http 로 보이는 것이 정상이다.
    """
    try:
        origin_host = URL(origin).host
    except (ValueError, TypeError):
        return False
    return bool(origin_host) and origin_host == host.split(":")[0]


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


def _config_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Discord snowflake를 브라우저에서 반올림되지 않는 문자열로 직렬화한다."""
    payload = dict(config)
    # tts/voice 채널은 대시보드가 고르지 않고 `/입장` 이 써 넣지만, 지금 어디에
    # 붙어 있는지를 읽기 전용으로 보여 주므로 계속 내려보낸다.
    for name in ("tts_channel_id", "voice_channel_id", "leaderboard_channel_id"):
        if name in payload:
            payload[name] = _id(int(payload[name] or 0))
    payload["pronunciations"] = normalize_pronunciations(payload.get("pronunciations"))
    return payload


def _pronunciation_rules(value: Any) -> dict[str, str]:
    """대시보드가 보낸 발음 사전을 검증한다.

    저장된 값을 읽을 때(normalize_pronunciations)는 조용히 버리지만, 사람이 지금
    입력한 값은 왜 안 들어갔는지 알려 줘야 하므로 여기서는 예외를 던진다.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("발음 사전 형식이 올바르지 않습니다.")
    if len(value) > MAX_PRONUNCIATION_RULES:
        raise ValueError(
            f"발음 규칙은 최대 {MAX_PRONUNCIATION_RULES}개까지 저장할 수 있습니다."
        )
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        read_as = "" if raw_value is None else str(raw_value).strip()
        if not key:
            raise ValueError("바꿀 말을 비워 둘 수 없습니다.")
        if len(key) > MAX_PRONUNCIATION_KEY:
            raise ValueError(
                f"바꿀 말은 {MAX_PRONUNCIATION_KEY}자 이하로 입력해 주세요: {key[:20]}"
            )
        if len(read_as) > MAX_PRONUNCIATION_VALUE:
            raise ValueError(
                f"읽을 말은 {MAX_PRONUNCIATION_VALUE}자 이하로 입력해 주세요: {key}"
            )
    return normalize_pronunciations(value)


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


@dataclass(slots=True)
class _SessionRecord:
    guild_id: int
    user_id: int
    created_at: float
    last_seen_at: float


class WebAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = ConfigStore()
        self.login_grants = OneTimeLoginStore()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._sessions: dict[str, _SessionRecord] = {}
        # 클라이언트 IP -> (연속 실패 횟수, 잠금 해제 시각)
        self._login_failures: dict[str, tuple[int, float]] = {}

    def _issue_session(self, guild_id: int, user_id: int) -> str:
        now = time.monotonic()
        self._sessions = {
            sid: record
            for sid, record in self._sessions.items()
            if now - record.created_at < _SESSION_ABSOLUTE_TTL_SECONDS
            and now - record.last_seen_at < _SESSION_IDLE_TTL_SECONDS
        }
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = _SessionRecord(guild_id, user_id, now, now)
        return sid

    def _session_record(self, sid: str, *, touch: bool = True) -> _SessionRecord | None:
        record = self._sessions.get(sid)
        if record is None:
            return None
        now = time.monotonic()
        if (
            now - record.created_at >= _SESSION_ABSOLUTE_TTL_SECONDS
            or now - record.last_seen_at >= _SESSION_IDLE_TTL_SECONDS
        ):
            self._sessions.pop(sid, None)
            return None
        if touch:
            record.last_seen_at = now
        return record

    def _session_scope(self, sid: str) -> tuple[bool, int | None]:
        record = self._session_record(sid)
        return (True, record.guild_id) if record is not None else (False, None)

    def _session_valid(self, sid: str) -> bool:
        return self._session_scope(sid)[0]

    async def revoke_sessions_for_guild(self, guild_id: int) -> int:
        """해당 길드의 활성 세션과 아직 쓰지 않은 로그인 권한을 모두 끊는다."""
        doomed = [sid for sid, record in self._sessions.items() if record.guild_id == guild_id]
        for sid in doomed:
            self._sessions.pop(sid, None)
        await self.login_grants.revoke_for_guild(guild_id)
        return len(doomed)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """봇이 서버에서 빠지면 그 서버의 세션과 미사용 링크를 즉시 폐기한다.

        매 요청 권한 재검사가 이미 접근을 막지만, 발급된 링크와 세션 레코드를
        남겨 둘 이유가 없다.
        """
        revoked = await self.revoke_sessions_for_guild(guild.id)
        if revoked:
            log.info("revoked %d web admin session(s) for departed guild %s", revoked, guild.id)

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        """실패 횟수를 셀 키.

        직전 홉이 ADMIN_WEB_TRUSTED_PROXIES 에 등록된 경우에만 Cloudflare 가 붙이는
        CF-Connecting-IP 를 쓴다. 그 경로에서 오지 않은 헤더는 위조로 보고 버린다.
        신뢰 프록시를 설정하지 않으면 터널 뒤 모든 사용자가 같은 키를 공유하게
        되는데, 그래도 유효한 링크는 막히지 않는다(_login_token 참고).
        """
        remote = request.remote or "unknown"
        trusted = _trusted_proxies()
        if not trusted:
            return remote
        try:
            peer = ipaddress.ip_address(remote)
        except ValueError:
            return remote
        if not any(peer in network for network in trusted):
            return remote
        forwarded = (request.headers.get("CF-Connecting-IP") or "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            return remote

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
        await self.login_grants.initialize()

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
                web.post("/login/token", self._login_token),
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
                "from outside the host, login grants and session cookies travel "
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

        Sec-Fetch-Site 를 보내지 않는 구형 브라우저를 위해 Origin 도 함께 본다.
        Origin 이 붙어 있는데 호스트가 다르면 그건 확실한 교차 출처 요청이다.
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
            origin = request.headers.get("Origin")
            if origin and not _same_host(origin, request.host or ""):
                log.warning(
                    "cross-origin %s to %s rejected (Origin: %s)",
                    request.method,
                    request.path,
                    origin,
                )
                return web.json_response({"error": "cross-site request"}, status=403)
        return await handler(request)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path in {"/login", "/login/token"}:
            return await handler(request)
        if self._authorized(request):
            return await handler(request)
        if request.path.startswith("/api/"):
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    def _authorized(self, request: web.Request) -> bool:
        """유효한 세션과 현재 Discord 관리자 권한을 함께 확인한다."""
        sid = request.cookies.get(_cookie_name(), "")
        if sid:
            record = self._session_record(sid)
            if record is not None:
                guild = self.bot.get_guild(record.guild_id)
                member = guild.get_member(record.user_id) if guild is not None else None
                permissions = getattr(member, "guild_permissions", None)
                if member is not None and bool(permissions and permissions.administrator):
                    request["scope"] = record.guild_id
                    request["user_id"] = record.user_id
                    return True
                self._sessions.pop(sid, None)
        return False

    @staticmethod
    async def _json_body(request: web.Request) -> dict[str, Any] | None:
        """JSON 객체 본문을 읽는다. 형식이 어긋나면 None.

        핸들러가 request.json() 을 직접 부르면 잘린 본문 하나에 500 과 스택트레이스가
        나간다. 형식 오류는 서버 잘못이 아니므로 400 으로 돌려준다.
        """
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _bad_body() -> web.Response:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    def _render(
        self,
        request: web.Request,
        name: str,
        *,
        error: str = "",
        status: int = 200,
    ) -> web.Response:
        html = _template(name).replace("{{NONCE}}", request.get("csp_nonce", ""))
        if error:
            html = html.replace("<!--ERROR-->", f'<p class="error" role="alert">{escape(error)}</p>')
        return web.Response(text=html, content_type="text/html", status=status)

    async def _login(self, request: web.Request) -> web.Response:
        if self._authorized(request):
            raise web.HTTPFound("/")
        return self._render(request, _LOGIN_TEMPLATE)

    async def _login_token(self, request: web.Request) -> web.Response:
        ip = self._client_ip(request)
        # 잠금 상태여도 먼저 거부하지 않는다. 유효한 링크는 언제나 통과해야
        # 공용 출발지 IP 하나로 모든 서버의 로그인을 막는 일이 생기지 않는다.
        # 길이 검사가 쓰레기 요청을 저장소에 닿기 전에 걸러 준다.
        locked = self._lockout_remaining(ip)

        payload = await self._json_body(request) or {}
        token = str(payload.get("token", ""))
        grant = None
        if 32 <= len(token) <= 128:
            # 아직 소비하지 않는다. 권한 확인이 실패했을 때(예: 재기동 직후
            # 멤버 캐시가 비어 있을 때) 멀쩡한 링크를 태우지 않기 위해서다.
            grant = await self.login_grants.peek(token)
        if grant is None:
            self._record_login_failure(ip)
            if locked:
                return web.json_response(
                    {"error": "로그인 요청이 잠시 제한되었습니다. 새 링크를 발급해 주세요."},
                    status=429,
                )
            return web.json_response(
                {"error": "로그인 링크가 만료되었거나 이미 사용되었습니다."}, status=401
            )

        guild = self.bot.get_guild(grant.guild_id)
        member = guild.get_member(grant.user_id) if guild is not None else None
        permissions = getattr(member, "guild_permissions", None)
        if guild is None or member is None or not bool(permissions and permissions.administrator):
            self._record_login_failure(ip)
            return web.json_response(
                {"error": "현재 이 서버의 관리자 권한을 확인할 수 없습니다."}, status=403
            )

        # 여기서 원자적으로 소비한다. 같은 링크를 동시에 열어도 이 DELETE 를
        # 이긴 한 요청만 세션을 받는다.
        consumed = await self.login_grants.consume(token)
        if consumed is None:
            self._record_login_failure(ip)
            return web.json_response(
                {"error": "로그인 링크가 만료되었거나 이미 사용되었습니다."}, status=401
            )

        self._login_failures.pop(ip, None)
        log.info(
            "web admin login: guild_id=%s user_id=%s", grant.guild_id, grant.user_id
        )
        response = web.json_response({"ok": True})
        response.set_cookie(
            _cookie_name(),
            self._issue_session(grant.guild_id, grant.user_id),
            httponly=True,
            secure=_cookie_secure(),
            samesite="Strict",
            path="/",
        )
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        # 세션을 서버에서 지운다. 쿠키만 지우면 복사해 둔 쿠키가 계속 통한다.
        cookie_name = _cookie_name()
        self._sessions.pop(request.cookies.get(cookie_name, ""), None)
        response = web.json_response({"ok": True})
        # 삭제용 Set-Cookie 도 발급 때와 같은 속성을 달아야 한다. __Host- 접두사는
        # Secure 가 없는 Set-Cookie 를 브라우저가 통째로 거부해서, 그냥 지우면
        # 죽은 쿠키가 계속 남는다.
        response.del_cookie(
            cookie_name,
            path="/",
            secure=_cookie_secure(),
            httponly=True,
            samesite="Strict",
        )
        return response

    async def _index(self, request: web.Request) -> web.Response:
        return self._render(request, _DASHBOARD_TEMPLATE)

    async def _api_state(self, request: web.Request) -> web.Response:
        guild = self._session_guild(request)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        cfg = await self.store.get(guild.id)
        return web.json_response(
            {
                "guilds": [{"id": _id(guild.id), "name": guild.name}],
                "selected": {
                    "id": _id(guild.id),
                    "name": guild.name,
                    "config": _config_payload(cfg),
                    "text_channels": [_channel_payload(ch) for ch in guild.text_channels],
                    "voice_channels": [_channel_payload(ch) for ch in guild.voice_channels],
                },
            }
        )

    async def _api_config(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        if payload is None:
            return self._bad_body()
        guild, denied = self._mutation_guild(request)
        if denied is not None:
            return denied

        # tts_channel_id / voice_channel_id 는 의도적으로 받지 않는다. `/입장` 과
        # 음성 패널이 연결할 때마다 둘을 현재 음성 채널로 덮어쓰므로, 여기서
        # 무엇을 저장하든 다음 연결에서 지워진다. 저장되는 척하는 입력을 두느니
        # 아예 받지 않는다.
        fields: dict[str, Any] = {}
        try:
            if "pronunciations" in payload:
                fields["pronunciations"] = _pronunciation_rules(payload["pronunciations"])
            if "leaderboard_channel_id" in payload:
                fields["leaderboard_channel_id"] = self._text_channel_id(guild, payload["leaderboard_channel_id"])
            if "leaderboard_daily_enabled" in payload:
                fields["leaderboard_daily_enabled"] = _bool_value(payload["leaderboard_daily_enabled"])
            if "leaderboard_post_time" in payload:
                fields["leaderboard_post_time"] = _clean_time(payload["leaderboard_post_time"])
            if "party_tier_badges" in payload:
                fields["party_tier_badges"] = _bool_value(payload["party_tier_badges"])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        await self.store.set(guild.id, **fields)
        return web.json_response(
            {"ok": True, "config": _config_payload(await self.store.get(guild.id))}
        )

    def _rank_cog(self, *required: str):
        """RankCog 를 돌려준다. 필요한 속성이 하나라도 없으면 None.

        핸들러마다 제각각 hasattr 을 부르면 한 곳만 빠뜨려 500 이 난다.
        store 는 모든 호출부가 쓰므로 항상 함께 확인한다.
        """
        cog = self.bot.get_cog("RankCog")
        return cog if all(hasattr(cog, name) for name in ("store", *required)) else None

    async def _api_leaderboard(self, request: web.Request) -> web.Response:
        guild = self._session_guild(request)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self._rank_cog()
        if rank_cog is None:
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
        guild = self._session_guild(request)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)
        rank_cog = self._rank_cog()
        if rank_cog is None:
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
        guild, denied = self._mutation_guild(request)
        if denied is not None:
            return denied
        cfg = await self.store.get(guild.id)
        channel = guild.get_channel(cfg.get("leaderboard_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "리더보드 채널을 먼저 저장해 주세요."}, status=400)
        rank_cog = self._rank_cog("_leaderboard_embed")
        if rank_cog is None:
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        await rank_cog.store.ensure_week()
        embed = await rank_cog._leaderboard_embed(guild, limit=10)
        if embed is None:
            return web.json_response({"error": "아직 집계된 활동 내역이 없습니다."}, status=400)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return web.json_response({"ok": True})

    async def _api_clear_leaderboard(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        if payload is None:
            return self._bad_body()
        guild, denied = self._mutation_guild(request)
        if denied is not None:
            return denied
        if payload.get("confirm") != "CLEAR":
            return web.json_response({"error": "confirmation required"}, status=400)
        rank_cog = self._rank_cog()
        if rank_cog is None:
            return web.json_response({"error": "rank cog unavailable"}, status=500)
        result = await rank_cog.store.clear_guild(guild.id)
        return web.json_response({"ok": True, **result})

    @staticmethod
    def _scope(request: web.Request) -> int | None:
        return request.get("scope")

    def _session_guild(self, request: web.Request) -> discord.Guild | None:
        guild_id = self._scope(request)
        return self.bot.get_guild(guild_id) if isinstance(guild_id, int) else None

    def _mutation_guild(
        self, request: web.Request
    ) -> tuple[discord.Guild | None, web.Response | None]:
        """세션 길드와 현재 Discord 관리자 권한을 동시에 확인한다."""
        guild = self._session_guild(request)
        user_id = request.get("user_id")
        member = guild.get_member(user_id) if guild is not None and user_id else None
        permissions = getattr(member, "guild_permissions", None)
        if guild is None:
            return None, web.json_response({"error": "guild not found"}, status=404)
        if member is None or not bool(permissions and permissions.administrator):
            return None, web.json_response(
                {"error": "Discord 관리자 권한을 다시 확인해 주세요."}, status=403
            )
        return guild, None

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebAdminCog(bot))
