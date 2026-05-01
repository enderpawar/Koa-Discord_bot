from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


def _store(tmp: Path):
    from cogs.rank_store import RankStore

    return RankStore(tmp)


@pytest.fixture
def rank_path(request: pytest.FixtureRequest) -> Path:
    path = Path("rank_test_tmp") / f"{request.node.name}.json"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    return path


async def test_voice_time_round_trip(rank_path: Path):
    store = _store(rank_path)
    await store.start_voice(1, 10, 100, now_ts=1000)
    active_stats = await store.user_stats(1, 10, now_ts=1060)
    added = await store.stop_voice(1, 10, now_ts=1365)

    stats = await store.user_stats(1, 10)
    assert active_stats["voice_seconds"] == 60
    assert added == 365
    assert stats["voice_seconds"] == 365
    assert stats["total_seconds"] == 365


async def test_chat_activity_uses_message_windows(rank_path: Path):
    store = _store(rank_path)
    await store.record_message(1, 10, now_ts=1000)
    await store.record_message(1, 10, now_ts=1030)
    await store.record_message(1, 10, now_ts=2000)

    stats = await store.user_stats(1, 10)
    assert stats["message_count"] == 3
    assert stats["chat_seconds"] == 150


async def test_leaderboard_orders_by_total_activity(rank_path: Path):
    store = _store(rank_path)
    await store.start_voice(1, 10, 100, now_ts=1000)
    await store.stop_voice(1, 10, now_ts=1100)
    await store.record_message(1, 20, now_ts=1000)
    await store.record_message(1, 20, now_ts=1200)

    rows = await store.leaderboard(1)
    assert [row["user_id"] for row in rows] == [20, 10]


async def test_weekly_reset_anchor_is_friday_kst():
    from cogs.rank_store import weekly_reset_anchor

    kst = ZoneInfo("Asia/Seoul")
    thursday = datetime(2026, 4, 30, 23, 59, tzinfo=kst)
    friday = datetime(2026, 5, 1, 0, 0, tzinfo=kst)

    assert weekly_reset_anchor(thursday) == "2026-04-24T00:00:00+09:00"
    assert weekly_reset_anchor(friday) == "2026-05-01T00:00:00+09:00"


async def test_ensure_week_clears_old_stats(rank_path: Path):
    store = _store(rank_path)
    await store.record_message(1, 10, now_ts=1000)

    changed = await store.ensure_week(
        now=datetime(2026, 5, 8, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )
    stats = await store.user_stats(1, 10)

    assert changed is True
    assert stats["total_seconds"] == 0


async def test_weekly_reset_preserves_active_voice_session(rank_path: Path):
    store = _store(rank_path)
    await store.start_voice(1, 10, 100, now_ts=1000)

    await store.ensure_week(
        now=datetime(2026, 5, 8, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )
    await store.stop_voice(
        1,
        10,
        now_ts=datetime(2026, 5, 8, 0, 10, tzinfo=ZoneInfo("Asia/Seoul")).timestamp(),
    )
    stats = await store.user_stats(1, 10)

    assert stats["voice_seconds"] == 600
