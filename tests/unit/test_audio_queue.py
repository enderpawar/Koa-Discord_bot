"""
Phase 5 — Audio Queue
guild별 직렬 재생, 모킹된 VoiceClient 기반.
실제 음성 전송은 통합 테스트로 검증.
"""
from __future__ import annotations
import asyncio
import importlib.util
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

    async def fake_play_streaming(self, vc, text, voice):
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

    async def flaky_play(self, vc, text, voice):
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

    source = StreamingMonoPCMToStereo(read_timeout_sec=0.1)
    try:
        source.feed_mono(b"\x01\x02\x03\x04")
        source.finish()

        frame = source.read()
        assert frame[:8] == b"\x01\x02\x01\x02\x03\x04\x03\x04"
        assert len(frame) == 3840
        assert source.read() == b""
    finally:
        source.cleanup()
