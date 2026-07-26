# Phase 6 — Slash Commands Manual Checklist

> 모두 통과해야 Phase 6 완료. 진행자: ____________ / 일시: ____________

## 사전 조건
- [ ] 봇이 테스트 서버에 초대됨
- [ ] `python bot.py`로 봇이 실행 중
- [ ] 콘솔에 `Synced N slash commands` 로그 확인 (N ≥ 6)

## 명령어 동작

### `/읽기채널 <채널>`
- [ ] `Manage Channels` 권한자가 실행 → ephemeral 응답 "TTS 채널을 #X으로 설정"
- [ ] 일반 사용자가 실행 → 권한 부족 안내
- [ ] `config.json` 에 `tts_channel_ids` 또는 `tts_channel_id` 반영됨

### `/음성채널 <채널>`
- [ ] 음성 채널 선택만 가능 (텍스트 채널 선택 불가)
- [ ] ephemeral 응답 정상
- [ ] `config.json`에 `voice_channel_id` 반영

### `/목소리 <종류>`
- [ ] 10개 보이스가 dropdown으로 표시
- [ ] 선택 시 `config.json`에 `voice` 반영

### `/입장`
- [ ] `voice_channel_id` 미설정이면 "먼저 /음성채널..." 안내
- [ ] 설정된 경우 봇이 음성 채널에 입장 (Discord UI 좌측에서 확인)

### `/퇴장`
- [ ] 봇이 음성 채널 미입장이면 "음성 채널에 없습니다"
- [ ] 입장 중이면 정상 퇴장

### `/상태`
- [ ] 현재 TTS 채널, 음성 채널, voice가 mention/식별자로 표시됨

## 회귀
- [ ] 봇 재시작 후 `config.json`의 설정이 그대로 유지

## 결과
- 통과: ____ / 실패: ____ / 비고:
