# Skill 05 — Audio Queue

## Purpose
guild별로 TTS 재생 요청을 직렬 처리한다. 빠른 연속 메시지가 와도 겹치지 않고 순서대로 재생되어야 한다. 큐가 비면 5분 후 자동 disconnect.

## 자료구조
```python
@dataclass
class AudioRequest:
    text: str
    voice: str
    voice_channel_id: int

# guild_id → asyncio.Queue[AudioRequest]
self._queues: dict[int, asyncio.Queue[AudioRequest]] = {}
self._workers: dict[int, asyncio.Task] = {}
```

## API
```python
class AudioQueue:
    async def enqueue(self, guild: discord.Guild, req: AudioRequest) -> None: ...
    async def shutdown(self) -> None: ...
```

## Worker Loop (핵심)
```python
async def _worker(self, guild: discord.Guild):
    queue = self._queues[guild.id]
    while True:
        try:
            req = await asyncio.wait_for(queue.get(), timeout=300)  # 5분 idle
        except asyncio.TimeoutError:
            await self._disconnect(guild)
            self._workers.pop(guild.id, None)
            return

        try:
            vc = await self._ensure_voice(guild, req.voice_channel_id)
            mp3 = await synthesize(req.text, req.voice)
            await self._play_blocking(vc, mp3)
        except Exception:
            log.exception("audio worker failed for guild=%s", guild.id)
        finally:
            queue.task_done()
```

## `_play_blocking` — 콜백을 async로 래핑
```python
async def _play_blocking(self, vc: discord.VoiceClient, mp3: Path):
    done = asyncio.Event()
    def _after(err):
        if err: log.warning("playback error: %s", err)
        try: mp3.unlink(missing_ok=True)
        except Exception: pass
        loop.call_soon_threadsafe(done.set)

    loop = asyncio.get_running_loop()
    source = discord.FFmpegPCMAudio(str(mp3))
    vc.play(source, after=_after)
    await done.wait()
```

## `_ensure_voice` — 연결/이동
```python
async def _ensure_voice(self, guild, channel_id) -> discord.VoiceClient:
    target = guild.get_channel(channel_id)
    if not target: raise RuntimeError("voice channel missing")
    vc = guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != channel_id:
            await vc.move_to(target)
        return vc
    return await target.connect(reconnect=True, self_deaf=True)
```

## Applied Rules
- [02-guild-isolation](../rules/02-guild-isolation.md): 큐·worker는 guild_id별로 독립
- [05-async-correctness](../rules/05-async-correctness.md): `vc.play` 콜백을 `Event`로 직렬화, `loop.call_soon_threadsafe`로 thread-safe 신호
- [03-error-resilience](../rules/03-error-resilience.md): worker는 try/except로 감싸 영구 실행, 단일 요청 실패가 다음 요청을 막지 않음

## Dependencies
- Skill [04-tts-engine](04-tts-engine.md), [06-voice-management](06-voice-management.md)
- FFmpeg (PATH)

## Validation
- 봇이 채널 입장 후 enqueue 3건 → 순서대로 재생되며 겹치지 않음
- 5분간 idle → 봇이 자동 disconnect
- 합성 실패 시뮬레이션 → worker는 다음 요청 정상 처리
