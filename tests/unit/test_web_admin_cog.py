from __future__ import annotations

import pytest

from cogs.web_admin_cog import _bool_value, _clean_time, _web_port


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
