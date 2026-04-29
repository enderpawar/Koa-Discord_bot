# Integration Tests

여기엔 **라이브 환경(Discord, 인터넷)이 필요한 시나리오**가 들어있습니다.

자동 hook은 단위 테스트만 돌리며, 본 디렉토리는 **수동 실행** 또는 **명시적 명령**으로만 실행됩니다.

## 실행 방법

### 라이브 단위 테스트 (`@pytest.mark.live` 표시 항목)

```bash
RUN_LIVE=1 python -m pytest tests/unit -m live
```

### 수동 체크리스트
- [`test_phase6_commands.md`](test_phase6_commands.md) — 슬래시 명령어 6종 동작
- [`test_phase7_events.md`](test_phase7_events.md) — on_message / on_voice_state_update 시나리오

각 체크리스트는 **테스트 서버**(또는 별도 dev 서버)에서 실행하며, 모든 항목 통과 시 해당 Phase 완료로 간주합니다.

## 사전 준비
- 봇이 테스트 서버에 초대됨
- 봇에게 필요한 권한 부여 (Connect, Speak, Read Messages, Use Slash Commands)
- `.env` 의 `DISCORD_TOKEN`이 dev 봇 토큰을 가리킴
- `python bot.py`로 봇 실행 중
