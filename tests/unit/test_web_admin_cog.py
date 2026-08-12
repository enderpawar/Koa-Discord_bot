from __future__ import annotations

import pytest
from aiohttp import web

from cogs import web_admin_cog
from cogs.web_admin_cog import (
    WebAdminCog,
    _DASHBOARD_TEMPLATE,
    _LOGIN_TEMPLATE,
    _allowed_guild_ids,
    _bool_value,
    _channel_payload,
    _clean_time,
    _cookie_secure,
    _id,
    _template,
    _web_host,
    _web_port,
)


def test_clean_time_accepts_hh_mm() -> None:
    assert _clean_time("23:59") == "23:59"
    assert _clean_time(None) == "00:00"


def test_clean_time_rejects_invalid_value() -> None:
    with pytest.raises(ValueError):
        _clean_time("24:00")


def test_bool_value_accepts_common_strings() -> None:
    assert _bool_value(True) is True
    assert _bool_value("true") is True
    assert _bool_value("on") is True
    assert _bool_value("false") is False


def test_web_port_falls_back_for_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_PORT", "bad")
    assert _web_port() == 8080


def test_web_host_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_HOST", raising=False)

    assert _web_host() == "127.0.0.1"


def test_web_host_uses_explicit_public_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_HOST", "0.0.0.0")

    assert _web_host() == "0.0.0.0"


def test_allowed_guild_ids_uses_admin_web_guild_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_WEB_GUILD_IDS", "123, 456")
    monkeypatch.setenv("TEST_GUILD_ID", "789")

    assert _allowed_guild_ids() == {123, 456}


def test_allowed_guild_ids_falls_back_to_test_guild_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_WEB_GUILD_IDS", raising=False)
    monkeypatch.setenv("TEST_GUILD_ID", "789")

    assert _allowed_guild_ids() == {789}


def test_id_is_serialized_as_string_to_preserve_discord_snowflakes() -> None:
    assert _id(123456789012345678) == "123456789012345678"


def test_channel_payload_serializes_id_as_string() -> None:
    class FakeChannel:
        id = 123456789012345678
        name = "일반"

    assert _channel_payload(FakeChannel()) == {
        "id": "123456789012345678",
        "name": "일반",
    }


def test_templates_exist_and_are_complete_documents() -> None:
    login = _template(_LOGIN_TEMPLATE)
    dashboard = _template(_DASHBOARD_TEMPLATE)

    for html in (login, dashboard):
        assert html.startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")

    # 로그인 실패 문구를 끼워 넣는 자리. 이게 없으면 401 응답이 조용히 안내 없는
    # 페이지가 된다.
    assert "<!--ERROR-->" in login
    # 대시보드는 guild_id 로 서버를 갈아끼우는 API 를 호출해야 한다.
    assert "/api/state" in dashboard


def test_template_is_cached_until_reload_is_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
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


class _FakeRequest:
    """세션/잠금 로직만 검사하기 위한 최소 요청 객체."""

    def __init__(self, *, cookies=None, headers=None, remote="10.0.0.1") -> None:
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.remote = remote
        self._state: dict = {}

    def __setitem__(self, key, value):
        self._state[key] = value

    def __getitem__(self, key):
        return self._state[key]

    def get(self, key, default=None):
        return self._state.get(key, default)


def _cog() -> WebAdminCog:
    return WebAdminCog(bot=None)


def test_session_cookie_never_carries_the_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "super-secret")
    cog = _cog()

    sid = cog._issue_session(None)

    # 쿠키가 새더라도 새는 것은 세션 하나여야 한다. 토큰 자체가 담기면 안 된다.
    assert sid != "super-secret"
    assert "super-secret" not in sid
    assert cog._session_valid(sid)


def test_expired_session_is_rejected_and_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    cog = _cog()
    sid = cog._issue_session(None)
    monkeypatch.setattr(
        web_admin_cog.time,
        "monotonic",
        lambda: cog._sessions[sid][1] + 1,
    )

    assert cog._session_valid(sid) is False
    assert sid not in cog._sessions


def test_query_string_token_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """?token= 은 접근 로그·Referer·히스토리에 남으므로 인증 경로가 아니어야 한다."""
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "super-secret")
    cog = _cog()
    request = _FakeRequest()
    request.query = {"token": "super-secret"}  # type: ignore[attr-defined]

    assert cog._authorized(request) is False


def test_header_token_still_authorizes_non_browser_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "super-secret")
    cog = _cog()

    assert cog._authorized(
        _FakeRequest(headers={"Authorization": "Bearer super-secret"})
    )
    assert cog._authorized(_FakeRequest(headers={"X-Admin-Token": "super-secret"}))
    assert not cog._authorized(_FakeRequest(headers={"X-Admin-Token": "wrong"}))


def test_login_locks_out_after_repeated_failures() -> None:
    cog = _cog()

    for _ in range(web_admin_cog._LOGIN_MAX_FAILURES - 1):
        cog._record_login_failure("10.0.0.1")
    assert cog._lockout_remaining("10.0.0.1") == 0

    cog._record_login_failure("10.0.0.1")
    assert cog._lockout_remaining("10.0.0.1") > 0
    # 잠금은 IP 단위다. 다른 클라이언트까지 막지 않는다.
    assert cog._lockout_remaining("10.0.0.2") == 0


def test_lockout_ignores_forwarded_for_header() -> None:
    """X-Forwarded-For 를 키로 쓰면 헤더만 바꿔가며 잠금을 우회할 수 있다."""
    cog = _cog()
    request = _FakeRequest(
        headers={"X-Forwarded-For": "1.2.3.4"}, remote="10.0.0.1"
    )

    assert cog._client_ip(request) == "10.0.0.1"


def test_security_headers_lock_down_the_page() -> None:
    response = web.Response(text="hi")

    web_admin_cog._apply_security_headers(response, "n0nce")

    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'nonce-n0nce'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_cookie_secure_follows_public_url_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com")
    assert _cookie_secure() is True

    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "http://203.0.113.7:8080")
    assert _cookie_secure() is False

    # 평문 HTTP 라도 리버스 프록시가 TLS 를 끊는 구성이면 직접 켤 수 있어야 한다.
    monkeypatch.setenv("ADMIN_WEB_COOKIE_SECURE", "1")
    assert _cookie_secure() is True


def test_templates_declare_a_csp_nonce_placeholder() -> None:
    """nonce 자리가 없으면 CSP 가 자기 스타일/스크립트를 막아 화면이 깨진다."""
    for name in (_LOGIN_TEMPLATE, _DASHBOARD_TEMPLATE):
        html = _template(name)
        assert '<style nonce="{{NONCE}}">' in html
    assert '<script nonce="{{NONCE}}">' in _template(_DASHBOARD_TEMPLATE)


def test_dashboard_has_no_inline_style_attributes() -> None:
    """CSP style-src 에 nonce 만 두면 마크업의 style="" 속성은 차단된다.

    nonce 는 요소에만 적용되고 속성에는 적용되지 않는다 (style-src-attr 폴백).
    인라인 스타일을 남겨두면 조용히 무시돼 화면이 어긋난다.
    """
    for name in (_LOGIN_TEMPLATE, _DASHBOARD_TEMPLATE):
        assert 'style="' not in _template(name)


def test_dashboard_confirms_destructive_reset_in_page() -> None:
    html = _template(_DASHBOARD_TEMPLATE)

    # 초기화는 확인 문구 입력 후에만 가능해야 한다. 확인 행은 기본으로 숨어 있다.
    assert 'id="clear_confirm" hidden' in html
    assert 'id="clear_input"' in html
    assert 'id="clear_go"' in html
    # 브라우저 prompt() 는 쓰지 않는다 — 봇 자동화와 일부 브라우저에서 막힌다.
    assert "prompt(" not in html


class _FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name
        self.text_channels: list = []
        self.voice_channels: list = []


class _ScopedBot:
    def __init__(self, *guilds: _FakeGuild) -> None:
        self.guilds = list(guilds)

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)


def _scoped_request(scope):
    request = _FakeRequest()
    request["scope"] = scope  # type: ignore[index]
    return request


def _req_with_scope(scope):
    """dict 접근을 흉내내는 최소 요청 객체."""

    class R(dict):
        cookies: dict = {}
        headers: dict = {}
        remote = "10.0.0.1"

    r = R()
    r["scope"] = scope
    return r


def test_guild_scoped_session_cannot_reach_another_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """서버별 키의 핵심 보증 — 남의 서버 ID 를 직접 넣어도 막혀야 한다."""
    monkeypatch.delenv("ADMIN_WEB_GUILD_IDS", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "내 서버"), _FakeGuild(222, "남의 서버")))
    request = _req_with_scope(111)

    assert cog._guild(request, 111) is not None
    assert cog._guild(request, "111") is not None
    assert cog._guild(request, 222) is None
    assert cog._guild(request, "222") is None


def test_guild_scoped_session_sees_only_its_own_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_WEB_GUILD_IDS", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "내 서버"), _FakeGuild(222, "남의 서버")))

    visible = cog._visible_guilds(_req_with_scope(111))

    assert [g.id for g in visible] == [111]


def test_operator_scope_sees_every_guild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_GUILD_IDS", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "a"), _FakeGuild(222, "b")))

    visible = cog._visible_guilds(_req_with_scope(None))

    assert [g.id for g in visible] == [111, 222]
    assert cog._guild(_req_with_scope(None), 222) is not None


def test_allowlist_restricts_operator_but_not_guild_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADMIN_WEB_GUILD_IDS 는 운영자 범위 전용.

    서버 주인이 직접 받은 키가 운영자의 편의 설정 때문에 막히면 안 된다.
    """
    monkeypatch.setenv("ADMIN_WEB_GUILD_IDS", "111")
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "a"), _FakeGuild(222, "b")))

    assert cog._guild(_req_with_scope(None), 222) is None
    assert cog._guild(_req_with_scope(222), 222) is not None


def test_session_scope_is_carried_from_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "operator-token")
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "a")))
    sid = cog._issue_session(111)
    request = _FakeRequest(cookies={web_admin_cog._COOKIE_NAME: sid})

    assert cog._authorized(request) is True
    assert request["scope"] == 111


def test_header_token_grants_operator_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "operator-token")
    cog = WebAdminCog(bot=_ScopedBot())
    request = _FakeRequest(headers={"X-Admin-Token": "operator-token"})

    assert cog._authorized(request) is True
    assert request["scope"] is None


def test_empty_operator_token_never_authorizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """전역 토큰 없이 운영하는 구성에서 빈 문자열로 뚫리면 안 된다."""
    monkeypatch.delenv("ADMIN_WEB_TOKEN", raising=False)
    cog = WebAdminCog(bot=_ScopedBot())

    assert cog._authorized(_FakeRequest(headers={"X-Admin-Token": ""})) is False
    assert cog._authorized(_FakeRequest(headers={"Authorization": "Bearer "})) is False


async def test_reissue_revokes_live_sessions_for_that_guild() -> None:
    cog = WebAdminCog(bot=_ScopedBot(_FakeGuild(111, "a"), _FakeGuild(222, "b")))
    mine = cog._issue_session(111)
    other = cog._issue_session(222)
    operator = cog._issue_session(None)

    dropped = await cog.revoke_sessions_for_guild(111)

    assert dropped == 1
    assert cog._session_valid(mine) is False
    assert cog._session_valid(other) is True
    assert cog._session_valid(operator) is True


def test_env_block_is_operator_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 는 남의 길드 ID(TEST_GUILD_ID)와 호스트 경로를 담는다.

    서버 주인에게 내보내면 자기 서버와 무관한 정보가 새어 나간다.
    """
    import inspect

    source = inspect.getsource(WebAdminCog._api_state)

    # env 는 반드시 운영자 범위 가드 안에서만 채워져야 한다.
    assert 'if self._scope(request) is None:' in source
    guarded = source.split("if self._scope(request) is None:", 1)[1]
    assert '"env"' in guarded
    assert '"test_guild_id"' in guarded
    # 가드 앞쪽에는 env 가 없어야 한다.
    assert '"env"' not in source.split("if self._scope(request) is None:", 1)[0]
