"""Phase 5 — guild별 TTS 재생 큐 + voice 라이프사이클.

각 guild 는 자기만의 asyncio.Queue 와 worker task 를 가지며 (Rule 02), worker 는
`_ensure_voice → synthesize → _play_blocking` 을 직렬로 수행한다. 단일 요청 실패는
log.exception 후 다음 요청으로 진행 (Rule 03). 큐가 5분간 비어 있으면 자동 disconnect.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import discord

from cogs.tts_engine import synthesize

log = logging.getLogger(__name__)

IDLE_TIMEOUT_SEC = 300


@dataclass
class AudioRequest:
    text: str
    voice: str
    voice_channel_id: int


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
                mp3 = await synthesize(req.text, req.voice)
                await self._play_blocking(vc, mp3)
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

    async def _play_blocking(self, vc: discord.VoiceClient, mp3: Path) -> None:
        # vc.play 의 after 콜백은 다른 스레드에서 실행되므로 loop.call_soon_threadsafe 필수
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def _after(err: BaseException | None) -> None:
            if err is not None:
                log.warning("playback callback error: %s", err)
            try:
                mp3.unlink(missing_ok=True)
            except OSError:
                pass
            loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegPCMAudio(str(mp3))
        vc.play(source, after=_after)
        await done.wait()

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
