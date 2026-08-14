"""파티 임베드에 붙일 게임 티어 스냅샷 캐시.

`lol_store` / `valorant_store` 가 "이 사람이 등록한 라이엇ID" 를 들고 있다면,
여기는 "그 계정이 마지막으로 조회됐을 때 몇 티어였는지" 를 조회 시각과 함께
들고 있다. 등록 정보와 파일을 나눈 이유는 수명이 다르기 때문이다 — 등록은
사용자가 지울 때까지 유효하지만 티어는 TTL 이 지나면 못 믿는다. 두 데이터를
한 파일에 두면 티어를 갱신할 때마다 등록 정보까지 다시 쓰게 된다.

파일 구조는 두 store 와 같은 `{guild_id: {user_id: {game: {...}}}}` 이고
(Rule 02), 쓰기도 같은 임시파일 → `os.replace` 원자적 패턴이다 (Rule 05).

`get_sync` 만 있고 비동기 get 이 없다. 이 파일을 쓰는 프로세스는 봇 하나뿐이라
인메모리 사본이 항상 권위 사본이고, 임베드를 그리는 경로에서 IO 를 타면
버튼 응답이 그만큼 늦어진다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 티어는 자주 바뀌지 않고, 갱신 한 번이 곧 API 호출이라 하루로 잡는다.
# 개발 키(롤 2분당 100회, 발로 분당 30회) 한도 안에서 파티가 붐벼도 버틴다.
_DEFAULT_TTL_HOURS = 24.0


def _default_path() -> Path:
    return Path(os.getenv("TIER_CACHE_PATH", "tier_cache.json"))


def _default_ttl_sec() -> float:
    raw = os.getenv("TIER_CACHE_TTL_HOURS", "").strip()
    if not raw:
        return _DEFAULT_TTL_HOURS * 3600
    try:
        hours = float(raw)
    except ValueError:
        log.warning("TIER_CACHE_TTL_HOURS 값이 숫자가 아님(%r) — 기본값 사용", raw)
        return _DEFAULT_TTL_HOURS * 3600
    if hours <= 0:
        log.warning("TIER_CACHE_TTL_HOURS 는 양수여야 함(%r) — 기본값 사용", raw)
        return _DEFAULT_TTL_HOURS * 3600
    return hours * 3600


@dataclass(frozen=True)
class TierSnapshot:
    """한 계정의 티어를 조회 시점과 함께 굳힌 값."""

    game: str
    # 정규화된 대문자 티어 키. 랭크가 없으면 "UNRANKED".
    tier: str
    # 티어 안의 단계. 롤은 "1"~"4", 발로란트는 "1"~"3", 최상위 티어는 빈 문자열.
    division: str
    # 화면에 그대로 쓰는 한국어 표기. 예: "골드 2", "언랭".
    label: str
    # 같은 게임 안에서의 정렬용 서열. 언랭이 0 이고 위로 갈수록 커진다.
    weight: int
    fetched_at: float
    # 롤 랭크 조회는 puuid 가 필요하다. 한 번 받아 두면 다음 갱신에서
    # account 조회 한 번을 아낄 수 있어 스냅샷에 같이 굳힌다.
    puuid: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": self.game,
            "tier": self.tier,
            "division": self.division,
            "label": self.label,
            "weight": self.weight,
            "fetched_at": self.fetched_at,
            "puuid": self.puuid,
        }

    @classmethod
    def from_dict(cls, game: str, raw: Any) -> "TierSnapshot | None":
        """저장된 dict 를 되살린다. 형식이 어긋나면 캐시 미스로 취급한다."""
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                game=game,
                tier=str(raw["tier"]),
                division=str(raw.get("division", "")),
                label=str(raw["label"]),
                weight=int(raw.get("weight", 0)),
                fetched_at=float(raw.get("fetched_at", 0.0)),
                puuid=str(raw.get("puuid", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class TierStore:
    def __init__(
        self, path: Path | str | None = None, *, ttl_sec: float | None = None
    ) -> None:
        self._path = Path(path) if path is not None else _default_path()
        self._ttl_sec = _default_ttl_sec() if ttl_sec is None else float(ttl_sec)
        self._lock = asyncio.Lock()
        # {guild_id: {user_id: {game: {...}}}}
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._load_from_disk()

    @property
    def ttl_sec(self) -> float:
        return self._ttl_sec

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 캐시는 언제든 다시 만들 수 있으므로 백업 없이 버린다.
            log.exception("tier cache unreadable, starting empty: %s", self._path)
            self._data = {}
            return
        if isinstance(loaded, dict):
            self._data = loaded

    def get_sync(self, guild_id: int, user_id: int, game: str) -> TierSnapshot | None:
        """락/IO 없이 캐시된 스냅샷을 반환한다. 신선도는 따지지 않는다."""
        raw = (
            self._data.get(str(guild_id), {})
            .get(str(user_id), {})
            .get(game)
        )
        return TierSnapshot.from_dict(game, raw)

    def is_fresh(self, snapshot: TierSnapshot, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return (current - snapshot.fetched_at) < self._ttl_sec

    async def set(self, guild_id: int, user_id: int, snapshot: TierSnapshot) -> None:
        async with self._lock:
            guild_bucket = self._data.setdefault(str(guild_id), {})
            user_bucket = guild_bucket.setdefault(str(user_id), {})
            user_bucket[snapshot.game] = snapshot.to_dict()
            await self._save_unlocked()

    async def remove_guild(self, guild_id: int) -> bool:
        async with self._lock:
            if self._data.pop(str(guild_id), None) is None:
                return False
            await self._save_unlocked()
            return True

    async def _save_unlocked(self) -> None:
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._atomic_write, snapshot)

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tier_", dir=parent)
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
