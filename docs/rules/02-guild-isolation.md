# Rule 02 — Guild Isolation

## Rule
**모든 변경 가능한 상태(설정·큐·voice client·worker task)는 `guild_id`를 키로 격리한다. guild 간에 데이터·예외·재생이 절대 교차하지 않는다.**

## Why
- 봇이 여러 서버에 동시 초대된 환경에서, 한 서버의 설정/장애가 다른 서버에 영향을 주면 신뢰성·디버깅이 망가짐
- 한 guild의 worker가 죽어도 다른 guild는 정상 운영되어야 함

## How to Apply
1. **자료구조**: `dict[int, X]` 형태로 항상 `guild_id` 인덱싱
   - `self._queues: dict[int, asyncio.Queue]`
   - `self._workers: dict[int, asyncio.Task]`
   - `ConfigStore._data[str(guild_id)]`
2. **함수 시그니처**: state를 다루는 모든 메서드는 `guild` 또는 `guild_id`를 첫 인자로 받음
3. **VoiceClient 접근**: 항상 `guild.voice_client`로 (전역 캐시 X)
4. **로그 컨텍스트**: 모든 INFO/WARNING 로그에 `guild_id=...` 포함
5. **on_guild_remove**: 봇이 추방되면 `ConfigStore.remove_guild`, 큐/worker 정리

## Counter-examples
```python
# ❌ 전역 단일 큐 → 서버 A의 메시지가 서버 B의 voice client로 재생될 위험
self._queue = asyncio.Queue()

# ✅ guild별 격리
self._queues: dict[int, asyncio.Queue] = {}
queue = self._queues.setdefault(guild.id, asyncio.Queue())
```

```python
# ❌ guild 컨텍스트 없이 voice client 검색
vc = bot.voice_clients[0]

# ✅ guild로 직접
vc = guild.voice_client
```

## 격리 단위 체크리스트
- [ ] Config는 guild_id key
- [ ] AudioQueue는 guild_id별 dict
- [ ] Worker task도 guild_id별 dict
- [ ] 로그에 guild_id 포함
- [ ] 한 guild의 예외가 다른 guild의 worker에 전파되지 않음 (try/except in worker loop)
