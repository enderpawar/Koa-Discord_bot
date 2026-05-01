"""
Phase 4 — TTS Engine
Azure Speech REST 호출은 mocked. 실제 네트워크 합성은 RUN_LIVE=1 + @pytest.mark.live.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

_HAS = importlib.util.find_spec("cogs.tts_engine") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 4 not yet implemented")


class _FakeResp:
    def __init__(self, body: bytes = b"\x00\x01\x02\x03", status: int = 200):
        self._body = body
        self.status = status
        self.content = self

    def raise_for_status(self) -> None:
        if self.status >= 400:
            import aiohttp
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status
            )

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def iter_any(self):
        yield self._body


class _FakeSession:
    def __init__(self, body: bytes = b"\x00" * 4096):
        self._body = body
        self.captured: dict = {}
        self.closed = False

    def post(self, url, data=None, headers=None):
        self.captured["url"] = url
        self.captured["data"] = data
        self.captured["headers"] = headers
        return _FakeResp(self._body)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _azure_env(monkeypatch, request):
    # live 테스트는 실제 .env 값을 그대로 사용
    if "live" in request.keywords:
        return
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")


def _patch_session(fake: _FakeSession):
    async def _get():
        return fake
    return patch("cogs.tts_engine._get_session", _get)


async def test_synthesize_returns_path():
    from cogs.tts_engine import synthesize

    fake = _FakeSession(body=b"\x00" * 4096)
    with _patch_session(fake):
        result = await synthesize("안녕하세요")
        assert isinstance(result, Path)
        assert result.exists()
        assert result.stat().st_size > 0
    result.unlink(missing_ok=True)


async def test_synthesize_uses_korean_voice():
    from cogs.tts_engine import synthesize

    fake = _FakeSession()
    with _patch_session(fake):
        path = await synthesize("ㅎㅇ", voice="ko-KR-InJoonNeural")

    body = fake.captured["data"]
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    assert "ko-KR-InJoonNeural" in body
    assert fake.captured["headers"]["Ocp-Apim-Subscription-Key"] == "test-key"
    assert "koreacentral" in fake.captured["url"]
    path.unlink(missing_ok=True)


async def test_synthesize_rejects_empty():
    from cogs.tts_engine import synthesize
    with pytest.raises((ValueError, AssertionError)):
        await synthesize("")


@pytest.mark.live
async def test_synthesize_live_smoke():
    """실제 Azure Speech 도달 — RUN_LIVE=1 일 때만."""
    from cogs.tts_engine import synthesize, close_session
    p = await synthesize("테스트")
    try:
        assert p.exists() and p.stat().st_size > 1024
    finally:
        p.unlink(missing_ok=True)
        await close_session()
