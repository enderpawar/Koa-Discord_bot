"""Phase 5 — guild별 TTS 재생 큐 + voice 라이프사이클.

각 guild 는 자기만의 asyncio.Queue 와 worker task 를 가지며 (Rule 02), worker 는
`_ensure_voice → _play_streaming` 을 직렬로 수행한다. 단일 요청 실패는 log.exception 후
다음 요청으로 진행 (Rule 03). 큐가 5분간 비어 있으면 자동 disconnect.
"""
from __future__ import annotations

import asyncio
import logging
import queue
from contextlib import suppress
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from typing import Final

import discord

from cogs.tts_engine import stream_synthesize

log = logging.getLogger(__name__)

IDLE_TIMEOUT_SEC = 300
MONO_PCM_FRAME_BYTES = 1920  # 20ms * 48kHz * 16-bit * mono
STEREO_PCM_FRAME_BYTES = 3840  # 20ms * 48kHz * 16-bit * stereo
_STREAM_END: Final = object()


@dataclass
class AudioRequest:
    text: str
    voice: str
    voice_channel_id: int


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


class StreamingMonoPCMToStereo(discord.AudioSource):
    """Thread-safe streaming source for Azure mono PCM chunks."""

    def __init__(self, *, read_timeout_sec: float = 15.0) -> None:
        self._frames: queue.Queue[bytes | object] = queue.Queue()
        self._mono_buffer = bytearray()
        self._read_timeout_sec = read_timeout_sec
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


class AudioQueue:
    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[AudioRequest]] = {}
        self._workers: dict[int, asyncio.Task] = {}

    async def enqueue(self, guild: discord.Guild, req: AudioRequest) -> None:
        queue = self._queues.setdefault(guild.id, asyncio.Queue())
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

            try:
                vc = await self._ensure_voice(guild, req.voice_channel_id)
                await self._play_streaming(vc, req.text, req.voice)
            except Exception:
                log.exception("audio worker failed: guild_id=%s", guild.id)
            finally:
                queue.task_done()

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

    async def _play_streaming(self, vc: discord.VoiceClient, text: str, voice: str) -> None:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        source = StreamingMonoPCMToStereo()

        async def _produce() -> None:
            try:
                async for chunk in stream_synthesize(text, voice):
                    source.feed_mono(chunk)
                source.finish()
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
