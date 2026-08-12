from __future__ import annotations

import pytest

from cogs import web_admin_cog
from cogs.web_admin_cog import (
    _DASHBOARD_TEMPLATE,
    _LOGIN_TEMPLATE,
    _allowed_guild_ids,
    _bool_value,
    _channel_payload,
    _clean_time,
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
