"""
Phase 5 — Audio Queue
guild별 직렬 재생, 모킹된 VoiceClient 기반.
실제 음성 전송은 통합 테스트로 검증.
"""
from __future__ import annotations
import asyncio
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def fake_synth(text, voice):
        play_order.append(f"synth:{text}")
        from pathlib import Path
        import tempfile
        f = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".mp3", delete=False)
        f.close()
        return type(Path)(f.name) if False else __import__("pathlib").Path(f.name)

    with patch("cogs.audio_queue.synthesize", side_effect=fake_synth):
        q = AudioQueue()
        guild = _make_guild()

        async def fake_ensure(*_a, **_kw):
            vc = MagicMock()
            vc.is_connected.return_value = True
            vc.play = MagicMock()
            return vc

        async def fake_play_blocking(self, vc, mp3):
            play_order.append(f"play:{mp3.name}")

        with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
             patch.object(AudioQueue, "_play_blocking", new=fake_play_blocking):
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

    async def flaky_synth(text, voice):
        calls.append(text)
        if text == "boom":
            raise RuntimeError("synthesize failed")
        from pathlib import Path
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False); f.close()
        return Path(f.name)

    with patch("cogs.audio_queue.synthesize", side_effect=flaky_synth):
        q = AudioQueue()
        guild = _make_guild()

        async def fake_ensure(*_a, **_kw):
            vc = MagicMock(); vc.is_connected.return_value = True; return vc

        async def fake_play(self, vc, mp3): pass

        with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
             patch.object(AudioQueue, "_play_blocking", new=fake_play):
            await q.enqueue(guild, AudioRequest("boom", "ko-KR-SunHiNeural", 1))
            await q.enqueue(guild, AudioRequest("ok", "ko-KR-SunHiNeural", 1))
            await asyncio.sleep(0.5)

        assert "boom" in calls and "ok" in calls
