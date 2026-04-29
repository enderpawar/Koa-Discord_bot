# Discord TTS Bot — Implementation Pipeline

> 본 파이프라인은 [`plan-to-make-discord-logical-bachman.md`](C:\Users\user\.claude\plans\plan-to-make-discord-logical-bachman.md)을 기반으로, 검증 가능한 단위로 잘게 쪼갠 단계별 구현 순서입니다.

## 원칙
- **점진적 구축**: 각 Phase 완료 후 단독 검증이 가능해야 함
- **하향식 의존**: 하위 Phase는 상위 Phase가 노출하는 인터페이스만 사용
- **Skill 매핑**: 각 Phase는 `docs/skills/`의 1개 이상 Skill을 구현
- **Rule 준수**: 모든 Phase에서 `docs/rules/`의 규칙을 적용
- **자동 검증**: 각 Phase는 [`testing.md`](testing.md)에 정의된 hook + 테스트 파이프라인으로 검증 가능
- **라이브 검증**: 실제 Discord 환경에서의 검증 절차는 [`discord-environment-testing.md`](discord-environment-testing.md) 참조

---

## Phase 1 — Project Scaffold
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`01-bot-foundation`](skills/01-bot-foundation.md) |
| 적용 Rule | [`04-secrets-and-security`](rules/04-secrets-and-security.md), [`06-logging-standards`](rules/06-logging-standards.md) |
| 산출물 | `requirements.txt`, `.env.example`, `.gitignore`, `bot.py`, `cogs/__init__.py` |
| 핵심 작업 | discord.py `Bot` 인스턴스, intents 설정, `.env` 로딩, `setup_hook`에서 cog 로드 + 슬래시 sync, 로거 설정 |
| 검증 | `python bot.py` → `Logged in as <name>` / `Synced 0 commands` 콘솔 출력 |

## Phase 2 — Config Store
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`02-config-store`](skills/02-config-store.md) |
| 적용 Rule | [`02-guild-isolation`](rules/02-guild-isolation.md), [`05-async-correctness`](rules/05-async-correctness.md) |
| 산출물 | `cogs/config_store.py`, 자동 생성되는 `config.json` |
| 핵심 작업 | `get(guild_id)`, `set(guild_id, **fields)`, `save()` API. `asyncio.Lock` + `os.replace`로 원자적 쓰기 |
| 검증 | 단위 테스트(`python -c`로 임시 set/get) → `config.json`에 정상 직렬화 확인 |

## Phase 3 — Message Preprocessing
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`03-message-preprocessing`](skills/03-message-preprocessing.md) |
| 적용 Rule | [`07-korean-text`](rules/07-korean-text.md), [`01-bot-loop-prevention`](rules/01-bot-loop-prevention.md) |
| 산출물 | `cogs/preprocess.py` (순수 함수) |
| 핵심 작업 | `clean_message(message) -> str`. 멘션/URL/이모지/마크다운/공백/길이 처리 (200자 truncate) |
| 검증 | `python -m unittest`로 테스트 케이스 5개 이상 (멘션, URL, 마크다운, 길이 초과, 빈 문자열) 통과 |

## Phase 4 — TTS Engine
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`04-tts-engine`](skills/04-tts-engine.md) |
| 적용 Rule | [`03-error-resilience`](rules/03-error-resilience.md), [`05-async-correctness`](rules/05-async-correctness.md) |
| 산출물 | `cogs/tts_engine.py` |
| 핵심 작업 | `synthesize(text, voice) -> Path` (mp3 임시파일). edge-tts `Communicate.stream()` 사용 |
| 검증 | 단독 스크립트로 "안녕하세요" 합성 → 외부 플레이어로 재생 확인 |

## Phase 5 — Audio Queue
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`05-audio-queue`](skills/05-audio-queue.md), [`06-voice-management`](skills/06-voice-management.md) |
| 적용 Rule | [`02-guild-isolation`](rules/02-guild-isolation.md), [`05-async-correctness`](rules/05-async-correctness.md), [`03-error-resilience`](rules/03-error-resilience.md) |
| 산출물 | `cogs/audio_queue.py` |
| 핵심 작업 | guild별 `asyncio.Queue` + worker task. `voice_client.play()` 콜백 → `asyncio.Event`로 sequential 보장. 5분 idle 시 자동 disconnect |
| 검증 | 봇이 음성 채널에 입장한 상태에서 enqueue 3건 → 순서대로 재생 |

## Phase 6 — Slash Commands
| 항목 | 내용 |
|------|------|
| 적용 Skill | [`07-slash-commands`](skills/07-slash-commands.md) |
| 적용 Rule | [`04-secrets-and-security`](rules/04-secrets-and-security.md), [`02-guild-isolation`](rules/02-guild-isolation.md) |
| 산출물 | `cogs/tts_cog.py` (명령어 부분) |
| 핵심 작업 | `/settts`, `/setvc`, `/setvoice`, `/join`, `/leave`, `/status` 6개. `Manage Channels` 권한 체크 |
| 검증 | 테스트 서버에서 6개 명령어 실행 → 응답·설정 반영 확인 |

## Phase 7 — Event Handlers
| 항목 | 내용 |
|------|------|
| 적용 Skill | 통합 (`tts_cog`) |
| 적용 Rule | [`01-bot-loop-prevention`](rules/01-bot-loop-prevention.md), [`07-korean-text`](rules/07-korean-text.md) |
| 산출물 | `cogs/tts_cog.py` (이벤트 부분) |
| 핵심 작업 | `on_message` → 전처리 → 큐 enqueue. `on_voice_state_update` → 입/퇴장 판정 → "{display_name}님 입장/퇴장" enqueue |
| 검증 | 텍스트 입력 → 음성 재생, 다른 사용자 입/퇴장 → 알림 음성 재생 |

## Phase 8 — Polish & Documentation
| 항목 | 내용 |
|------|------|
| 적용 Skill | 전체 |
| 적용 Rule | [`03-error-resilience`](rules/03-error-resilience.md), [`06-logging-standards`](rules/06-logging-standards.md) |
| 산출물 | `README.md`, 다듬어진 에러 메시지, 정돈된 로그 |
| 핵심 작업 | OAuth 초대 URL 가이드, FFmpeg 설치 가이드, 트러블슈팅, 5분 idle 정책 명문화 |
| 검증 | 신규 사용자가 README만 보고 봇 실행 가능 |

---

## 의존 그래프

```
Phase 1 (foundation)
  ├─→ Phase 2 (config)
  ├─→ Phase 3 (preprocess)
  └─→ Phase 4 (tts)
        └─→ Phase 5 (queue) ←─ Phase 4
              ├─→ Phase 6 (slash) ←─ Phase 2
              └─→ Phase 7 (events) ←─ Phase 2, 3, 5
                    └─→ Phase 8 (polish)
```

## 마일스톤

| 마일스톤 | 포함 Phase | 정의 |
|----------|-----------|------|
| **M1: Static Components** | 1–3 | 비음성 파트 모두 단위 테스트 통과 |
| **M2: Audio Output** | 4–5 | 봇이 한국어 음성을 채널에 출력 가능 |
| **M3: Feature Complete** | 6–7 | 요구사항 1, 2 모두 동작 |
| **M4: Release Ready** | 8 | 외부 사용자 배포 가능 |

## 위험 요소 (사전 대응)

| 위험 | 영향 | 대응 |
|------|------|------|
| FFmpeg 미설치 | 음성 재생 전 크래시 | Phase 1에서 시작 시 `shutil.which("ffmpeg")` 체크 후 명확한 에러 |
| edge-tts 일시 장애 | 합성 실패 | Phase 4에서 1회 retry + 실패 로그, 큐는 다음 항목으로 진행 |
| Discord API rate limit | 슬래시 명령 sync 실패 | Phase 1의 `setup_hook`에서 1회만 sync |
| 봇 토큰 노출 | 보안 사고 | Phase 1에서 `.gitignore`에 `.env` 등록, `.env.example`만 배포 |
| voice client 끊김 | TTS 재생 중단 | Phase 5에서 재연결 로직 + 큐 보존 |
