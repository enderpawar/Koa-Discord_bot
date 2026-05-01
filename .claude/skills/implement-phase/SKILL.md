---
name: implement-phase
description: Implements ONE Phase of the Discord TTS Bot defined in docs/pipeline.md. Reads the Phase's referenced Skills (docs/skills/) and Rules (docs/rules/), satisfies the test contract in tests/unit/, and verifies via .claude/scripts/check_phase_status.py. Use when the user says "implement phase N", "Phase N 구현", "phase N 진행", "다음 phase", or refers to building components mapped to a specific Phase. Always implements only ONE Phase per invocation.
---

# Skill: Implement Phase

본 스킬은 Discord TTS Bot의 **단일 Phase**(1~8)를 체계적으로 구현한다. 사전에 정의된 docs(pipeline·skills·rules)와 tests/unit/의 contract를 만족시키는 것이 목표.

## When to invoke
- 사용자가 `/implement-phase` 또는 `Phase N 구현` 등으로 호출
- 사용자가 "다음 Phase 진행해", "Phase 4 시작", "implement phase 5" 같은 말을 한 경우
- 단, 한 번에 **한 Phase만** 구현. 연속 호출은 사용자 명시적 요청 시.

## Workflow

### Step 1 — Resolve target Phase
1. 사용자 입력에서 Phase 번호(1–8) 추출.
2. 번호가 명시되지 않으면 다음 명령으로 현황 파악:
   ```
   python .claude/scripts/check_phase_status.py
   ```
3. 가장 낮은 `TODO` Phase를 후보로 제시하고 사용자 확인 받음.
4. **이미 DONE 인 Phase를 다시 구현하려는 경우** 사용자에게 의도 확인 (덮어쓰기 위험).

### Step 2 — Gather context (READ ONLY)
대상 Phase N에 대해 다음을 모두 읽는다. 생략 금지.

| 파일 | 목적 |
|------|------|
| `docs/pipeline.md` Phase N 섹션 | 산출물·검증·적용 Skill/Rule |
| `docs/skills/<NN>-*.md` (Phase가 참조하는 Skill 모두) | 인터페이스, Implementation Sketch, Applied Rules |
| `docs/rules/<NN>-*.md` (Phase가 참조하는 Rule 모두) | 불변 제약 |
| `tests/unit/test_<phase>.py` | 만족시켜야 할 contract |
| 이전 Phase의 소스 (`bot.py`, `cogs/*.py`) | 재사용·일관성 |
| `plan-to-make-discord-logical-bachman.md` | 전체 플랜 컨텍스트 (선택) |

> 단축 시도 금지: docs는 contract와 constraint를 동시에 정의한다.

### Step 3 — Plan minimum implementation
다음을 머릿속/스크래치패드에서 정리:
- **만들/수정할 파일 목록** (정확한 경로)
- **만족시켜야 할 테스트 케이스 목록** (`pytest --collect-only tests/unit/test_<phase>.py -q` 활용)
- **적용할 Rule 체크리스트** (Skill 문서의 Applied Rules 섹션 그대로)
- **이전 Phase에서 노출된 인터페이스** 중 사용할 것

### Step 4 — Implement
- `Write` / `Edit`로 소스 파일 작성
- Skill 문서의 *Implementation Sketch*를 출발점으로 삼되, 테스트가 강제하는 시그니처/동작을 우선
- 코멘트는 *why*만, *what*은 식별자가 설명 (CLAUDE.md / project rules 준수)
- 비동기 IO는 항상 `await`, blocking 호출 금지 (Rule 05)
- 모든 변경 가능 상태는 `guild_id`로 격리 (Rule 02)
- 봇/봇간 루프 방지 가드를 핸들러 첫 줄에 배치 (Rule 01)

### Step 5 — Verify
```bash
python -m pytest tests/unit/test_<phase>.py -v
python .claude/scripts/check_phase_status.py
```
다음을 모두 만족해야 완료:
- 단위 테스트 전부 통과 (skip 제외)
- `check_phase_status.py` 출력에서 해당 Phase가 `DONE`
- Phase 6/7/8은 통합 체크리스트(`tests/integration/test_phase{6,7}_*.md`, `tests/manual/phase8_release_checklist.md`) 경로를 사용자에게 안내

### Step 6 — Report
짧은 보고:
- ✅ 만든/수정한 파일 (경로 + 라인 수)
- ✅ pytest 결과 요약 (`N passed, M skipped`)
- ✅ Phase 상태 변화 (`TODO → DONE`)
- ⚠ 추가 수동 검증이 필요한 경우 그 경로 명시
- 🔜 다음 Phase 후보 1줄 (사용자가 트리거할 때까지 대기)

## Per-Phase Reference

| Phase | Source files | Unit tests | Manual? |
|-------|-------------|-----------|---------|
| 1 | `bot.py`, `requirements.txt`, `.env.example`, `.gitignore`, `cogs/__init__.py` | `test_smoke.py` | – |
| 2 | `cogs/config_store.py` | `test_config_store.py` | – |
| 3 | `cogs/preprocess.py` | `test_preprocess.py` | – |
| 4 | `cogs/tts_engine.py` | `test_tts_engine.py` (mocked) + 옵션 live | – |
| 5 | `cogs/audio_queue.py` | `test_audio_queue.py` | – |
| 6 | `cogs/tts_cog.py` (commands part) | (선택) `test_tts_cog.py` | `tests/integration/test_phase6_commands.md` |
| 7 | `cogs/tts_cog.py` (events part) | – | `tests/integration/test_phase7_events.md` |
| 8 | `README.md`, polish | – | `tests/manual/phase8_release_checklist.md` |

## Rules of Engagement (이 스킬이 절대 어기지 말 것)

1. **단일 Phase**. 한 번에 하나만. 다음 Phase는 사용자 재호출 후 진행.
2. **테스트는 Contract**. 테스트가 코드를 따르는 게 아니라 코드가 테스트를 따른다. 테스트 수정은 사용자 동의 필수.
3. **Docs 우선**. Skill/Rule 문서와 코드가 충돌하면 사용자에게 즉시 보고하고 결정 받음. 임의로 doc/code 한쪽 편집 금지.
4. **외부 의존 격리**. 단위 테스트에서 Discord/Azure Speech 라이브 호출 금지. `live` 마커로만 허용.
5. **선제 구현 금지**. 다음 Phase의 함수·클래스를 미리 만들지 않음.
6. **자기 의심**. 테스트가 통과해도 Rule 체크리스트를 다시 훑어 확인.

## Anti-patterns

| ❌ Bad | ✅ Good |
|-------|--------|
| Phase 5 구현 중 Phase 6의 슬래시 명령을 미리 정의 | Phase 5는 큐만, 슬래시는 Phase 6 호출 시 |
| 테스트가 실패하니 테스트의 assertion을 완화 | 테스트가 정확한지 사용자에게 확인 후 진행 |
| 단위 테스트에서 Azure Speech REST 를 실제 호출 | `unittest.mock.patch`로 `_get_session` mocking |
| `print(...)`로 디버깅 흔적 남김 | `logging.getLogger(__name__).info(...)` |
| `time.sleep()` 사용 | `await asyncio.sleep()` |
| `member.bot` 가드 누락 | 모든 메시지/voice 핸들러 첫 줄에 가드 |

## Hook 통합

이 스킬을 사용할 때 PostToolUse hook이 활성화되어 있다면(`.claude/hooks.example.json` 참조), 파일을 Write/Edit 할 때마다 매핑된 단위 테스트가 자동 실행된다. 실패 시 stderr가 컨텍스트로 들어와 즉시 수정 가능.

Hook 비활성 상태에서도 동일 검증을 수동으로 수행:
```bash
python -m pytest tests/unit/test_<phase>.py -v
```

## 시작 템플릿 (스킬 호출 시 첫 응답)

```
Phase N — <이름> 구현을 시작합니다.

[Step 1] 컨텍스트 로딩 중...
- 읽은 문서: docs/pipeline.md, docs/skills/<N>-*.md (M개), docs/rules/<N>-*.md (K개)
- 테스트 contract: tests/unit/test_<phase>.py (T개 케이스)

[Step 2] 산출물 계획:
- <file_1>
- <file_2>
...

진행할까요? (y/N)
```

> 단순 Phase(1, 2, 3)는 확인 생략하고 즉시 구현 가능. Phase 5+ 처럼 영향 범위가 크면 반드시 확인 받기.
