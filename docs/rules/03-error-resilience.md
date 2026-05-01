# Rule 03 — Error Resilience

## Rule
**어떤 단일 메시지·이벤트·요청의 실패도 봇 프로세스 / 큐 worker / 다른 guild의 처리를 중단시켜선 안 된다.**

## Why
- TTS 봇은 long-running 프로세스. 한 번의 네트워크 hiccup, 한 번의 잘못된 입력으로 봇이 죽으면 재시작 자동화/모니터링이 강제됨
- "한 번 실패하면 그 사용자 메시지만 못 읽고 다음은 정상" 이 가장 좋은 UX

## Layered Catch 정책

| 레이어 | catch 범위 | 행동 |
|--------|-----------|------|
| **TTS 합성** (`tts_engine.synthesize`) | `aiohttp.ClientError`, `asyncio.TimeoutError` (Azure Speech REST) | 1회 retry → 그래도 실패면 raise |
| **Audio worker loop** | `Exception` | `log.exception` + `continue` (다음 큐 항목) |
| **Event handler** (`on_message` 등) | `Exception` | `log.exception` + return (사용자에게 무응답) |
| **Slash command handler** | `Exception` | `interaction.followup`으로 한국어 안내 + log |
| **Bot top-level** | `KeyboardInterrupt`만 별도 | discord.py가 자체 관리 |

## How to Apply
```python
# Worker loop
while True:
    req = await queue.get()
    try:
        await self._process(req)
    except Exception:
        log.exception("audio worker failed for guild=%s", guild.id)
    finally:
        queue.task_done()

# Event handler
@commands.Cog.listener()
async def on_message(self, message):
    try:
        await self._handle_tts(message)
    except Exception:
        log.exception("on_message failed for guild=%s", message.guild.id if message.guild else None)
```

## 절대 금지
- `bare raise SystemExit` 
- `os._exit()` (cleanup 우회)
- `except Exception: pass` (silent swallow → 디버깅 불가)
- 재시도 무한 루프 (반드시 횟수 제한)

## 사용자에게 알릴 가치가 있는 실패
- 음성 채널 권한 없음 → 텍스트 채널에 1회 안내
- 음성 채널이 삭제됨 → `/setvc` 재설정 안내
- TTS 합성 연속 5회 실패 → "TTS 서비스가 일시적으로 불가합니다" 안내

## 사용자에게 알리지 않을 실패
- 단일 메시지 합성 실패 (다음 메시지 정상이면 됨)
- 일시 네트워크 오류
