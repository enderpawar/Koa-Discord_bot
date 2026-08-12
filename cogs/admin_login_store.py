"""Atomic, single-use web dashboard login grants.

Discord에서 관리자 권한을 확인한 뒤 발급한 짧은 토큰을 SQLite에 해시로만
보관한다. 소비는 DELETE ... RETURNING 한 문장으로 처리해 같은 토큰을 동시에
제출해도 정확히 한 요청만 성공한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

_TOKEN_BYTES = 32
DEFAULT_LOGIN_GRANT_TTL_SECONDS = 5 * 60


def _default_path() -> Path:
    return Path(os.getenv("ADMIN_LOGIN_DB_PATH", "admin_login.sqlite3"))


def hash_login_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class LoginGrant:
    guild_id: int
    user_id: int
    expires_at: int


class OneTimeLoginStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_path()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def issue(
        self,
        guild_id: int,
        user_id: int,
        *,
        ttl_seconds: int = DEFAULT_LOGIN_GRANT_TTL_SECONDS,
        now_ts: int | None = None,
    ) -> str:
        await self.initialize()
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = int(time.time()) if now_ts is None else int(now_ts)
        expires_at = now + max(1, int(ttl_seconds))
        await asyncio.to_thread(
            self._issue_sync,
            hash_login_token(token),
            guild_id,
            user_id,
            now,
            expires_at,
        )
        return token

    async def peek(self, token: str, *, now_ts: int | None = None) -> LoginGrant | None:
        """토큰을 쓰지 않고 귀속 정보만 읽는다.

        Discord 관리자 권한 확인이 실패했을 때 링크를 태우지 않으려면, 소비보다
        먼저 대상을 알아야 한다. 단일 사용 보장은 consume() 의
        DELETE ... RETURNING 이 그대로 책임지므로 이 조회는 경쟁을 만들지 않는다.
        """
        if not token:
            return None
        await self.initialize()
        now = int(time.time()) if now_ts is None else int(now_ts)
        row = await asyncio.to_thread(self._peek_sync, hash_login_token(token), now)
        return self._grant(row)

    async def consume(self, token: str, *, now_ts: int | None = None) -> LoginGrant | None:
        if not token:
            return None
        await self.initialize()
        now = int(time.time()) if now_ts is None else int(now_ts)
        row = await asyncio.to_thread(self._consume_sync, hash_login_token(token), now)
        return self._grant(row)

    @staticmethod
    def _grant(row: tuple[str, str, int] | None) -> LoginGrant | None:
        if row is None:
            return None
        return LoginGrant(guild_id=int(row[0]), user_id=int(row[1]), expires_at=int(row[2]))

    async def revoke_for_guild(self, guild_id: int) -> int:
        await self.initialize()
        return await asyncio.to_thread(self._revoke_for_guild_sync, guild_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _session(self):
        """커밋하고 나서 반드시 닫는다.

        sqlite3.Connection 을 with 로만 감싸면 트랜잭션은 끝나지만 연결은 열린 채
        남는다. 로그인 시도마다 연결이 쌓이지 않게 closing() 을 겉에 두른다.
        """
        return closing(self._connect())

    def _initialize_sync(self) -> None:
        parent = self._path.parent if str(self._path.parent) else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS login_grants (
                    token_hash BLOB PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS login_grants_expiry ON login_grants(expires_at)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS login_grants_principal
                ON login_grants(guild_id, user_id)
                """
            )
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _issue_sync(
        self,
        token_hash: bytes,
        guild_id: int,
        user_id: int,
        issued_at: int,
        expires_at: int,
    ) -> None:
        with self._session() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM login_grants WHERE expires_at <= ?", (issued_at,))
            # 같은 사용자·길드에서 새 링크를 만들면 이전 미사용 링크도 즉시 폐기한다.
            connection.execute(
                "DELETE FROM login_grants WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            )
            connection.execute(
                """
                INSERT INTO login_grants(token_hash, guild_id, user_id, issued_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, str(guild_id), str(user_id), issued_at, expires_at),
            )

    def _peek_sync(self, token_hash: bytes, now: int) -> tuple[str, str, int] | None:
        with self._session() as connection:
            return connection.execute(
                """
                SELECT guild_id, user_id, expires_at FROM login_grants
                WHERE token_hash = ? AND expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()

    def _consume_sync(self, token_hash: bytes, now: int) -> tuple[str, str, int] | None:
        with self._session() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM login_grants WHERE expires_at <= ?", (now,))
            row = connection.execute(
                """
                DELETE FROM login_grants
                WHERE token_hash = ? AND expires_at > ?
                RETURNING guild_id, user_id, expires_at
                """,
                (token_hash, now),
            ).fetchone()
            return row

    def _revoke_for_guild_sync(self, guild_id: int) -> int:
        with self._session() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM login_grants WHERE guild_id = ?", (str(guild_id),)
            )
            return max(0, cursor.rowcount)
