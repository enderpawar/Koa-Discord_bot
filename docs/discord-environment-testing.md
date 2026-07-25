# Discord 라이브 환경 테스트 가이드

> 본 문서는 **실제 Discord 서버에서 봇을 동작시켜 검증하는 절차**를 정의합니다.
> 단위 테스트(`tests/unit/`)가 다룰 수 없는 영역 — 실제 음성 송출, 슬래시 명령 sync, 이벤트 게이트웨이, 권한 — 을 다룹니다.
>
> 자동화된 단위 테스트 정책은 [`testing.md`](testing.md) 참조.

---

## 0. 이 문서를 읽어야 할 시점

| 시점 | 이유 |
|------|------|
| Phase 4 live smoke (`RUN_LIVE=1`) | Azure Speech 실제 합성 검증 |
| Phase 5 audio queue 통합 | 실제 음성 채널에서의 직렬 재생 |
| Phase 6 slash commands | Discord에서만 검증 가능 |
| Phase 7 event handlers | `on_message` / `on_voice_state_update` 게이트웨이 |
| Phase 8 release | 신규 사용자 시뮬레이션 |
| 회귀 검증 | 라이브러리 업그레이드, intents 변경 후 |

---

## 1. 사전 준비

### 1.1 Dev 봇 계정 (운영 봇과 분리)

운영 봇 토큰으로 라이브 테스트하지 않는다. 별도 dev 애플리케이션 생성:

1. https://discord.com/developers/applications 접속
2. **New Application** → 이름 예: `TTS Bot Dev`
3. 좌측 **Bot** 탭:
   - **Reset Token** → 생성된 토큰을 `.env`에 저장
   - **Privileged Gateway Intents** 모두 ON:
     - ✅ Presence Intent (선택)
     - ✅ Server Members Intent **(필수)**
     - ✅ Message Content Intent **(필수)**
4. 좌측 **OAuth2 → URL Generator**:
   - **Scopes**: ✅ `bot`, ✅ `applications.commands`
   - **Bot Permissions**:
     - ✅ View Channels
     - ✅ Send Messages
     - ✅ Read Message History
     - ✅ Connect
     - ✅ Speak
     - ✅ Use Voice Activity
     - ✅ Use Slash Commands
   - 생성된 URL을 브라우저에서 열어 **테스트 서버에 초대**

### 1.2 테스트 서버 (Test Guild)

운영 서버와 분리된 별도 서버 사용:

| 채널 | 형태 | 목적 |
|------|------|------|
| `#일반` | 텍스트 | 일반 트래픽 (봇 무반응 검증용) |
| `#tts-입력` | 텍스트 | TTS 메시지 입력 채널 |
| `🔊 일반음성` | 음성 | 봇 출력 + 입/퇴장 알림 대상 |
| `🔊 보조음성` | 음성 | 채널 이동·다중 VC 시나리오 |

> 한국어 닉네임 검증을 위해 테스트 서버에서 자기 닉네임을 한글로 변경(예: `김민수`)해 두기.

### 1.3 봇 권한 확인 체크리스트

테스트 시작 전 다음을 모두 확인:

- [ ] 봇이 테스트 서버 멤버 목록에 표시됨
- [ ] 봇이 모든 채널을 볼 수 있음 (서버 설정에서 봇 역할 확인)
- [ ] 봇이 `🔊 일반음성`에 Connect/Speak 권한
- [ ] `python bot.py` 실행 시 `Synced N slash commands` 콘솔 로그 (N ≥ 6)

---

## 2. 환경 변수와 라이브 모드

`.env` 예시:

```ini
DISCORD_TOKEN=<dev 봇 토큰>
LOG_LEVEL=INFO

# 라이브 테스트 옵션
RUN_LIVE=1                       # @pytest.mark.live 활성화
TEST_GUILD_ID=123456789012345678 # 슬래시 명령을 즉시 sync (전역 sync는 최대 1시간 대기)
```

`bot.py` 가 `TEST_GUILD_ID` 가 있으면 해당 guild로 즉시 sync 하도록 분기:

```python
async def setup_hook(self) -> None:
    await self.load_extension("cogs.tts_cog")
    if gid := os.getenv("TEST_GUILD_ID"):
        guild = discord.Object(id=int(gid))
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)   # 즉시 반영
    else:
        await self.tree.sync()              # 운영: 전역 sync (캐시 1시간)
```

---

## 3. 테스트 매트릭스 (Phase × 라이브 시나리오)

각 Phase는 자동(단위) 테스트로 다 못 잡는 영역이 있고, 본 매트릭스로 라이브 검증을 보충한다.

| Phase | 자동(단위) | 라이브 시나리오 | 체크리스트 위치 |
|-------|-----------|-----------------|----------------|
| 4 | mocked 합성 | 실제 Azure Speech 도달, 음질 청취 | §4.1 |
| 5 | mocked vc | 실제 VC 입장 → 재생 → 직렬화 | §4.2 |
| 6 | (옵션) | 6개 슬래시 명령 동작 | [`tests/integration/test_phase6_commands.md`](../tests/integration/test_phase6_commands.md) |
| 7 | – | on_message / on_voice_state_update | [`tests/integration/test_phase7_events.md`](../tests/integration/test_phase7_events.md) |
| 8 | – | 신규 사용자 e2e | [`tests/manual/phase8_release_checklist.md`](../tests/manual/phase8_release_checklist.md) |

---

## 4. 핵심 시나리오 상세

### 4.1 TTS Engine — 라이브 합성 (Phase 4)

```bash
RUN_LIVE=1 python -m pytest tests/unit/test_tts_engine.py -m live -v
```

기대:
- exit 0
- 임시 mp3 파일 크기 > 5KB
- 외부 플레이어로 들었을 때 한국어 자연스러움

실패 케이스:
| 증상 | 원인 후보 | 조치 |
|------|----------|------|
| `aiohttp.ClientResponseError 401` | `AZURE_SPEECH_KEY` 가 잘못됨/만료됨 | Azure Portal 에서 키 재확인·재발급 |
| `aiohttp.ClientResponseError 429` | F0 무료 티어 월 500K char 한도 초과 | S0 로 업그레이드 또는 다음 달까지 대기 |
| `aiohttp.ClientConnectorError` | 인터넷 / 방화벽 / 잘못된 region | 네트워크 점검, `AZURE_SPEECH_REGION` 확인 |
| `asyncio.TimeoutError` | Azure Speech 일시 지연 | 1회 자동 retry 됨, 지속되면 region 변경 검토 |
| 파일은 생성되나 재생 시 무음 | voice 인자가 잘못됨 (영문 voice + 한글 텍스트) | `ko-KR-*` 인지 확인 |

### 4.2 Audio Queue — 실시간 재생 (Phase 5)

수동 시나리오:

1. `python bot.py` 실행
2. Discord에서 `/setvc 🔊 일반음성`, `/settts #tts-입력`
3. `/join` → 봇이 음성 채널 입장 확인
4. **사용자가 직접** 큐를 인큐하기 위해 임시 명령(`/say <text>`) 또는 텍스트 채널 메시지 입력 (Phase 7 의존이지만 5에선 임시 helper로 검증)
5. 5건 빠르게 입력 → 순서대로 끊김 없이 재생되는지 청취
6. 5분간 입력 없이 대기 → 봇이 연결을 유지하는지 확인
7. 그 후 메시지 입력 → 재입장 지연 없이 즉시 재생

기록:
- 첫 메시지 입력 ~ 음성 시작까지 latency: ____ s
- 5건 연속 재생 중 끊김/중복: ____ 건
- persistent voice 동작: ✅/❌

### 4.3 Slash Commands (Phase 6)

전체 시나리오는 [`tests/integration/test_phase6_commands.md`](../tests/integration/test_phase6_commands.md). 핵심만 요약:

| 명령 | 검증 포인트 |
|------|------------|
| `/settts` | 권한 없는 사용자에게 차단됨 |
| `/setvc` | 음성 채널만 선택 가능 (UI 필터링) |
| `/setvoice` | dropdown에 4개 한국어 보이스 |
| `/join` | 사전 `/setvc` 없으면 안내 |
| `/leave` | 미입장 상태에서도 안전 |
| `/status` | 모든 설정 + 현재 voice 표시 |

### 4.4 Event Handlers (Phase 7)

전체 시나리오는 [`tests/integration/test_phase7_events.md`](../tests/integration/test_phase7_events.md). 핵심:

#### on_message
- TTS 채널 메시지만 합성 (다른 채널 메시지 무시)
- URL `https://x.com` → "링크"
- 멘션 `@김민수` → "김민수"
- `**굵게**` → "굵게"
- 250자 입력 → 200자 잘림 + `…`
- 봇 메시지 → 무반응

#### on_voice_state_update
- 사용자 입장 → "{닉네임}님 입장"
- 사용자 퇴장 → "{닉네임}님 퇴장"
- mute/deafen 변경 → 무반응
- 봇 자기 자신의 입퇴장 → 무반응

---

## 5. 자주 만나는 문제와 디버깅

### 5.1 슬래시 명령이 안 보임
- 전역 sync는 캐시 최대 1시간 → 개발 시 `TEST_GUILD_ID` 사용 (§2 참조)
- `applications.commands` scope 누락 → OAuth URL 재생성 후 재초대
- 봇이 서버에 들어와 있지 않음 → 멤버 목록 확인

### 5.2 음성 재생이 안 됨
1. FFmpeg PATH: `where ffmpeg` (Windows) / `which ffmpeg`
2. PyNaCl 설치: `pip show PyNaCl`
3. 봇 권한: 채널 권한 → `Connect`, `Speak` 모두 ✅
4. `voice_client.is_connected()` 콘솔 로그 추가
5. region 이슈: Discord 음성 region이 비정상이면 변경

### 5.3 메시지 본문이 비어 보임 (`message.content == ""`)
- **Message Content Intent 미활성화** — Developer Portal에서 ON
- intents 코드: `intents.message_content = True`

### 5.4 입/퇴장 이벤트가 안 옴
- **Server Members Intent 미활성화**
- `intents.members = True` + `intents.voice_states = True`

### 5.5 봇이 자기 자신의 입장도 안내함
- Rule 01 위반 — 핸들러 첫 줄에 `if member.bot: return` 누락

### 5.6 한글이 깨져 발음됨
- Rule 07 위반 — 영문 voice 사용 또는 인코딩 문제
- `config.json` 을 텍스트 에디터로 열어 한글 그대로인지 확인 (`ensure_ascii=False`)

---

## 6. 관찰성 (라이브 디버깅 도구)

### 6.1 로그 레벨 임시 상승
```bash
LOG_LEVEL=DEBUG python bot.py
```

### 6.2 discord.py 내부 디버그
콘솔에 게이트웨이 이벤트가 다 보임. 평소엔 노이즈가 많아 WARNING 권장:
```python
logging.getLogger("discord").setLevel(logging.DEBUG)   # 임시 진단 시
```

### 6.3 voice 패킷 모니터링
필요 시 디버그 모드로 `voice_client.encoder` 상태 출력. 일반적으로 불필요.

### 6.4 큐 상태 확인 명령 (선택)
디버그용 임시 슬래시 명령 `/qstatus`를 추가해 현재 큐 길이를 응답하게 하면 라이브 검증에 편리. 운영 배포 전 제거.

---

## 7. 청취 평가 기준 (Phase 4, 5)

자동 테스트로 못 잡는 음질·UX는 사람이 들어 평가:

| 항목 | 통과 기준 |
|------|----------|
| 발음 정확성 | 한국어 본문이 의도대로 발음됨 (오발음 < 5%) |
| 자연스러움 | 기계적이지 않은 억양, 끊김 없음 |
| 볼륨 | 다른 사용자 대화와 비슷한 수준 |
| 끊김/지연 | 메시지 입력 ~ 재생 시작 < 3초 |
| 큐 직렬화 | 두 메시지가 겹치지 않음 |

평가 예: ☑ 통과 / ☐ 보류 / ☒ 실패 — 보류/실패 시 노이즈 캡처(콘솔 로그 + `qstatus`) 첨부.

---

## 8. 보안

| 항목 | 정책 |
|------|------|
| Dev 봇 토큰 | `.env`에만, 절대 커밋 금지 |
| 테스트 서버 초대 링크 | 외부 공개 금지 |
| 라이브 테스트 메시지 | PII/실명 사용 금지 |
| 라이브 로그 공유 | 토큰/계정 ID 마스킹 후 공유 |

토큰 유출 시 즉시 [Rule 04](rules/04-secrets-and-security.md)의 절차대로 reset.

---

## 9. 통합 검증 흐름 (e2e 한 사이클)

배포 직전 다음을 한 번에 수행:

```bash
# 1. 단위 회귀
python -m pytest tests/unit -q

# 2. 라이브 단위 (Azure Speech 도달)
RUN_LIVE=1 python -m pytest tests/unit -m live -q

# 3. 봇 실행
python bot.py
```

다음을 사람이 수동:

1. [`tests/integration/test_phase6_commands.md`](../tests/integration/test_phase6_commands.md) 모두 ✅
2. [`tests/integration/test_phase7_events.md`](../tests/integration/test_phase7_events.md) 모두 ✅
3. [`tests/manual/phase8_release_checklist.md`](../tests/manual/phase8_release_checklist.md) 모두 ✅

세 단계 모두 통과해야 release 가능.

---

## 10. 변경 사항 추적

라이브 테스트 결과는 다음 형식으로 PR/커밋 메시지에 첨부:

```
## Live test report
- env: dev guild 123456..., python 3.13, ffmpeg 7.1
- date: 2026-04-29
- run_live unit: 4 passed, 0 failed
- phase6 checklist: 18/18 pass
- phase7 checklist: 14/14 pass
- notes: persistent voice tested past 5min; next-message cold start absent
- audio quality: 자연스러움 ☑, 끊김 없음 ☑
```
