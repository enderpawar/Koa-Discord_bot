from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from cogs import web_admin_cog
from cogs.admin_login_store import LoginGrant
from cogs.web_admin_cog import (
    WebAdminCog,
    _DASHBOARD_TEMPLATE,
    _LOGIN_TEMPLATE,
    _bool_value,
    _channel_payload,
    _clean_time,
    _config_payload,
    _cookie_name,
    _cookie_secure,
    _id,
    _template,
    _web_host,
    _web_port,
)


def test_clean_time_and_bool_value() -> None:
    assert _clean_time("23:59") == "23:59"
    assert _clean_time(None) == "00:00"
    with pytest.raises(ValueError):
        _clean_time("24:00")
    assert _bool_value("on") is True
    assert _bool_value("false") is False


def test_web_bind_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_HOST", raising=False)
    monkeypatch.setenv("ADMIN_WEB_PORT", "bad")
    assert _web_host() == "127.0.0.1"
    assert _web_port() == 8080


def test_discord_ids_are_serialized_as_strings() -> None:
    snowflake = 123456789012345678
    channel = SimpleNamespace(id=snowflake, name="일반")

    assert _id(snowflake) == str(snowflake)
    assert _channel_payload(channel) == {"id": str(snowflake), "name": "일반"}
    assert _config_payload(
        {
            "tts_channel_id": snowflake,
            "voice_channel_id": snowflake + 1,
            "leaderboard_channel_id": snowflake + 2,
            "leaderboard_daily_enabled": True,
        }
    ) == {
        "tts_channel_id": str(snowflake),
        "voice_channel_id": str(snowflake + 1),
        "leaderboard_channel_id": str(snowflake + 2),
        "leaderboard_daily_enabled": True,
    }


def test_login_template_exchanges_fragment_without_exposing_guild() -> None:
    login = _template(_LOGIN_TEMPLATE)
    dashboard = _template(_DASHBOARD_TEMPLATE)

    assert login.startswith("<!doctype html>") and login.rstrip().endswith("</html>")
    assert dashboard.startswith("<!doctype html>") and dashboard.rstrip().endswith("</html>")
    assert "location.hash" in login
    assert "history.replaceState" in login
    assert "'/login/token'" in login
    assert 'name="token"' not in login
    assert "guild_hint" not in login
    assert "/g/" not in login
    assert '<style nonce="{{NONCE}}">' in login
    assert '<script nonce="{{NONCE}}">' in login
    assert '<script nonce="{{NONCE}}">' in dashboard


def test_dashboard_never_sends_client_selected_guild_id() -> None:
    html = _template(_DASHBOARD_TEMPLATE)

    assert "?guild_id=" not in html
    assert "guild_id:guildId" not in html
    assert "fetch('/api/state')" in html


def test_template_cache_can_be_reloaded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    target = tmp_path / "admin_login.html"
    target.write_text("<!doctype html>first</html>", encoding="utf-8")
    monkeypatch.setattr(web_admin_cog, "_TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(web_admin_cog, "_template_cache", {})
    monkeypatch.delenv("ADMIN_WEB_TEMPLATE_RELOAD", raising=False)

    assert _template("admin_login.html") == "<!doctype html>first</html>"
    target.write_text("<!doctype html>second</html>", encoding="utf-8")
    assert _template("admin_login.html") == "<!doctype html>first</html>"
    monkeypatch.setenv("ADMIN_WEB_TEMPLATE_RELOAD", "1")
    assert _template("admin_login.html") == "<!doctype html>second</html>"


class _Request(dict):
    def __init__(
        self,
        *,
        cookies=None,
        headers=None,
        payload=None,
        remote="10.0.0.1",
        method="POST",
        host="admin.example.com",
        path="/api/config",
        json_error=False,
    ) -> None:
        super().__init__()
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.remote = remote
        self.method = method
        self.host = host
        self.path = path
        self._payload = {} if payload is None else payload
        self._json_error = json_error

    async def json(self):
        if self._json_error:
            raise json.JSONDecodeError("Expecting value", "not json", 0)
        return self._payload


class _FakeGuild:
    def __init__(self, guild_id: int, name: str, *, admin_user_id: int | None = None) -> None:
        self.id = guild_id
        self.name = name
        self.text_channels: list = []
        self.voice_channels: list = []
        self._admin_user_id = admin_user_id

    def get_member(self, user_id: int):
        if user_id != self._admin_user_id:
            return None
        return SimpleNamespace(
            id=user_id, guild_permissions=SimpleNamespace(administrator=True)
        )

    def get_channel(self, channel_id: int):
        return None


class _FakeBot:
    def __init__(self, *guilds: _FakeGuild) -> None:
        self.guilds = list(guilds)

    def get_guild(self, guild_id: int):
        return next((guild for guild in self.guilds if guild.id == guild_id), None)

    def get_cog(self, name: str):
        return None


def _cog(*guilds: _FakeGuild) -> WebAdminCog:
    return WebAdminCog(bot=_FakeBot(*guilds))


def test_session_is_opaque_and_carries_only_one_guild_and_user() -> None:
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=222))

    sid = cog._issue_session(111, 222)
    request = _Request(cookies={_cookie_name(): sid})

    assert "111" not in sid and "222" not in sid
    assert cog._authorized(request) is True
    assert request["scope"] == 111
    assert request["user_id"] == 222


def test_discord_admin_revocation_invalidates_existing_session() -> None:
    guild = _FakeGuild(111, "내 서버", admin_user_id=222)
    cog = _cog(guild)
    sid = cog._issue_session(111, 222)
    guild._admin_user_id = None

    request = _Request(cookies={_cookie_name(): sid})

    assert cog._authorized(request) is False
    assert sid not in cog._sessions


def test_idle_and_absolute_session_expiry_are_server_enforced(monkeypatch) -> None:
    cog = _cog()
    sid = cog._issue_session(111, 222)
    record = cog._sessions[sid]

    monkeypatch.setattr(
        web_admin_cog.time,
        "monotonic",
        lambda: record.last_seen_at + web_admin_cog._SESSION_IDLE_TTL_SECONDS,
    )
    assert cog._session_valid(sid) is False

    sid = cog._issue_session(111, 222)
    record = cog._sessions[sid]
    monkeypatch.setattr(
        web_admin_cog.time,
        "monotonic",
        lambda: record.created_at + web_admin_cog._SESSION_ABSOLUTE_TTL_SECONDS,
    )
    assert cog._session_valid(sid) is False


def test_master_headers_and_query_tokens_never_authorize(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "legacy-master-token")
    cog = _cog()
    request = _Request(headers={"X-Admin-Token": "legacy-master-token"})
    request.query = {"token": "legacy-master-token"}  # type: ignore[attr-defined]

    assert cog._authorized(request) is False
    assert cog._authorized(
        _Request(headers={"Authorization": "Bearer legacy-master-token"})
    ) is False


def _grants(cog: WebAdminCog, grant: LoginGrant | None) -> None:
    """저장소를 타지 않도록 peek/consume 을 함께 대역으로 세운다."""
    cog.login_grants.peek = AsyncMock(return_value=grant)
    cog.login_grants.consume = AsyncMock(return_value=grant)


async def test_single_use_grant_creates_scoped_session_cookie() -> None:
    guild = _FakeGuild(111, "내 서버", admin_user_id=222)
    cog = _cog(guild)
    _grants(cog, LoginGrant(guild_id=111, user_id=222, expires_at=999))
    request = _Request(payload={"token": "x" * 43})

    response = await cog._login_token(request)  # type: ignore[arg-type]

    assert response.status == 200
    assert response.cookies[_cookie_name()]["httponly"] is True
    assert response.cookies[_cookie_name()]["samesite"] == "Strict"
    assert [(r.guild_id, r.user_id) for r in cog._sessions.values()] == [(111, 222)]
    cog.login_grants.consume.assert_awaited_once()


async def test_invalid_or_used_grant_is_rejected_without_scope_leak() -> None:
    cog = _cog(_FakeGuild(111, "비밀 서버", admin_user_id=222))
    _grants(cog, None)

    response = await cog._login_token(
        _Request(payload={"token": "x" * 43})  # type: ignore[arg-type]
    )

    assert response.status == 401
    assert "비밀 서버" not in response.text
    assert cog._sessions == {}


async def test_grant_is_rejected_if_discord_admin_permission_was_removed() -> None:
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=None))
    _grants(cog, LoginGrant(guild_id=111, user_id=222, expires_at=999))

    response = await cog._login_token(
        _Request(payload={"token": "x" * 43})  # type: ignore[arg-type]
    )

    assert response.status == 403
    assert cog._sessions == {}


async def test_failed_permission_check_does_not_burn_the_grant() -> None:
    """재기동 직후 멤버 캐시가 비어 있어도 멀쩡한 링크를 태우지 않는다."""
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=None))
    _grants(cog, LoginGrant(guild_id=111, user_id=222, expires_at=999))

    response = await cog._login_token(
        _Request(payload={"token": "x" * 43})  # type: ignore[arg-type]
    )

    assert response.status == 403
    cog.login_grants.peek.assert_awaited_once()
    cog.login_grants.consume.assert_not_awaited()


async def test_concurrent_race_loser_gets_no_session() -> None:
    """peek 은 통과했지만 원자적 소비에서 진 요청은 세션을 받지 못한다."""
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=222))
    cog.login_grants.peek = AsyncMock(
        return_value=LoginGrant(guild_id=111, user_id=222, expires_at=999)
    )
    cog.login_grants.consume = AsyncMock(return_value=None)

    response = await cog._login_token(
        _Request(payload={"token": "x" * 43})  # type: ignore[arg-type]
    )

    assert response.status == 401
    assert cog._sessions == {}


async def test_login_lockout_never_blocks_a_holder_of_a_valid_grant() -> None:
    """공용 출발지 IP 하나로 모든 서버의 로그인을 막을 수 없어야 한다.

    Cloudflare Tunnel 뒤에서는 신뢰 프록시를 지정하지 않는 한 모든 사용자의
    request.remote 가 cloudflared 컨테이너 IP 하나로 같아진다.
    """
    shared_ip = "172.18.0.5"
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=222))
    for _ in range(web_admin_cog._LOGIN_MAX_FAILURES * 3):
        cog._record_login_failure(shared_ip)
    assert cog._lockout_remaining(shared_ip) > 0

    _grants(cog, LoginGrant(guild_id=111, user_id=222, expires_at=999))
    response = await cog._login_token(
        _Request(payload={"token": "x" * 43}, remote=shared_ip)  # type: ignore[arg-type]
    )

    assert response.status == 200
    assert [(r.guild_id, r.user_id) for r in cog._sessions.values()] == [(111, 222)]
    # 성공하면 그 출발지의 실패 기록도 걷힌다.
    assert cog._lockout_remaining(shared_ip) == 0


async def test_locked_out_source_still_gets_throttle_signal_for_bad_tokens() -> None:
    cog = _cog()
    for _ in range(web_admin_cog._LOGIN_MAX_FAILURES):
        cog._record_login_failure("10.0.0.1")
    _grants(cog, None)

    response = await cog._login_token(
        _Request(payload={"token": "x" * 43})  # type: ignore[arg-type]
    )

    assert response.status == 429


def test_forwarded_client_ip_is_trusted_only_from_a_registered_proxy(monkeypatch) -> None:
    cog = _cog()
    spoofed = {"CF-Connecting-IP": "203.0.113.9"}

    monkeypatch.delenv("ADMIN_WEB_TRUSTED_PROXIES", raising=False)
    assert cog._client_ip(_Request(remote="172.18.0.5", headers=spoofed)) == "172.18.0.5"

    monkeypatch.setenv("ADMIN_WEB_TRUSTED_PROXIES", "172.18.0.5")
    assert cog._client_ip(_Request(remote="172.18.0.5", headers=spoofed)) == "203.0.113.9"

    # 컨테이너 IP 는 유동적이라 대역으로도 적을 수 있어야 한다.
    monkeypatch.setenv("ADMIN_WEB_TRUSTED_PROXIES", "10.9.9.9, 172.16.0.0/12")
    assert cog._client_ip(_Request(remote="172.18.0.7", headers=spoofed)) == "203.0.113.9"
    monkeypatch.setenv("ADMIN_WEB_TRUSTED_PROXIES", "172.18.0.5")
    # 등록된 프록시라도 헤더가 IP 가 아니면 직전 홉으로 되돌린다.
    assert (
        cog._client_ip(_Request(remote="172.18.0.5", headers={"CF-Connecting-IP": "junk"}))
        == "172.18.0.5"
    )
    # 등록되지 않은 출발지가 보낸 헤더는 무시한다.
    assert cog._client_ip(_Request(remote="198.51.100.7", headers=spoofed)) == "198.51.100.7"


def test_session_guild_ignores_client_supplied_guild_ids() -> None:
    mine = _FakeGuild(111, "내 서버", admin_user_id=222)
    other = _FakeGuild(222, "남의 서버", admin_user_id=222)
    cog = _cog(mine, other)
    request = _Request(payload={"guild_id": 222})
    request["scope"] = 111
    request.query = {"guild_id": "222"}  # type: ignore[attr-defined]

    assert cog._session_guild(request) is mine  # type: ignore[arg-type]
    for handler in (
        WebAdminCog._api_state,
        WebAdminCog._api_config,
        WebAdminCog._api_leaderboard,
        WebAdminCog._api_leaderboard_history,
        WebAdminCog._api_post_leaderboard,
        WebAdminCog._api_clear_leaderboard,
    ):
        source = inspect.getsource(handler)
        assert 'payload.get("guild_id")' not in source
        assert 'request.query.get("guild_id")' not in source


def test_mutations_recheck_current_discord_admin_permission() -> None:
    guild = _FakeGuild(111, "내 서버", admin_user_id=222)
    cog = _cog(guild)
    request = _Request()
    request["scope"] = 111
    request["user_id"] = 222

    allowed, denied = cog._mutation_guild(request)  # type: ignore[arg-type]
    assert allowed is guild and denied is None

    guild._admin_user_id = None
    allowed, denied = cog._mutation_guild(request)  # type: ignore[arg-type]
    assert allowed is None and denied is not None and denied.status == 403


async def test_revoke_guild_drops_only_its_sessions_and_unused_grants() -> None:
    cog = _cog()
    mine = cog._issue_session(111, 1)
    other = cog._issue_session(222, 2)
    cog.login_grants.revoke_for_guild = AsyncMock(return_value=1)

    assert await cog.revoke_sessions_for_guild(111) == 1
    assert not cog._session_valid(mine)
    assert cog._session_valid(other)
    cog.login_grants.revoke_for_guild.assert_awaited_once_with(111)


def test_security_headers_lock_down_pages_and_cookies(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com")
    response = web.Response(text="hi")

    web_admin_cog._apply_security_headers(response, "n0nce")

    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'nonce-n0nce'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
    assert _cookie_secure() is True
    assert _cookie_name() == "__Host-koa_admin_session"


def test_login_failure_tracking_is_bounded_and_expires() -> None:
    cog = _cog()
    for i in range(web_admin_cog._LOGIN_FAILURE_MAX_TRACKED + 100):
        cog._record_login_failure(f"10.0.{i // 256}.{i % 256}")
    assert len(cog._login_failures) <= web_admin_cog._LOGIN_FAILURE_MAX_TRACKED

    later = time.monotonic() + web_admin_cog._LOGIN_LOCKOUT_MAX_SECONDS + 1
    cog._prune_login_failures(later)
    assert cog._login_failures == {}


async def test_logout_clears_the_cookie_with_the_attributes_it_was_issued_with(
    monkeypatch,
) -> None:
    """__Host- 쿠키는 Secure 없는 Set-Cookie 를 브라우저가 통째로 거부한다."""
    monkeypatch.setenv("ADMIN_WEB_COOKIE_SECURE", "1")
    cog = _cog()
    sid = cog._issue_session(111, 222)
    name = _cookie_name()

    response = await cog._logout(
        _Request(cookies={name: sid}, path="/logout")  # type: ignore[arg-type]
    )

    assert sid not in cog._sessions
    morsel = response.cookies[name]
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Strict"
    assert morsel["path"] == "/"


async def test_malformed_json_body_is_a_client_error_not_a_crash() -> None:
    guild = _FakeGuild(111, "내 서버", admin_user_id=222)
    cog = _cog(guild)

    for handler in (cog._api_config, cog._api_clear_leaderboard):
        request = _Request(json_error=True)
        request["scope"] = 111
        request["user_id"] = 222

        response = await handler(request)  # type: ignore[arg-type]

        assert response.status == 400


async def test_non_object_json_body_is_rejected() -> None:
    cog = _cog(_FakeGuild(111, "내 서버", admin_user_id=222))
    request = _Request(payload=["not", "an", "object"])
    request["scope"] = 111
    request["user_id"] = 222

    response = await cog._api_config(request)  # type: ignore[arg-type]

    assert response.status == 400


async def test_cross_origin_state_change_is_rejected_without_sec_fetch_site() -> None:
    """Sec-Fetch-Site 를 보내지 않는 구형 브라우저도 Origin 으로 걸러진다."""
    cog = _cog()

    async def _handler(_request):
        return web.json_response({"ok": True})

    evil = _Request(headers={"Origin": "https://evil.example.com"}, host="admin.example.com")
    blocked = await cog._csrf_middleware(evil, _handler)  # type: ignore[arg-type]
    assert blocked.status == 403

    same = _Request(headers={"Origin": "https://admin.example.com"}, host="admin.example.com")
    allowed = await cog._csrf_middleware(same, _handler)  # type: ignore[arg-type]
    assert allowed.status == 200

    # 터널이 TLS 를 끊고 평문으로 넘겨도 스킴 차이로 막히면 안 된다.
    tunneled = _Request(
        headers={"Origin": "https://admin.example.com"}, host="admin.example.com:8080"
    )
    assert (await cog._csrf_middleware(tunneled, _handler)).status == 200  # type: ignore[arg-type]

    # 비브라우저(Origin 없음)는 그대로 통과한다.
    assert (await cog._csrf_middleware(_Request(), _handler)).status == 200  # type: ignore[arg-type]


async def test_leaving_a_guild_revokes_its_sessions_and_unused_links() -> None:
    cog = _cog()
    mine = cog._issue_session(111, 1)
    other = cog._issue_session(222, 2)
    cog.login_grants.revoke_for_guild = AsyncMock(return_value=1)

    await cog.on_guild_remove(SimpleNamespace(id=111))  # type: ignore[arg-type]

    assert not cog._session_valid(mine)
    assert cog._session_valid(other)
    cog.login_grants.revoke_for_guild.assert_awaited_once_with(111)


def test_rank_cog_guard_is_uniform_across_handlers() -> None:
    """핸들러마다 다른 hasattr 조합을 쓰다 한 곳만 빠뜨리는 일을 막는다."""
    cog = _cog()

    assert cog._rank_cog() is None  # RankCog 자체가 없음
    cog.bot.get_cog = lambda _name: SimpleNamespace(store=object())  # type: ignore[assignment]
    assert cog._rank_cog() is not None
    assert cog._rank_cog("_leaderboard_embed") is None

    cog.bot.get_cog = lambda _name: SimpleNamespace(  # type: ignore[assignment]
        store=object(), _leaderboard_embed=object()
    )
    assert cog._rank_cog("_leaderboard_embed") is not None

    for handler in (
        WebAdminCog._api_leaderboard,
        WebAdminCog._api_leaderboard_history,
        WebAdminCog._api_post_leaderboard,
        WebAdminCog._api_clear_leaderboard,
    ):
        assert 'self.bot.get_cog("RankCog")' not in inspect.getsource(handler)


def test_compose_publishes_admin_port_to_loopback_only() -> None:
    raw = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    lines = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    published = [
        line for line in lines if line.startswith('- "') and line.rstrip().endswith('8080"')
    ]
    assert published == ['- "127.0.0.1:8080:8080"']
