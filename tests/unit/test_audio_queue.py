"""
Phase 5 — Audio Queue
guild별 직렬 재생, 모킹된 VoiceClient 기반.
실제 음성 전송은 통합 테스트로 검증.
"""
from __future__ import annotations
import asyncio
import importlib.util
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HAS = importlib.util.find_spec("cogs.audio_queue") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 5 not yet implemented")


def _make_guild(guild_id: int = 1):
    g = MagicMock()
    g.id = guild_id
    g.voice_client = None
    g.get_channel = MagicMock(return_value=MagicMock())
    return g


async def test_enqueue_processes_in_order():
    """3개 enqueue → synthesize/play가 순서대로 호출."""
    from cogs.audio_queue import AudioQueue, AudioRequest

    play_order: list[str] = []

    q = AudioQueue()
    guild = _make_guild()

    async def fake_ensure(*_a, **_kw):
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.play = MagicMock()
        return vc

    async def fake_play_streaming(self, vc, text, voice, **_kwargs):
        play_order.append(f"synth:{text}")
        play_order.append(f"play:{text}")

    with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
         patch.object(AudioQueue, "_play_streaming", new=fake_play_streaming):
        for t in ("a", "b", "c"):
            await q.enqueue(guild, AudioRequest(text=t, voice="ko-KR-SunHiNeural",
                                               voice_channel_id=1))
        # worker가 처리할 시간을 줌
        await asyncio.sleep(0.5)

        synth_calls = [x for x in play_order if x.startswith("synth:")]
        assert synth_calls == ["synth:a", "synth:b", "synth:c"]


async def test_failed_request_does_not_block_next():
    """한 요청이 실패해도 worker가 살아 있어야 한다."""
    from cogs.audio_queue import AudioQueue, AudioRequest

    calls = []

    async def flaky_play(self, vc, text, voice, **_kwargs):
        calls.append(text)
        if text == "boom":
            raise RuntimeError("synthesize failed")

    q = AudioQueue()
    guild = _make_guild()

    async def fake_ensure(*_a, **_kw):
        vc = MagicMock(); vc.is_connected.return_value = True; return vc

    with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
         patch.object(AudioQueue, "_play_streaming", new=flaky_play):
        await q.enqueue(guild, AudioRequest("boom", "ko-KR-SunHiNeural", 1))
        await q.enqueue(guild, AudioRequest("ok", "ko-KR-SunHiNeural", 1))
        await asyncio.sleep(0.5)

    assert "boom" in calls and "ok" in calls


async def test_play_streaming_uses_pcm_cache():
    from cogs.audio_queue import AudioQueue

    q = AudioQueue()
    q._cache.put("cached", "voice", b"\x01\x02")
    frames: list[bytes] = []
    vc = MagicMock()

    def fake_play(source, after):
        def _drain():
            while True:
                frame = source.read()
                if not frame:
                    break
                frames.append(frame)
            after(None)

        threading.Thread(target=_drain, daemon=True).start()

    async def fail_stream(*_a, **_kw):
        raise AssertionError("cache hit should not call Azure")
        yield b""

    vc.play = MagicMock(side_effect=fake_play)
    with patch("cogs.audio_queue.stream_synthesize", fail_stream):
        await q._play_streaming(vc, "cached", "voice")

    assert any(frame[:4] == b"\x01\x02\x01\x02" for frame in frames)


async def test_play_streaming_uses_opus_cache():
    from cogs.audio_queue import AudioQueue

    q = AudioQueue()
    q._opus_cache.put("cached", "voice", b"\x00\x03abc")
    frames: list[bytes] = []
    vc = MagicMock()

    def fake_play(source, after):
        assert source.is_opus() is True
        frames.append(source.read())
        after(None)

    async def fail_stream(*_a, **_kw):
        raise AssertionError("opus cache hit should not call Azure")
        yield b""

    vc.play = MagicMock(side_effect=fake_play)
    with patch("cogs.audio_queue.stream_synthesize", fail_stream):
        await q._play_streaming(vc, "cached", "voice")

    assert frames == [b"abc"]


def test_cached_opus_source_reads_length_prefixed_frames():
    from cogs.audio_queue import CachedOpusSource

    source = CachedOpusSource(b"\x00\x03abc\x00\x02de")
    assert source.is_opus() is True
    assert source.read() == b"abc"
    assert source.read() == b"de"
    assert source.read() == b""


def test_mono_pcm_to_stereo_source(tmp_path: Path):
    from cogs.audio_queue import MonoPCMToStereo

    pcm = tmp_path / "sample.pcm"
    pcm.write_bytes(b"\x01\x02\x03\x04")

    source = MonoPCMToStereo(pcm)
    try:
        assert source.is_opus() is False
        frame = source.read()
        assert frame[:8] == b"\x01\x02\x01\x02\x03\x04\x03\x04"
        assert len(frame) == 3840
        assert source.read() == b""
    finally:
        source.cleanup()

    assert not pcm.exists()


def test_streaming_mono_pcm_to_stereo_source():
    from cogs.audio_queue import StreamingMonoPCMToStereo

    source = StreamingMonoPCMToStereo(read_timeout_sec=0.1, pre_roll_ms=0)
    try:
        source.feed_mono(b"\x01\x02\x03\x04")
        source.finish()

        frame = source.read()
        assert frame[:8] == b"\x01\x02\x01\x02\x03\x04\x03\x04"
        assert len(frame) == 3840
        assert source.read() == b""
    finally:
        source.cleanup()


def test_streaming_source_returns_silence_during_pre_roll():
    from cogs.audio_queue import STEREO_PCM_FRAME_BYTES, StreamingMonoPCMToStereo

    source = StreamingMonoPCMToStereo(read_timeout_sec=0.1, pre_roll_ms=40)
    try:
        assert source.read() == b"\x00" * STEREO_PCM_FRAME_BYTES
        assert source.read() == b"\x00" * STEREO_PCM_FRAME_BYTES
        source.feed_mono(b"\x01\x02")
        source.finish()

        frame = source.read()
        assert frame[:4] == b"\x01\x02\x01\x02"
        assert len(frame) == STEREO_PCM_FRAME_BYTES
    finally:
        source.cleanup()


def test_streaming_source_does_not_delay_ready_audio():
    from cogs.audio_queue import StreamingMonoPCMToStereo

    source = StreamingMonoPCMToStereo(read_timeout_sec=0.1, pre_roll_ms=120)
    try:
        source.feed_mono(b"\x01\x02")
        source.finish()

        frame = source.read()
        assert frame[:4] == b"\x01\x02\x01\x02"
    finally:
        source.cleanup()


def test_pcm_cache_lru_and_item_limit():
    from cogs.audio_queue import PCMCache

    cache = PCMCache(max_entries=2, max_bytes=10, max_item_bytes=6)
    cache.put("a", "v", b"1111")
    cache.put("b", "v", b"2222")
    assert cache.get("a", "v") == b"1111"

    cache.put("c", "v", b"3333")
    assert cache.get("b", "v") is None
    assert cache.get("a", "v") == b"1111"
    assert cache.get("c", "v") == b"3333"

    cache.put("too-big", "v", b"1234567")
    assert cache.get("too-big", "v") is None
