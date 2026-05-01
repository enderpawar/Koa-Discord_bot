from __future__ import annotations

import pytest

from cogs.web_admin_cog import _bool_value, _clean_time, _web_host, _web_port


def test_clean_time_accepts_hh_mm() -> None:
    assert _clean_time("23:59") == "23:59"
    assert _clean_time(None) == "23:59"


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
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)

    assert _web_host() == "127.0.0.1"


def test_web_host_defaults_to_public_bind_on_railway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_WEB_HOST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    assert _web_host() == "0.0.0.0"
