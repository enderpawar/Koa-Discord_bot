"""
공용 pytest fixture.
- repo root를 sys.path에 추가해 cogs / bot 모듈 import 가능하게 함
- 임시 config 디렉토리 fixture 제공
- live 마커는 기본 스킵 (CI 안정성)
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# live 테스트가 .env 의 AZURE_SPEECH_* 등을 읽을 수 있도록 로드.
# 단위 테스트는 monkeypatch.setenv 가 우선하므로 영향 없음.
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(ROOT / ".env")
except ImportError:
    pass


def pytest_collection_modifyitems(config, items):
    """live 마커는 RUN_LIVE=1 일 때만 실행."""
    if os.getenv("RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live tests skipped (set RUN_LIVE=1)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """ConfigStore에 넘길 임시 JSON 경로."""
    return tmp_path / "config.json"
