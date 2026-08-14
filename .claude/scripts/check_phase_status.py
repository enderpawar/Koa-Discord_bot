#!/usr/bin/env python3
"""
SessionStart hook — 현재까지 어느 Phase가 구현 + 테스트 통과 상태인지 보고한다.

각 Phase의 산출물 파일 존재 여부 + 단위 테스트 통과 여부로 상태를 결정한다.
출력은 Claude의 컨텍스트로 흘러가 다음 작업의 출발점이 된다.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

# Windows cp949 환경에서도 유니코드 출력이 깨지지 않게 강제
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PHASES = [
    ("Phase 1 Foundation",          ["bot.py", "requirements.txt"],         "tests/unit/test_smoke.py"),
    ("Phase 2 Config Store",        ["cogs/config_store.py"],               "tests/unit/test_config_store.py"),
    ("Phase 3 Preprocessing",       ["cogs/preprocess.py"],                 "tests/unit/test_preprocess.py"),
    ("Phase 4 TTS Engine",          ["cogs/tts_engine.py"],                 "tests/unit/test_tts_engine.py"),
    ("Phase 5 Audio Queue",         ["cogs/audio_queue.py"],                "tests/unit/test_audio_queue.py"),
    ("Phase 6 Slash Commands",      ["cogs/tts_cog.py"],                    "tests/unit/test_tts_cog.py"),
    ("Phase 7 Event Handlers",      ["cogs/tts_cog.py"],                    None),
    ("Phase 8 Polish & Docs",       ["README.md", ".env.example"],          None),
    (
        "Feature VALORANT Stats",
        ["cogs/valorant_api.py", "cogs/valorant_store.py", "cogs/valorant_cog.py"],
        "tests/unit/test_valorant.py",
    ),
    (
        "Feature LoL Stats",
        ["cogs/lol_api.py", "cogs/lol_store.py", "cogs/lol_cog.py"],
        "tests/unit/test_lol.py",
    ),
    (
        "Feature Party Recruitment",
        ["cogs/party_store.py", "cogs/party_cog.py"],
        "tests/unit/test_party.py",
    ),
    (
        "Feature Party Tier Badges",
        ["cogs/tier_store.py", "cogs/tier_badge.py"],
        "tests/unit/test_tier_badge.py",
    ),
    (
        "Feature Daily Fortune",
        ["cogs/fortune_cog.py"],
        "tests/unit/test_fortune.py",
    ),
    (
        "Feature Web Admin Auth",
        ["cogs/admin_login_store.py", "cogs/admin_cog.py", "cogs/web_admin_cog.py"],
        "tests/unit/test_admin_login_store.py",
    ),
]


def _exists(rel: str) -> bool:
    return (PROJECT_ROOT / rel).exists()


def _all_exist(rels: list[str]) -> bool:
    return all(_exists(r) for r in rels)


def _run_pytest(test_rel: str) -> str:
    """returns: '✅' | '❌' | '·' (없음/skip)"""
    p = PROJECT_ROOT / test_rel
    if not p.exists():
        return "·"
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(p),
                "-q",
                "--tb=no",
                "--no-header",
                f"--basetemp={PROJECT_ROOT / '.pytest-tmp' / p.stem}",
            ],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "?"
    if r.returncode == 0:
        return "✅"
    if r.returncode == 5:  # no tests collected
        return "·"
    return "❌"


def main() -> int:
    print("=" * 60)
    print("Discord TTS Bot — Phase Status")
    print("=" * 60)

    # pytest 설치 여부 사전 체크
    try:
        import pytest  # noqa: F401
        pytest_ok = True
    except ImportError:
        pytest_ok = False

    if not pytest_ok:
        print("ℹ pytest 미설치 — 'pip install -r requirements-dev.txt' 후 자동 테스트 활성화")
        print()

    for name, srcs, test in PHASES:
        src_ok = "✅" if _all_exist(srcs) else "·"
        test_status = _run_pytest(test) if (test and pytest_ok) else "·"
        # 산출물 + 테스트로 종합 판단
        if src_ok == "✅" and test_status == "✅":
            label = "DONE"
        elif src_ok == "✅" and test_status == "❌":
            label = "FAILING"
        elif src_ok == "✅":
            label = "PARTIAL"
        else:
            label = "TODO"
        print(f"  {src_ok} src   {test_status} test   [{label:7s}]   {name}")

    print()
    print("범례: ✅ 통과 / ❌ 실패 / · 해당없음 / ? 환경오류")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
