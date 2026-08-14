# Skill 08 — Exclusive Music Mode

## Purpose

길드별 음성 연결을 `tts` 또는 `music` 모드 하나로만 운영하고, 공개된
단일 YouTube 영상 URL의 오디오를 길드별 대기열로 재생한다.

## Contract

- `audio_mode` 저장값은 `tts | music`이며 미설정·잘못된 값은 `tts`로 본다.
- 모드 전환은 guild lock 안에서 반대쪽 현재 재생과 대기열을 정리한 뒤 수행한다.
- 음악 모드의 일반 채팅과 입·퇴장 알림은 TTS 입력으로 받지 않는다.
- `/재생`, `/스킵`, `/중지`, `/재생목록`은 봇과 같은 음성 채널의 사용자만 쓴다.
- 검색어, 재생목록 URL, 라이브, 인증이 필요한 영상은 지원하지 않는다.
- yt-dlp는 `asyncio.to_thread` 밖에서 실행하고 FFmpeg 재생 완료는
  `loop.call_soon_threadsafe` 콜백으로 asyncio에 전달한다.

## Validation

- 두 guild의 음악 큐가 섞이지 않고 각각 순서대로 재생된다.
- 모드 전환 후 반대 입력이 enqueue되지 않는다.
- 단일 곡 실패 후 워커는 다음 곡을 계속 재생한다.
- `RUN_LIVE=1` 테스트에서 공개 YouTube URL 추출과 FFmpeg 1초 디코딩을 확인한다.
