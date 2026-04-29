# Rule 01 — Bot Loop Prevention

## Rule
**모든 메시지/음성 이벤트 핸들러는 진입 즉시 봇 작성자/봇 멤버를 필터링한다.**

## Why
- 봇이 만든 메시지를 다시 TTS로 읽으면 봇이 음성 채널에 있으면서 텍스트 채널에 응답하는 봇이 동시에 있다면 무한 루프가 발생할 수 있음
- 봇이 음성 채널에 입/퇴장하는 자기 자신의 이벤트를 트리거로 또 다른 알림을 만들면 안 됨 (자기 입장 → 알림 합성 → 봇이 voice connect → 다시 voice_state 변경 등)
- 다른 음악봇/공지봇이 같은 텍스트 채널에 메시지를 보내면 우리 봇이 매번 읽게 되어 노이즈

## How to Apply
| 핸들러 | 첫 줄에 둘 가드 |
|--------|----------------|
| `on_message(message)` | `if message.author.bot: return` |
| `on_voice_state_update(member, before, after)` | `if member.bot: return` |
| 슬래시 명령어 | `interaction.user.bot`이 가능한 케이스는 사실상 없으므로 생략 가능 |

## Counter-examples
```python
# ❌ 봇 자신의 입장도 알림으로 합성됨
async def on_voice_state_update(self, member, before, after):
    if before.channel != after.channel:
        await self.queue.enqueue(...)

# ✅ 봇 필터 후 처리
async def on_voice_state_update(self, member, before, after):
    if member.bot:
        return
    if before.channel != after.channel:
        ...
```

## 추가 안전장치
- TTS 채널을 구별: 봇이 사용하는 명령 응답은 `ephemeral=True`로 텍스트 채널에 잔류시키지 않음
- 만약 발신자가 webhook이라면 `message.webhook_id`가 truthy → 별도 정책으로 처리(현재는 무시)
