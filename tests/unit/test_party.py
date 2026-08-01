from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import discord
from discord.ext import commands

from cogs.party_cog import find_game_role, parse_party_start, party_embed
from cogs.party_store import PartyStore

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def store(tmp_path: Path) -> PartyStore:
    return PartyStore(tmp_path / "party.db")


async def _bound_party(
    store: PartyStore,
    *,
    guild_id: int = 1,
    owner_id: int = 10,
    capacity: int = 2,
    starts_at: float = 2000,
):
    party = await store.create(
        guild_id,
        100,
        owner_id,
        game="롤",
        capacity=capacity,
        starts_at=starts_at,
        note="즐겜",
        now_ts=1000,
    )
    return await store.bind_message(guild_id, party.id, 500)


def test_parse_party_start_supports_korean_and_rolls_clock_forward() -> None:
    now = datetime(2026, 7, 26, 20, 0, tzinfo=KST)

    assert parse_party_start("오늘 21:00", now=now) == datetime(
        2026, 7, 26, 21, 0, tzinfo=KST
    )
    assert parse_party_start("내일 19:30", now=now) == datetime(
        2026, 7, 27, 19, 30, tzinfo=KST
    )
    assert parse_party_start("19:00", now=now) == datetime(
        2026, 7, 27, 19, 0, tzinfo=KST
    )


def test_parse_party_start_rejects_past_explicit_time() -> None:
    now = datetime(2026, 7, 26, 20, 0, tzinfo=KST)

    with pytest.raises(ValueError, match="현재보다 이후"):
        parse_party_start("오늘 19:00", now=now)


def test_find_game_role_matches_exact_name_case_insensitively() -> None:
    default_role = SimpleNamespace(name="LOL", is_default=lambda: True)
    lol_role = SimpleNamespace(name="LoL", is_default=lambda: False)
    guild = SimpleNamespace(roles=[default_role, lol_role])

    assert find_game_role(guild, "  @LOL ") is lol_role
    assert find_game_role(guild, "롤") is None


async def test_create_and_bind_message_is_guild_isolated(store: PartyStore) -> None:
    party = await _bound_party(store)

    assert party.message_id == 500
    assert party.members == (10,)
    assert await store.get_by_message(1, 500) == party
    assert await store.get_by_message(2, 500) is None


async def test_join_waitlist_cancel_and_promote(store: PartyStore) -> None:
    await _bound_party(store, capacity=2)

    joined = await store.join(1, 500, 20, now_ts=1001)
    waiting = await store.join(1, 500, 30, now_ts=1002)
    cancelled = await store.cancel(1, 500, 20)

    assert joined.outcome == "joined"
    assert joined.party is not None and joined.party.members == (10, 20)
    assert waiting.outcome == "waitlisted"
    assert waiting.party is not None and waiting.party.waitlist == (30,)
    assert cancelled.outcome == "cancelled"
    assert cancelled.promoted_user_id == 30
    assert cancelled.party is not None
    assert cancelled.party.members == (10, 30)
    assert cancelled.party.waitlist == ()


async def test_concurrent_join_never_exceeds_capacity(store: PartyStore) -> None:
    await _bound_party(store, capacity=2)

    results = await asyncio.gather(
        *(
            store.join(1, 500, user_id, now_ts=1000 + user_id)
            for user_id in range(20, 30)
        )
    )
    final = await store.get_by_message(1, 500)

    assert final is not None
    assert len(final.members) == 2
    assert len(final.waitlist) == 9
    assert sum(result.outcome == "joined" for result in results) == 1


async def test_join_is_idempotent_and_owner_cannot_cancel(store: PartyStore) -> None:
    await _bound_party(store)

    first = await store.join(1, 500, 20, now_ts=1001)
    duplicate = await store.join(1, 500, 20, now_ts=1002)
    owner_cancel = await store.cancel(1, 500, 10)

    assert first.outcome == "joined"
    assert duplicate.outcome == "already_member"
    assert owner_cancel.outcome == "owner_cannot_cancel"


async def test_only_owner_or_moderator_can_close(store: PartyStore) -> None:
    await _bound_party(store)

    denied = await store.close(1, 500, 99)
    closed = await store.close(1, 500, 99, can_manage_messages=True)

    assert denied.outcome == "forbidden"
    assert denied.party is not None and denied.party.status == "open"
    assert closed.outcome == "closed_now"
    assert closed.party is not None and closed.party.status == "closed"


async def test_reminders_are_claimed_once_and_expiry_closes_party(
    store: PartyStore,
) -> None:
    await _bound_party(store, starts_at=2000)

    first = await store.claim_due_reminders(now_ts=1900, window_sec=1800)
    second = await store.claim_due_reminders(now_ts=1900, window_sec=1800)
    expired = await store.claim_expired(now_ts=2000)

    assert [party.id for party in first] == [1]
    assert second == []
    assert len(expired) == 1
    assert expired[0].status == "closed"


async def test_list_open_and_list_for_user(store: PartyStore) -> None:
    await _bound_party(store)
    await store.join(1, 500, 20, now_ts=1001)

    open_parties = await store.list_open(1)
    user_parties = await store.list_for_user(1, 20)

    assert [party.id for party in open_parties] == [1]
    assert [party.id for party in user_parties] == [1]
    assert await store.list_open(2) == []


async def test_remove_guild_does_not_touch_other_guilds(store: PartyStore) -> None:
    await _bound_party(store, guild_id=1)
    second = await store.create(
        2,
        200,
        20,
        game="마인크래프트",
        capacity=4,
        starts_at=3000,
        now_ts=1000,
    )
    await store.bind_message(2, second.id, 600)

    removed = await store.remove_guild(1)

    assert removed == 1
    assert await store.list_open(1) == []
    assert [party.guild_id for party in await store.list_open(2)] == [2]


async def test_party_embed_shows_capacity_and_status(store: PartyStore) -> None:
    party = await _bound_party(store, capacity=4)

    embed = party_embed(party)

    assert embed.title == "🎮 롤 파티 모집"
    assert embed.fields[1].value == "**1 / 4명**"
    assert embed.fields[2].value == "🟢 모집 중"


async def test_party_extension_registers_commands_and_persistent_view(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PARTY_DB_PATH", str(tmp_path / "extension-party.db"))
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.load_extension("cogs.party_cog")
        names = {command.name for command in bot.tree.get_commands()}

        assert {"파티모집", "파티목록", "내파티"} <= names
        assert bot.persistent_views
    finally:
        if bot.get_cog("PartyCog") is not None:
            await bot.unload_extension("cogs.party_cog")
        await bot.close()
