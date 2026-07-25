"""디스코드 유저 → 롤 라이엇ID 등록 매핑 저장.

valorant_store 와 같은 원자적 JSON 패턴. 게임별 저장을 분리해 결합을 피한다
(롤은 region 대신 platform 을 보관: kr/na1/euw1/jp1).
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


def _default_path() -> Path:
    return Path(os.getenv("LOL_STORE_PATH", "lol_ids.json"))


class LolStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_path()
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = loaded
        except (OSError, json.JSONDecodeError):
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("lol store corrupt, backed up to %s", backup)
            except OSError:
                log.exception("lol store corrupt and backup failed: %s", self._path)
            self._data = {}

    async def set(self, user_id: int, *, name: str, tag: str, platform: str) -> None:
        async with self._lock:
            self._data[str(user_id)] = {"name": name, "tag": tag, "platform": platform}
            await self._save_unlocked()

    async def get(self, user_id: int) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._data.get(str(user_id))
            return dict(entry) if entry else None

    async def remove(self, user_id: int) -> bool:
        async with self._lock:
            existed = self._data.pop(str(user_id), None) is not None
            if existed:
                await self._save_unlocked()
            return existed

    async def _save_unlocked(self) -> None:
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._atomic_write, snapshot)

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".lol_", dir=parent)
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
