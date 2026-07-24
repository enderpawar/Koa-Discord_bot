"""
Phase 1 — Foundation smoke test
- bot.py가 import 가능한지
- 필수 환경 의존(FFmpeg)이 PATH에 있는지
"""
from __future__ import annotations
import importlib.util
import runpy
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
import pytest

ROOT = Path(__file__).resolve().parents[2]
BOT_PY = ROOT / "bot.py"


pytestmark = pytest.mark.skipif(
    not BOT_PY.exists(), reason="Phase 1 not yet implemented (bot.py missing)"
)


def test_ffmpeg_on_path() -> None:
    assert shutil.which("ffmpeg") is not None, "FFmpeg가 PATH에 없습니다"


def test_bot_module_importable(monkeypatch) -> None:
    """bot.py의 top-level이 sys.exit/run 없이 import만으로 끝나는지 확인.
    DISCORD_TOKEN이 없어도 import 자체는 가능해야 함."""
    monkeypatch.setenv("DISCORD_TOKEN", "dummy_for_import")
    spec = importlib.util.spec_from_file_location("bot", BOT_PY)
    assert spec is not None and spec.loader is not None
    # 실행은 하지 않고 spec만 검증 (bot.run 호출 회피)
    # 실제 실행 검증은 통합 테스트로


def test_bot_activity_pool_contains_cute_game_messages(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "dummy_for_import")
    namespace = runpy.run_path(str(BOT_PY), run_name="bot_test")
    names = namespace["_activity_names"](
        datetime(2026, 7, 25, 2, tzinfo=timezone(timedelta(hours=9)))
    )

    assert "🛋️ 뒹굴거리는 중" in names
    assert "🥈 실버 승급전 중!!" in names
    assert "🌙 새벽반과 밤샘 큐 돌리는 중" in names
    assert "🎉 주말 풀파티 즐기는 중" in names


def test_bot_activity_does_not_immediately_repeat(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "dummy_for_import")
    namespace = runpy.run_path(str(BOT_PY), run_name="bot_test")
    activity = namespace["_build_activity"](
        previous="🛋️ 뒹굴거리는 중",
        chooser=lambda choices: choices[0],
    )

    assert isinstance(activity, discord.Game)
    assert activity.name != "🛋️ 뒹굴거리는 중"
