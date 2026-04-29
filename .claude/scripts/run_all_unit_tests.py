#!/usr/bin/env python3
"""
Stop hook — Claude 턴 종료 시 전체 단위 테스트 회귀 검사.

실패해도 turn 자체는 끝나야 하므로 exit 0로 마감.
실패 정보는 stdout으로 출력해 사용자/다음 턴 컨텍스트에 남는다.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "tests" / "unit"


def main() -> int:
    if not TESTS_DIR.exists():
        return 0

    try:
        import pytest  # noqa: F401
    except ImportError:
        # 아직 dev 환경 미구성 — 침묵
        return 0

    # 단위 테스트만, 짧은 출력
    cmd = [sys.executable, "-m", "pytest", str(TESTS_DIR),
           "-q", "--tb=line", "--no-header"]
    try:
        r = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                           text=True, timeout=80)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("[stop-hook] regression test skipped (env error)")
        return 0

    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        print("[stop-hook] ✅ unit regression passed")
    elif r.returncode == 5:
        # no tests collected — 정상 (Phase 1 직전 등)
        return 0
    else:
        print("[stop-hook] ❌ unit regression FAILED — please review:")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
