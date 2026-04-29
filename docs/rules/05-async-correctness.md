# Rule 05 — Async Correctness

## Rule
**이벤트 루프를 차단하지 않는다. 모든 I/O는 `await`. 콜백 기반 API는 `asyncio.Event`로 직렬화. 공유 상태 수정은 `asyncio.Lock`.**

## Why
- discord.py는 단일 이벤트 루프 위에서 모든 gateway/voice 처리를 담당. blocking 호출 한 번이 heartbeat 누락 → disconnect 야기
- `voice_client.play()`는 sync 콜백을 받지만 그 안에서 다음 재생을 곧바로 트리거하면 타이밍 race
- 동시에 들어오는 슬래시 명령어가 같은 config 파일을 쓰면 손상 가능

## How to Apply

### 1. Blocking I/O 금지
```python
# ❌ 동기 파일 IO
with open("config.json") as f:
    data = json.load(f)

# ✅ 작은 파일이면 init 시점에만 sync OK; runtime은 async or thread executor
async def save(self):
    async with self._lock:
        await asyncio.to_thread(self._write_to_disk)
```

### 2. `time.sleep` 금지
```python
# ❌ time.sleep(5)
# ✅ await asyncio.sleep(5)
```

### 3. 콜백 → Event 변환 (필수 패턴)
```python
async def play_and_wait(vc, source):
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    def _after(err):
        loop.call_soon_threadsafe(done.set)   # thread-safe
    vc.play(source, after=_after)
    await done.wait()
```
- `vc.play`의 `after` 콜백은 **다른 thread**에서 호출됨 → `loop.call_soon_threadsafe` 필수

### 4. 공유 상태 보호
```python
# ConfigStore의 _data 수정
async with self._lock:
    self._data[gid] = ...
    await self._save_unlocked()
```

### 5. Task 누수 방지
```python
# ❌ asyncio.create_task(...) 후 참조 잃어버림 → GC가 task를 거두면 silently 사라짐
# ✅ self._workers[guild.id] = asyncio.create_task(...)
```

### 6. `asyncio.gather(..., return_exceptions=True)` 활용
다수 guild를 병렬 처리할 때 한 guild의 실패가 나머지를 죽이지 않도록.

## Counter-examples
```python
# ❌ blocking requests
import requests
def fetch_voice():
    return requests.get(url).content

# ✅ aiohttp 또는 라이브러리의 async API
async with aiohttp.ClientSession() as s:
    async with s.get(url) as r:
        return await r.read()
```

```python
# ❌ vc.play 후 즉시 다음 enqueue 호출 → 두 음원이 겹침
vc.play(s1)
vc.play(s2)   # AttributeError or 끊김

# ✅ Event로 끝날 때까지 대기
await play_and_wait(vc, s1)
await play_and_wait(vc, s2)
```

## 체크리스트
- [ ] 모든 I/O가 `await`
- [ ] `time.sleep` 없음 (`grep`으로 확인)
- [ ] `vc.play` 콜백이 `loop.call_soon_threadsafe` 사용
- [ ] `asyncio.create_task` 결과를 변수에 저장
- [ ] config 쓰기는 lock 보호
