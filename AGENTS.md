# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 프로젝트 개요

한국어 Discord TTS 봇. 런타임은 Python 3.10+, discord.py, aiohttp(Azure Speech REST), FFmpeg(PATH 등록 필수). 소스 코드는 Phase 단위로 점진적으로 구축되며, 작성 시점 기준 저장소에는 docs·tests·툴링만 존재합니다. `bot.py` 와 `cogs/*.py` 는 각 Phase 구현 시 생성됩니다.

## 자주 쓰는 명령

환경 셋업 (Windows 셸):
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt   # = requirements.txt + pytest 스택
copy .env.example .env                # 이후 DISCORD_TOKEN 입력
```

테스트:
```bash
python -m pytest tests/unit -q                          # 전체 단위 회귀
python -m pytest tests/unit/test_<phase>.py -v          # 특정 Phase
python -m pytest tests/unit/test_x.py::test_name -v     # 단일 테스트
RUN_LIVE=1 python -m pytest tests/unit -m live -q       # 옵트인 라이브 (Azure Speech/Discord)
```

파이프라인 상태 및 hook 스크립트 (hook 비활성 상태에서도 수동 실행 가능):
```bash
python .Codex/scripts/check_phase_status.py    # SessionStart hook — Phase 상태표
python .Codex/scripts/test_for_file.py         # PostToolUse hook — 파일→테스트 매퍼
python .Codex/scripts/run_all_unit_tests.py    # Stop hook — 비-블로킹 회귀
```

봇 실행 (Phase 1 완료 후):
```bash
python bot.py
```

## 아키텍처 — docs는 계약(contract)

구현은 `docs/` 의 세 문서군이 통제합니다. 이 문서들은 **단일 진실 공급원(SST)** 입니다. 코드와 docs가 충돌하면 임의로 한쪽을 고치지 말고 충돌을 표면화하세요.

- **`docs/pipeline.md`** — 순서가 정해진 8개 Phase (1 Foundation → 8 Polish). 각 Phase 행에 *적용 Skill*, *적용 Rule*, *산출물*, *검증* 이 명시됨. 문서 하단의 의존 그래프가 구현 순서를 결정하므로 건너뛰지 않습니다.
- **`docs/skills/`** (01–07) — 재사용 가능한 능력 단위. 각 Skill 은 *Inputs/Outputs*, *Implementation Sketch*, *Applied Rules*, *Validation* 을 정의. Sketch 는 출발점이며, 테스트 contract 와 어긋나면 테스트가 우선합니다.
- **`docs/rules/`** (01–07) — 봇 전반에 적용되는 불변 제약. 충돌 우선순위: **01 ≥ 02 ≥ 03 ≥ 04 ≥ 05 ≥ 06 ≥ 07** (루프 방지 > guild 격리 > 에러 복원력 > 시크릿 > async 정확성 > 로깅 > 한국어).

무엇을 구현·수정하든, 해당 Phase 행을 먼저 읽고 거기에 인용된 모든 Skill/Rule 을 읽으세요. 이게 곧 제약 조건입니다.

## 아키텍처 — 런타임 구성 (목표 형태)

`bot.py` (Phase 1) 는 `TTSBot(commands.Bot)` 을 생성하고 intents `message_content`, `members`, `voice_states` 를 켭니다. `setup_hook` 에서 `cogs.tts_cog` 로딩 + `tree.sync()` 1회 호출 (전역 sync 는 캐시 1시간이므로, `TEST_GUILD_ID` 가 있으면 `sync(guild=...)` 로 즉시 반영). `shutil.which("ffmpeg")` 가 None 이면 즉시 명확한 에러로 종료해야 합니다.

Cog (각각 = Phase 산출물 1개):
- `cogs/config_store.py` — guild→설정 JSON. `asyncio.Lock` + `os.replace` 로 원자적 쓰기.
- `cogs/preprocess.py` — 순수 함수 `clean_message(message) -> str`. 멘션/URL/마크다운 제거, 200자 truncate.
- `cogs/tts_engine.py` — `synthesize(text, voice) -> Path`. Azure Speech REST (`{region}.tts.speech.microsoft.com/cognitiveservices/v1`) + module-level `aiohttp.ClientSession` 재사용.
- `cogs/audio_queue.py` — guild별 `asyncio.Queue` + worker task. `voice_client.play()` 콜백을 `asyncio.Event` 로 직렬화. 5분 idle 시 자동 disconnect.
- `cogs/tts_cog.py` — 슬래시 명령 (`/읽기채널 /음성채널 /목소리 /입장 /퇴장 /상태`) + 이벤트 핸들러 (`on_message`, `on_voice_state_update`).

모든 변경 가능 상태는 `guild_id` 로 격리 (Rule 02). 메시지/voice 핸들러는 첫 줄에 봇/자기-자신 가드 배치 (Rule 01).

## 테스트 파이프라인

세 개의 Codex hook 이 검증을 자동화합니다. 정의는 `.Codex/hooks.example.json` 에 보관되어 있고, `.Codex/settings.local.json` 에 병합해야 활성화됩니다. 동일 스크립트는 수동 호출도 가능합니다.

| Hook | 트리거 | 스크립트 | 목적 |
|------|--------|---------|------|
| `PostToolUse` | `Edit\|Write\|MultiEdit` | `test_for_file.py` | 파일→테스트 매핑. 실패 시 exit **2** (블로킹) |
| `SessionStart` | startup/resume/clear | `check_phase_status.py` | Phase별 `DONE/FAILING/PARTIAL/TODO` 보고 |
| `Stop` | 턴 종료 | `run_all_unit_tests.py` | 전체 회귀, 비-블로킹 (항상 exit 0) |

파일→테스트 매핑 (`test_for_file.py::MAPPING` 에도 동일하게 정의됨):
```
bot.py                → tests/unit/test_smoke.py
cogs/config_store.py  → tests/unit/test_config_store.py
cogs/preprocess.py    → tests/unit/test_preprocess.py
cogs/tts_engine.py    → tests/unit/test_tts_engine.py
cogs/audio_queue.py   → tests/unit/test_audio_queue.py
cogs/tts_cog.py       → tests/unit/test_tts_cog.py
```

테스트는 `pytest-asyncio` 의 `auto` 모드를 사용합니다. 외부 의존(Azure Speech HTTP, Discord 게이트웨이/voice)은 단위 테스트에서 반드시 **mock** 처리하고, 실제 네트워크 호출은 `@pytest.mark.live` 로 분리되어 `RUN_LIVE=1` 일 때만 실행됩니다. `tests/conftest.py` 가 repo root 를 `sys.path` 에 추가하고, 기본적으로 live 테스트를 자동 skip 처리합니다.

Phase 6/7/8 은 단위 테스트로 완전 검증이 불가능합니다. 수동 체크리스트는 `tests/integration/test_phase{6,7}_*.md` 와 `tests/manual/phase8_release_checklist.md` 에 있습니다. Discord 라이브 환경 절차는 `docs/discord-environment-testing.md` 참조.

## Phase 구현 워크플로

`implement-phase` 스킬(`.Codex/skills/implement-phase/SKILL.md`)이 Phase 구현을 주도합니다. 이 스킬의 절대 규칙:

1. **호출당 단일 Phase**. 다음 Phase 의 심볼을 미리 만들지 않습니다.
2. **테스트가 곧 contract** — 코드가 테스트를 따르지, 그 반대가 아닙니다. red 를 green 으로 바꾸려고 assertion 을 완화하지 말고 먼저 사용자에게 확인하세요.
3. **Docs 우선** — 코드와 충돌하면 한쪽을 임의로 패치하지 말고 즉시 보고합니다.
4. 검증은 `pytest tests/unit/test_<phase>.py` 와 `check_phase_status.py` 둘 다 실행. 해당 Phase 가 `DONE` 으로 전환되어야 완료입니다.

새 모듈을 추가할 때는 다음을 **모두** 갱신: `cogs/<module>.py`, `tests/unit/test_<module>.py`, `test_for_file.py` 의 `MAPPING`, `check_phase_status.py` 의 `PHASES`, `docs/pipeline.md` 의 Phase 표, `docs/testing.md` 의 매핑 표.
