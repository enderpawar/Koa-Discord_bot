"""
Phase 4 — TTS Engine
Azure Speech REST 호출은 mocked. 실제 네트워크 합성은 RUN_LIVE=1 + @pytest.mark.live.
"""
from __future__ import annotations
import asyncio
import importlib.util
from pathlib import Path
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
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


# ---------- 감정 스타일 ----------


@pytest.fixture(autouse=True)
def _reset_voice_styles():
    """모듈 전역 카탈로그가 테스트 사이로 새지 않게 한다."""
    import cogs.tts_engine as engine

    engine._voice_styles = None
    yield
    engine._voice_styles = None


class _FakeCatalogResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeCatalogSession:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.captured: dict = {}

    def get(self, url, headers=None):
        self.captured["url"] = url
        self.captured["headers"] = headers
        return _FakeCatalogResp(self._payload, self._status)


@contextmanager
def _patch_catalog(session):
    async def _get():
        return session

    with patch("cogs.tts_engine._get_session", _get):
        yield


def test_ssml_without_style_is_unchanged() -> None:
    """카탈로그를 못 받은 경우 기존과 완전히 같은 요청이 나가야 한다."""
    from cogs.tts_engine import _build_ssml

    assert _build_ssml("안녕", "ko-KR-SunHiNeural") == (
        '<speak version="1.0" xml:lang="ko-KR">'
        '<voice name="ko-KR-SunHiNeural">안녕</voice>'
        "</speak>"
    )


def test_ssml_with_style_declares_the_mstts_namespace() -> None:
    from cogs.tts_engine import _build_ssml

    ssml = _build_ssml("안녕", "ko-KR-SunHiNeural", "cheerful")

    assert 'xmlns:mstts="https://www.w3.org/2001/mstts"' in ssml
    assert '<mstts:express-as style="cheerful">안녕</mstts:express-as>' in ssml


def test_style_is_only_used_when_the_voice_actually_supports_it() -> None:
    import cogs.tts_engine as engine

    # 카탈로그를 아직 못 읽었으면 감정 기능이 통째로 꺼진 것처럼 동작한다.
    assert engine.style_for("ko-KR-SunHiNeural", "cheerful") is None

    engine._voice_styles = {
        "ko-KR-SunHiNeural": frozenset({"cheerful", "sad"}),
        "ko-KR-PlainNeural": frozenset(),
    }
    assert engine.style_for("ko-KR-SunHiNeural", "cheerful") == "cheerful"
    assert engine.style_for("ko-KR-PlainNeural", "cheerful") is None
    assert engine.style_for("ko-KR-UnknownNeural", "cheerful") is None
    assert engine.style_for("ko-KR-SunHiNeural", None) is None


def test_style_falls_back_down_the_chain() -> None:
    """`excited` 를 모르는 보이스는 `cheerful` 로 내려간다."""
    import cogs.tts_engine as engine

    engine._voice_styles = {"v": frozenset({"cheerful"})}
    assert engine.style_for("v", "excited") == "cheerful"

    engine._voice_styles = {"v": frozenset({"angry"})}
    assert engine.style_for("v", "excited") is None


async def test_load_voice_styles_reads_the_azure_catalog() -> None:
    from cogs.tts_engine import load_voice_styles

    session = _FakeCatalogSession(
        [
            {"ShortName": "ko-KR-SunHiNeural", "StyleList": ["cheerful", "sad"]},
            {"ShortName": "ko-KR-PlainNeural"},
            {"no": "shortname"},
        ]
    )
    with _patch_catalog(session):
        styles = await load_voice_styles()

    assert styles["ko-KR-SunHiNeural"] == frozenset({"cheerful", "sad"})
    assert styles["ko-KR-PlainNeural"] == frozenset()
    assert "voices/list" in session.captured["url"]


async def test_catalog_failure_degrades_to_no_styles_not_a_crash() -> None:
    """감정은 부가 기능이다. 카탈로그를 못 받아도 읽기는 멀쩡해야 한다."""
    from cogs.tts_engine import load_voice_styles, style_for

    with _patch_catalog(_FakeCatalogSession(None, status=500)):
        styles = await load_voice_styles()

    assert styles == {}
    assert style_for("ko-KR-SunHiNeural", "cheerful") is None


async def test_synthesize_sends_the_style_when_the_voice_supports_it() -> None:
    import cogs.tts_engine as engine

    engine._voice_styles = {"ko-KR-SunHiNeural": frozenset({"cheerful"})}
    fake = _FakeSession()
    with _patch_session(fake):
        path = await engine.synthesize(
            "ㅋㅋㅋ 대박", voice="ko-KR-SunHiNeural", tone="cheerful"
        )

    body = fake.captured["data"].decode("utf-8")
    assert 'style="cheerful"' in body
    path.unlink(missing_ok=True)


async def test_synthesize_omits_the_style_when_the_voice_lacks_it() -> None:
    import cogs.tts_engine as engine

    engine._voice_styles = {"ko-KR-SunHiNeural": frozenset()}
    fake = _FakeSession()
    with _patch_session(fake):
        path = await engine.synthesize(
            "ㅋㅋㅋ 대박", voice="ko-KR-SunHiNeural", tone="cheerful"
        )

    body = fake.captured["data"].decode("utf-8")
    assert "express-as" not in body
    path.unlink(missing_ok=True)


@pytest.mark.live
async def test_synthesize_live_smoke():
    """실제 Azure Speech 도달 — RUN_LIVE=1 일 때만."""
    from cogs.tts_engine import synthesize, close_session
    p = None
    try:
        p = await synthesize("테스트")
        assert p.exists() and p.stat().st_size > 1024
    finally:
        if p is not None:
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


async def test_keepalive_failure_does_not_mark_slot_warm(monkeypatch):
    import cogs.tts_engine as engine

    slot = engine._WSSlot(index=0)
    monkeypatch.setattr(engine, "_BACKEND", "ws")
    monkeypatch.setattr(engine, "_last_keepalive_at", 123.0)

    async def fail_request(*_args, **_kwargs):
        raise RuntimeError("closed")

    monkeypatch.setattr(engine, "_request_ws_once", fail_request)

    with pytest.raises(RuntimeError):
        await engine._warm_up_keepalive(slot=slot)

    assert engine._last_keepalive_at == 123.0


async def test_keepalive_cycle_visits_every_cold_slot_and_survives_one_failure(
    monkeypatch,
):
    import cogs.tts_engine as engine

    slots = [engine._WSSlot(index=0), engine._WSSlot(index=1)]
    monkeypatch.setattr(engine, "_BACKEND", "ws")
    monkeypatch.setattr(engine, "_pool", slots)
    monkeypatch.setattr(engine.time, "monotonic", lambda: 100.0)
    visited: list[int] = []

    async def fake_warm(_voice=engine.DEFAULT_VOICE, *, slot=None):
        visited.append(slot.index)
        if slot.index == 0:
            raise RuntimeError("first slot failed")
        slot.last_activity_at = 100.0
        return True

    monkeypatch.setattr(engine, "_warm_up_keepalive", fake_warm)

    await engine._run_keepalive_cycle()

    assert visited == [0, 1]


async def test_cancelled_ws_stream_discards_slot(monkeypatch):
    import cogs.tts_engine as engine

    class BlockingWS:
        def __init__(self):
            self.closed = False

        async def send_str(self, _data):
            return None

        async def receive(self, *, timeout):
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    ws = BlockingWS()
    slot = engine._WSSlot(index=0, ws=ws)
    monkeypatch.setattr(engine, "_pool", [slot])

    stream = engine._stream_ws_once("테스트", engine.DEFAULT_VOICE)
    pending = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert ws.closed is True
    assert slot.ws is None


async def test_early_closed_ws_stream_discards_unread_turn(monkeypatch):
    import cogs.tts_engine as engine

    class PartialWS:
        def __init__(self):
            self.closed = False
            self.receive_count = 0

        async def send_str(self, _data):
            return None

        async def receive(self, *, timeout):
            self.receive_count += 1
            if self.receive_count == 1:
                header = b"Path:audio\r\nX-RequestId:test\r\n"
                return SimpleNamespace(
                    type=engine.aiohttp.WSMsgType.BINARY,
                    data=len(header).to_bytes(2, "big") + header + b"pcm",
                )
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    ws = PartialWS()
    slot = engine._WSSlot(index=0, ws=ws)
    monkeypatch.setattr(engine, "_pool", [slot])

    stream = engine._stream_ws_once("긴 문구", engine.DEFAULT_VOICE)
    assert await stream.__anext__() == b"pcm"
    await stream.aclose()

    assert ws.closed is True
    assert slot.ws is None
