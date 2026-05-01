# Discord 한국어 TTS 봇

텍스트 채널의 메시지와 음성 채널의 입·퇴장을 한국어 음성으로 읽어주는 Discord 봇입니다.

- **TTS**: 지정된 텍스트 채널에 입력된 메시지를 [Azure Speech (Neural TTS)](https://learn.microsoft.com/azure/ai-services/speech-service/text-to-speech) 한국어 보이스로 합성하여 음성 채널에 재생
- **입·퇴장 알림**: 지정된 음성 채널에 사용자가 들어오거나 나갈 때 `{닉네임}님 입장/퇴장` 안내
- **자동 절전**: 5분간 메시지가 없으면 자동으로 음성 채널에서 퇴장, 다음 메시지에 자동 재입장

---

## 빠른 시작

```bash
# 1. 저장소 클론 후 진입
git clone <this-repo> discord-tts-bot && cd discord-tts-bot

# 2. 가상환경 + 의존성
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

# 3. .env 작성 (.env.example 복사 후 토큰 채우기)
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

# 4. 봇 실행
python bot.py
```

콘솔에 `logged in as <봇이름>` / `synced N slash commands (global)` 가 보이면 성공.

---

## 사전 요구사항

| 항목 | 버전/조건 |
|------|----------|
| Python | 3.10 이상 |
| FFmpeg | 시스템 PATH 등록 (음성 인코딩) |
| Discord 봇 토큰 | Developer Portal 에서 발급 |
| Azure Speech 키 + 리전 | [Azure Portal](https://portal.azure.com) 에서 Speech Service 리소스 생성 (F0 무료 티어 가능) |
| 인터넷 연결 | Azure Speech 엔드포인트 호출용 |

### FFmpeg 설치

| OS | 명령 |
|----|------|
| Windows | `winget install --id=Gyan.FFmpeg` (또는 `choco install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install -y ffmpeg` |

설치 확인: 새 터미널에서 `ffmpeg -version` 출력이 보여야 합니다. PATH 등록 안 되면 봇이 시작 시 `RuntimeError: FFmpeg가 PATH에 없습니다` 로 종료됩니다.

---

## Discord Application 생성

1. https://discord.com/developers/applications 접속 → **New Application**
2. 좌측 **Bot** 탭 → **Reset Token** 으로 토큰 발급 → `.env` 의 `DISCORD_TOKEN` 에 입력
3. 같은 탭의 **Privileged Gateway Intents** 에서 다음 두 개를 **반드시 활성화**:
   - ✅ Server Members Intent
   - ✅ Message Content Intent
4. 좌측 **OAuth2 → URL Generator**:
   - **Scopes**: `bot`, `applications.commands`
   - **Bot Permissions**: View Channels / Send Messages / Read Message History / Connect / Speak / Use Voice Activity / Use Slash Commands
5. 생성된 URL 을 브라우저에서 열어 봇을 서버에 초대

> **권한 누락 시 증상**: 슬래시 명령이 안 보이거나 (`applications.commands` 누락), 음성 채널에 입장 못 함 (`Connect/Speak` 누락), 메시지 본문이 빈 문자열로 도착 (Message Content Intent 누락).

---

## 환경변수 (`.env`)

`.env.example` 을 복사한 뒤 다음 값을 채웁니다.

```ini
DISCORD_TOKEN=<발급받은 봇 토큰>
LOG_LEVEL=INFO

AZURE_SPEECH_KEY=<Azure Speech 리소스의 Key 1 또는 Key 2>
AZURE_SPEECH_REGION=koreacentral

# 선택 — 개발 시 슬래시 명령을 특정 길드로 즉시 sync (전역 sync 는 캐시 1시간)
# TEST_GUILD_ID=123456789012345678
```

`.env` 는 절대 커밋하지 마세요. `.gitignore` 에 이미 등록되어 있습니다.

---

## 명령어

모든 명령은 슬래시 명령이며, 응답은 ephemeral (자기에게만 보임).

| 명령 | 권한 | 설명 |
|------|------|------|
| `/settts <text-channel>` | 채널 관리 | TTS 가 읽을 텍스트 채널 지정 |
| `/setvc <voice-channel>` | 채널 관리 | 봇이 음성 출력할 음성 채널 지정 |
| `/setvoice <voice>` | 채널 관리 | 보이스 변경 (4종 한국어 보이스) |
| `/join` | 일반 | 설정된 음성 채널로 즉시 입장 |
| `/leave` | 일반 | 음성 채널에서 퇴장 |
| `/status` | 일반 | 현재 설정 확인 |

**보이스 선택지**: `ko-KR-SunHiNeural` (여성, 차분 — 기본) / `ko-KR-InJoonNeural` (남성, 자연) / `ko-KR-BongJinNeural` (남성, 무게감) / `ko-KR-GookMinNeural` (남성, 친근).

### 일반적인 사용 흐름

```
/settts #tts-입력          ← TTS 입력용 텍스트 채널 지정
/setvc 🔊 일반음성          ← 봇이 출력할 음성 채널 지정
/setvoice 남성-자연         ← (선택) 보이스 변경
/join                      ← 봇이 음성 채널 입장
# 이후 #tts-입력 채널에 메시지 → 음성 채널에서 재생됨
```

설정은 `config.json` 에 guild 별로 저장되며 봇 재시작 후에도 유지됩니다.

---

## 동작 세부 사항

### 메시지 처리 (`on_message`)
- 다른 봇/webhook 메시지는 무시 (루프 방지)
- 멘션은 표시명으로, URL 은 `링크` 로, 마크다운은 제거하여 합성
- 200자 초과 시 잘리고 끝에 `…` 부착
- 빈 메시지 (첨부만)는 무반응

### 입·퇴장 알림 (`on_voice_state_update`)
- `/setvc` 로 지정한 음성 채널에 누군가 들어오거나 나갈 때만 알림
- mute/deafen/카메라 변경 등은 무시
- 봇 자기 자신의 입·퇴장은 무시 (루프 방지)

### 5분 idle 정책
- 큐에 메시지가 5분간 없으면 봇이 음성 채널에서 자동 퇴장
- 새 메시지가 들어오면 자동으로 다시 입장 후 재생
- "혼자 채널에 머무는 봇" 으로 인한 UX 노이즈 방지

---

## 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 시작 시 `FFmpeg가 PATH에 없습니다` | FFmpeg 미설치 | 위 §FFmpeg 설치 참고 |
| 슬래시 명령이 보이지 않음 | 전역 sync 캐시 (최대 1시간) | `.env` 에 `TEST_GUILD_ID=...` 추가 후 재시작 |
| `/settts` 권한 부족 안내 | 사용자가 채널 관리 권한 없음 | 서버 역할 설정에서 부여 |
| 음성 채널 입장 실패 | Connect / Speak 권한 부족 | OAuth 권한 재검토 또는 채널 권한 오버라이드 확인 |
| 메시지 본문이 빈 문자열로 도착 | Message Content Intent 미활성 | Developer Portal 에서 활성화 후 재시작 |
| 입·퇴장 이벤트가 오지 않음 | Server Members Intent 미활성 | Developer Portal 에서 활성화 후 재시작 |
| 봇이 자기 입장도 안내함 | 비정상 (Rule 01 위반) | 코드 이슈 — 이슈 트래커에 보고 |
| 한글이 깨져 발음됨 | 영문 보이스 사용 | `/setvoice` 로 `ko-KR-*` 보이스 선택 |
| 합성은 되는데 무음 | 음성 region 이슈 | Discord 서버 설정에서 region 변경 후 재시도 |

`LOG_LEVEL=DEBUG` 로 환경변수 변경 후 재시작하면 상세 로그가 출력됩니다.

---

## 24시간 운영 (배포)

본 봇은 단일 Python 프로세스이므로 별도 백엔드 서버 없이 **어디든 켜져 있는 컴퓨터** 에서만 동작합니다.

| 옵션 | 비용 | 가이드 |
|------|------|--------|
| Railway | 무료 크레딧 $5/월 (실측 ~$2–3 사용) | [`docs/deploy-railway.md`](docs/deploy-railway.md) |
| Oracle Cloud Always Free VM | 평생 무료 | systemd unit + git pull |
| 본인 PC | 전기료 | `python bot.py` (PC 끄면 봇 꺼짐) |

---

## 개발자 가이드

본 프로젝트는 **8개 Phase** 로 점진적 구축되었습니다. 코드 구조·테스트 파이프라인·구현 워크플로는 다음 문서를 참고하세요.

- **`CLAUDE.md`** — 명령 / 아키텍처 / Phase 워크플로 요약 (Claude Code 용)
- **`docs/pipeline.md`** — 8 Phase 정의와 의존 그래프
- **`docs/skills/`** — 재사용 능력 단위 (bot 부트스트랩, config 저장, 메시지 정제, TTS, 큐, voice, 슬래시)
- **`docs/rules/`** — 봇 전반의 불변 제약 (루프 방지 · guild 격리 · 복원력 · 보안 · async · 로깅 · 한국어)
- **`docs/testing.md`** — 자동/수동 테스트 파이프라인
- **`docs/discord-environment-testing.md`** — 라이브 환경 검증 절차

### 테스트
```bash
pip install -r requirements-dev.txt
python -m pytest tests/unit -q                       # 단위 회귀
RUN_LIVE=1 python -m pytest tests/unit -m live -q    # 라이브 (Azure Speech 도달)
python .claude/scripts/check_phase_status.py         # Phase 상태표
```

수동 체크리스트:
- `tests/integration/test_phase6_commands.md` — 슬래시 명령 6종
- `tests/integration/test_phase7_events.md` — TTS / 입퇴장 / 봇 루프 방지
- `tests/manual/phase8_release_checklist.md` — 릴리즈 준비 점검
