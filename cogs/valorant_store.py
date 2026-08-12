"""디스코드 유저 → 라이엇ID 등록 매핑 저장.

op.gg처럼 한 번 등록해 두면 이후 `/발로란트 전적`으로 라이엇ID를 다시 치지
않아도 되게 한다. 라이엇ID 는 길드가 아니라 개인 정체성이므로 guild 분리 없이
user_id 로만 키를 잡는다 (rank_store 의 원자적 쓰기 패턴 재사용).
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
    return Path(os.getenv("VALORANT_STORE_PATH", "valorant_ids.json"))


class ValorantStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_path()
        self._lock = asyncio.Lock()
        # {guild_id: {user_id: {...}}} — 등록은 길드 단위로 격리된다.
        self._data: dict[str, dict[str, Any]] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = loaded
                self._migrate_legacy_unlocked()
        except (OSError, json.JSONDecodeError):
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("valorant store corrupt, backed up to %s", backup)
            except OSError:
                log.exception("valorant store corrupt and backup failed: %s", self._path)
            self._data = {}

    def _migrate_legacy_unlocked(self) -> None:
        """`{user_id: {...}}` 평면 구조를 버린다.

        예전에는 등록이 계정 단위 전역이라 A 서버에서 등록한 라이엇ID 가
        B 서버에서도 조회됐다. 이제는 `{guild_id: {user_id: {...}}}` 다.
        옛 항목이 어느 길드 것이었는지 알 방법이 없으므로, 임의의 길드로
        옮기지 않고 백업 후 버린다 — 사용자는 쓰는 서버에서 다시 등록한다.
        """
        if not self._data:
            return
        legacy = [
            key
            for key, value in self._data.items()
            if isinstance(value, dict) and "name" in value and "tag" in value
        ]
        if not legacy:
            return
        backup = self._path.with_suffix(self._path.suffix + ".legacy")
        try:
            self._path.replace(backup)
            log.warning(
                "%s: dropped %d global registrations (backed up to %s) — "
                "registrations are per-guild now, users must re-register",
                type(self).__name__,
                len(legacy),
                backup,
            )
        except OSError:
            log.exception("legacy registration backup failed: %s", self._path)
        self._data = {}

    async def set(
        self, guild_id: int, user_id: int, *, name: str, tag: str, region: str, platform: str
    ) -> None:
        async with self._lock:
            bucket = self._data.setdefault(str(guild_id), {})
            bucket[str(user_id)] = {
                "name": name,
                "tag": tag,
                "region": region,
                "platform": platform,
            }
            await self._save_unlocked()

    async def get(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._data.get(str(guild_id), {}).get(str(user_id))
            return dict(entry) if entry else None

    async def remove(self, guild_id: int, user_id: int) -> bool:
        async with self._lock:
            bucket = self._data.get(str(guild_id), {})
            existed = bucket.pop(str(user_id), None) is not None
            if not bucket:
                self._data.pop(str(guild_id), None)
            if existed:
                await self._save_unlocked()
            return existed

    async def _save_unlocked(self) -> None:
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._atomic_write, snapshot)

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".valorant_", dir=parent)
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
