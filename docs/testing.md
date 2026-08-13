# Testing Pipeline & Hook Definition

본 문서는 **각 Phase가 완료되었음을 자동/수동으로 검증하는 파이프라인**을 정의합니다.
[`pipeline.md`](pipeline.md)의 8개 Phase와 1:1 대응합니다.

> 🎤 **Discord 라이브 환경에서의 수동 테스트 절차**는 [`discord-environment-testing.md`](discord-environment-testing.md)에 별도 정의되어 있습니다. 본 문서는 자동(단위) 파이프라인 + hook 동작에 집중.

---

## 1. 파이프라인 개요

```
┌──────────────────────────────────────────────────────────────┐
│ Trigger          │ Hook                  │ 실행 대상           │
├──────────────────┼───────────────────────┼─────────────────────┤
│ 세션 시작         │ SessionStart          │ Phase 상태 보고      │
│ 파일 편집/생성    │ PostToolUse           │ 해당 Phase 단위테스트│
│ Claude 턴 종료   │ Stop                  │ 전체 단위테스트(회귀) │
│ 수동 (RUN_LIVE=1)│ —                     │ live 단위테스트      │
│ 수동 체크리스트   │ —                     │ 통합/매뉴얼 검증     │
└──────────────────────────────────────────────────────────────┘
```

## 2. Hook 정의

### 2.1 활성화 방법
Hook 정의 본문은 [`.claude/hooks.example.json`](../.claude/hooks.example.json)에 보관합니다. 활성화하려면:

1. `.claude/hooks.example.json` 의 `"hooks"` 객체를 복사
2. `.claude/settings.local.json` 의 최상위에 붙여넣기 (기존 `permissions`와 형제 키)
3. Claude Code 세션 재시작 또는 `/config` 로 적용

```jsonc
// .claude/settings.local.json (병합 결과 예시)
{
  "permissions": { ... },
  "hooks": {                        // ← 여기 추가
    "PostToolUse": [...],
    "SessionStart": [...],
    "Stop": [...]
  }
}
```

### 2.2 Hook 종류

세 개의 hook이 활성화 후 항상 동작합니다.

| Hook | Matcher | 실행 스크립트 | timeout | 목적 |
|------|---------|--------------|---------|------|
| `PostToolUse` | `Edit\|Write\|MultiEdit` | `.claude/scripts/test_for_file.py` | 60s | 편집된 소스 → 매핑 테스트만 즉시 실행 |
| `SessionStart` | `startup\|resume\|clear` | `.claude/scripts/check_phase_status.py` | 30s | Phase별 진척/실패 상태 보고 |
| `Stop` | (전체) | `.claude/scripts/run_all_unit_tests.py` | 90s | 턴 종료 시 회귀 검사 |

### 2.3 비활성 상태에서도 동등 동작
Hook을 활성화하지 않아도 동일한 명령을 **수동 호출**하면 동일한 검증을 얻을 수 있습니다.

| 상황 | 수동 명령 |
|------|----------|
| Phase 상태 보기 | `python .claude/scripts/check_phase_status.py` |
| 한 파일 변경 후 그 Phase만 검사 | `python -m pytest tests/unit/test_<phase>.py -q` |
| 전체 회귀 검사 | `python .claude/scripts/run_all_unit_tests.py` |

### exit code 규약
- `0` — 통과 / 해당 없음 → 진행
- `2` — 단위 테스트 실패 (PostToolUse 한정) → Claude에 stderr 피드백 전달

### Stop hook은 비-블로킹
- exit 0로 마감하되 결과를 stdout에 남겨, 다음 사용자 턴에서 사람/AI가 모두 인지 가능

## 3. 테스트 레이어

```
tests/
├── conftest.py                ← sys.path / live skip 처리
├── unit/                      ← 자동 (hook이 실행)
│   ├── test_smoke.py              Phase 1
│   ├── test_config_store.py       Phase 2
│   ├── test_preprocess.py         Phase 3
│   ├── test_tts_engine.py         Phase 4 (mocked + live opt-in)
│   ├── test_audio_queue.py        Phase 5
│   ├── test_tts_cog.py            Phase 6 (선택, 없으면 ·)
│   ├── test_lol.py                롤 전적 API/저장/표시
│   └── test_valorant.py           발로란트 전적 API/저장/표시
├── integration/               ← 수동
│   ├── test_phase6_commands.md
│   └── test_phase7_events.md
└── manual/                    ← 수동
    └── phase8_release_checklist.md
```

### 자동 단위 테스트 — 정책
- 모듈이 아직 없으면 `pytest.mark.skipif`로 **자동 스킵** (False positive 방지)
- 외부 의존(Azure Speech HTTP, Discord) 은 **mocked**
- 실제 라이브 호출은 `@pytest.mark.live` + `RUN_LIVE=1` 환경변수로 분리

### 매핑 규칙 (PostToolUse hook)
편집한 파일 → 실행되는 테스트:
```
bot.py                    →  tests/unit/test_smoke.py
cogs/config_store.py      →  tests/unit/test_config_store.py
cogs/preprocess.py        →  tests/unit/test_preprocess.py
cogs/tts_engine.py        →  tests/unit/test_tts_engine.py
cogs/audio_queue.py       →  tests/unit/test_audio_queue.py
cogs/tts_cog.py           →  tests/unit/test_tts_cog.py
cogs/lol_{api,store,cog}.py
                          →  tests/unit/test_lol.py
cogs/valorant_{api,store,cog}.py
                          →  tests/unit/test_valorant.py
cogs/party_{store,cog}.py →  tests/unit/test_party.py
cogs/fortune_cog.py       →  tests/unit/test_fortune.py
tests/unit/test_X.py      →  자기 자신
```

## 4. Phase별 Definition of Done

| Phase | 자동 (단위) | 수동 (통합/체크리스트) | 완료 조건 |
|-------|-----------|-------------------|----------|
| 1 Foundation | `test_smoke.py` import 성공 | – | 자동 통과 |
| 2 Config Store | `test_config_store.py` 5건 | – | 자동 통과 |
| 3 Preprocess | `test_preprocess.py` 7건 | – | 자동 통과 |
| 4 TTS Engine | `test_tts_engine.py` mocked 3건 | (옵션) `live` 1건 | mocked 자동 통과 |
| 5 Audio Queue | `test_audio_queue.py` 2건 | – | 자동 통과 |
| 6 Slash Commands | `test_tts_cog.py` (선택) | `test_phase6_commands.md` 모두 ✅ | 자동 + 체크리스트 |
| 7 Event Handlers | – | `test_phase7_events.md` 모두 ✅ | 체크리스트 |
| 8 Polish | – | `phase8_release_checklist.md` 모두 ✅ | 체크리스트 |
| LoL Stats | `test_lol.py` | 실제 키가 있을 때 Discord에서 `/롤 검색` | 자동 + 선택 라이브 |
| VALORANT Stats | `test_valorant.py` | 실제 키가 있을 때 Discord에서 `/발로란트 검색` | 자동 + 선택 라이브 |
| Party Recruitment | `test_party.py` | Discord에서 생성·참가·취소·마감·재시작 복원 | 자동 + 수동 |
| Party Cleanup | `test_party.py` | 7일 경과 후 실제 정리 (`party_cleanup` 6시간 주기) | 자동 + 장기 수동 |
| Daily Fortune | `test_fortune.py` | Discord에서 개인 표시·공유 버튼 | 자동 + 수동 |

`check_phase_status.py` 가 자동 단위 테스트의 ✅/❌/·를 출력하므로,
세션 시작 시 즉시 "지금까지 어디까지 이상 없는지" 파악 가능.

## 5. 수동 실행 명령

| 목적 | 명령 |
|------|------|
| 전체 단위 테스트 | `python -m pytest tests/unit -q` |
| 특정 Phase | `python -m pytest tests/unit/test_config_store.py -q` |
| 라이브 테스트 (네트워크 도달) | `RUN_LIVE=1 python -m pytest tests/unit -m live` |
| Phase 상태 한 번 보기 | `python .claude/scripts/check_phase_status.py` |

## 6. 개발 환경 부트스트랩

```bash
# 1. 가상환경
python -m venv .venv
.venv\Scripts\activate     # Windows

# 2. 의존성 (개발용 = 런타임 + pytest)
pip install -r requirements-dev.txt

# 3. .env 작성
copy .env.example .env
# DISCORD_TOKEN=... 채우기

# 4. Phase 상태 확인
python .claude/scripts/check_phase_status.py
```

## 7. 수정 가이드

### 새 Phase / 새 모듈을 추가할 때
1. `cogs/<module>.py` 작성
2. `tests/unit/test_<module>.py` 작성 (모듈 미존재 시 skip 처리)
3. `.claude/scripts/test_for_file.py` 의 `MAPPING`에 한 줄 추가
4. `.claude/scripts/check_phase_status.py` 의 `PHASES`에 한 줄 추가
5. `docs/pipeline.md` 의 Phase 표 갱신
6. 본 문서의 매핑 표 갱신

### Hook을 일시 비활성하고 싶을 때
- `.claude/settings.local.json` 의 `hooks` 키를 통째로 주석 처리하거나 빈 객체로 교체
- 단, **Stop / PostToolUse hook은 회귀 방지의 핵심**이므로 영구 비활성은 권장하지 않음

## 8. 한계 및 트레이드오프

| 한계 | 영향 | 대응 |
|------|------|------|
| pytest 미설치 시 hook이 silent skip | 초기 환경에서 hook 효용 0 | `requirements-dev.txt` 설치를 README 1단계로 명시 |
| Discord 라이브 동작은 자동화 어려움 | Phase 6, 7은 매뉴얼 | 체크리스트로 표준화 |
| Stop hook timeout(90s) 초과 시 | 회귀 보고 누락 | 단위 테스트는 가볍게 유지, 무거운 통합은 별도 명령 |
| Windows/Unix 경로 차이 | hook 매퍼가 `/`로 정규화 | `_normalize` 함수로 흡수 |
