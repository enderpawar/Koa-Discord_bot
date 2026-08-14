#!/usr/bin/env python3
"""
PostToolUse hook — 편집/생성된 파일에 매핑되는 단위 테스트만 실행한다.

stdin으로 hook context(JSON)를 받음. tool_input.file_path를 파싱해
관련 테스트만 빠르게 돌리고 실패 시 exit 2로 Claude에 피드백을 전달한다.

exit codes:
  0  성공/대상 없음   → 진행
  2  테스트 실패     → Claude에 stderr 전달 (블로킹)
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# pytest 미설치 시 silent skip (Phase 1 전 setup 단계 보호)
try:
    import pytest  # noqa: F401
except ImportError:
    sys.exit(0)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 소스 파일 → 단위 테스트 매핑 (정규식)
MAPPING: list[tuple[str, str]] = [
    (r"(^|/)bot\.py$",                "tests/unit/test_smoke.py"),
    (r"cogs/config_store\.py$",       "tests/unit/test_config_store.py"),
    (r"cogs/admin_login_store\.py$",  "tests/unit/test_admin_login_store.py"),
    (r"cogs/public_url\.py$",         "tests/unit/test_public_url.py"),
    (r"cogs/preprocess\.py$",         "tests/unit/test_preprocess.py"),
    (r"cogs/tts_engine\.py$",         "tests/unit/test_tts_engine.py"),
    (r"cogs/audio_queue\.py$",        "tests/unit/test_audio_queue.py"),
    (r"cogs/tts_cog\.py$",            "tests/unit/test_tts_cog.py"),
    (r"cogs/valorant_api\.py$",       "tests/unit/test_valorant.py"),
    (r"cogs/valorant_store\.py$",     "tests/unit/test_valorant.py"),
    (r"cogs/valorant_cog\.py$",       "tests/unit/test_valorant.py"),
    (r"cogs/lol_api\.py$",            "tests/unit/test_lol.py"),
    (r"cogs/lol_store\.py$",          "tests/unit/test_lol.py"),
    (r"cogs/lol_cog\.py$",            "tests/unit/test_lol.py"),
    (r"cogs/party_store\.py$",         "tests/unit/test_party.py"),
    (r"cogs/party_cog\.py$",           "tests/unit/test_party.py"),
    (r"cogs/fortune_cog\.py$",         "tests/unit/test_fortune.py"),
    # 테스트 파일 자체를 편집한 경우 → 그 테스트 다시 실행
    (r"tests/unit/(test_[a-z_]+)\.py$", r"tests/unit/\1.py"),
]


def _read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _normalize(p: str) -> str:
    return p.replace("\\", "/")


def _match_test(file_path: str) -> str | None:
    fp = _normalize(file_path)
    for pat, target in MAPPING:
        m = re.search(pat, fp)
        if m:
            # backref 지원
            return re.sub(pat, target, fp[m.start():])
    return None


def main() -> int:
    ctx = _read_stdin_json()
    file_path = ((ctx.get("tool_input") or {}).get("file_path")) or ""
    if not file_path:
        return 0

    test_rel = _match_test(file_path)
    if not test_rel:
        return 0

    test_path = PROJECT_ROOT / test_rel
    if not test_path.exists():
        # 아직 해당 테스트 파일이 없는 경우 (정상)
        return 0

    cmd = [sys.executable, "-m", "pytest", str(test_path),
           "-q", "--tb=short", "--no-header"]
    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=50
        )
    except subprocess.TimeoutExpired:
        print(f"[hook] pytest timeout for {test_rel}", file=sys.stderr)
        return 2

    if result.returncode == 0:
        # 성공은 stdout으로 짧게 알림
        print(f"[hook] ✅ {test_rel}")
        return 0

    # pytest exit 5 = "no tests collected" → 통과로 취급
    if result.returncode == 5:
        return 0

    print(f"[hook] ❌ unit tests failed: {test_rel}", file=sys.stderr)
    print(result.stdout, file=sys.stderr)
    print(result.stderr, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
