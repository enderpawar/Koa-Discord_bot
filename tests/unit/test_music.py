from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yt_dlp

from cogs.audio_mode import AUDIO_MODE_MUSIC, AUDIO_MODE_TTS, mode_from_config
from cogs.music_cog import MusicCog, _format_duration
from cogs.music_player import (
    InvalidYouTubeURL,
    MusicExtractionError,
    MusicPlayer,
    MusicQueueFull,
    MusicTrack,
    YouTubeExtractor,
    _download_error_message,
    _extractor_args,
    _ffmpeg_before_options,
    _safe_http_headers,
)


def _track(title: str, *, guild_id: int = 1) -> MusicTrack:
    return MusicTrack(
        webpage_url=f"https://www.youtube.com/watch?v={title}",
        title=title,
        duration=65,
        requester_id=10,
        request_channel_id=20 + guild_id,
        voice_channel_id=30 + guild_id,
        stream_url=f"https://stream.example/{title}",
        resolved_at=10**9,
    )


def _guild(guild_id: int) -> MagicMock:
    guild = MagicMock()
    guild.id = guild_id
    guild.voice_client = MagicMock()
    return guild


def test_audio_mode_defaults_to_tts_and_rejects_unknown_values() -> None:
    assert mode_from_config({}) == AUDIO_MODE_TTS
    assert mode_from_config({"audio_mode": "broken"}) == AUDIO_MODE_TTS
    assert mode_from_config({"audio_mode": AUDIO_MODE_MUSIC}) == AUDIO_MODE_MUSIC


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/BaW_jenozKc",
        "https://www.youtube.com/watch?v=BaW_jenozKc",
        "https://music.youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com/shorts/BaW_jenozKc",
    ],
)
def test_youtube_url_validation_accepts_supported_single_video_hosts(url: str) -> None:
    assert YouTubeExtractor.validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    ["hello", "https://example.com/watch?v=x", "file:///tmp/music.mp3"],
)
def test_youtube_url_validation_rejects_non_youtube_input(url: str) -> None:
    with pytest.raises(InvalidYouTubeURL):
        YouTubeExtractor.validate_url(url)


def test_extract_sync_builds_track_without_downloading() -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.__exit__.return_value = False
    ydl.extract_info.return_value = {
        "title": "test song",
        "duration": 125.8,
        "url": "https://stream.example/audio",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "live_status": "not_live",
        "http_headers": {"User-Agent": "test-browser", "Accept": "*/*"},
    }

    with patch("cogs.music_player.yt_dlp.YoutubeDL", return_value=ydl):
        track = YouTubeExtractor._extract_sync(
            "https://youtu.be/abc", 1, 2, 3
        )

    ydl.extract_info.assert_called_once_with("https://youtu.be/abc", download=False)
    assert track.title == "test song"
    assert track.duration == 125
    assert track.stream_url == "https://stream.example/audio"
    assert track.requester_id == 1
    assert track.request_channel_id == 2
    assert track.voice_channel_id == 3
    assert track.http_headers == {"User-Agent": "test-browser", "Accept": "*/*"}


def test_po_token_provider_configures_mweb_client(monkeypatch) -> None:
    monkeypatch.setenv(
        "YOUTUBE_PO_TOKEN_PROVIDER_URL", "http://bgutil-provider:4416/"
    )

    assert _extractor_args() == {
        "youtube": {"player_client": ["mweb"]},
        "youtubepot-bgutilhttp": {
            "base_url": ["http://bgutil-provider:4416"]
        },
    }


def test_ffmpeg_headers_are_sanitized_and_quoted() -> None:
    headers = _safe_http_headers(
        {
            "User-Agent": "browser agent",
            "Accept": "*/*",
            "Bad\r\nHeader": "injected",
            "Bad Header": "also injected",
            "Also-Bad": "value\r\nInjected: yes",
        }
    )
    options = _ffmpeg_before_options(headers)

    assert headers == {"User-Agent": "browser agent", "Accept": "*/*"}
    assert "-headers" in options
    assert "User-Agent: browser agent\r\n" in options
    assert "Injected" not in options


def test_extract_sync_rejects_playlist_and_live_stream() -> None:
    for info in (
        {"_type": "playlist"},
        {"is_live": True, "url": "https://stream.example/live"},
    ):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.return_value = info
        with patch("cogs.music_player.yt_dlp.YoutubeDL", return_value=ydl):
            with pytest.raises(InvalidYouTubeURL):
                YouTubeExtractor._extract_sync("https://youtu.be/abc", 1, 2, 3)


@pytest.mark.asyncio
async def test_extractor_maps_download_failure_to_user_safe_error() -> None:
    extractor = YouTubeExtractor()
    with patch.object(
        extractor,
        "_extract_sync",
        side_effect=yt_dlp.utils.DownloadError("internal details"),
    ):
        with pytest.raises(MusicExtractionError, match="다시 시도"):
            await extractor.extract(
                "https://youtu.be/abc",
                requester_id=1,
                request_channel_id=2,
                voice_channel_id=3,
            )


@pytest.mark.asyncio
async def test_extractor_hides_unexpected_internal_failure() -> None:
    extractor = YouTubeExtractor()
    with patch.object(extractor, "_extract_sync", side_effect=RuntimeError("secret")):
        with pytest.raises(MusicExtractionError, match="오류가 발생") as raised:
            await extractor.extract(
                "https://youtu.be/abc",
                requester_id=1,
                request_channel_id=2,
                voice_channel_id=3,
            )

    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("Sign in to confirm you're not a bot", "자동화 트래픽"),
        ("Private video", "비공개"),
        ("Sign in to confirm your age", "연령 확인"),
        ("This video is not available in your country", "서버 지역"),
        ("Video unavailable", "사용할 수 없는"),
    ],
)
def test_download_errors_have_actionable_korean_messages(
    detail: str, expected: str
) -> None:
    error = yt_dlp.utils.DownloadError(detail)
    assert expected in _download_error_message(error)


@pytest.mark.asyncio
async def test_music_player_keeps_guild_queues_isolated_and_ordered() -> None:
    modes = MagicMock()
    modes.cached_mode.return_value = AUDIO_MODE_MUSIC
    player = MusicPlayer(MagicMock(), MagicMock(), modes)
    guild_a = _guild(1)
    guild_b = _guild(2)
    started: list[tuple[int, str]] = []
    release = asyncio.Event()

    async def fake_play(guild, track):
        started.append((guild.id, track.title))
        if track.title == "a1":
            await release.wait()

    with patch.object(player, "_play_track", side_effect=fake_play):
        assert await player.enqueue(guild_a, _track("a1")) == 0
        assert await player.enqueue(guild_a, _track("a2")) >= 1
        assert await player.enqueue(guild_b, _track("b1", guild_id=2)) == 0
        for _ in range(20):
            if (2, "b1") in started:
                break
            await asyncio.sleep(0.01)
        release.set()
        for _ in range(20):
            if (1, "a2") in started:
                break
            await asyncio.sleep(0.01)

    assert started.index((1, "a1")) < started.index((1, "a2"))
    assert (2, "b1") in started
    await player.shutdown()


@pytest.mark.asyncio
async def test_music_player_stop_clears_current_and_pending_tracks() -> None:
    modes = MagicMock()
    modes.cached_mode.return_value = AUDIO_MODE_MUSIC
    player = MusicPlayer(MagicMock(), MagicMock(), modes)
    guild = _guild(1)
    playing = asyncio.Event()

    async def hold_play(_guild, _track):
        playing.set()
        await asyncio.Event().wait()

    with patch.object(player, "_play_track", side_effect=hold_play):
        await player.enqueue(guild, _track("one"))
        await player.enqueue(guild, _track("two"))
        await playing.wait()
        assert await player.stop_guild(guild) is True

    current, pending = player.snapshot(guild.id)
    assert current is None
    assert pending == []


@pytest.mark.asyncio
async def test_music_player_continues_after_one_track_fails() -> None:
    modes = MagicMock()
    modes.cached_mode.return_value = AUDIO_MODE_MUSIC
    player = MusicPlayer(MagicMock(), MagicMock(), modes)
    guild = _guild(1)
    played: list[str] = []

    async def fake_play(_guild, track):
        played.append(track.title)
        if track.title == "broken":
            raise RuntimeError("decode failure")

    with (
        patch.object(player, "_play_track", side_effect=fake_play),
        patch.object(player, "_send_playback_error", new_callable=AsyncMock) as notify,
    ):
        await player.enqueue(guild, _track("broken"))
        await player.enqueue(guild, _track("next"))
        for _ in range(20):
            if "next" in played:
                break
            await asyncio.sleep(0.01)

    assert played == ["broken", "next"]
    notify.assert_awaited_once()
    await player.shutdown()


@pytest.mark.asyncio
async def test_music_queue_has_a_hard_limit(monkeypatch) -> None:
    modes = MagicMock()
    modes.cached_mode.return_value = AUDIO_MODE_MUSIC
    player = MusicPlayer(MagicMock(), MagicMock(), modes)
    guild = _guild(1)
    monkeypatch.setattr("cogs.music_player.MAX_MUSIC_QUEUE_SIZE", 1)
    hold = asyncio.Event()

    async def hold_play(_guild, _track):
        await hold.wait()

    with patch.object(player, "_play_track", side_effect=hold_play):
        await player.enqueue(guild, _track("current"))
        await asyncio.sleep(0)
        await player.enqueue(guild, _track("waiting"))
        with pytest.raises(MusicQueueFull):
            await player.enqueue(guild, _track("overflow"))
        hold.set()
        await asyncio.sleep(0)
    await player.stop_guild(guild)


def test_music_command_names_and_duration_format() -> None:
    assert {
        MusicCog.play.name,
        MusicCog.skip.name,
        MusicCog.stop.name,
        MusicCog.playlist.name,
    } == {"재생", "스킵", "중지", "재생목록"}
    assert _format_duration(65) == "1:05"
    assert _format_duration(3661) == "1:01:01"


@pytest.mark.live
def test_live_youtube_extracts_public_test_video() -> None:
    if os.getenv("RUN_LIVE") != "1":
        pytest.skip("RUN_LIVE=1 required")
    last_error = ""
    # CDN stream URLs can be rejected transiently, so the network-only check gets
    # one bounded fresh-URL retry instead of becoming flaky.
    for _ in range(2):
        track = YouTubeExtractor._extract_sync(
            "https://youtu.be/2yRS6BOSQ4s?si=AYFNoQYIT6YqF7X4", 1, 2, 3
        )
        assert track.stream_url.startswith("http")
        assert track.title
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *shlex.split(_ffmpeg_before_options(track.http_headers)),
                "-i",
                track.stream_url,
                "-t",
                "1",
                "-vn",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return
        last_error = result.stderr
    pytest.fail(last_error)
