from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import discord
from discord.ext import commands

from cogs.party_cog import (
    PartyCog,
    can_mention_game_role,
    parse_party_start,
    party_embed,
)
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


def test_game_role_can_use_role_setting_or_bot_permission() -> None:
    public_role = SimpleNamespace(is_default=lambda: False, mentionable=True)
    private_role = SimpleNamespace(is_default=lambda: False, mentionable=False)
    everyone = SimpleNamespace(is_default=lambda: True, mentionable=True)
    no_permissions = discord.Permissions.none()
    mention_permissions = discord.Permissions(mention_everyone=True)

    assert can_mention_game_role(public_role, no_permissions)
    assert can_mention_game_role(private_role, mention_permissions)
    assert not can_mention_game_role(private_role, no_permissions)
    assert not can_mention_game_role(everyone, mention_permissions)


_TEST_BOT = commands.Bot(command_prefix="!", intents=discord.Intents.none())


def _party_options() -> list[dict]:
    return PartyCog.create_party.to_dict(_TEST_BOT.tree)["options"]


def test_game_name_is_free_text_and_independent_of_any_role() -> None:
    """게임 이름은 순수 텍스트다.

    역할 타입이면 Discord 가 `롤`, `발로` 를 "올바른 역할이 아닙니다" 로 막고,
    자동완성을 붙이면 Tab 이 역할 후보를 대신 집어넣는다. 둘 다 없어야 한다.
    """
    annotations = PartyCog.create_party.callback.__annotations__
    assert annotations["game"] == "str"

    game = next(option for option in _party_options() if option["name"] == "게임")
    assert game["type"] == 3
    assert game["required"] is True
    assert not game.get("autocomplete", False)


def test_ping_role_is_a_separate_optional_role_option() -> None:
    tag = next(option for option in _party_options() if option["name"] == "태그")

    assert tag["type"] == 8
    assert tag["required"] is False


def test_unmentionable_tag_does_not_block_party_creation() -> None:
    """태그를 못 붙여도 파티는 열려야 한다. 예전에는 여기서 끊겼다."""
    source = inspect.getsource(PartyCog.create_party.callback)

    # 태그 검사가 조기 return 으로 파티 생성을 막지 않는다.
    assert "태그 생략" in source
    assert source.index("self.store.create") < source.index("태그 생략")


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
    assert embed.fields[0].value == "<t:2000:t> (<t:2000:R>)"
    assert embed.fields[1].value == "**1 / 4명**"
    assert embed.fields[2].value == "🟢 모집 중"
    assert "시작 시 자동 마감" in embed.footer.text


def _guild_with_member(user_id: int, *, name: str, avatar: str):
    member = SimpleNamespace(
        id=user_id,
        display_name=name,
        display_avatar=SimpleNamespace(url=avatar),
    )
    return SimpleNamespace(get_member=lambda uid: member if uid == user_id else None)


async def test_party_embed_puts_the_owner_avatar_on_top(store: PartyStore) -> None:
    party = await _bound_party(store, owner_id=10)
    guild = _guild_with_member(10, name="미소", avatar="https://cdn/avatar.png")

    embed = party_embed(party, guild)

    assert embed.author.name == "미소 님이 모집"
    assert embed.author.icon_url == "https://cdn/avatar.png"
    # author 아이콘은 24px 라 눈에 안 띈다. 실제로 보이는 크기는 썸네일이다.
    assert embed.thumbnail.url == "https://cdn/avatar.png"
    # 상단에 모집자가 서 있으므로 footer 에서 중복을 뺀다.
    assert embed.footer.text == "시작 시 자동 마감"


async def test_party_embed_survives_an_uncached_owner(store: PartyStore) -> None:
    """재시작 직후나 서버를 떠난 모집자는 멤버 캐시에 없다."""
    party = await _bound_party(store, owner_id=10)
    guild = SimpleNamespace(get_member=lambda _uid: None)

    embed = party_embed(party, guild)

    assert embed.author.name is None
    assert embed.thumbnail.url is None
    assert "모집자" in embed.footer.text
    assert "시작 시 자동 마감" in embed.footer.text


async def test_party_embed_distinguishes_cancelled_from_closed(
    store: PartyStore,
) -> None:
    """취소는 마감과 다른 결과다. 같은 회색 배지로 뭉뚱그리면 오해를 부른다."""
    party = await _bound_party(store)

    closed = party_embed(replace(party, status="closed"))
    cancelled = party_embed(replace(party, status="cancelled"))

    assert closed.fields[2].value == "⚫ 모집 마감"
    assert cancelled.fields[2].value == "🚫 모집 취소"
    assert "모집자가 취소함" in cancelled.footer.text


# ---------- 파티 취소와 보관 정리 ----------


async def _party_at(
    store: PartyStore,
    *,
    message_id: int,
    starts_at: float,
    guild_id: int = 1,
    owner_id: int = 10,
):
    party = await store.create(
        guild_id,
        100,
        owner_id,
        game="롤",
        capacity=4,
        starts_at=starts_at,
        now_ts=1000,
    )
    return await store.bind_message(guild_id, party.id, message_id)


async def test_only_the_owner_can_cancel_a_party(store: PartyStore) -> None:
    party = await _bound_party(store, owner_id=10)
    await store.join(1, 500, 20, now_ts=1001)

    stranger = await store.delete_owned(1, party.id, 20)
    assert stranger.outcome == "not_owner"
    assert await store.get(1, party.id) is not None

    owner = await store.delete_owned(1, party.id, 10)
    assert owner.outcome == "party_cancelled"
    # 삭제 전 스냅샷이 와야 모집 메시지를 고치고 참가자에게 알릴 수 있다.
    assert owner.party is not None
    assert owner.party.message_id == 500
    assert 20 in owner.party.members
    assert await store.get(1, party.id) is None


async def test_cancelling_a_party_drops_its_member_rows(store: PartyStore) -> None:
    """FK CASCADE 가 꺼져 있으면 참가자 행이 고아로 남는다."""
    party = await _bound_party(store, owner_id=10)
    await store.join(1, 500, 20, now_ts=1001)

    await store.delete_owned(1, party.id, 10)

    with store._connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM party_members WHERE party_id = ?", (party.id,)
        ).fetchone()[0]
    assert remaining == 0


async def test_cancel_is_guild_isolated(store: PartyStore) -> None:
    party = await _party_at(store, message_id=500, starts_at=2000, guild_id=1)

    other_guild = await store.delete_owned(2, party.id, 10)

    assert other_guild.outcome == "not_found"
    assert await store.get(1, party.id) is not None


async def test_list_owned_returns_only_my_open_parties(store: PartyStore) -> None:
    mine = await _party_at(store, message_id=500, starts_at=2000, owner_id=10)
    await _party_at(store, message_id=501, starts_at=2100, owner_id=99)
    closed = await _party_at(store, message_id=502, starts_at=2200, owner_id=10)
    await store.close(1, 502, 10)

    owned = await store.list_owned(1, 10)

    assert [party.id for party in owned] == [mine.id]
    assert closed.id not in {party.id for party in owned}
    assert await store.list_owned(1, 12345) == []


async def test_purge_old_removes_finished_parties_but_never_open_ones(
    store: PartyStore,
) -> None:
    still_open = await _party_at(store, message_id=500, starts_at=2000)
    long_done = await _party_at(store, message_id=501, starts_at=2000)
    just_done = await _party_at(store, message_id=502, starts_at=4500)
    await store.close(1, 501, 10)
    await store.close(1, 502, 10)

    removed = await store.purge_old(retention_sec=1000, now_ts=5000)

    assert removed == 1
    assert await store.get(1, long_done.id) is None
    # 보관 기간이 지나지 않은 것과 아직 열린 것은 남는다.
    assert await store.get(1, just_done.id) is not None
    assert await store.get(1, still_open.id) is not None


async def test_scheduler_scan_uses_the_open_party_index(store: PartyStore) -> None:
    """마감 파티가 쌓여도 30초 스캔이 전체 테이블을 훑지 않아야 한다."""
    await _bound_party(store)

    with store._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM parties "
            "WHERE status = 'open' AND starts_at <= 1"
        ).fetchall()

    assert any("idx_parties_open_start" in str(tuple(row)) for row in plan), plan


async def test_party_extension_registers_commands_and_persistent_view(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PARTY_DB_PATH", str(tmp_path / "extension-party.db"))
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.load_extension("cogs.party_cog")
        names = {command.name for command in bot.tree.get_commands()}

        assert {"파티모집", "파티목록", "내파티", "파티취소"} <= names
        assert bot.persistent_views
    finally:
        if bot.get_cog("PartyCog") is not None:
            await bot.unload_extension("cogs.party_cog")
        await bot.close()
