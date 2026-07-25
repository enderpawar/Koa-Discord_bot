"""
Phase 5 — Audio Queue
guild별 직렬 재생, 모킹된 VoiceClient 기반.
실제 음성 전송은 통합 테스트로 검증.
"""
from __future__ import annotations
import asyncio
import importlib.util
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

_HAS = importlib.util.find_spec("cogs.audio_queue") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 5 not yet implemented")


@pytest.fixture(autouse=True)
def _disable_voice_keepalive(monkeypatch):
    monkeypatch.setattr(
        "cogs.audio_queue.VOICE_SILENCE_KEEPALIVE_SEC",
        0,
    )
    monkeypatch.setattr(
        "cogs.audio_queue.VOICE_MEDIA_STABILIZE_SEC",
        0,
    )


def _make_guild(guild_id: int = 1):
    g = MagicMock()
    g.id = guild_id
    g.voice_client = None
    g.get_channel = MagicMock(return_value=MagicMock())
    return g


async def _noop_prefetch(self, req):
    return


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
         patch.object(AudioQueue, "_play_streaming", new=fake_play_streaming), \
         patch.object(AudioQueue, "_prefetch", new=_noop_prefetch):
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
         patch.object(AudioQueue, "_play_streaming", new=flaky_play), \
         patch.object(AudioQueue, "_prefetch", new=_noop_prefetch):
        await q.enqueue(guild, AudioRequest("boom", "ko-KR-SunHiNeural", 1))
        await q.enqueue(guild, AudioRequest("ok", "ko-KR-SunHiNeural", 1))
        await asyncio.sleep(0.5)

    assert "boom" in calls and "ok" in calls


async def test_ensure_voice_cleans_stale_client_before_reconnect(monkeypatch):
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr("cogs.audio_queue.VOICE_RECONNECT_GRACE_SEC", 0)
    q = AudioQueue()
    guild = _make_guild()
    target = guild.get_channel.return_value
    connected = MagicMock()
    target.connect = AsyncMock(return_value=connected)

    stale = MagicMock()
    stale.is_connected.return_value = False
    stale.disconnect = AsyncMock()
    guild.voice_client = stale

    result = await q.ensure_voice(guild, target.id)

    assert result is connected
    stale.disconnect.assert_awaited_once_with(force=True)
    target.connect.assert_awaited_once_with(
        timeout=10.0,
        reconnect=True,
        self_deaf=True,
    )


async def test_ensure_voice_waits_for_discord_reconnect(monkeypatch):
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr("cogs.audio_queue.VOICE_RECONNECT_GRACE_SEC", 0.2)
    q = AudioQueue()
    guild = _make_guild()
    target = guild.get_channel.return_value

    connected = False
    vc = MagicMock()
    vc.channel = target
    vc.is_connected.side_effect = lambda: connected

    async def finish_reconnect():
        nonlocal connected
        await asyncio.sleep(0.02)
        connected = True

    vc.disconnect = AsyncMock()
    guild.voice_client = vc
    reconnect = asyncio.create_task(finish_reconnect())

    result = await q.ensure_voice(guild, target.id)
    await reconnect

    assert result is vc
    vc.disconnect.assert_not_awaited()
    target.connect.assert_not_called()


async def test_ensure_voice_retries_initial_connect_timeout(monkeypatch):
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr("cogs.audio_queue.VOICE_CONNECT_RETRY_DELAY_SEC", 0)
    q = AudioQueue()
    guild = _make_guild()
    target = guild.get_channel.return_value
    connected = MagicMock()
    target.connect = AsyncMock(
        side_effect=[asyncio.TimeoutError(), connected],
    )

    result = await q.ensure_voice(guild, target.id)

    assert result is connected
    assert target.connect.await_count == 2


async def test_ensure_voice_retries_websocket_transport_reset(monkeypatch):
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr("cogs.audio_queue.VOICE_CONNECT_RETRY_DELAY_SEC", 0)
    q = AudioQueue()
    guild = _make_guild()
    target = guild.get_channel.return_value
    connected = MagicMock()
    target.connect = AsyncMock(
        side_effect=[
            aiohttp.ClientConnectionResetError(
                "Cannot write to closing transport"
            ),
            connected,
        ],
    )

    result = await q.ensure_voice(guild, target.id)

    assert result is connected
    assert target.connect.await_count == 2


async def test_disconnect_voice_cleans_disconnected_client_state():
    from cogs.audio_queue import AudioQueue

    q = AudioQueue()
    guild = _make_guild()
    vc = MagicMock()
    vc.is_connected.return_value = False
    vc.disconnect = AsyncMock()
    guild.voice_client = vc
    q._voice_stable_clients[guild.id] = vc

    await q.disconnect_voice(guild)

    vc.disconnect.assert_awaited_once_with(force=True)
    assert guild.id not in q._voice_stable_clients


async def test_worker_stays_alive_when_idle_disconnect_is_disabled(monkeypatch):
    from cogs.audio_queue import AudioQueue, AudioRequest

    monkeypatch.setattr("cogs.audio_queue.IDLE_TIMEOUT_SEC", 0)
    q = AudioQueue()
    guild = _make_guild()
    played = asyncio.Event()

    async def fake_ensure(*_a, **_kw):
        vc = MagicMock()
        vc.is_connected.return_value = True
        return vc

    async def fake_play(self, vc, text, voice, **_kwargs):
        played.set()

    with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
         patch.object(AudioQueue, "_play_streaming", new=fake_play), \
         patch.object(AudioQueue, "_prefetch", new=_noop_prefetch):
        await q.enqueue(guild, AudioRequest("hello", "voice", 1))
        await asyncio.wait_for(played.wait(), timeout=1)
        await asyncio.sleep(0)

        assert q._workers[guild.id].done() is False
        await q.shutdown()


async def test_voice_silence_keepalive_sends_when_idle(monkeypatch):
    from discord import opus
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr(
        "cogs.audio_queue.VOICE_SILENCE_KEEPALIVE_SEC",
        0.01,
    )
    q = AudioQueue()
    guild = _make_guild()
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    guild.voice_client = vc

    q._start_voice_keepalive(guild, vc)
    await asyncio.sleep(0.035)

    vc.send_audio_packet.assert_called_with(opus.OPUS_SILENCE, encode=False)
    await q.shutdown()


async def test_voice_media_ready_does_not_block_on_dave(monkeypatch):
    from cogs.audio_queue import AudioQueue

    monkeypatch.setattr("cogs.audio_queue.VOICE_MEDIA_STABILIZE_SEC", 0.02)
    monkeypatch.setattr("cogs.audio_queue.VOICE_MEDIA_READY_TIMEOUT_SEC", 0.4)
    q = AudioQueue()
    guild = _make_guild()
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc._connection.dave_protocol_version = 1
    vc._connection.can_encrypt = False
    guild.voice_client = vc

    result = await q._wait_for_voice_media_ready(guild, vc)

    assert result is True
    assert vc._connection.can_encrypt is False
    assert q._voice_stable_clients[guild.id] is vc


async def test_wait_for_playback_stops_on_voice_disconnect():
    from cogs.audio_queue import AudioQueue, VoicePlaybackDisconnected

    done = asyncio.Event()
    vc = MagicMock()
    vc.is_connected.return_value = False
    vc.stop.side_effect = done.set

    with pytest.raises(VoicePlaybackDisconnected):
        await AudioQueue._wait_for_playback(vc, done)

    vc.stop.assert_called_once()


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


def test_streaming_underflow_returns_silence_without_blocking():
    from cogs.audio_queue import STEREO_PCM_FRAME_BYTES, StreamingMonoPCMToStereo

    source = StreamingMonoPCMToStereo(read_timeout_sec=1.0, pre_roll_ms=0)
    try:
        started = time.perf_counter()
        frame = source.read()
        elapsed = time.perf_counter() - started

        assert frame == b"\x00" * STEREO_PCM_FRAME_BYTES
        assert elapsed < 0.1

        source.feed_mono(b"\x01\x02")
        source.finish()
        assert source.read()[:4] == b"\x01\x02\x01\x02"
    finally:
        source.cleanup()


def test_streaming_feed_defers_stereo_conversion_to_audio_read():
    import cogs.audio_queue as audio_queue

    source = audio_queue.StreamingMonoPCMToStereo(read_timeout_sec=1.0, pre_roll_ms=0)
    mono = b"\x01\x02" * (audio_queue.MONO_PCM_FRAME_BYTES * 10 // 2)
    with patch(
        "cogs.audio_queue._mono_frame_to_stereo",
        wraps=audio_queue._mono_frame_to_stereo,
    ) as convert:
        try:
            source.feed_mono(mono)
            assert convert.call_count == 0
            assert source._chunks.qsize() == 1

            source.read()
            assert convert.call_count == 1
        finally:
            source.cleanup()


def test_streaming_long_chunk_preserves_all_frames():
    from cogs.audio_queue import MONO_PCM_FRAME_BYTES, StreamingMonoPCMToStereo

    first = b"\x01\x02" * (MONO_PCM_FRAME_BYTES // 2)
    second = b"\x03\x04" * (MONO_PCM_FRAME_BYTES // 2)
    source = StreamingMonoPCMToStereo(read_timeout_sec=1.0, pre_roll_ms=0)
    try:
        source.feed_mono(first + second)
        source.finish()

        assert source.read()[:4] == b"\x01\x02\x01\x02"
        assert source.read()[:4] == b"\x03\x04\x03\x04"
        assert source.read() == b""
    finally:
        source.cleanup()


def test_streaming_underflow_timeout_eventually_ends(monkeypatch):
    import cogs.audio_queue as audio_queue

    now = [100.0]
    monkeypatch.setattr(audio_queue.time, "monotonic", lambda: now[0])
    source = audio_queue.StreamingMonoPCMToStereo(read_timeout_sec=0.5, pre_roll_ms=0)
    try:
        assert source.read() == b"\x00" * audio_queue.STEREO_PCM_FRAME_BYTES
        now[0] += 0.6
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


def test_to_stereo_matches_reference():
    """선택된 _to_stereo 콜러블이 reference Python 루프 결과와 byte-equal 한지 검증."""
    from cogs.audio_queue import _to_stereo, _to_stereo_purepy

    sample = bytes(range(0, 64))  # 32 16-bit 샘플
    assert _to_stereo(sample) == _to_stereo_purepy(sample)
    assert len(_to_stereo(sample)) == len(sample) * 2


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


async def test_prefetch_warms_cache_for_next_item():
    """다음 항목 prefetch 가 stream_synthesize 결과를 _cache 에 채운다."""
    from cogs.audio_queue import AudioQueue, AudioRequest

    q = AudioQueue()
    guild = _make_guild()

    async def fake_ensure(*_a, **_kw):
        vc = MagicMock(); vc.is_connected.return_value = True; return vc

    play_block = asyncio.Event()
    plays: list[str] = []

    async def slow_play(self, vc, text, voice, **_kwargs):
        plays.append(text)
        # 첫 항목 재생 동안 prefetch 가 다음 항목을 캐시에 채우도록 일시 대기
        if text == "first":
            await play_block.wait()

    chunks_for: dict[str, bytes] = {"second": b"\x10\x20\x30\x40"}

    async def fake_stream(text, voice):
        data = chunks_for.get(text)
        if data is None:
            raise RuntimeError(f"unexpected stream_synthesize for {text!r}")
        yield data

    with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
         patch.object(AudioQueue, "_play_streaming", new=slow_play), \
         patch("cogs.audio_queue.stream_synthesize", fake_stream):
        await q.enqueue(guild, AudioRequest("first", "v", 1))
        await q.enqueue(guild, AudioRequest("second", "v", 1))
        # prefetch 가 끝날 시간을 줌
        for _ in range(20):
            await asyncio.sleep(0.05)
            if q._cache.get("second", "v") is not None:
                break
        assert q._cache.get("second", "v") == b"\x10\x20\x30\x40"
        # 워커가 멈추지 않게 풀어준다
        play_block.set()
        await asyncio.sleep(0.2)


async def test_prefetch_skips_when_cache_already_warm():
    """이미 캐시된 항목은 prefetch 가 stream_synthesize 를 호출하지 않는다."""
    from cogs.audio_queue import AudioQueue, AudioRequest

    q = AudioQueue()
    q._cache.put("ready", "v", b"\x01\x02\x03\x04")

    called = False

    async def fail_stream(*_a, **_kw):
        nonlocal called
        called = True
        raise AssertionError("stream_synthesize should not be called")
        yield b""

    with patch("cogs.audio_queue.stream_synthesize", fail_stream):
        await q._prefetch(AudioRequest("ready", "v", 1))

    assert called is False


async def test_enqueue_drops_oldest_on_overflow():
    """큐가 MAX_QUEUE_SIZE 를 넘으면 가장 오래된 항목을 드롭한다."""
    from cogs.audio_queue import AudioQueue, AudioRequest, MAX_QUEUE_SIZE

    q = AudioQueue()
    guild = _make_guild()

    async def fake_ensure(*_a, **_kw):
        vc = MagicMock(); vc.is_connected.return_value = True; return vc

    block = asyncio.Event()

    async def hold_play(self, vc, text, voice, **_kwargs):
        await block.wait()

    with patch.object(AudioQueue, "_ensure_voice", new=fake_ensure), \
         patch.object(AudioQueue, "_play_streaming", new=hold_play), \
         patch.object(AudioQueue, "_prefetch", new=_noop_prefetch):
        # 큐 채우기: 첫 항목은 즉시 worker 가 가져가고 hold 됨. 그 다음부터 큐에 쌓임.
        for i in range(MAX_QUEUE_SIZE + 5):
            await q.enqueue(guild, AudioRequest(f"msg-{i}", "v", 1))
        # 짧게 대기해서 worker 가 첫 항목을 잡게 한다
        await asyncio.sleep(0.05)

        inner = q._queues[guild.id]
        assert inner.qsize() <= MAX_QUEUE_SIZE
        # 가장 오래된 잔여 항목들이 드롭되었는지: 마지막 enqueue 가 살아있어야 함
        remaining = list(inner._queue)
        last_text = f"msg-{MAX_QUEUE_SIZE + 4}"
        assert any(r.text == last_text for r in remaining)

        block.set()
        await asyncio.sleep(0.05)
