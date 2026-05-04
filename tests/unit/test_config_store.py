"""
Phase 2 — Config Store
guild별 격리, atomic 저장, 동시성 락 검증.
"""
from __future__ import annotations
import asyncio
import json
import importlib.util
from pathlib import Path

import pytest

# 모듈 미구현이면 전체 스킵
_HAS = importlib.util.find_spec("cogs.config_store") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 2 not yet implemented")


def _store(tmp: Path):
    from cogs.config_store import ConfigStore  # noqa: WPS433
    # path-singleton 이므로 매 테스트마다 인스턴스 캐시를 비워야 격리된다.
    ConfigStore._reset_instances_for_tests()
    return ConfigStore(tmp)


async def test_set_and_get_round_trip(tmp_config_path):
    s = _store(tmp_config_path)
    await s.set(123, tts_channel_id=456, voice_channel_id=789)
    cfg = await s.get(123)
    assert cfg["tts_channel_id"] == 456
    assert cfg["voice_channel_id"] == 789


async def test_persists_to_disk(tmp_config_path):
    s = _store(tmp_config_path)
    await s.set(111, voice="ko-KR-SunHiNeural")
    raw = json.loads(tmp_config_path.read_text(encoding="utf-8"))
    assert raw["111"]["voice"] == "ko-KR-SunHiNeural"


async def test_guild_isolation(tmp_config_path):
    s = _store(tmp_config_path)
    await s.set(1, tts_channel_id=10)
    await s.set(2, tts_channel_id=20)
    assert (await s.get(1))["tts_channel_id"] == 10
    assert (await s.get(2))["tts_channel_id"] == 20


async def test_concurrent_writes_no_corruption(tmp_config_path):
    s = _store(tmp_config_path)
    await asyncio.gather(*(s.set(i, tts_channel_id=i * 10) for i in range(20)))
    raw = json.loads(tmp_config_path.read_text(encoding="utf-8"))
    assert len(raw) == 20
    for i in range(20):
        assert raw[str(i)]["tts_channel_id"] == i * 10


async def test_get_missing_returns_empty(tmp_config_path):
    s = _store(tmp_config_path)
    cfg = await s.get(99999)
    assert cfg == {} or cfg is None


async def test_multiple_store_instances_see_latest_config(tmp_config_path):
    """동일 path 의 ConfigStore() 호출은 같은 인스턴스를 반환 (path-singleton)."""
    from cogs.config_store import ConfigStore  # noqa: WPS433

    first = _store(tmp_config_path)
    # 같은 path 라면 _reset_instances_for_tests 없이 그대로 호출 시 동일 인스턴스
    second = ConfigStore(tmp_config_path)
    assert first is second

    await first.set(123, leaderboard_daily_enabled=True)
    assert (await second.get(123))["leaderboard_daily_enabled"] is True

    await second.set(123, leaderboard_post_time="23:59")
    cfg = await first.get(123)
    assert cfg["leaderboard_daily_enabled"] is True
    assert cfg["leaderboard_post_time"] == "23:59"


async def test_singleton_get_cached_sync_reflects_other_caller_set(tmp_config_path):
    """동일 path 면 한 caller 의 set() 가 다른 caller 의 get_cached_sync() 에 즉시 반영."""
    from cogs.config_store import ConfigStore  # noqa: WPS433

    first = _store(tmp_config_path)
    second = ConfigStore(tmp_config_path)

    await first.set(7, tts_channel_id=42)
    assert second.get_cached_sync(7).get("tts_channel_id") == 42


async def test_get_skips_disk_when_unchanged(tmp_config_path, monkeypatch):
    """mtime 변동이 없으면 read_text 를 호출하지 않는다."""
    s = _store(tmp_config_path)
    await s.set(7, voice="ko-KR-SunHiNeural")

    calls = {"n": 0}
    real_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        calls["n"] += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)

    cfg1 = await s.get(7)
    cfg2 = await s.get(7)
    assert cfg1["voice"] == "ko-KR-SunHiNeural"
    assert cfg2["voice"] == "ko-KR-SunHiNeural"
    assert calls["n"] == 0  # 디스크 read 발생 안 함


async def test_get_cached_sync_no_io(tmp_config_path):
    """get_cached_sync 는 디스크 IO 없이 인메모리 사본을 반환."""
    s = _store(tmp_config_path)
    await s.set(42, tts_channel_id=999)
    cfg = s.get_cached_sync(42)
    assert cfg["tts_channel_id"] == 999
    # 미설정 길드는 빈 dict
    assert s.get_cached_sync(404) == {}


async def test_get_reloads_after_external_mtime_change(tmp_config_path):
    """싱글톤 외부 (다른 프로세스) 가 파일을 덮어쓰면 mtime 변동 → 다음 get 이 새 값."""
    s = _store(tmp_config_path)
    await s.set(11, voice="A")
    assert (await s.get(11))["voice"] == "A"

    # 외부 프로세스 시뮬레이션: 직접 파일을 덮어씀 (singleton _data 우회)
    await asyncio.sleep(0.02)  # mtime resolution 보장
    payload = json.dumps({"11": {"voice": "B"}}, ensure_ascii=False)
    tmp_config_path.write_text(payload, encoding="utf-8")

    cfg = await s.get(11)
    assert cfg["voice"] == "B"
