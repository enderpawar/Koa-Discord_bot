from __future__ import annotations

import asyncio
import sqlite3

from cogs.admin_login_store import OneTimeLoginStore, hash_login_token


async def test_grant_is_scoped_and_single_use(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    token = await store.issue(111, 222, now_ts=100, ttl_seconds=300)

    grant = await store.consume(token, now_ts=101)

    assert grant is not None
    assert (grant.guild_id, grant.user_id, grant.expires_at) == (111, 222, 400)
    assert await store.consume(token, now_ts=102) is None


async def test_concurrent_consumers_have_exactly_one_winner(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    token = await store.issue(111, 222, now_ts=100)

    results = await asyncio.gather(*(store.consume(token, now_ts=101) for _ in range(8)))

    assert sum(result is not None for result in results) == 1


async def test_new_grant_revokes_previous_grant_for_same_principal(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    old = await store.issue(111, 222, now_ts=100)
    new = await store.issue(111, 222, now_ts=101)

    assert await store.consume(old, now_ts=102) is None
    assert await store.consume(new, now_ts=102) is not None


async def test_peek_reads_the_grant_without_consuming_it(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    token = await store.issue(111, 222, now_ts=100, ttl_seconds=300)

    peeked = await store.peek(token, now_ts=101)

    assert peeked is not None
    assert (peeked.guild_id, peeked.user_id) == (111, 222)
    # 여러 번 들여다봐도 링크는 살아 있고, 소비는 여전히 한 번만 성공한다.
    assert await store.peek(token, now_ts=102) is not None
    assert await store.consume(token, now_ts=103) is not None
    assert await store.peek(token, now_ts=104) is None


async def test_peek_rejects_expired_and_unknown_tokens(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    token = await store.issue(111, 222, now_ts=100, ttl_seconds=5)

    assert await store.peek(token, now_ts=105) is None
    assert await store.peek("nonexistent-token", now_ts=101) is None
    assert await store.peek("", now_ts=101) is None


async def test_expired_grant_is_rejected(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    token = await store.issue(111, 222, now_ts=100, ttl_seconds=5)

    assert await store.consume(token, now_ts=105) is None


async def test_database_stores_only_token_hash(tmp_path) -> None:
    path = tmp_path / "login.sqlite3"
    store = OneTimeLoginStore(path)
    token = await store.issue(111, 222, now_ts=100)

    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT token_hash FROM login_grants").fetchone()

    assert row == (hash_login_token(token),)
    assert token.encode() not in path.read_bytes()


async def test_revoke_for_guild_does_not_touch_other_guild(tmp_path) -> None:
    store = OneTimeLoginStore(tmp_path / "login.sqlite3")
    mine = await store.issue(111, 1, now_ts=100)
    other = await store.issue(222, 1, now_ts=100)

    assert await store.revoke_for_guild(111) == 1
    assert await store.consume(mine, now_ts=101) is None
    assert await store.consume(other, now_ts=101) is not None
