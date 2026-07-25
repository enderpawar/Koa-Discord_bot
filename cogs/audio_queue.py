"""Phase 5 — guild별 TTS 재생 큐 + voice 라이프사이클.

각 guild 는 자기만의 asyncio.Queue 와 worker task 를 가지며 (Rule 02), worker 는
`_ensure_voice → _play_streaming` 을 직렬로 수행한다. 단일 요청 실패는 log.exception 후
다음 요청으로 진행 (Rule 03). 기본값은 명시적으로 TTS를 끌 때까지 연결을 유지한다.
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
import aiohttp
from discord import opus

from cogs.tts_engine import stream_synthesize

log = logging.getLogger(__name__)

# 0 means "stay connected until TTS is explicitly disabled".  A commercial
# chat TTS bot should not pay a fresh Discord voice handshake merely because
# the chat was quiet for five minutes.
IDLE_TIMEOUT_SEC = float(os.getenv("TTS_IDLE_DISCONNECT_SEC", "0"))
MONO_PCM_FRAME_BYTES = 1920  # 20ms * 48kHz * 16-bit * mono
STEREO_PCM_FRAME_BYTES = 3840  # 20ms * 48kHz * 16-bit * stereo
PRE_ROLL_MS = int(os.getenv("TTS_PRE_ROLL_MS", "120"))
CACHE_MAX_ENTRIES = int(os.getenv("TTS_CACHE_MAX_ENTRIES", "256"))
CACHE_MAX_BYTES = int(os.getenv("TTS_CACHE_MAX_BYTES", str(32 * 1024 * 1024)))
CACHE_MAX_ITEM_BYTES = int(os.getenv("TTS_CACHE_MAX_ITEM_BYTES", str(512 * 1024)))
OPUS_CACHE_MAX_BYTES = int(os.getenv("TTS_OPUS_CACHE_MAX_BYTES", str(16 * 1024 * 1024)))
MAX_QUEUE_SIZE = int(os.getenv("TTS_MAX_QUEUE_SIZE", "20"))
VOICE_CONNECT_TIMEOUT_SEC = float(os.getenv("TTS_VOICE_CONNECT_TIMEOUT_SEC", "10"))
VOICE_RECONNECT_GRACE_SEC = float(os.getenv("TTS_VOICE_RECONNECT_GRACE_SEC", "8"))
VOICE_CONNECT_ATTEMPTS = max(1, int(os.getenv("TTS_VOICE_CONNECT_ATTEMPTS", "3")))
VOICE_CONNECT_RETRY_DELAY_SEC = float(
    os.getenv("TTS_VOICE_CONNECT_RETRY_DELAY_SEC", "0.5")
)
VOICE_SILENCE_KEEPALIVE_SEC = float(
    os.getenv("TTS_VOICE_SILENCE_KEEPALIVE_SEC", "0")
)
VOICE_MEDIA_STABILIZE_SEC = float(
    os.getenv("TTS_VOICE_MEDIA_STABILIZE_SEC", "0.25")
)
VOICE_MEDIA_READY_TIMEOUT_SEC = float(
    os.getenv("TTS_VOICE_MEDIA_READY_TIMEOUT_SEC", "8")
)
VOICE_PLAYBACK_ATTEMPTS = max(
    1,
    int(os.getenv("TTS_VOICE_PLAYBACK_ATTEMPTS", "2")),
)
_STREAM_END: Final = object()
_SILENCE_STEREO_FRAME: Final = b"\x00" * STEREO_PCM_FRAME_BYTES


class VoicePlaybackDisconnected(RuntimeError):
    """Raised when Discord voice disconnects while an audio source is playing."""


def _to_stereo_purepy(mono: bytes) -> bytes:
    """Reference 구현 — audioop / numpy 가 없을 때만 사용. 빠르지 않다."""
    stereo = bytearray(len(mono) * 2)
    out = 0
    for i in range(0, len(mono), 2):
        sample = mono[i : i + 2]
        stereo[out : out + 2] = sample
        stereo[out + 2 : out + 4] = sample
        out += 4
    return bytes(stereo)


def _select_to_stereo():
    """audioop (C, stdlib) → numpy → pure-py 단계적 fallback.

    audio 송출 스레드가 20ms 마다 호출하는 핫 경로다. CPython stdlib audioop
    는 C 구현이라 순수 Python 루프 대비 자릿수 빠르다.
    """
    try:
        import audioop  # CPython stdlib (3.13 에서 제거 예정 → numpy 로 fallback)

        def _via_audioop(mono: bytes) -> bytes:
            return audioop.tostereo(mono, 2, 1.0, 1.0)

        return _via_audioop
    except ImportError:
        pass

    try:
        import numpy as np

        def _via_numpy(mono: bytes) -> bytes:
            arr = np.frombuffer(mono, dtype=np.int16)
            return np.repeat(arr, 2).tobytes()

        return _via_numpy
    except ImportError:
        return _to_stereo_purepy


_to_stereo = _select_to_stereo()


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

        stereo = _to_stereo(mono)
        if len(stereo) != STEREO_PCM_FRAME_BYTES:
            raise RuntimeError("invalid PCM frame size")
        return stereo

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
    stereo = _to_stereo(mono)
    if len(stereo) != STEREO_PCM_FRAME_BYTES:
        raise RuntimeError("invalid stereo PCM frame size")
    return stereo


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
    """Thread-safe streaming source for Azure mono PCM chunks.

    ``AudioPlayer`` 스레드의 ``read`` 는 절대 네트워크 청크를 기다리지 않는다.
    Azure 청크가 잠시 비면 20ms 무음을 반환해 Discord RTP 송출을 유지한다.
    mono → stereo 변환도 이 오디오 스레드에서 프레임 하나씩 수행하므로, 긴
    Azure 청크가 이벤트 루프를 오래 점유하지 않는다.
    """

    def __init__(
        self, *, read_timeout_sec: float = 15.0, pre_roll_ms: int = PRE_ROLL_MS
    ) -> None:
        self._chunks: queue.Queue[bytes | object] = queue.Queue()
        self._current_chunk: memoryview | None = None
        self._current_offset = 0
        self._partial_frame = bytearray()
        self._read_timeout_sec = read_timeout_sec
        self._pre_roll_frames = max(0, pre_roll_ms // 20)
        self._last_feed_at = time.monotonic()
        self._finished = False
        self._aborted = False
        self._closed = False

    def feed_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        self._last_feed_at = time.monotonic()
        self._chunks.put(bytes(chunk))

    def finish(self) -> None:
        if self._closed:
            return
        self._last_feed_at = time.monotonic()
        self._chunks.put(_STREAM_END)

    def abort(self) -> None:
        if self._closed:
            return
        self._aborted = True
        self._chunks.put(_STREAM_END)

    def _take_mono_frame(self) -> bytes | None:
        """현재 도착한 청크에서 mono 20ms 프레임 하나를 만든다.

        ``None`` 은 아직 데이터가 부족하다는 뜻이며 스트림 종료와 구분된다.
        """
        while len(self._partial_frame) < MONO_PCM_FRAME_BYTES and not self._finished:
            if self._current_chunk is None:
                try:
                    item = self._chunks.get_nowait()
                except queue.Empty:
                    break
                if item is _STREAM_END:
                    self._finished = True
                    break
                self._current_chunk = memoryview(item)
                self._current_offset = 0

            remaining = len(self._current_chunk) - self._current_offset
            needed = MONO_PCM_FRAME_BYTES - len(self._partial_frame)
            take = min(remaining, needed)
            start = self._current_offset
            self._partial_frame.extend(self._current_chunk[start : start + take])
            self._current_offset += take
            if self._current_offset >= len(self._current_chunk):
                self._current_chunk = None
                self._current_offset = 0

        if len(self._partial_frame) >= MONO_PCM_FRAME_BYTES:
            frame = bytes(self._partial_frame)
            self._partial_frame.clear()
            return frame

        if self._finished and self._partial_frame:
            if len(self._partial_frame) % 2:
                self._partial_frame.append(0)
            self._partial_frame.extend(
                b"\x00" * (MONO_PCM_FRAME_BYTES - len(self._partial_frame))
            )
            frame = bytes(self._partial_frame)
            self._partial_frame.clear()
            return frame
        return None

    def read(self) -> bytes:
        if self._closed or self._aborted:
            return b""

        mono = self._take_mono_frame()
        if mono is not None:
            self._pre_roll_frames = 0
            return _mono_frame_to_stereo(mono)
        if self._finished:
            return b""

        # Azure의 첫 청크/중간 청크를 기다리는 동안에도 AudioPlayer 스레드를
        # 막지 않는다. 무음 RTP를 계속 보내 Discord 연결 상태를 정상 유지한다.
        if time.monotonic() - self._last_feed_at >= self._read_timeout_sec:
            return b""
        if self._pre_roll_frames > 0:
            self._pre_roll_frames -= 1
        return _SILENCE_STEREO_FRAME

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._closed = True
        self._current_chunk = None
        self._current_offset = 0
        self._partial_frame.clear()


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
        self._voice_locks: dict[int, asyncio.Lock] = {}
        self._voice_keepalives: dict[int, asyncio.Task] = {}
        self._voice_keepalive_clients: dict[int, discord.VoiceClient] = {}
        self._voice_stable_clients: dict[int, discord.VoiceClient] = {}
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
        self._voice_locks.clear()
        keepalives = list(self._voice_keepalives.values())
        for task in keepalives:
            task.cancel()
        for task in keepalives:
            with suppress(asyncio.CancelledError):
                await task
        self._voice_keepalives.clear()
        self._voice_keepalive_clients.clear()
        self._voice_stable_clients.clear()

    async def _worker(self, guild: discord.Guild) -> None:
        queue = self._queues[guild.id]
        prefetch_task: asyncio.Task | None = None
        try:
            while True:
                try:
                    if IDLE_TIMEOUT_SEC > 0:
                        req = await asyncio.wait_for(
                            queue.get(), timeout=IDLE_TIMEOUT_SEC
                        )
                    else:
                        req = await queue.get()
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
                    queued_ms = (time.perf_counter() - req.enqueued_at) * 1000
                    for playback_attempt in range(1, VOICE_PLAYBACK_ATTEMPTS + 1):
                        trace_start = time.perf_counter()
                        vc = await self._ensure_voice(guild, req.voice_channel_id)
                        ensure_ms = (time.perf_counter() - trace_start) * 1000
                        try:
                            await self._play_streaming(
                                vc,
                                req.text,
                                req.voice,
                                guild_id=guild.id,
                                queued_ms=queued_ms,
                                ensure_ms=ensure_ms,
                                trace_start=trace_start,
                            )
                            break
                        except VoicePlaybackDisconnected:
                            self._voice_stable_clients.pop(guild.id, None)
                            if playback_attempt == VOICE_PLAYBACK_ATTEMPTS:
                                raise
                            log.warning(
                                "voice playback interrupted, retrying: guild_id=%s "
                                "attempt=%d/%d",
                                guild.id,
                                playback_attempt,
                                VOICE_PLAYBACK_ATTEMPTS,
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
        await self._store_synthesis(req.text, req.voice, pcm_audio)

    async def _store_synthesis(self, text: str, voice: str, pcm_audio: bytes) -> None:
        """합성 결과를 캐시에 적재한다.

        PCM 적재는 이벤트 루프에서 즉시 수행하고, CPU 비용이 큰 Opus 인코딩은
        `asyncio.to_thread` 로 오프로드한다. 인코딩은 발화 전체 프레임을 도는
        루프라, 이벤트 루프에서 돌리면 그동안 다른 guild 의 오디오 송출 예약과
        네트워크 콜백이 밀린다.
        """
        self._cache.put(text, voice, pcm_audio)
        opus_stream = await asyncio.to_thread(_mono_pcm_to_opus_stream, pcm_audio)
        if opus_stream is not None:
            self._opus_cache.put(text, voice, opus_stream)

    async def _ensure_voice(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.VoiceClient:
        target = guild.get_channel(channel_id)
        if target is None:
            raise RuntimeError(f"voice channel missing: id={channel_id}")
        lock = self._voice_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            for attempt in range(1, VOICE_CONNECT_ATTEMPTS + 1):
                attempt_started = time.perf_counter()
                vc = guild.voice_client
                if vc is not None and vc.is_connected():
                    if vc.channel.id != channel_id:
                        await vc.move_to(target)
                        self._voice_stable_clients.pop(guild.id, None)
                    if self._voice_stable_clients.get(guild.id) is not vc:
                        if not await self._wait_for_voice_media_ready(guild, vc):
                            await self._cleanup_stale_voice(guild, vc)
                            continue
                    self._start_voice_keepalive(guild, vc)
                    log.info(
                        "voice ready: guild_id=%s mode=reuse channel_id=%s "
                        "elapsed=%.1fms",
                        guild.id,
                        channel_id,
                        (time.perf_counter() - attempt_started) * 1000,
                    )
                    return vc

                if vc is not None:
                    # Discord 화면에는 봇이 먼저 입장한 것처럼 보여도 voice
                    # WebSocket/UDP handshake 또는 자동 재연결은 아직 진행 중일 수
                    # 있다. 이때 force-disconnect 하면 4006 재연결 루프가 생긴다.
                    if await self._wait_for_voice_reconnect(guild, vc):
                        if vc.channel.id != channel_id:
                            await vc.move_to(target)
                        if not await self._wait_for_voice_media_ready(guild, vc):
                            await self._cleanup_stale_voice(guild, vc)
                            continue
                        self._start_voice_keepalive(guild, vc)
                        log.info(
                            "voice ready: guild_id=%s mode=reconnect "
                            "channel_id=%s elapsed=%.1fms",
                            guild.id,
                            channel_id,
                            (time.perf_counter() - attempt_started) * 1000,
                        )
                        return vc
                    await self._cleanup_stale_voice(guild, vc)

                try:
                    connected = await target.connect(
                        timeout=VOICE_CONNECT_TIMEOUT_SEC,
                        reconnect=True,
                        self_deaf=True,
                    )
                    if not await self._wait_for_voice_media_ready(guild, connected):
                        await self._cleanup_stale_voice(guild, connected)
                        raise asyncio.TimeoutError(
                            "voice media path did not become stable"
                        )
                    self._start_voice_keepalive(guild, connected)
                    log.info(
                        "voice ready: guild_id=%s mode=new channel_id=%s "
                        "attempt=%d elapsed=%.1fms",
                        guild.id,
                        channel_id,
                        attempt,
                        (time.perf_counter() - attempt_started) * 1000,
                    )
                    return connected
                except (
                    asyncio.TimeoutError,
                    aiohttp.ClientError,
                    discord.ConnectionClosed,
                    OSError,
                ) as exc:
                    if attempt == VOICE_CONNECT_ATTEMPTS:
                        raise
                    log.warning(
                        "voice connect failed, retrying: guild_id=%s "
                        "attempt=%d/%d error=%s",
                        guild.id,
                        attempt,
                        VOICE_CONNECT_ATTEMPTS,
                        exc.__class__.__name__,
                    )
                    stale = guild.voice_client
                    if stale is not None:
                        await self._cleanup_stale_voice(guild, stale)
                    await asyncio.sleep(
                        VOICE_CONNECT_RETRY_DELAY_SEC * attempt
                    )

            raise RuntimeError("voice connection attempts exhausted")

    async def _wait_for_voice_reconnect(
        self,
        guild: discord.Guild, vc: discord.VoiceClient
    ) -> bool:
        if VOICE_RECONNECT_GRACE_SEC <= 0:
            return False
        deadline = asyncio.get_running_loop().time() + VOICE_RECONNECT_GRACE_SEC
        while guild.voice_client is vc:
            if vc.is_connected():
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.1, remaining))
        return False

    async def _wait_for_voice_media_ready(
        self, guild: discord.Guild, vc: discord.VoiceClient
    ) -> bool:
        if VOICE_MEDIA_STABILIZE_SEC <= 0:
            self._voice_stable_clients[guild.id] = vc
            return vc.is_connected()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + VOICE_MEDIA_READY_TIMEOUT_SEC
        stable_since: float | None = None
        while guild.voice_client is vc and loop.time() < deadline:
            if not vc.is_connected():
                stable_since = None
                await asyncio.sleep(0.1)
                continue

            now = loop.time()
            if stable_since is None:
                stable_since = now
            if now - stable_since >= VOICE_MEDIA_STABILIZE_SEC:
                self._voice_stable_clients[guild.id] = vc
                connection = getattr(vc, "_connection", None)
                dave_version = int(
                    getattr(connection, "dave_protocol_version", 0) or 0
                )
                dave_ready = dave_version == 0 or bool(
                    getattr(connection, "can_encrypt", False)
                )
                log.info(
                    "voice media ready: guild_id=%s dave_version=%d "
                    "dave_ready=%s",
                    guild.id,
                    dave_version,
                    dave_ready,
                )
                return True
            await asyncio.sleep(0.1)
        return False

    async def _cleanup_stale_voice(
        self, guild: discord.Guild, vc: discord.VoiceClient
    ) -> None:
        self._stop_voice_keepalive(guild.id, vc)
        self._voice_stable_clients.pop(guild.id, None)
        log.warning("cleaning stale voice client: guild_id=%s", guild.id)
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning(
                "stale voice disconnect timed out: guild_id=%s",
                guild.id,
            )

    def _start_voice_keepalive(
        self, guild: discord.Guild, vc: discord.VoiceClient
    ) -> None:
        if VOICE_SILENCE_KEEPALIVE_SEC <= 0:
            return
        current = self._voice_keepalives.get(guild.id)
        if (
            current is not None
            and not current.done()
            and self._voice_keepalive_clients.get(guild.id) is vc
        ):
            return
        self._stop_voice_keepalive(guild.id)
        self._voice_keepalive_clients[guild.id] = vc
        self._voice_keepalives[guild.id] = asyncio.create_task(
            self._voice_keepalive_loop(guild, vc),
            name=f"voice-silence-keepalive-{guild.id}",
        )

    def _stop_voice_keepalive(
        self, guild_id: int, vc: discord.VoiceClient | None = None
    ) -> None:
        if vc is not None and self._voice_keepalive_clients.get(guild_id) is not vc:
            return
        task = self._voice_keepalives.pop(guild_id, None)
        self._voice_keepalive_clients.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _voice_keepalive_loop(
        self, guild: discord.Guild, vc: discord.VoiceClient
    ) -> None:
        next_send = (
            asyncio.get_running_loop().time()
            + VOICE_SILENCE_KEEPALIVE_SEC
        )
        try:
            while guild.voice_client is vc:
                await asyncio.sleep(
                    min(0.25, VOICE_SILENCE_KEEPALIVE_SEC)
                )
                if not vc.is_connected():
                    self._voice_stable_clients.pop(guild.id, None)
                    continue
                if vc.is_playing() or vc.is_paused():
                    continue
                now = asyncio.get_running_loop().time()
                if now < next_send:
                    continue
                try:
                    vc.send_audio_packet(opus.OPUS_SILENCE, encode=False)
                    next_send = now + VOICE_SILENCE_KEEPALIVE_SEC
                    log.debug(
                        "voice silence keepalive sent: guild_id=%s",
                        guild.id,
                    )
                except Exception:
                    log.debug(
                        "voice silence keepalive failed: guild_id=%s",
                        guild.id,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._voice_keepalives.get(guild.id) is current:
                self._voice_keepalives.pop(guild.id, None)
                self._voice_keepalive_clients.pop(guild.id, None)
            if self._voice_stable_clients.get(guild.id) is vc:
                self._voice_stable_clients.pop(guild.id, None)

    async def ensure_voice(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.VoiceClient:
        """명령/UI와 worker가 같은 연결 직렬화 경로를 사용하게 하는 공개 API."""
        return await self._ensure_voice(guild, channel_id)

    async def disconnect_voice(self, guild: discord.Guild) -> None:
        """Explicitly stop a guild voice session and clear all local state."""
        await self._disconnect(guild, reason="explicit")

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
        await self._wait_for_playback(vc, done)

    @staticmethod
    async def _wait_for_playback(
        vc: discord.VoiceClient, done: asyncio.Event
    ) -> None:
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                if vc.is_connected():
                    continue
                vc.stop()
                await done.wait()
                raise VoicePlaybackDisconnected(
                    "Discord voice disconnected during playback"
                )

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
                    await self._store_synthesis(text, voice, pcm_audio)
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

        try:
            await self._wait_for_playback(vc, done)
        except VoicePlaybackDisconnected:
            if not producer.done():
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer
            raise
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

    async def _disconnect(self, guild: discord.Guild, *, reason: str = "idle") -> None:
        lock = self._voice_locks.setdefault(guild.id, asyncio.Lock())
        try:
            async with lock:
                vc = guild.voice_client
                if vc is None:
                    self._stop_voice_keepalive(guild.id)
                    self._voice_stable_clients.pop(guild.id, None)
                    return
                self._stop_voice_keepalive(guild.id, vc)
                self._voice_stable_clients.pop(guild.id, None)
                await asyncio.wait_for(
                    vc.disconnect(force=True),
                    timeout=VOICE_CONNECT_TIMEOUT_SEC,
                )
                log.info(
                    "voice disconnected: guild_id=%s reason=%s",
                    guild.id,
                    reason,
                )
        except Exception:
            log.exception(
                "disconnect failed: guild_id=%s reason=%s",
                guild.id,
                reason,
            )
