"""Phase 2 — guild별 설정 영속화.

JSON 파일에 `{str(guild_id): {...}}` 구조로 저장한다.
- Rule 02: 상태는 guild_id 키 dict 로 격리
- Rule 05: 동시 쓰기는 asyncio.Lock + 디스크 IO는 asyncio.to_thread 로 루프 비차단
- 원자성: 임시파일 → os.replace (POSIX/Windows 양쪽에서 atomic)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ConfigStore:
    def __init__(self, path: Path | str = Path("config.json")) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 손상된 파일: 백업 후 빈 상태로 시작 (Rule 03)
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("config corrupt, backed up to %s", backup)
            except OSError:
                log.exception("config corrupt and backup failed: %s", self._path)
            self._data = {}

    async def get(self, guild_id: int) -> dict[str, Any]:
        async with self._lock:
            return dict(self._data.get(str(guild_id), {}))

    async def set(self, guild_id: int, **fields: Any) -> None:
        async with self._lock:
            cur = self._data.setdefault(str(guild_id), {})
            cur.update({k: v for k, v in fields.items() if v is not None})
            await self._save_unlocked()
            log.info("config updated: guild_id=%s keys=%s", guild_id, list(fields.keys()))

    async def remove_guild(self, guild_id: int) -> None:
        async with self._lock:
            if self._data.pop(str(guild_id), None) is not None:
                await self._save_unlocked()
                log.info("config removed: guild_id=%s", guild_id)

    async def save(self) -> None:
        async with self._lock:
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        # 스냅샷을 lock 안에서 만들고, 디스크 IO는 별도 스레드로 위임
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._atomic_write, snapshot)

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cfg_", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
