"""User activity rank persistence.

Guild-separated JSON store for weekly activity ranking.
Voice time is measured from voice state join/leave events.
Chat time is approximated from message activity windows because Discord does not
expose a "chat presence duration" signal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
RESET_WEEKDAY = 4  # Friday
CHAT_ACTIVE_GRACE_SEC = 60
CHAT_ACTIVE_WINDOW_SEC = 300
VOICE_WEIGHT = 70
CHAT_WEIGHT = 30


def activity_score(voice_seconds: int, chat_seconds: int) -> int:
    """Return weighted activity score in centiseconds-equivalent units."""
    return (voice_seconds * VOICE_WEIGHT) + (chat_seconds * CHAT_WEIGHT)


def _default_rank_path() -> Path:
    return Path(os.getenv("RANK_PATH", "rank_stats.json"))


def weekly_reset_anchor(now: datetime | None = None) -> str:
    """Return the latest Friday 00:00 KST anchor as an ISO string."""
    current = now.astimezone(KST) if now else datetime.now(KST)
    today_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_friday = (today_midnight.weekday() - RESET_WEEKDAY) % 7
    anchor = today_midnight - timedelta(days=days_since_friday)
    return anchor.isoformat()


def _timestamp_from_anchor(anchor: str) -> float:
    return datetime.fromisoformat(anchor).timestamp()


class RankStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_rank_path()
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.exception("rank stats corrupt, backed up to %s", backup)
            except OSError:
                log.exception("rank stats corrupt and backup failed: %s", self._path)
            self._data = {}

    async def ensure_week(self, *, now: datetime | None = None) -> bool:
        anchor = weekly_reset_anchor(now)
        anchor_ts = _timestamp_from_anchor(anchor)
        async with self._lock:
            changed = False
            for guild in self._data.values():
                if guild.get("week_anchor") != anchor:
                    active_users = {}
                    for user_id, user in guild.get("users", {}).items():
                        if "voice_joined_at" not in user or "voice_channel_id" not in user:
                            continue
                        active_users[user_id] = {
                            "voice_seconds": 0,
                            "chat_seconds": 0,
                            "message_count": 0,
                            "voice_joined_at": anchor_ts,
                            "voice_channel_id": user["voice_channel_id"],
                        }
                    guild["week_anchor"] = anchor
                    guild["users"] = active_users
                    changed = True
            if changed:
                await self._save_unlocked()
                log.info("weekly rank stats reset: anchor=%s", anchor)
            return changed

    async def record_message(
        self, guild_id: int, user_id: int, *, now_ts: float | None = None
    ) -> None:
        now_value = now_ts if now_ts is not None else datetime.now(KST).timestamp()
        async with self._lock:
            user = self._user_unlocked(guild_id, user_id)
            last_chat_at = user.get("last_chat_at")
            user["message_count"] = int(user.get("message_count", 0)) + 1
            if isinstance(last_chat_at, (int, float)):
                gap = max(0, int(now_value - float(last_chat_at)))
                if gap <= CHAT_ACTIVE_WINDOW_SEC:
                    user["chat_seconds"] = int(user.get("chat_seconds", 0)) + gap
                else:
                    user["chat_seconds"] = int(user.get("chat_seconds", 0)) + CHAT_ACTIVE_GRACE_SEC
            else:
                user["chat_seconds"] = int(user.get("chat_seconds", 0)) + CHAT_ACTIVE_GRACE_SEC
            user["last_chat_at"] = now_value
            await self._save_unlocked()

    async def start_voice(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        *,
        now_ts: float | None = None,
    ) -> None:
        now_value = now_ts if now_ts is not None else datetime.now(KST).timestamp()
        async with self._lock:
            user = self._user_unlocked(guild_id, user_id)
            user["voice_joined_at"] = now_value
            user["voice_channel_id"] = channel_id
            await self._save_unlocked()

    async def stop_voice(
        self, guild_id: int, user_id: int, *, now_ts: float | None = None
    ) -> int:
        now_value = now_ts if now_ts is not None else datetime.now(KST).timestamp()
        async with self._lock:
            user = self._user_unlocked(guild_id, user_id)
            joined_at = user.pop("voice_joined_at", None)
            user.pop("voice_channel_id", None)
            added = 0
            if isinstance(joined_at, (int, float)):
                added = max(0, int(now_value - float(joined_at)))
                user["voice_seconds"] = int(user.get("voice_seconds", 0)) + added
            await self._save_unlocked()
            return added

    async def leaderboard(
        self, guild_id: int, *, limit: int = 10, now_ts: float | None = None
    ) -> list[dict[str, int]]:
        now_value = now_ts if now_ts is not None else datetime.now(KST).timestamp()
        async with self._lock:
            guild = self._guild_unlocked(guild_id)
            rows = []
            for raw_user_id, user in guild.get("users", {}).items():
                voice_seconds = self._voice_seconds_with_active_unlocked(user, now_value)
                chat_seconds = int(user.get("chat_seconds", 0))
                rows.append(
                    {
                        "user_id": int(raw_user_id),
                        "voice_seconds": voice_seconds,
                        "chat_seconds": chat_seconds,
                        "message_count": int(user.get("message_count", 0)),
                        "total_seconds": voice_seconds + chat_seconds,
                        "score": activity_score(voice_seconds, chat_seconds),
                    }
                )
            rows.sort(key=lambda item: item["score"], reverse=True)
            return rows[:limit]

    async def user_stats(
        self, guild_id: int, user_id: int, *, now_ts: float | None = None
    ) -> dict[str, int]:
        now_value = now_ts if now_ts is not None else datetime.now(KST).timestamp()
        async with self._lock:
            user = self._guild_unlocked(guild_id).get("users", {}).get(str(user_id), {})
            voice_seconds = self._voice_seconds_with_active_unlocked(user, now_value)
            chat_seconds = int(user.get("chat_seconds", 0))
            return {
                "user_id": user_id,
                "voice_seconds": voice_seconds,
                "chat_seconds": chat_seconds,
                "message_count": int(user.get("message_count", 0)),
                "total_seconds": voice_seconds + chat_seconds,
                "score": activity_score(voice_seconds, chat_seconds),
            }

    def _guild_unlocked(self, guild_id: int) -> dict[str, Any]:
        guild = self._data.setdefault(
            str(guild_id),
            {"week_anchor": weekly_reset_anchor(), "users": {}},
        )
        guild.setdefault("week_anchor", weekly_reset_anchor())
        guild.setdefault("users", {})
        return guild

    def _user_unlocked(self, guild_id: int, user_id: int) -> dict[str, Any]:
        guild = self._guild_unlocked(guild_id)
        return guild["users"].setdefault(
            str(user_id),
            {"voice_seconds": 0, "chat_seconds": 0, "message_count": 0},
        )

    @staticmethod
    def _voice_seconds_with_active_unlocked(user: dict[str, Any], now_ts: float) -> int:
        voice_seconds = int(user.get("voice_seconds", 0))
        joined_at = user.get("voice_joined_at")
        if isinstance(joined_at, (int, float)):
            voice_seconds += max(0, int(now_ts - float(joined_at)))
        return voice_seconds

    async def _save_unlocked(self) -> None:
        snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._atomic_write, snapshot)

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".rank_", dir=parent)
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
