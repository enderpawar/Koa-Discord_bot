# Skill 02 — Config Store

## Purpose
guild별 설정(TTS 채널, 음성 채널, 보이스 등)을 JSON 파일에 영속화한다. 동시 쓰기에서 손상되지 않게 원자적으로 저장한다.

## Data Schema
```json
{
  "<guild_id>": {
    "tts_channel_id": 123,
    "voice_channel_id": 456,
    "voice": "ko-KR-SunHiNeural"
  }
}
```

## API
```python
class ConfigStore:
    async def get(self, guild_id: int) -> dict: ...
    async def set(self, guild_id: int, **fields) -> None: ...
    async def save(self) -> None: ...
    async def remove_guild(self, guild_id: int) -> None: ...  # on_guild_remove
```

## Implementation Sketch
```python
import json, os, asyncio, tempfile
from pathlib import Path

class ConfigStore:
    def __init__(self, path: Path = Path("config.json")):
        self._path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, dict] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    async def get(self, guild_id: int) -> dict:
        async with self._lock:
            return dict(self._data.get(str(guild_id), {}))

    async def set(self, guild_id: int, **fields) -> None:
        async with self._lock:
            cur = self._data.setdefault(str(guild_id), {})
            cur.update({k: v for k, v in fields.items() if v is not None})
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        # 원자적 쓰기: 임시파일에 쓰고 rename
        fd, tmp = tempfile.mkstemp(prefix="cfg_", dir=self._path.parent or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try: os.unlink(tmp)
            except FileNotFoundError: pass
            raise
```

## Applied Rules
- [02-guild-isolation](../rules/02-guild-isolation.md): 키는 항상 `guild_id`, 다른 guild 데이터 노출 금지
- [05-async-correctness](../rules/05-async-correctness.md): `asyncio.Lock`으로 동시 쓰기 직렬화
- [03-error-resilience](../rules/03-error-resilience.md): 손상된 JSON 파일 발견 시 백업 후 빈 dict로 시작 (선택)

## Dependencies
- 표준 라이브러리만 사용 (`json`, `asyncio`, `tempfile`)

## Validation
```python
# 단위 테스트 예시
import asyncio
from cogs.config_store import ConfigStore
async def t():
    s = ConfigStore(Path("/tmp/test.json"))
    await s.set(123, tts_channel_id=456)
    assert (await s.get(123))["tts_channel_id"] == 456
asyncio.run(t())
```
