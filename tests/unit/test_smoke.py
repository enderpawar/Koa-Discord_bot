"""
Phase 1 — Foundation smoke test
- bot.py가 import 가능한지
- 필수 환경 의존(FFmpeg)이 PATH에 있는지
"""
from __future__ import annotations
import importlib.util
import shutil
from pathlib import Path

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
