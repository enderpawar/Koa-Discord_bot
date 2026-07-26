"""SQLite persistence for lightweight Discord party recruitment."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Party:
    id: int
    guild_id: int
    channel_id: int
    message_id: int
    owner_id: int
    game: str
    capacity: int
    starts_at: float
    note: str
    status: str
    created_at: float
    reminder_sent: bool
    members: tuple[int, ...] = ()
    waitlist: tuple[int, ...] = ()


@dataclass(frozen=True)
class PartyMutation:
    party: Party | None
    outcome: str
    promoted_user_id: int | None = None


def _default_party_path() -> Path:
    return Path(os.getenv("PARTY_DB_PATH", "party.db"))


class PartyStore:
    """Guild-isolated party state with serialized, off-loop SQLite access."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_party_path()
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS parties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL DEFAULT 0,
                    owner_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    capacity INTEGER NOT NULL,
                    starts_at REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at REAL NOT NULL,
                    reminder_sent INTEGER NOT NULL DEFAULT 0
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_parties_message
                ON parties(message_id) WHERE message_id > 0;

                CREATE INDEX IF NOT EXISTS idx_parties_guild_status_start
                ON parties(guild_id, status, starts_at);

                CREATE TABLE IF NOT EXISTS party_members (
                    party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY (party_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_party_members_order
                ON party_members(party_id, role, joined_at);
                """
            )

    async def create(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        *,
        game: str,
        capacity: int,
        starts_at: float,
        note: str = "",
        now_ts: float | None = None,
    ) -> Party:
        if capacity < 2:
            raise ValueError("capacity must be at least 2")
        if not game.strip():
            raise ValueError("game is required")
        created_at = now_ts if now_ts is not None else time.time()
        async with self._lock:
            return await asyncio.to_thread(
                self._create_sync,
                guild_id,
                channel_id,
                owner_id,
                game.strip(),
                capacity,
                starts_at,
                note.strip(),
                created_at,
            )

    def _create_sync(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        game: str,
        capacity: int,
        starts_at: float,
        note: str,
        created_at: float,
    ) -> Party:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO parties (
                    guild_id, channel_id, owner_id, game, capacity,
                    starts_at, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    owner_id,
                    game,
                    capacity,
                    starts_at,
                    note,
                    created_at,
                ),
            )
            party_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO party_members (party_id, user_id, role, joined_at)
                VALUES (?, ?, 'member', ?)
                """,
                (party_id, owner_id, created_at),
            )
            return self._get_party_sync(conn, guild_id, party_id)

    async def bind_message(self, guild_id: int, party_id: int, message_id: int) -> Party:
        async with self._lock:
            return await asyncio.to_thread(
                self._bind_message_sync, guild_id, party_id, message_id
            )

    def _bind_message_sync(self, guild_id: int, party_id: int, message_id: int) -> Party:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE parties SET message_id = ? WHERE id = ? AND guild_id = ?",
                (message_id, party_id, guild_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("party not found")
            return self._get_party_sync(conn, guild_id, party_id)

    async def delete(self, guild_id: int, party_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, guild_id, party_id)

    def _delete_sync(self, guild_id: int, party_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM parties WHERE id = ? AND guild_id = ?",
                (party_id, guild_id),
            )

    async def remove_guild(self, guild_id: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._remove_guild_sync, guild_id)

    def _remove_guild_sync(self, guild_id: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM parties WHERE guild_id = ?",
                (guild_id,),
            )
            return max(0, int(cursor.rowcount))

    async def get(self, guild_id: int, party_id: int) -> Party | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, guild_id, party_id)

    def _get_sync(self, guild_id: int, party_id: int) -> Party | None:
        with self._connect() as conn:
            return self._get_party_sync(conn, guild_id, party_id, required=False)

    async def get_by_message(self, guild_id: int, message_id: int) -> Party | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_by_message_sync, guild_id, message_id
            )

    def _get_by_message_sync(self, guild_id: int, message_id: int) -> Party | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM parties WHERE guild_id = ? AND message_id = ?",
                (guild_id, message_id),
            ).fetchone()
            if row is None:
                return None
            return self._get_party_sync(conn, guild_id, int(row["id"]))

    async def join(
        self,
        guild_id: int,
        message_id: int,
        user_id: int,
        *,
        now_ts: float | None = None,
    ) -> PartyMutation:
        joined_at = now_ts if now_ts is not None else time.time()
        async with self._lock:
            return await asyncio.to_thread(
                self._join_sync, guild_id, message_id, user_id, joined_at
            )

    def _join_sync(
        self, guild_id: int, message_id: int, user_id: int, joined_at: float
    ) -> PartyMutation:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM parties WHERE guild_id = ? AND message_id = ?",
                (guild_id, message_id),
            ).fetchone()
            if row is None:
                return PartyMutation(None, "not_found")
            party_id = int(row["id"])
            party = self._get_party_sync(conn, guild_id, party_id)
            if party.status != "open":
                return PartyMutation(party, "closed")

            existing = conn.execute(
                "SELECT role FROM party_members WHERE party_id = ? AND user_id = ?",
                (party_id, user_id),
            ).fetchone()
            if existing is not None:
                outcome = (
                    "already_member"
                    if existing["role"] == "member"
                    else "already_waiting"
                )
                return PartyMutation(party, outcome)

            member_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM party_members
                    WHERE party_id = ? AND role = 'member'
                    """,
                    (party_id,),
                ).fetchone()[0]
            )
            role = "member" if member_count < party.capacity else "waitlist"
            conn.execute(
                """
                INSERT INTO party_members (party_id, user_id, role, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (party_id, user_id, role, joined_at),
            )
            updated = self._get_party_sync(conn, guild_id, party_id)
            return PartyMutation(updated, "joined" if role == "member" else "waitlisted")

    async def cancel(
        self, guild_id: int, message_id: int, user_id: int
    ) -> PartyMutation:
        async with self._lock:
            return await asyncio.to_thread(
                self._cancel_sync, guild_id, message_id, user_id
            )

    def _cancel_sync(
        self, guild_id: int, message_id: int, user_id: int
    ) -> PartyMutation:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM parties WHERE guild_id = ? AND message_id = ?",
                (guild_id, message_id),
            ).fetchone()
            if row is None:
                return PartyMutation(None, "not_found")
            party_id = int(row["id"])
            party = self._get_party_sync(conn, guild_id, party_id)
            if party.status != "open":
                return PartyMutation(party, "closed")
            if user_id == party.owner_id:
                return PartyMutation(party, "owner_cannot_cancel")

            member = conn.execute(
                "SELECT role FROM party_members WHERE party_id = ? AND user_id = ?",
                (party_id, user_id),
            ).fetchone()
            if member is None:
                return PartyMutation(party, "not_joined")

            role = str(member["role"])
            conn.execute(
                "DELETE FROM party_members WHERE party_id = ? AND user_id = ?",
                (party_id, user_id),
            )
            promoted_user_id: int | None = None
            if role == "member":
                waiting = conn.execute(
                    """
                    SELECT user_id FROM party_members
                    WHERE party_id = ? AND role = 'waitlist'
                    ORDER BY joined_at, user_id
                    LIMIT 1
                    """,
                    (party_id,),
                ).fetchone()
                if waiting is not None:
                    promoted_user_id = int(waiting["user_id"])
                    conn.execute(
                        """
                        UPDATE party_members SET role = 'member'
                        WHERE party_id = ? AND user_id = ?
                        """,
                        (party_id, promoted_user_id),
                    )

            updated = self._get_party_sync(conn, guild_id, party_id)
            return PartyMutation(updated, "cancelled", promoted_user_id)

    async def close(
        self,
        guild_id: int,
        message_id: int,
        actor_id: int,
        *,
        can_manage_messages: bool = False,
    ) -> PartyMutation:
        async with self._lock:
            return await asyncio.to_thread(
                self._close_sync,
                guild_id,
                message_id,
                actor_id,
                can_manage_messages,
            )

    def _close_sync(
        self,
        guild_id: int,
        message_id: int,
        actor_id: int,
        can_manage_messages: bool,
    ) -> PartyMutation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parties WHERE guild_id = ? AND message_id = ?",
                (guild_id, message_id),
            ).fetchone()
            if row is None:
                return PartyMutation(None, "not_found")
            party_id = int(row["id"])
            party = self._get_party_sync(conn, guild_id, party_id)
            if party.status != "open":
                return PartyMutation(party, "closed")
            if actor_id != party.owner_id and not can_manage_messages:
                return PartyMutation(party, "forbidden")
            conn.execute(
                "UPDATE parties SET status = 'closed' WHERE id = ?",
                (party_id,),
            )
            return PartyMutation(
                self._get_party_sync(conn, guild_id, party_id),
                "closed_now",
            )

    async def list_open(self, guild_id: int, *, limit: int = 10) -> list[Party]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_open_sync, guild_id, max(1, limit)
            )

    def _list_open_sync(self, guild_id: int, limit: int) -> list[Party]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM parties
                WHERE guild_id = ? AND status = 'open'
                ORDER BY starts_at, created_at
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
            return [
                self._get_party_sync(conn, guild_id, int(row["id"])) for row in rows
            ]

    async def list_for_user(
        self, guild_id: int, user_id: int, *, limit: int = 10
    ) -> list[Party]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_for_user_sync, guild_id, user_id, max(1, limit)
            )

    def _list_for_user_sync(
        self, guild_id: int, user_id: int, limit: int
    ) -> list[Party]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.id
                FROM parties p
                JOIN party_members pm ON pm.party_id = p.id
                WHERE p.guild_id = ? AND p.status = 'open' AND pm.user_id = ?
                ORDER BY p.starts_at, p.created_at
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
            return [
                self._get_party_sync(conn, guild_id, int(row["id"])) for row in rows
            ]

    async def claim_due_reminders(
        self, *, now_ts: float | None = None, window_sec: float = 1800
    ) -> list[Party]:
        current = now_ts if now_ts is not None else time.time()
        async with self._lock:
            return await asyncio.to_thread(
                self._claim_due_reminders_sync, current, window_sec
            )

    def _claim_due_reminders_sync(
        self, now_ts: float, window_sec: float
    ) -> list[Party]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, guild_id FROM parties
                WHERE status = 'open'
                  AND reminder_sent = 0
                  AND starts_at > ?
                  AND starts_at <= ?
                ORDER BY starts_at
                """,
                (now_ts, now_ts + window_sec),
            ).fetchall()
            if rows:
                conn.executemany(
                    "UPDATE parties SET reminder_sent = 1 WHERE id = ?",
                    [(int(row["id"]),) for row in rows],
                )
            return [
                self._get_party_sync(
                    conn, int(row["guild_id"]), int(row["id"])
                )
                for row in rows
            ]

    async def claim_expired(self, *, now_ts: float | None = None) -> list[Party]:
        current = now_ts if now_ts is not None else time.time()
        async with self._lock:
            return await asyncio.to_thread(self._claim_expired_sync, current)

    def _claim_expired_sync(self, now_ts: float) -> list[Party]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, guild_id FROM parties
                WHERE status = 'open' AND starts_at <= ?
                ORDER BY starts_at
                """,
                (now_ts,),
            ).fetchall()
            if rows:
                conn.executemany(
                    "UPDATE parties SET status = 'closed' WHERE id = ?",
                    [(int(row["id"]),) for row in rows],
                )
            return [
                self._get_party_sync(
                    conn, int(row["guild_id"]), int(row["id"])
                )
                for row in rows
            ]

    def _get_party_sync(
        self,
        conn: sqlite3.Connection,
        guild_id: int,
        party_id: int,
        *,
        required: bool = True,
    ) -> Party | None:
        row = conn.execute(
            "SELECT * FROM parties WHERE id = ? AND guild_id = ?",
            (party_id, guild_id),
        ).fetchone()
        if row is None:
            if required:
                raise LookupError("party not found")
            return None
        member_rows = conn.execute(
            """
            SELECT user_id, role FROM party_members
            WHERE party_id = ?
            ORDER BY joined_at, user_id
            """,
            (party_id,),
        ).fetchall()
        members = tuple(
            int(member["user_id"])
            for member in member_rows
            if member["role"] == "member"
        )
        waitlist = tuple(
            int(member["user_id"])
            for member in member_rows
            if member["role"] == "waitlist"
        )
        return Party(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            message_id=int(row["message_id"]),
            owner_id=int(row["owner_id"]),
            game=str(row["game"]),
            capacity=int(row["capacity"]),
            starts_at=float(row["starts_at"]),
            note=str(row["note"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            reminder_sent=bool(row["reminder_sent"]),
            members=members,
            waitlist=waitlist,
        )
