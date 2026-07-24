from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cogs.rank_cog import (
    _format_duration,
    _format_score,
    _leaderboard_post_is_due,
    _rank_icon,
    _rank_stats_embed,
)


def test_format_duration() -> None:
    assert _format_duration(4) == "4초"
    assert _format_duration(65) == "1분 5초"
    assert _format_duration(3660) == "1시간 1분"


def test_format_score() -> None:
    assert _format_score(7000) == "70.00점"
    assert _format_score(10000) == "100.00점"


def test_rank_icon() -> None:
    assert _rank_icon(1) == "🥇"
    assert _rank_icon(2) == "🥈"
    assert _rank_icon(3) == "🥉"
    assert _rank_icon(4) == "`4`"


def test_rank_stats_embed_groups_metrics() -> None:
    embed = _rank_stats_embed(
        "미소",
        {"score": 8750, "voice_seconds": 3660, "message_count": 42},
    )

    assert embed.title == "미소 활동 내역"
    assert [field.name for field in embed.fields] == ["활동 점수", "활동 지표", "점수 기준"]
    assert "87.50점" in embed.fields[0].value
    assert "음성 시간: `1시간 1분`" in embed.fields[1].value
    assert "메시지: `42개`" in embed.fields[1].value


def test_leaderboard_post_catches_up_after_scheduled_minute() -> None:
    now = datetime(2026, 7, 24, 0, 7, tzinfo=ZoneInfo("Asia/Seoul"))
    assert _leaderboard_post_is_due(now, post_time="00:00", last_post_date=None)


def test_leaderboard_post_waits_until_scheduled_time() -> None:
    now = datetime(2026, 7, 24, 11, 59, tzinfo=ZoneInfo("Asia/Seoul"))
    assert not _leaderboard_post_is_due(now, post_time="12:00", last_post_date=None)


def test_leaderboard_posts_only_once_per_day() -> None:
    now = datetime(2026, 7, 24, 23, 59, tzinfo=ZoneInfo("Asia/Seoul"))
    assert not _leaderboard_post_is_due(
        now,
        post_time="00:00",
        last_post_date="2026-07-24",
    )


def test_leaderboard_rejects_invalid_post_time() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert not _leaderboard_post_is_due(now, post_time="24:00", last_post_date=None)
