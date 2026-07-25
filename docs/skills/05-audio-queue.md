# Skill 05 — Audio Queue

## Purpose
guild별로 TTS 재생 요청을 직렬 처리한다. 빠른 연속 메시지가 와도 겹치지 않고 순서대로 재생되어야 한다. 기본값은 TTS를 명시적으로 끌 때까지 음성 연결을 유지한다.

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
        req = await queue.get()  # 기본값: persistent voice session

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

## 스트리밍 언더플로 처리

Azure 스트리밍 청크는 항상 20ms 간격으로 도착하지 않는다. 특히 긴 문장은 첫 청크
또는 중간 청크가 늦어질 수 있으므로 `AudioSource.read()`에서 네트워크 데이터를
blocking wait 하면 안 된다.

- 생산자는 mono PCM 청크를 thread-safe 큐에 넣기만 한다.
- Discord `AudioPlayer` 스레드는 청크를 20ms 프레임으로 나누고 stereo로 변환한다.
- 아직 프레임이 없으면 빈 바이트가 아니라 20ms 무음 PCM을 즉시 반환해 RTP 송출을
  유지한다.
- 15초 동안 새 청크가 없거나 생산자가 종료 신호를 보내면 빈 바이트를 반환해 재생을
  끝낸다.

이 구조는 긴 청크의 프레임 변환이 asyncio 이벤트 루프를 점유하는 문제와, 청크
언더플로 때 Discord 음성 연결에 `!` 경고가 나타나는 문제를 함께 방지한다.

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
- 긴 문장 재생 중 Azure 청크가 잠시 비어도 무음 RTP가 이어지고 연결 경고가 없음
- 5분 이상 idle 후에도 연결 유지 → 다음 문장이 cold start 없이 재생
- 합성 실패 시뮬레이션 → worker는 다음 요청 정상 처리
