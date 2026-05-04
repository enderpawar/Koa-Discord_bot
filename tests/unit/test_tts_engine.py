"""
Phase 4 — TTS Engine
Azure Speech REST 호출은 mocked. 실제 네트워크 합성은 RUN_LIVE=1 + @pytest.mark.live.
"""
from __future__ import annotations
import asyncio
import importlib.util
from pathlib import Path
from contextlib import ExitStack, contextmanager
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


@contextmanager
def _patch_session(fake: _FakeSession):
    async def _get():
        return fake

    with ExitStack() as stack:
        stack.enter_context(patch("cogs.tts_engine._BACKEND", "rest"))
        stack.enter_context(patch("cogs.tts_engine._get_session", _get))
        yield


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
    assert (
        fake.captured["headers"]["X-Microsoft-OutputFormat"]
        == "raw-48khz-16bit-mono-pcm"
    )
    assert "koreacentral" in fake.captured["url"]
    path.unlink(missing_ok=True)


async def test_warm_up_discards_audio():
    from cogs.tts_engine import warm_up

    fake = _FakeSession()
    with _patch_session(fake):
        await warm_up()


async def test_synthesize_rejects_empty():
    from cogs.tts_engine import synthesize
    with pytest.raises((ValueError, AssertionError)):
        await synthesize("")


async def test_stream_synthesize_yields_chunks():
    from cogs.tts_engine import stream_synthesize

    fake = _FakeSession(body=b"\x00" * 4096)
    with _patch_session(fake):
        chunks = [chunk async for chunk in stream_synthesize("안녕하세요")]

    assert chunks == [b"\x00" * 4096]
    assert fake.captured["headers"]["X-Microsoft-OutputFormat"] == "raw-48khz-16bit-mono-pcm"


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


async def test_ws_pool_initializes_to_configured_size(monkeypatch):
    """풀 lazy 초기화: _get_pool 호출 시 _WS_POOL_SIZE 만큼의 슬롯 생성."""
    import cogs.tts_engine as engine

    monkeypatch.setattr(engine, "_WS_POOL_SIZE", 3)
    monkeypatch.setattr(engine, "_pool", [])

    pool = await engine._get_pool()
    assert len(pool) == 3
    for slot in pool:
        assert slot.ws is None
        assert slot.speech_config_sent is False


async def test_ws_pool_acquire_picks_free_slot(monkeypatch):
    """비어있는 슬롯이 있으면 첫 free 슬롯을 잡는다."""
    import cogs.tts_engine as engine

    monkeypatch.setattr(engine, "_WS_POOL_SIZE", 2)
    monkeypatch.setattr(engine, "_pool", [])

    pool = await engine._get_pool()
    # 슬롯 0 을 미리 점유
    await pool[0].lock.acquire()
    try:
        async with engine._acquire_slot() as slot:
            assert slot is pool[1]
    finally:
        pool[0].lock.release()


async def test_ws_pool_acquire_waits_when_all_busy(monkeypatch):
    """모든 슬롯 busy 면 첫 슬롯의 락을 기다림 (결과적으로 직렬화)."""
    import cogs.tts_engine as engine

    monkeypatch.setattr(engine, "_WS_POOL_SIZE", 1)
    monkeypatch.setattr(engine, "_pool", [])

    pool = await engine._get_pool()
    await pool[0].lock.acquire()

    async def acquire_and_check():
        async with engine._acquire_slot() as slot:
            return slot is pool[0]

    task = asyncio.create_task(acquire_and_check())
    # 잠시 대기 — 아직 lock 보유 중이므로 task 진행 안 됨
    await asyncio.sleep(0.05)
    assert not task.done()

    pool[0].lock.release()
    assert await task is True


async def test_ws_pool_close_session_clears_all(monkeypatch):
    """close_session 이 모든 슬롯의 ws 를 닫고 풀을 비운다."""
    import cogs.tts_engine as engine

    monkeypatch.setattr(engine, "_WS_POOL_SIZE", 2)
    monkeypatch.setattr(engine, "_pool", [])

    pool = await engine._get_pool()
    # 가짜 ws 연결 시뮬레이션
    closed_flags = [False, False]

    class _FakeWS:
        def __init__(self, idx):
            self.idx = idx
            self.closed = False

        async def close(self):
            closed_flags[self.idx] = True
            self.closed = True

    pool[0].ws = _FakeWS(0)
    pool[1].ws = _FakeWS(1)

    await engine.close_session()
    assert closed_flags == [True, True]
    assert engine._pool == []
