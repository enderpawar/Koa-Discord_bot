# Skill 06 — Voice Management

## Purpose
discord.py `VoiceClient`의 라이프사이클(연결/이동/끊김 복구/idle 종료)을 단일 위치에서 관리한다.

## 동작 규칙
| 상황 | 처리 |
|------|------|
| 봇이 음성 채널에 미연결 | `channel.connect(reconnect=True, self_deaf=True)` |
| 다른 음성 채널에 연결됨 | `voice_client.move_to(target)` |
| 같은 채널에 이미 연결 | 그대로 사용 |
| 큐가 오래 비어 있음 | 기본값은 연결 유지 (`TTS_IDLE_DISCONNECT_SEC>0`일 때만 자동 종료) |
| 마지막 사용자가 채널을 떠남 | 대기 큐를 비우고 즉시 연결 종료 |
| 네트워크 끊김 | `reconnect=True`로 자동 복구; 실패 시 `_ensure_voice`가 재연결 |
| 봇이 음성 권한 없음 | `discord.Forbidden` → 텍스트 채널에 안내 |

## 핵심 코드
- `_ensure_voice(guild, channel_id)`: 이미 [Skill 05](05-audio-queue.md)에 정의됨, 본 Skill은 그 정책의 명세화

## self_deaf=True 이유
- 봇이 자기 음성을 다시 받지 않아 대역폭 절감
- TTS 봇이 다른 사람의 음성을 들을 필요 없음

## disconnect 정책
- 기본값 `TTS_IDLE_DISCONNECT_SEC=0`: TTS 끄기 또는 `/퇴장` 전까지 연결 유지
- 양수로 설정한 경우에만 해당 초만큼 idle 후 자동 종료
- 기본 연결 유지로 첫 문장의 Discord voice cold start를 제거
- 단, 음성 채널에 사람이 한 명도 남지 않으면 봇만 대기하지 않고 즉시 퇴장
- 빈 음성 채널을 대상으로 들어온 새 TTS 메시지는 자동 재입장하지 않고 무시

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
- 봇이 채널 A에 있을 때 `/음성채널 B` + 메시지 입력 → 채널 B로 이동 후 재생
- 봇 강제 disconnect (관리자가 강제 추방) → 다음 enqueue에서 재연결
- 권한 박탈 시 텍스트 채널 안내 메시지 표시
