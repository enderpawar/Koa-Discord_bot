from __future__ import annotations

import asyncio
import inspect
import sqlite3
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
    PartyCreateModal,
    can_mention_game_role,
    format_headcount,
    parse_capacity,
    parse_party_start,
    party_embed,
)
from cogs.party_store import PartyStore
from cogs.tier_badge import PartyBadges

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
        title="롤",
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


def test_parse_party_start_defaults_to_now() -> None:
    """실제 모집 글은 시간을 안 적고 올린 즉시 시작한다. 그게 기본값이어야 한다.

    예전에는 `시작` 이 필수인 데다 과거 시각을 전부 거부해서, "지금 하실 분" 을
    입력할 방법 자체가 없었다.
    """
    now = datetime(2026, 7, 26, 20, 0, tzinfo=KST)

    assert parse_party_start("", now=now) == now
    assert parse_party_start("지금", now=now) == now
    assert parse_party_start("  바로 ", now=now) == now


def test_parse_party_start_supports_relative_offsets() -> None:
    now = datetime(2026, 7, 26, 20, 0, tzinfo=KST)

    assert parse_party_start("30분 뒤", now=now) == datetime(
        2026, 7, 26, 20, 30, tzinfo=KST
    )
    assert parse_party_start("2시간 후", now=now) == datetime(
        2026, 7, 26, 22, 0, tzinfo=KST
    )
    with pytest.raises(ValueError, match="최대"):
        parse_party_start("999시간 뒤", now=now)


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


def _modal_fields() -> list[dict]:
    return PartyCreateModal(cog=None).to_dict()["components"]


def test_party_command_takes_no_options_and_opens_the_form() -> None:
    """입력은 전부 모달에서 받는다.

    슬래시 옵션 방식은 제목을 넣고 나면 남은 항목이 이름만 나열돼 흐름이 끊겼고,
    `시작`·`정원` 후보를 보려면 항목을 눌러 봇에 왕복해야 했다.
    """
    assert PartyCog.create_party.to_dict(_TEST_BOT.tree).get("options", []) == []

    source = inspect.getsource(PartyCog.create_party.callback)
    assert "send_modal" in source


def test_modal_asks_five_things_with_only_the_title_required() -> None:
    """제목만 필수다. 나머지를 비우면 지금 바로 · 제한 없음으로 열린다."""
    fields = _modal_fields()

    assert [field["label"] for field in fields] == [
        "제목",
        "시작",
        "정원",
        "메모",
        "알림 역할",
    ]
    required = {
        field["label"] for field in fields if field["component"].get("required")
    }
    assert required == {"제목"}
    # 비워 두면 무엇이 되는지 칸 옆에 적혀 있어야 한다.
    assert fields[1]["component"]["placeholder"] == "지금"
    assert fields[2]["component"]["placeholder"] == "제한 없음"


def test_modal_start_and_capacity_are_free_text() -> None:
    """드롭다운이면 `2026-08-01 20:00` 이나 7명 같은 값을 아예 못 넣는다.

    목록을 넉넉히 채워도 결국 누군가는 목록 밖을 원한다.
    """
    fields = _modal_fields()

    assert fields[0]["component"]["type"] == 4  # 제목: 텍스트
    assert fields[1]["component"]["type"] == 4  # 시작: 자유 입력
    assert fields[2]["component"]["type"] == 4  # 정원: 자유 입력
    assert fields[3]["component"]["type"] == 4  # 메모: 텍스트
    assert fields[4]["component"]["type"] == 6  # 알림 역할: 역할 선택기
    assert fields[4]["component"]["required"] is False


def test_modal_refills_what_the_user_already_typed() -> None:
    """모달은 제출과 동시에 닫힌다. 되물을 때 친 것이 날아가면 안 된다."""
    draft = {"title": "롤 칼바람", "start": "25시", "capacity": "5", "note": "즐겜"}

    fields = PartyCreateModal(cog=None, draft=draft).to_dict()["components"]

    assert [field["component"].get("value") for field in fields[:4]] == [
        "롤 칼바람",
        "25시",
        "5",
        "즐겜",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   ", 0),
        ("제한 없음", 0),
        ("무제한", 0),
        ("0", 0),
        ("5", 5),
        ("5명", 5),
        (" 12 인 ", 12),
        ("20", 20),
    ],
)
def test_parse_capacity_accepts_what_people_actually_type(
    text: str, expected: int
) -> None:
    assert parse_capacity(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1", "2명 이상"),
        ("21", "최대"),
        ("네 명", "숫자"),
        ("많이", "숫자"),
    ],
)
def test_parse_capacity_rejects_impossible_values(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_capacity(text)


def test_unmentionable_tag_does_not_block_party_creation() -> None:
    """태그를 못 붙여도 파티는 열려야 한다. 예전에는 여기서 끊겼다."""
    source = inspect.getsource(PartyCog.open_party)

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
        title="마인크래프트",
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

    # 제목은 자유 입력이라("리썰 3/4 급구") 뒤에 "파티 모집"을 붙이면 겹쳐 읽힌다.
    assert embed.title == "🎮 롤"
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


def _roster_field(embed):
    return next(field for field in embed.fields if field.name.startswith("참가자"))


async def test_party_embed_without_badges_is_unchanged(store: PartyStore) -> None:
    """뱃지 인자를 안 주면 티어 기능이 붙기 전과 똑같이 그려져야 한다."""
    party = await _bound_party(store, owner_id=10)

    field = _roster_field(party_embed(party))

    assert field.name == "참가자"
    assert field.value == "1. <@10>"


async def test_party_embed_puts_tiers_on_the_roster(store: PartyStore) -> None:
    party = await _bound_party(store, owner_id=10)
    await store.join(party.guild_id, party.message_id, 11)
    party = await store.get_by_message(party.guild_id, party.message_id)

    embed = party_embed(
        party,
        badges=PartyBadges(
            "lol", {10: "🥇 골드 2", 11: "🥈 실버 1"}, "🥇골드 1 · 🥈실버 1"
        ),
    )

    field = _roster_field(embed)
    # 구성 요약은 필드 이름에 붙는다 — 시작/인원/상태가 이미 한 줄을 채운다.
    assert field.name == "참가자 · 🥇골드 1 · 🥈실버 1"
    assert field.value == "1. <@10>  🥇 골드 2\n2. <@11>  🥈 실버 1"


async def test_party_embed_badges_only_the_users_it_knows(store: PartyStore) -> None:
    """등록 안 한 참가자는 뱃지 없이 이름만 나온다."""
    party = await _bound_party(store, owner_id=10)
    await store.join(party.guild_id, party.message_id, 11)
    party = await store.get_by_message(party.guild_id, party.message_id)

    embed = party_embed(party, badges=PartyBadges("lol", {11: "🥈 실버 1"}, "🥈실버 1"))

    assert _roster_field(embed).value == "1. <@10>\n2. <@11>  🥈 실버 1"


async def test_party_embed_badges_reach_the_waitlist(store: PartyStore) -> None:
    party = await _bound_party(store, owner_id=10, capacity=2)
    await store.join(party.guild_id, party.message_id, 11)
    await store.join(party.guild_id, party.message_id, 12)
    party = await store.get_by_message(party.guild_id, party.message_id)

    embed = party_embed(party, badges=PartyBadges("lol", {12: "💎 다이아 1"}, ""))

    waiting = next(field for field in embed.fields if field.name.startswith("대기자"))
    assert waiting.value == "1. <@12>  💎 다이아 1"


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
        title="롤",
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


@pytest.mark.parametrize(
    ("column", "index"),
    [
        ("starts_at", "idx_parties_open_start"),
        ("expires_at", "idx_parties_open_expiry"),
    ],
)
async def test_scheduler_scans_use_the_open_party_indexes(
    store: PartyStore, column: str, index: str
) -> None:
    """마감 파티가 쌓여도 30초 스캔이 전체 테이블을 훑지 않아야 한다.

    리마인더는 starts_at, 자동 마감은 expires_at 을 훑는다. 둘 다 부분 인덱스가
    필요하다 — 한쪽만 있으면 나머지 스캔이 조용히 전체 테이블로 돌아간다.
    """
    await _bound_party(store)

    with store._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM parties "
            f"WHERE status = 'open' AND {column} <= 1"
        ).fetchall()

    assert any(index in str(tuple(row)) for row in plan), plan


# ---------- 즉시 모집("지금") ----------


async def test_instant_party_is_not_closed_the_moment_it_opens(
    store: PartyStore,
) -> None:
    """`지금` 파티는 시작 시각이 곧 생성 시각이다.

    마감을 starts_at 으로 잡으면 올린 즉시 30초 스케줄러가 닫아 버린다. 실제
    모집 글은 전부 즉시 시작이라, 이게 막히면 명령을 쓸 이유가 없어진다.
    """
    party = await store.create(
        1,
        100,
        10,
        title="리썰 하실분",
        starts_at=1000,
        expires_at=1000 + 7200,
        now_ts=1000,
    )
    await store.bind_message(1, party.id, 500)

    assert await store.claim_expired(now_ts=1000) == []
    assert await store.claim_expired(now_ts=1000 + 7199) == []

    expired = await store.claim_expired(now_ts=1000 + 7200)
    assert [p.id for p in expired] == [party.id]


async def test_instant_party_embed_says_now_and_shows_its_close_window(
    store: PartyStore,
) -> None:
    party = await store.create(
        1,
        100,
        10,
        title="리썰 하실분",
        starts_at=1000,
        expires_at=1000 + 7200,
        now_ts=1000,
    )

    embed = party_embed(party)

    # "0분 전 시작" 같은 소리 대신 사실을 적는다.
    assert embed.fields[0].value == "**지금 바로**"
    assert "2시간 뒤 자동 마감" in embed.footer.text


# ---------- 정원 제한 없음 ----------


async def test_capacity_zero_means_unlimited_and_never_waitlists(
    store: PartyStore,
) -> None:
    """모집 글의 절반 이상이 인원을 안 적는다. 그 경우 대기열이 생기면 안 된다."""
    party = await store.create(
        1, 100, 10, title="배그 하실분", capacity=0, starts_at=2000, now_ts=1000
    )
    await store.bind_message(1, party.id, 500)

    for user_id in range(20, 30):
        mutation = await store.join(1, 500, user_id, now_ts=1001)
        assert mutation.outcome == "joined"

    latest = await store.get(1, party.id)
    assert latest is not None
    assert len(latest.members) == 11
    assert latest.waitlist == ()
    assert format_headcount(latest) == "**11명** · 제한 없음"


async def test_capacity_of_one_is_rejected(store: PartyStore) -> None:
    """혼자 하는 파티는 없다. 0(제한 없음)과 1(오타)은 구분해야 한다."""
    with pytest.raises(ValueError):
        await store.create(
            1, 100, 10, title="롤", capacity=1, starts_at=2000, now_ts=1000
        )


# ---------- 제목 자동완성 ----------


async def test_recent_titles_are_deduped_newest_first_and_guild_isolated(
    store: PartyStore,
) -> None:
    for index, (guild_id, title, ts) in enumerate(
        [
            (1, "롤 칼바람", 1000),
            (1, "리썰 하실분", 2000),
            (1, "롤 칼바람", 3000),
            (2, "발로 5인", 4000),
        ]
    ):
        party = await store.create(
            guild_id, 100, 10, title=title, starts_at=ts + 10, now_ts=ts
        )
        await store.bind_message(guild_id, party.id, 500 + index)

    assert await store.recent_titles(1) == ["롤 칼바람", "리썰 하실분"]
    assert await store.recent_titles(1, keyword="리썰") == ["리썰 하실분"]
    assert await store.recent_titles(2) == ["발로 5인"]


# ---------- 스키마 마이그레이션 ----------


async def test_store_upgrades_a_pre_title_database(tmp_path: Path) -> None:
    """운영 중인 party.db 는 `game` 컬럼에 expires_at 이 없는 옛 스키마다.

    `CREATE TABLE IF NOT EXISTS` 는 기존 테이블을 안 건드리므로, 마이그레이션이
    없으면 배포 직후 모든 파티 조회가 터진다.
    """
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE parties (
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
            CREATE TABLE party_members (
                party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY (party_id, user_id)
            );
            INSERT INTO parties (
                guild_id, channel_id, message_id, owner_id, game,
                capacity, starts_at, created_at
            ) VALUES (1, 100, 500, 10, '롤', 5, 2000, 1000);
            INSERT INTO party_members (party_id, user_id, role, joined_at)
            VALUES (1, 10, 'member', 1000);
            """
        )

    upgraded = PartyStore(path)
    party = await upgraded.get(1, 1)

    assert party is not None
    assert party.title == "롤"
    # 옛 파티는 시작 시각에 닫히던 것들이다. 그 동작이 그대로 옮겨져야 한다.
    assert party.expires_at == party.starts_at
    # 두 번째 기동에서도 조용히 통과해야 한다.
    assert (await PartyStore(path).get(1, 1)) is not None


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
