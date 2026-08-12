"""서버(길드)별 웹 대시보드 로그인 키 영속화.

`{str(guild_id): {"hash": ..., "issued_to": ..., "issued_at": ...}}` 구조.

**평문 키는 저장하지 않는다.** 발급 순간에만 호출자에게 돌려주고, 디스크에는
sha256 해시만 남긴다. 파일이 유출돼도 그 자체로는 로그인할 수 없다. 키가
`secrets.token_urlsafe(32)` = 256비트 난수라 사전 공격 대상이 아니므로,
bcrypt 같은 느린 KDF 대신 sha256 이면 충분하다 (느린 KDF 는 오히려 로그인마다
전체 키를 순회하는 이 구조에서 응답 지연이 된다).

- Rule 02: 상태는 guild_id 키 dict 로 격리
- Rule 03: 파일이 깨지면 백업 후 빈 상태로 시작 — 봇을 죽이지 않는다
- Rule 05: asyncio.Lock + 디스크 IO 는 asyncio.to_thread
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

log = logging.getLogger(__name__)

_KEY_BYTES = 32


def _default_path() -> Path:
    return Path(os.getenv("ADMIN_KEYS_PATH", "admin_keys.json"))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AdminKeyStore:
    _instances: ClassVar[dict[str, "AdminKeyStore"]] = {}

    def __new__(cls, path: Path | str | None = None) -> "AdminKeyStore":
        resolved = cls._resolve_path(path)
        existing = cls._instances.get(resolved)
        if existing is not None:
            return existing
        instance = super().__new__(cls)
        instance._initialized = False
        cls._instances[resolved] = instance
        return instance

    @staticmethod
    def _resolve_path(path: Path | str | None) -> str:
        target = Path(path) if path is not None else _default_path()
        return os.path.abspath(str(target))

    def __init__(self, path: Path | str | None = None) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._path = Path(path) if path is not None else _default_path()
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._last_mtime: float = 0.0
        self._reload_from_disk()

    @classmethod
    def _reset_instances_for_tests(cls) -> None:
        cls._instances.clear()

    def _stat_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return 0.0
        except OSError:
            return self._last_mtime

    def _reload_from_disk(self) -> None:
        if not self._path.exists():
            self._data = {}
            self._last_mtime = 0.0
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            self._last_mtime = self._stat_mtime()
        except (OSError, json.JSONDecodeError):
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("admin keys corrupt, backed up to %s", backup)
            except OSError:
                log.exception("admin keys corrupt and backup failed: %s", self._path)
            self._data = {}
            self._last_mtime = 0.0

    async def _maybe_reload(self) -> None:
        current = await asyncio.to_thread(self._stat_mtime)
        if current != self._last_mtime:
            await asyncio.to_thread(self._reload_from_disk)

    async def issue(self, guild_id: int, *, issued_to: int | None = None) -> str:
        """새 키를 발급하고 평문을 돌려준다. 기존 키는 즉시 무효가 된다.

        평문을 볼 수 있는 것은 이 반환값 한 번뿐이다.
        """
        key = secrets.token_urlsafe(_KEY_BYTES)
        async with self._lock:
            await self._maybe_reload()
            self._data[str(guild_id)] = {
                "hash": hash_key(key),
                "issued_to": str(issued_to) if issued_to is not None else None,
                "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            await self._save_unlocked()
        log.info("dashboard key issued: guild_id=%s", guild_id)
        return key

    async def find_guild(self, key: str) -> int | None:
        """키에 해당하는 guild_id. 없으면 None."""
        if not key:
            return None
        candidate = hash_key(key)
        async with self._lock:
            await self._maybe_reload()
            for guild_id, record in self._data.items():
                stored = record.get("hash") or ""
                if stored and hmac.compare_digest(stored, candidate):
                    try:
                        return int(guild_id)
                    except ValueError:
                        return None
        return None

    async def revoke(self, guild_id: int) -> bool:
        async with self._lock:
            await self._maybe_reload()
            if self._data.pop(str(guild_id), None) is None:
                return False
            await self._save_unlocked()
        log.info("dashboard key revoked: guild_id=%s", guild_id)
        return True

    async def info(self, guild_id: int) -> dict[str, Any] | None:
        """발급 이력 메타데이터. 해시는 돌려주지 않는다."""
        async with self._lock:
            await self._maybe_reload()
            record = self._data.get(str(guild_id))
            if record is None:
                return None
            return {
                "issued_to": record.get("issued_to"),
                "issued_at": record.get("issued_at"),
            }

    async def _save_unlocked(self) -> None:
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        new_mtime = await asyncio.to_thread(self._atomic_write, snapshot)
        self._last_mtime = new_mtime

    def _atomic_write(self, payload: str) -> float:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".keys_", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
            # 키 해시 파일은 소유자만 읽게 한다 (POSIX). Windows 는 무시된다.
            os.chmod(self._path, 0o600)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0
