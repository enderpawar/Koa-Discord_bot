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
