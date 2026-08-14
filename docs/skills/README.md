# Skills Index

Skill은 본 봇에서 **재사용 가능한 단위 능력**을 정의합니다. 각 파일은 다음을 담습니다.
- **Purpose**: 무엇을 하는 능력인가
- **Inputs / Outputs**: 인터페이스 계약
- **Implementation Sketch**: 핵심 로직 스니펫
- **Dependencies**: 다른 Skill / 외부 라이브러리
- **Validation**: 단독 검증 방법

| # | Skill | Purpose | 구현 위치 |
|---|-------|---------|----------|
| 01 | [bot-foundation](01-bot-foundation.md) | discord.py Bot 초기화·intents·환경변수·cog 로드 | `bot.py` |
| 02 | [config-store](02-config-store.md) | guild별 설정의 영속화 (JSON, 원자적 쓰기) | `cogs/config_store.py` |
| 03 | [message-preprocessing](03-message-preprocessing.md) | TTS 전 메시지 정제 (멘션/URL/마크다운/길이) | `cogs/preprocess.py` |
| 04 | [tts-engine](04-tts-engine.md) | Azure Speech REST로 한국어 텍스트 → mp3 합성 | `cogs/tts_engine.py` |
| 05 | [audio-queue](05-audio-queue.md) | guild별 비동기 직렬 재생 큐 | `cogs/audio_queue.py` |
| 06 | [voice-management](06-voice-management.md) | VoiceClient 연결/이동/연결 유지/복구 | `cogs/audio_queue.py` 내부 |
| 07 | [slash-commands](07-slash-commands.md) | 슬래시 명령어 정의·sync·권한 체크 | `cogs/tts_cog.py` |
| 08 | [music-mode](08-music-mode.md) | YouTube URL 음악 재생과 TTS/음악 배타 모드 | `cogs/music_player.py`, `cogs/music_cog.py` |

## 적용 매핑
- 각 Skill이 어느 Phase에서 구현되는지는 [`pipeline.md`](../pipeline.md) 참조.
- 각 Skill이 따라야 하는 Rule은 해당 Skill 파일 내 **Applied Rules** 섹션 참조.
