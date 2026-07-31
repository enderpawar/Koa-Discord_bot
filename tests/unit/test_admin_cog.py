from __future__ import annotations

from cogs.admin_cog import (
    _configured,
    _dashboard_embed,
    _web_dashboard_url,
)


def test_configured_masks_secrets() -> None:
    assert _configured("token", secret=True) == "설정됨"
    assert _configured("koreacentral") == "`koreacentral`"
    assert _configured(None) == "미설정"


def test_dashboard_embed_points_to_web_ui(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "token")
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com")
    embed = _dashboard_embed()

    assert embed.title == "관리자 대시보드"
    assert [field.name for field in embed.fields] == ["접속", "관리 항목"]
    assert "웹 대시보드 열기" in embed.fields[0].value
    assert "TTS 입력 채널" in embed.fields[1].value


def test_web_dashboard_url_prefers_public_url(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "token")
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com/")

    assert _web_dashboard_url() == "https://admin.example.com"


def test_web_dashboard_url_uses_localhost_when_available(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_PUBLIC_URL", raising=False)
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "token")
    monkeypatch.setenv("ADMIN_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("ADMIN_WEB_PORT", "9090")

    assert _web_dashboard_url() == "http://127.0.0.1:9090"


def test_web_dashboard_url_hides_unspecified_public_host(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_PUBLIC_URL", raising=False)
    monkeypatch.setenv("ADMIN_WEB_TOKEN", "token")
    monkeypatch.setenv("ADMIN_WEB_HOST", "0.0.0.0")

    assert _web_dashboard_url() is None
