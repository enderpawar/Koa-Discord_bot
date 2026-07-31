from __future__ import annotations

import pytest

from cogs.web_admin_cog import (
    _allowed_guild_ids,
    _bool_value,
    _channel_payload,
    _clean_time,
    _id,
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
