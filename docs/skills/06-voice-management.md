# Skill 06 — Voice Management

## Purpose
discord.py `VoiceClient`의 라이프사이클(연결/이동/끊김 복구/idle 종료)을 단일 위치에서 관리한다.

## 동작 규칙
| 상황 | 처리 |
|------|------|
| 봇이 음성 채널에 미연결 | `channel.connect(reconnect=True, self_deaf=True)` |
| 다른 음성 채널에 연결됨 | `voice_client.move_to(target)` |
| 같은 채널에 이미 연결 | 그대로 사용 |
| 5분간 큐가 비어 있음 | `voice_client.disconnect()` (다음 요청 시 재연결) |
| 네트워크 끊김 | `reconnect=True`로 자동 복구; 실패 시 `_ensure_voice`가 재연결 |
| 봇이 음성 권한 없음 | `discord.Forbidden` → 텍스트 채널에 안내 |

## 핵심 코드
- `_ensure_voice(guild, channel_id)`: 이미 [Skill 05](05-audio-queue.md)에 정의됨, 본 Skill은 그 정책의 명세화

## self_deaf=True 이유
- 봇이 자기 음성을 다시 받지 않아 대역폭 절감
- TTS 봇이 다른 사람의 음성을 들을 필요 없음

## disconnect 정책 (5분 idle)
- 큐 worker의 `wait_for(timeout=300)`이 트리거
- 사용자가 다시 메시지를 보내면 새로운 worker가 즉시 재연결
- 봇이 항상 음성 채널에 머물러 있어 "X명 접속" 표시가 늘어나는 UX 이슈 방지

## 입/퇴장 알림과의 관계
- 입/퇴장 알림은 본 큐를 그대로 사용 → 동일한 직렬화 적용
- 즉, 사용자가 입장 → 봇이 음성 채널 미연결 상태라면 자동 연결 후 안내 재생

## Applied Rules
- [01-bot-loop-prevention](../rules/01-bot-loop-prevention.md): `member.bot` 체크가 voice event handler 진입 시 필수
- [02-guild-isolation](../rules/02-guild-isolation.md): VoiceClient는 `guild.voice_client`로 항상 guild 컨텍스트
- [03-error-resilience](../rules/03-error-resilience.md): connect/move 실패는 catch 후 다음 요청

## Dependencies
- `discord.py`의 `VoiceClient`, `VoiceChannel.connect`
- PyNaCl (음성 암호화)

## Validation
- 봇이 채널 A에 있을 때 `/setvc B` + 메시지 입력 → 채널 B로 이동 후 재생
- 봇 강제 disconnect (관리자가 강제 추방) → 다음 enqueue에서 재연결
- 권한 박탈 시 텍스트 채널 안내 메시지 표시
