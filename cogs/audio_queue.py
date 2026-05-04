"""Phase 5 — guild별 TTS 재생 큐 + voice 라이프사이클.

각 guild 는 자기만의 asyncio.Queue 와 worker task 를 가지며 (Rule 02), worker 는
`_ensure_voice → _play_streaming` 을 직렬로 수행한다. 단일 요청 실패는 log.exception 후
다음 요청으로 진행 (Rule 03). 큐가 5분간 비어 있으면 자동 disconnect.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from io import BufferedReader
from pathlib import Path
from typing import Final

import discord
from discord import opus

from cogs.tts_engine import stream_synthesize

log = logging.getLogger(__name__)

IDLE_TIMEOUT_SEC = 300
MONO_PCM_FRAME_BYTES = 1920  # 20ms * 48kHz * 16-bit * mono
STEREO_PCM_FRAME_BYTES = 3840  # 20ms * 48kHz * 16-bit * stereo
PRE_ROLL_MS = int(os.getenv("TTS_PRE_ROLL_MS", "120"))
CACHE_MAX_ENTRIES = int(os.getenv("TTS_CACHE_MAX_ENTRIES", "256"))
CACHE_MAX_BYTES = int(os.getenv("TTS_CACHE_MAX_BYTES", str(32 * 1024 * 1024)))
CACHE_MAX_ITEM_BYTES = int(os.getenv("TTS_CACHE_MAX_ITEM_BYTES", str(512 * 1024)))
OPUS_CACHE_MAX_BYTES = int(os.getenv("TTS_OPUS_CACHE_MAX_BYTES", str(16 * 1024 * 1024)))
MAX_QUEUE_SIZE = int(os.getenv("TTS_MAX_QUEUE_SIZE", "20"))
_STREAM_END: Final = object()
_SILENCE_STEREO_FRAME: Final = b"\x00" * STEREO_PCM_FRAME_BYTES


@dataclass
class AudioRequest:
    text: str
    voice: str
    voice_channel_id: int
    enqueued_at: float = field(default_factory=time.perf_counter)


class MonoPCMToStereo(discord.AudioSource):
    """48kHz 16-bit mono PCM file source adapted to Discord stereo PCM."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BufferedReader = path.open("rb")

    def read(self) -> bytes:
        mono = self._file.read(MONO_PCM_FRAME_BYTES)
        if not mono:
            return b""
        if len(mono) % 2:
            mono += b"\x00"
        if len(mono) < MONO_PCM_FRAME_BYTES:
            mono += b"\x00" * (MONO_PCM_FRAME_BYTES - len(mono))

        stereo = bytearray(len(mono) * 2)
        out = 0
        for i in range(0, len(mono), 2):
            sample = mono[i : i + 2]
            stereo[out : out + 2] = sample
            stereo[out + 2 : out + 4] = sample
            out += 4
        if len(stereo) != STEREO_PCM_FRAME_BYTES:
            raise RuntimeError("invalid PCM frame size")
        return bytes(stereo)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self._file.close()
        finally:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def _mono_frame_to_stereo(mono: bytes) -> bytes:
    if len(mono) != MONO_PCM_FRAME_BYTES:
        raise RuntimeError("invalid mono PCM frame size")

    stereo = bytearray(STEREO_PCM_FRAME_BYTES)
    out = 0
    for i in range(0, MONO_PCM_FRAME_BYTES, 2):
        sample = mono[i : i + 2]
        stereo[out : out + 2] = sample
        stereo[out + 2 : out + 4] = sample
        out += 4
    return bytes(stereo)


class PCMCache:
    """Small LRU cache for completed syntheses."""

    def __init__(self, *, max_entries: int, max_bytes: int, max_item_bytes: int) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_item_bytes = max_item_bytes
        self._items: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._total_bytes = 0

    def get(self, text: str, voice: str) -> bytes | None:
        key = (voice, text)
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item

    def put(self, text: str, voice: str, audio: bytes) -> None:
        if self.max_entries <= 0 or self.max_bytes <= 0 or not audio:
            return
        if len(audio) > self.max_item_bytes:
            return

        key = (voice, text)
        old = self._items.pop(key, None)
        if old is not None:
            self._total_bytes -= len(old)

        self._items[key] = audio
        self._total_bytes += len(audio)
        while len(self._items) > self.max_entries or self._total_bytes > self.max_bytes:
            _, evicted = self._items.popitem(last=False)
            self._total_bytes -= len(evicted)


def _mono_pcm_to_stereo_frames(audio: bytes) -> list[bytes]:
    frames: list[bytes] = []
    offset = 0
    while offset < len(audio):
        mono = audio[offset : offset + MONO_PCM_FRAME_BYTES]
        offset += MONO_PCM_FRAME_BYTES
        if len(mono) % 2:
            mono += b"\x00"
        if len(mono) < MONO_PCM_FRAME_BYTES:
            mono += b"\x00" * (MONO_PCM_FRAME_BYTES - len(mono))
        frames.append(_mono_frame_to_stereo(mono))
    return frames


def _mono_pcm_to_opus_stream(audio: bytes) -> bytes | None:
    if not opus.is_loaded():
        return None

    encoder = opus.Encoder()
    encoded_frames = bytearray()
    for pcm_frame in _mono_pcm_to_stereo_frames(audio):
        encoded = encoder.encode(pcm_frame, opus.Encoder.SAMPLES_PER_FRAME)
        if len(encoded) > 65535:
            return None
        encoded_frames.extend(len(encoded).to_bytes(2, "big"))
        encoded_frames.extend(encoded)
    return bytes(encoded_frames)


class StreamingMonoPCMToStereo(discord.AudioSource):
    """Thread-safe streaming source for Azure mono PCM chunks."""

    def __init__(
        self, *, read_timeout_sec: float = 15.0, pre_roll_ms: int = PRE_ROLL_MS
    ) -> None:
        self._frames: queue.Queue[bytes | object] = queue.Queue()
        self._mono_buffer = bytearray()
        self._read_timeout_sec = read_timeout_sec
        self._pre_roll_frames = max(0, pre_roll_ms // 20)
        self._closed = False

    def feed_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        self._mono_buffer.extend(chunk)
        while len(self._mono_buffer) >= MONO_PCM_FRAME_BYTES:
            frame = bytes(self._mono_buffer[:MONO_PCM_FRAME_BYTES])
            del self._mono_buffer[:MONO_PCM_FRAME_BYTES]
            self._frames.put(_mono_frame_to_stereo(frame))

    def finish(self) -> None:
        if self._closed:
            return
        if self._mono_buffer:
            remainder = bytes(self._mono_buffer)
            self._mono_buffer.clear()
            if len(remainder) % 2:
                remainder += b"\x00"
            remainder += b"\x00" * (MONO_PCM_FRAME_BYTES - len(remainder))
            self._frames.put(_mono_frame_to_stereo(remainder))
        self._frames.put(_STREAM_END)

    def abort(self) -> None:
        if self._closed:
            return
        self._frames.put(_STREAM_END)

    def read(self) -> bytes:
        if self._pre_roll_frames > 0:
            try:
                frame = self._frames.get_nowait()
            except queue.Empty:
                self._pre_roll_frames -= 1
                return _SILENCE_STEREO_FRAME
            if frame is _STREAM_END:
                return b""
            return frame

        try:
            frame = self._frames.get(timeout=self._read_timeout_sec)
        except queue.Empty:
            return b""
        if frame is _STREAM_END:
            return b""
        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._closed = True
        self._mono_buffer.clear()


class CachedOpusSource(discord.AudioSource):
    """Replay cached Discord-ready Opus frames."""

    def __init__(self, encoded_stream: bytes) -> None:
        self._stream = memoryview(encoded_stream)
        self._offset = 0

    def read(self) -> bytes:
        if self._offset >= len(self._stream):
            return b""
        if self._offset + 2 > len(self._stream):
            self._offset = len(self._stream)
            return b""
        frame_len = int.from_bytes(self._stream[self._offset : self._offset + 2], "big")
        self._offset += 2
        frame = self._stream[self._offset : self._offset + frame_len]
        self._offset += frame_len
        return frame.tobytes()

    def is_opus(self) -> bool:
        return True

    def cleanup(self) -> None:
        self._offset = len(self._stream)


class AudioQueue:
    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[AudioRequest]] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._cache = PCMCache(
            max_entries=CACHE_MAX_ENTRIES,
            max_bytes=CACHE_MAX_BYTES,
            max_item_bytes=CACHE_MAX_ITEM_BYTES,
        )
        self._opus_cache = PCMCache(
            max_entries=CACHE_MAX_ENTRIES,
            max_bytes=OPUS_CACHE_MAX_BYTES,
            max_item_bytes=OPUS_CACHE_MAX_BYTES,
        )

    async def enqueue(self, guild: discord.Guild, req: AudioRequest) -> None:
        queue = self._queues.setdefault(guild.id, asyncio.Queue())
        # 채팅 폭주 시 가장 오래된 미실행 요청을 드롭해 누적 지연을 방지한다.
        while queue.qsize() >= MAX_QUEUE_SIZE:
            try:
                dropped = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            queue.task_done()
            log.warning(
                "audio queue overflow, dropped: guild_id=%s text=%r",
                guild.id, dropped.text[:40],
            )
        await queue.put(req)
        worker = self._workers.get(guild.id)
        if worker is None or worker.done():
            self._workers[guild.id] = asyncio.create_task(
                self._worker(guild), name=f"audio-worker-{guild.id}"
            )

    async def shutdown(self) -> None:
        tasks = list(self._workers.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        self._queues.clear()

    async def _worker(self, guild: discord.Guild) -> None:
        queue = self._queues[guild.id]
        prefetch_task: asyncio.Task | None = None
        try:
            while True:
                try:
                    req = await asyncio.wait_for(queue.get(), timeout=IDLE_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    await self._disconnect(guild)
                    # disconnect 도중 enqueue 가 들어왔다면 worker 를 살려둔다
                    if queue.empty():
                        self._workers.pop(guild.id, None)
                        return
                    continue

                # 이전 iter 에서 본 req 에 대한 prefetch 가 있으면 캐시 적재가 끝날 때까지 대기.
                # _play_streaming 이 이후 캐시 히트로 즉시 재생을 시작할 수 있다.
                if prefetch_task is not None:
                    with suppress(Exception):
                        await prefetch_task
                    prefetch_task = None

                # 다음 항목을 미리 합성해 캐시에 채워둔다 (peek, dequeue 하지 않음).
                next_req = self._peek_next(queue)
                if next_req is not None:
                    prefetch_task = asyncio.create_task(
                        self._prefetch(next_req),
                        name=f"audio-prefetch-{guild.id}",
                    )

                try:
                    trace_start = time.perf_counter()
                    queued_ms = (trace_start - req.enqueued_at) * 1000
                    vc = await self._ensure_voice(guild, req.voice_channel_id)
                    ensure_ms = (time.perf_counter() - trace_start) * 1000
                    await self._play_streaming(
                        vc,
                        req.text,
                        req.voice,
                        guild_id=guild.id,
                        queued_ms=queued_ms,
                        ensure_ms=ensure_ms,
                        trace_start=trace_start,
                    )
                except Exception:
                    log.exception("audio worker failed: guild_id=%s", guild.id)
                finally:
                    queue.task_done()
        finally:
            if prefetch_task is not None and not prefetch_task.done():
                prefetch_task.cancel()

    @staticmethod
    def _peek_next(queue: asyncio.Queue[AudioRequest]) -> AudioRequest | None:
        # asyncio.Queue 는 내부적으로 collections.deque (`_queue`) 를 사용한다.
        # 단일 consumer (worker) 환경이므로 직접 peek 해도 안전하다.
        deque_ = getattr(queue, "_queue", None)
        if not deque_:
            return None
        return deque_[0]

    async def _prefetch(self, req: AudioRequest) -> None:
        """다음 요청을 백그라운드로 합성해 PCM/Opus 캐시에 채운다."""
        if self._opus_cache.get(req.text, req.voice) is not None:
            return
        if self._cache.get(req.text, req.voice) is not None:
            return

        collected = bytearray()
        try:
            async for chunk in stream_synthesize(req.text, req.voice):
                if len(collected) + len(chunk) > CACHE_MAX_ITEM_BYTES:
                    return  # 너무 커서 캐시 불가, prefetch 포기
                collected.extend(chunk)
        except Exception:
            log.debug("prefetch failed: text=%r", req.text[:40], exc_info=True)
            return

        pcm_audio = bytes(collected)
        if not pcm_audio:
            return
        self._cache.put(req.text, req.voice, pcm_audio)
        opus_stream = _mono_pcm_to_opus_stream(pcm_audio)
        if opus_stream is not None:
            self._opus_cache.put(req.text, req.voice, opus_stream)

    async def _ensure_voice(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.VoiceClient:
        target = guild.get_channel(channel_id)
        if target is None:
            raise RuntimeError(f"voice channel missing: id={channel_id}")
        vc = guild.voice_client
        if vc is not None and vc.is_connected():
            if vc.channel.id != channel_id:
                await vc.move_to(target)
            return vc
        return await target.connect(reconnect=True, self_deaf=True)

    async def _play_blocking(self, vc: discord.VoiceClient, audio: Path) -> None:
        # vc.play 의 after 콜백은 다른 스레드에서 실행되므로 loop.call_soon_threadsafe 필수
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        source = MonoPCMToStereo(audio)

        def _after(err: BaseException | None) -> None:
            if err is not None:
                log.warning("playback callback error: %s", err)
            source.cleanup()
            loop.call_soon_threadsafe(done.set)

        try:
            vc.play(source, after=_after)
        except Exception:
            source.cleanup()
            raise
        await done.wait()

    async def _play_source(self, vc: discord.VoiceClient, source: discord.AudioSource) -> None:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def _after(err: BaseException | None) -> None:
            if err is not None:
                log.warning("playback callback error: %s", err)
            source.cleanup()
            loop.call_soon_threadsafe(done.set)

        try:
            vc.play(source, after=_after)
        except Exception:
            source.cleanup()
            raise
        await done.wait()

    async def _play_streaming(
        self,
        vc: discord.VoiceClient,
        text: str,
        voice: str,
        *,
        guild_id: int | None = None,
        queued_ms: float = 0.0,
        ensure_ms: float = 0.0,
        trace_start: float | None = None,
    ) -> None:
        trace_start = trace_start if trace_start is not None else time.perf_counter()
        opus_audio = self._opus_cache.get(text, voice)
        if opus_audio is not None:
            play_start = time.perf_counter()
            await self._play_source(vc, CachedOpusSource(opus_audio))
            log.info(
                "tts latency: guild_id=%s cache=opus queued=%.1fms ensure=%.1fms "
                "play_start=%.1fms first_chunk=0.0ms bytes=%d",
                guild_id,
                queued_ms,
                ensure_ms,
                (play_start - trace_start) * 1000,
                len(opus_audio),
            )
            return

        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        source = StreamingMonoPCMToStereo()
        cached_audio = self._cache.get(text, voice)
        cache_kind = "pcm" if cached_audio is not None else "miss"
        play_called_at = 0.0
        first_chunk_at: float | None = None
        cached_bytes = len(cached_audio) if cached_audio is not None else 0

        async def _produce() -> None:
            nonlocal first_chunk_at, cached_bytes
            if cached_audio is not None:
                first_chunk_at = time.perf_counter()
                source.feed_mono(cached_audio)
                source.finish()
                return

            collected = bytearray()
            should_cache = True
            try:
                async for chunk in stream_synthesize(text, voice):
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter()
                    source.feed_mono(chunk)
                    if should_cache and len(collected) + len(chunk) <= CACHE_MAX_ITEM_BYTES:
                        collected.extend(chunk)
                    else:
                        should_cache = False
                        collected.clear()
                source.finish()
                if should_cache and len(collected) <= CACHE_MAX_ITEM_BYTES:
                    pcm_audio = bytes(collected)
                    cached_bytes = len(pcm_audio)
                    self._cache.put(text, voice, pcm_audio)
                    opus_stream = _mono_pcm_to_opus_stream(pcm_audio)
                    if opus_stream is not None:
                        self._opus_cache.put(text, voice, opus_stream)
            except Exception:
                source.abort()
                raise

        producer = asyncio.create_task(_produce(), name="tts-stream-producer")

        def _after(err: BaseException | None) -> None:
            if err is not None:
                log.warning("playback callback error: %s", err)
            source.cleanup()
            loop.call_soon_threadsafe(done.set)

        try:
            play_called_at = time.perf_counter()
            vc.play(source, after=_after)
        except Exception:
            producer.cancel()
            source.cleanup()
            raise

        await done.wait()
        if not producer.done():
            producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer
            return

        try:
            await producer
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tts stream producer failed")
            raise
        log.info(
            "tts latency: guild_id=%s cache=%s queued=%.1fms ensure=%.1fms "
            "play_start=%.1fms first_chunk=%.1fms bytes=%d",
            guild_id,
            cache_kind,
            queued_ms,
            ensure_ms,
            (play_called_at - trace_start) * 1000,
            ((first_chunk_at - trace_start) * 1000) if first_chunk_at is not None else -1.0,
            cached_bytes,
        )

    async def _disconnect(self, guild: discord.Guild) -> None:
        vc = guild.voice_client
        if vc is None:
            return
        try:
            if vc.is_connected():
                await vc.disconnect()
                log.info("idle disconnect: guild_id=%s", guild.id)
        except Exception:
            log.exception("disconnect failed: guild_id=%s", guild.id)
