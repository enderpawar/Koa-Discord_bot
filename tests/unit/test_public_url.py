"""대시보드 공개 주소 해석 — 고정 주소 우선, 임시 터널 폴백."""
from __future__ import annotations

import pytest

from cogs import public_url
from cogs.public_url import cached_url, reset_cache_for_tests, resolve_url


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    reset_cache_for_tests()
    monkeypatch.delenv("ADMIN_WEB_PUBLIC_URL", raising=False)
    monkeypatch.delenv("CLOUDFLARED_METRICS_URL", raising=False)
    yield
    reset_cache_for_tests()


async def test_static_url_wins_and_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_WEB_PUBLIC_URL", "https://admin.example.com/")
    monkeypatch.setenv("CLOUDFLARED_METRICS_URL", "http://cloudflared:2000")

    # 고정 주소가 있으면 터널에 묻지 않는다 — 물으러 가면 이 테스트가 멈춘다.
    assert await resolve_url() == "https://admin.example.com"
    assert cached_url() == "https://admin.example.com"


async def test_no_config_means_no_url() -> None:
    assert await resolve_url() is None
    assert cached_url() is None


async def test_quick_tunnel_hostname_becomes_https_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDFLARED_METRICS_URL", "http://cloudflared:2000")
    _fake_metrics(monkeypatch, {"hostname": "abc-def.trycloudflare.com"})

    assert await resolve_url() == "https://abc-def.trycloudflare.com"
    # 동기 경로도 같은 값을 본다.
    assert cached_url() == "https://abc-def.trycloudflare.com"


async def test_tunnel_outage_falls_back_to_last_known_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """터널이 잠깐 죽었다고 키 DM 이 주소 없이 나가면 안 된다."""
    monkeypatch.setenv("CLOUDFLARED_METRICS_URL", "http://cloudflared:2000")
    _fake_metrics(monkeypatch, {"hostname": "abc-def.trycloudflare.com"})
    assert await resolve_url() == "https://abc-def.trycloudflare.com"

    public_url._cached_at = 0.0  # 캐시 만료시켜 재조회를 강제
    _fake_metrics(monkeypatch, None, fail=True)

    assert await resolve_url() == "https://abc-def.trycloudflare.com"


def _fake_metrics(monkeypatch: pytest.MonkeyPatch, payload, *, fail: bool = False) -> None:
    class _Response:
        def raise_for_status(self):
            return None

        async def json(self, content_type=None):
            return payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def get(self, url):
            if fail:
                raise public_url.aiohttp.ClientError("tunnel down")
            return _Response()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(public_url.aiohttp, "ClientSession", lambda **kw: _Session())
