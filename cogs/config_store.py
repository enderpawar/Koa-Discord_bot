"""Phase 2 — guild별 설정 영속화.

JSON 파일에 `{str(guild_id): {...}}` 구조로 저장한다.
- Rule 02: 상태는 guild_id 키 dict 로 격리
- Rule 05: 동시 쓰기는 asyncio.Lock + 디스크 IO는 asyncio.to_thread 로 루프 비차단
- 원자성: 임시파일 → os.replace (POSIX/Windows 양쪽에서 atomic)

`_data` 는 인메모리 권위 사본이며, get() 은 mtime 변동이 감지될 때만
디스크에서 reload 한다 (외부 프로세스가 같은 파일을 수정하는 경우 대응).
read_text 매 호출의 동기 IO 가 이벤트 루프를 블록하던 문제를 제거한다.

여러 cog 가 `ConfigStore()` 를 따로 호출해도 동일 path 면 같은 인스턴스를
반환한다 (path-level singleton). web_admin_cog 가 `set()` 한 결과를 tts_cog
가 `get_cached_sync()` 로 즉시 관측하기 위함 — 이게 보장 안 되면 핫 경로
fast-path 가 stale cache 를 들고 외부 변경을 영영 못 본다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar

log = logging.getLogger(__name__)


def _default_config_path() -> Path:
    # Railway 등 컨테이너 배포에서는 영구 volume 경로를 CONFIG_PATH 로 주입한다.
    # 로컬 실행은 환경변수 부재 → 기존 동작(repo root/config.json) 유지.
    return Path(os.getenv("CONFIG_PATH", "config.json"))


class ConfigStore:
    _instances: ClassVar[dict[str, "ConfigStore"]] = {}

    def __new__(cls, path: Path | str | None = None) -> "ConfigStore":
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
        target = Path(path) if path is not None else _default_config_path()
        return os.path.abspath(str(target))

    def __init__(self, path: Path | str | None = None) -> None:
        # __new__ 가 동일 path 에 대해 같은 인스턴스를 반환하므로, 두 번째
        # __init__ 호출은 무시해야 _data/_lock 이 리셋되지 않는다.
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._path = Path(path) if path is not None else _default_config_path()
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._last_mtime: float = 0.0
        # 부팅 시 1회만 동기 로드. 이후 호출은 mtime 변동 시에만 to_thread 로 reload.
        self._reload_from_disk()

    @classmethod
    def _reset_instances_for_tests(cls) -> None:
        """Test-only: tmp_config_path 마다 새 인스턴스를 보장하기 위해."""
        cls._instances.clear()

    def _stat_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return 0.0
        except OSError:
            # 일시적 IO 오류는 캐시된 mtime 을 그대로 사용 (다음 호출에서 재시도)
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
            # 손상된 파일: 백업 후 빈 상태로 시작 (Rule 03)
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("config corrupt, backed up to %s", backup)
            except OSError:
                log.exception("config corrupt and backup failed: %s", self._path)
            self._data = {}
            self._last_mtime = 0.0

    async def _maybe_reload(self) -> None:
        current = await asyncio.to_thread(self._stat_mtime)
        if current != self._last_mtime:
            await asyncio.to_thread(self._reload_from_disk)

    async def get(self, guild_id: int) -> dict[str, Any]:
        async with self._lock:
            await self._maybe_reload()
            return dict(self._data.get(str(guild_id), {}))

    def get_cached_sync(self, guild_id: int) -> dict[str, Any]:
        """락/IO 없이 인메모리 사본을 반환. 핫 경로용 fast-path.

        외부 프로세스가 파일을 수정한 경우 다음 await get() 호출에서 자연
        동기화되므로, 채널 라우팅 같은 즉시 분기 용도에만 쓰일 것.
        """
        return dict(self._data.get(str(guild_id), {}))

    async def set(self, guild_id: int, **fields: Any) -> None:
        async with self._lock:
            await self._maybe_reload()
            cur = self._data.setdefault(str(guild_id), {})
            cur.update({k: v for k, v in fields.items() if v is not None})
            await self._save_unlocked()
            log.info("config updated: guild_id=%s keys=%s", guild_id, list(fields.keys()))

    async def remove_guild(self, guild_id: int) -> None:
        async with self._lock:
            await self._maybe_reload()
            if self._data.pop(str(guild_id), None) is not None:
                await self._save_unlocked()
                log.info("config removed: guild_id=%s", guild_id)

    async def save(self) -> None:
        async with self._lock:
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        # 스냅샷을 lock 안에서 만들고, 디스크 IO는 별도 스레드로 위임
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        new_mtime = await asyncio.to_thread(self._atomic_write, snapshot)
        self._last_mtime = new_mtime

    def _atomic_write(self, payload: str) -> float:
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
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0
