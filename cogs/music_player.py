"""YouTube metadata extraction and guild-isolated Discord music playback."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import discord
import yt_dlp

from cogs.audio_mode import AUDIO_MODE_MUSIC, AudioModeCoordinator
from cogs.audio_queue import AudioQueue
from cogs.ui import notice_embed

if TYPE_CHECKING:
    from discord.ext import commands


log = logging.getLogger(__name__)

ALLOWED_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)
MAX_MUSIC_QUEUE_SIZE = 20
STREAM_URL_REFRESH_SEC = 600
EXTRACTION_TIMEOUT_SEC = 45


class MusicError(RuntimeError):
    """Base exception carrying a user-safe Korean message."""


class InvalidYouTubeURL(MusicError):
    pass


class MusicExtractionError(MusicError):
    pass


class MusicQueueFull(MusicError):
    pass


@dataclass(slots=True)
class MusicTrack:
    webpage_url: str
    title: str
    duration: int | None
    requester_id: int
    request_channel_id: int
    voice_channel_id: int
    stream_url: str
    resolved_at: float


class _YTDLPLogger:
    def debug(self, message: str) -> None:
        log.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        log.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        log.error("yt-dlp: %s", message)


class YouTubeExtractor:
    """Resolve public single-video YouTube URLs without blocking the event loop."""

    def __init__(self, *, concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)

    @staticmethod
    def validate_url(url: str) -> str:
        value = url.strip()
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise InvalidYouTubeURL("올바른 YouTube URL을 입력해 주세요.") from exc
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in ALLOWED_YOUTUBE_HOSTS:
            raise InvalidYouTubeURL("YouTube 영상 URL만 사용할 수 있어요.")
        return value

    async def extract(
        self,
        url: str,
        *,
        requester_id: int,
        request_channel_id: int,
        voice_channel_id: int,
    ) -> MusicTrack:
        validated = self.validate_url(url)
        async with self._semaphore:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._extract_sync,
                        validated,
                        requester_id,
                        request_channel_id,
                        voice_channel_id,
                    ),
                    timeout=EXTRACTION_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError as exc:
                raise MusicExtractionError(
                    "YouTube 응답이 늦어 요청을 종료했어요. 잠시 후 다시 시도해 주세요."
                ) from exc
            except yt_dlp.utils.DownloadError as exc:
                raise MusicExtractionError(
                    "영상을 불러오지 못했어요. 공개 영상인지 확인해 주세요."
                ) from exc
            except MusicError:
                raise
            except Exception as exc:
                log.exception("unexpected YouTube extraction failure")
                raise MusicExtractionError(
                    "영상을 불러오는 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."
                ) from exc

    @staticmethod
    def _extract_sync(
        url: str,
        requester_id: int,
        request_channel_id: int,
        voice_channel_id: int,
    ) -> MusicTrack:
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreconfig": True,
            "socket_timeout": 15,
            "retries": 2,
            "extractor_retries": 2,
            "logger": _YTDLPLogger(),
            "js_runtimes": {"deno": {}, "node": {}},
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info or info.get("_type") == "playlist":
            raise InvalidYouTubeURL("재생목록이 아닌 단일 YouTube 영상 URL을 입력해 주세요.")
        if info.get("is_live") or info.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise InvalidYouTubeURL("라이브 스트림은 아직 지원하지 않아요.")

        stream_url = info.get("url")
        if not isinstance(stream_url, str) or not stream_url:
            raise MusicExtractionError("재생할 수 있는 오디오를 찾지 못했어요.")

        duration = info.get("duration")
        if not isinstance(duration, (int, float)):
            duration = None
        return MusicTrack(
            webpage_url=str(info.get("webpage_url") or url),
            title=str(info.get("title") or "제목 없는 영상"),
            duration=int(duration) if duration is not None else None,
            requester_id=requester_id,
            request_channel_id=request_channel_id,
            voice_channel_id=voice_channel_id,
            stream_url=stream_url,
            resolved_at=time.monotonic(),
        )


class MusicPlayer:
    """One serial music worker per guild, sharing Koa's existing voice manager."""

    def __init__(
        self,
        bot: commands.Bot,
        voice_queue: AudioQueue,
        modes: AudioModeCoordinator,
        extractor: YouTubeExtractor | None = None,
    ) -> None:
        self.bot = bot
        self.voice_queue = voice_queue
        self.modes = modes
        self.extractor = extractor or YouTubeExtractor()
        self._queues: dict[int, asyncio.Queue[MusicTrack]] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._current: dict[int, MusicTrack] = {}
        self._guilds: dict[int, discord.Guild] = {}
        self._skip_requested: set[int] = set()

    async def enqueue(self, guild: discord.Guild, track: MusicTrack) -> int:
        queue = self._queues.setdefault(guild.id, asyncio.Queue())
        pending = queue.qsize()
        if pending >= MAX_MUSIC_QUEUE_SIZE:
            raise MusicQueueFull(f"대기열은 최대 {MAX_MUSIC_QUEUE_SIZE}곡까지 담을 수 있어요.")
        await queue.put(track)
        self._guilds[guild.id] = guild
        worker = self._workers.get(guild.id)
        if worker is None or worker.done():
            self._workers[guild.id] = asyncio.create_task(
                self._worker(guild), name=f"music-worker-{guild.id}"
            )
        return pending + (1 if guild.id in self._current else 0)

    def snapshot(self, guild_id: int) -> tuple[MusicTrack | None, list[MusicTrack]]:
        queue = self._queues.get(guild_id)
        pending = list(queue._queue) if queue is not None else []
        return self._current.get(guild_id), pending

    async def skip(self, guild: discord.Guild) -> MusicTrack | None:
        track = self._current.get(guild.id)
        if track is None:
            return None
        self._skip_requested.add(guild.id)
        vc = guild.voice_client
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        return track

    async def stop_guild(self, guild: discord.Guild) -> bool:
        had_audio = guild.id in self._current
        queue = self._queues.get(guild.id)
        if queue is not None:
            had_audio = had_audio or not queue.empty()
            self._drain_queue(queue)

        worker = self._workers.pop(guild.id, None)
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._current.pop(guild.id, None)
        self._skip_requested.discard(guild.id)
        self._queues.pop(guild.id, None)
        self._guilds.pop(guild.id, None)
        return had_audio

    async def shutdown(self) -> None:
        for guild in list(self._guilds.values()):
            await self.stop_guild(guild)

    async def _worker(self, guild: discord.Guild) -> None:
        queue = self._queues[guild.id]
        try:
            while True:
                track = await queue.get()
                self._current[guild.id] = track
                try:
                    if self.modes.cached_mode(guild.id) != AUDIO_MODE_MUSIC:
                        continue
                    await self._play_track(guild, track)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "music playback failed: guild_id=%s title=%r",
                        guild.id,
                        track.title,
                    )
                    await self._send_playback_error(guild, track)
                finally:
                    self._current.pop(guild.id, None)
                    self._skip_requested.discard(guild.id)
                    queue.task_done()
        finally:
            current = asyncio.current_task()
            if self._workers.get(guild.id) is current:
                self._workers.pop(guild.id, None)

    async def _play_track(self, guild: discord.Guild, track: MusicTrack) -> None:
        if time.monotonic() - track.resolved_at > STREAM_URL_REFRESH_SEC:
            refreshed = await self.extractor.extract(
                track.webpage_url,
                requester_id=track.requester_id,
                request_channel_id=track.request_channel_id,
                voice_channel_id=track.voice_channel_id,
            )
            track.stream_url = refreshed.stream_url
            track.resolved_at = refreshed.resolved_at

        if guild.id in self._skip_requested:
            return

        vc = await self.voice_queue.ensure_voice(guild, track.voice_channel_id)
        source = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn -loglevel warning",
        )
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def after(error: BaseException | None) -> None:
            if error is not None:
                log.warning(
                    "music callback error: guild_id=%s error=%s", guild.id, error
                )
            source.cleanup()
            loop.call_soon_threadsafe(done.set)

        try:
            vc.play(source, after=after)
        except Exception:
            source.cleanup()
            raise

        log.info(
            "music started: guild_id=%s title=%r requester_id=%s",
            guild.id,
            track.title,
            track.requester_id,
        )
        try:
            await done.wait()
        except asyncio.CancelledError:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            try:
                await asyncio.wait_for(done.wait(), timeout=2)
            except asyncio.TimeoutError:
                source.cleanup()
            raise

    async def _send_playback_error(
        self, guild: discord.Guild, track: MusicTrack
    ) -> None:
        channel = guild.get_channel(track.request_channel_id)
        if channel is None or not hasattr(channel, "send"):
            return
        try:
            await channel.send(
                embed=notice_embed(
                    "재생 오류",
                    f"`{track.title}` 재생에 실패해 다음 곡으로 넘어갑니다.",
                    tone="error",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.debug("music error notice failed: guild_id=%s", guild.id)

    @staticmethod
    def _drain_queue(queue: asyncio.Queue[MusicTrack]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            queue.task_done()
