from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs import admin_cog
from cogs.admin_cog import AdminCog, _dashboard_embed, _one_time_login_url, _web_dashboard_url


def test_one_time_login_url_hides_guild_and_uses_fragment() -> None:
    url = _one_time_login_url("https://abc.trycloudflare.com/", "secret-token")

    assert url == "https://abc.trycloudflare.com/login#token=secret-token"
    assert "/g/" not in url
    assert _one_time_login_url(None, "secret-token") is None


def test_dashboard_embed_explains_single_use_scope() -> None:
    embed = _dashboard_embed("https://admin.example.com/login#token=x")

    assert embed.title == "관리자 대시보드"
    assert [field.name for field in embed.fields] == ["접속", "보안", "관리 항목"]
    assert "한 번" in embed.description
    assert "5분" in embed.fields[1].value
    assert "서버에만" in embed.fields[1].value
    assert "로그인 키" not in embed.description


def test_web_dashboard_url_prefers_public_url(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com/")

    assert _web_dashboard_url() == "https://admin.example.com"


def test_web_dashboard_url_uses_localhost_when_enabled(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_PUBLIC_URL", raising=False)
    monkeypatch.setenv("ADMIN_WEB_ENABLED", "1")
    monkeypatch.setenv("ADMIN_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("ADMIN_WEB_PORT", "9090")

    assert _web_dashboard_url() == "http://127.0.0.1:9090"


def test_web_dashboard_url_hides_unspecified_public_host(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_WEB_PUBLIC_URL", raising=False)
    monkeypatch.setenv("ADMIN_WEB_ENABLED", "1")
    monkeypatch.setenv("ADMIN_WEB_HOST", "0.0.0.0")

    assert _web_dashboard_url() is None


async def test_panel_issues_scoped_token_and_ephemeral_fragment_link(monkeypatch) -> None:
    bot = MagicMock()
    cog = AdminCog(bot)
    cog.login_grants.issue = AsyncMock(return_value="one-time-token")
    monkeypatch.setattr(
        admin_cog, "resolve_url", AsyncMock(return_value="https://abc.trycloudflare.com")
    )
    interaction = MagicMock()
    interaction.guild_id = 111
    interaction.user = SimpleNamespace(id=222)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await AdminCog.panel.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    cog.login_grants.issue.assert_awaited_once_with(111, 222)
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["view"].children[0].url.endswith("/login#token=one-time-token")
    assert "111" not in kwargs["view"].children[0].url
