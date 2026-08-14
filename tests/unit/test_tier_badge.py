"""파티 티어 뱃지 — 게임 판별, 티어 정규화, 캐시 TTL, 갱신 경로."""
from __future__ import annotations

import json
import time

import pytest

from cogs import tier_badge
from cogs.tier_badge import (
    GAME_LOL,
    GAME_VALORANT,
    PartyBadges,
    TierEmojis,
    TierService,
    detect_game,
    emoji_name,
    format_badge,
    is_known_tier,
    lol_emblem_url,
    lol_emblem_urls,
    lol_snapshot_from_entries,
    parse_valorant_tier_name,
    summarize,
    tier_slots,
    valorant_snapshot_from_mmr,
)
from cogs.tier_store import TierSnapshot, TierStore


# ─────────────────────────── 게임 판별 ───────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("롤 듀오 구함", GAME_LOL),
        ("칼바람 5인", GAME_LOL),
        ("솔랭 같이 하실 분", GAME_LOL),
        ("LoL ranked", GAME_LOL),
        ("발로 3인", GAME_VALORANT),
        ("발로란트 경쟁전", GAME_VALORANT),
        ("VALORANT 5스택", GAME_VALORANT),
        ("저녁 먹으러 갈 사람", None),
        ("", None),
        ("   ", None),
    ],
)
def test_detect_game_reads_the_title(title: str, expected: str | None) -> None:
    assert detect_game(title) == expected


def test_tft_is_not_a_lol_party() -> None:
    """제목에 '롤' 이 있어도 롤토체스는 소환사 랭크와 무관하다."""
    assert detect_game("롤체 한판") is None
    assert detect_game("롤토체스 같이 하실 분") is None
    assert detect_game("TFT 초보 환영") is None


# ─────────────────────────── 티어 정규화 ───────────────────────────


def test_lol_snapshot_uses_solo_queue_only() -> None:
    entries = [
        {"queueType": "RANKED_FLEX_SR", "tier": "DIAMOND", "rank": "I"},
        {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II"},
    ]
    snapshot = lol_snapshot_from_entries(entries, puuid="p-1")
    assert snapshot.tier == "GOLD"
    assert snapshot.label == "골드 2"
    assert snapshot.puuid == "p-1"


def test_lol_snapshot_without_solo_entry_is_unranked() -> None:
    """자유 랭크만 있는 계정을 다이아로 표시하면 요약이 거짓말이 된다."""
    entries = [{"queueType": "RANKED_FLEX_SR", "tier": "DIAMOND", "rank": "I"}]
    snapshot = lol_snapshot_from_entries(entries)
    assert snapshot.tier == "UNRANKED"
    assert snapshot.label == "언랭"
    assert snapshot.weight == 0


def test_lol_master_tier_drops_its_division() -> None:
    entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "MASTER", "rank": "I"}]
    assert lol_snapshot_from_entries(entries).label == "마스터"


def test_lol_unknown_tier_keeps_its_raw_name() -> None:
    entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "MYTHIC", "rank": "II"}]
    snapshot = lol_snapshot_from_entries(entries)
    assert snapshot.tier == "MYTHIC"
    assert snapshot.label == "Mythic"
    assert snapshot.weight == 0


def test_valorant_snapshot_splits_the_trailing_division() -> None:
    snapshot = valorant_snapshot_from_mmr({"current": {"tier": {"name": "Gold 2"}}})
    assert (snapshot.tier, snapshot.division, snapshot.label) == ("GOLD", "2", "골드 2")


def test_valorant_radiant_has_no_division() -> None:
    snapshot = valorant_snapshot_from_mmr({"current": {"tier": {"name": "Radiant"}}})
    assert snapshot.label == "레디언트"


@pytest.mark.parametrize("mmr", [{}, {"current": None}, {"current": {"tier": {}}}])
def test_valorant_missing_rank_is_unranked(mmr: dict) -> None:
    assert valorant_snapshot_from_mmr(mmr).tier == "UNRANKED"


# ─────────────────────────── 표기 ───────────────────────────


def _snap(game: str, tier: str, label: str, weight: int) -> TierSnapshot:
    return TierSnapshot(
        game=game, tier=tier, division="", label=label, weight=weight, fetched_at=0.0
    )


def test_format_badge_uses_unicode_without_uploaded_emojis() -> None:
    badge = format_badge(_snap(GAME_LOL, "GOLD", "골드 2", 4))
    assert badge == "🥇 골드 2"


def test_format_badge_prefers_the_uploaded_emblem() -> None:
    emojis = TierEmojis()
    emojis.load([_FakeEmoji(emoji_name(GAME_LOL, "GOLD"))])
    badge = format_badge(_snap(GAME_LOL, "GOLD", "골드 2", 4), emojis=emojis)
    assert badge == "<:koa_lol_gold:1> 골드 2"


def test_summarize_groups_by_tier_high_to_low() -> None:
    snapshots = [
        _snap(GAME_LOL, "SILVER", "실버 1", 3),
        _snap(GAME_LOL, "GOLD", "골드 4", 4),
        _snap(GAME_LOL, "GOLD", "골드 1", 4),
    ]
    assert summarize(snapshots) == "🥇골드 2 · 🥈실버 1"


def test_summarize_folds_the_tail_into_a_remainder() -> None:
    snapshots = [
        _snap(GAME_LOL, "CHALLENGER", "챌린저", 10),
        _snap(GAME_LOL, "DIAMOND", "다이아 1", 7),
        _snap(GAME_LOL, "GOLD", "골드 1", 4),
        _snap(GAME_LOL, "SILVER", "실버 1", 3),
        _snap(GAME_LOL, "IRON", "아이언 4", 1),
    ]
    assert summarize(snapshots, top=3).endswith("외 2")


def test_summarize_of_nothing_is_empty() -> None:
    assert summarize([]) == ""


# ─────────────────────────── 엠블럼 주소 ───────────────────────────


def test_every_lol_tier_has_an_emblem_url() -> None:
    urls = lol_emblem_urls()
    assert len(urls) == 11  # 랭크 10종 + 언랭
    assert all(url.endswith(".png") for url in urls.values())
    assert "emblem-emerald.png" in urls["koa_lol_emerald"]
    # 언랭도 반드시 그림이 있어야 한다. 등록은 했지만 배치 안 끝난 사람이
    # 파티에서 혼자 빈칸으로 보이면 안 된다.
    assert "shared-components" in urls["koa_lol_unranked"]


def test_unranked_gets_a_badge_in_both_games() -> None:
    for game in (GAME_LOL, GAME_VALORANT):
        assert ("UNRANKED", "") in tier_slots(game)
    assert lol_snapshot_from_entries([]).label == "언랭"
    assert format_badge(lol_snapshot_from_entries([])) == "▫️ 언랭"


def test_unknown_tier_has_no_emblem() -> None:
    assert lol_emblem_url("MYTHIC") is None


def test_lol_shares_one_emblem_across_divisions() -> None:
    """롤은 골드 1~4 가 엠블럼 하나를 쓴다. 단계별로 올릴 이유가 없다."""
    slots = tier_slots(GAME_LOL)
    assert len(slots) == 11
    assert all(division == "" for _, division in slots)
    assert emoji_name(GAME_LOL, "GOLD") == "koa_lol_gold"


def test_valorant_needs_one_emoji_per_division() -> None:
    """발로란트는 단계마다 화살표 수가 달라 그림이 전부 다르다."""
    slots = tier_slots(GAME_VALORANT)
    # 8티어 × 3단계 + 레디언트 + 언랭
    assert len(slots) == 26
    assert ("GOLD", "2") in slots
    assert ("RADIANT", "") in slots
    assert ("RADIANT", "1") not in slots
    assert emoji_name(GAME_VALORANT, "GOLD", "2") == "koa_valorant_gold_2"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Gold 2", ("GOLD", "2")),
        ("ASCENDANT 3", ("ASCENDANT", "3")),
        ("Radiant", ("RADIANT", "")),
        ("UNRANKED", ("UNRANKED", "")),
        ("", None),
    ],
)
def test_parse_valorant_tier_name(name: str, expected) -> None:
    assert parse_valorant_tier_name(name) == expected


def test_placeholder_tiers_are_filtered_by_the_caller() -> None:
    """valorant-api 표에는 `Unused1` 같은 자리표시자가 섞여 있다."""
    parsed = parse_valorant_tier_name("Unused1")
    assert parsed == ("UNUSED1", "")
    assert not is_known_tier(GAME_VALORANT, parsed[0])
    assert is_known_tier(GAME_VALORANT, "GOLD")


class _FakeEmoji:
    def __init__(self, name: str, emoji_id: int = 1) -> None:
        self.name = name
        self.id = emoji_id

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"


def test_emoji_registry_ignores_unrelated_emojis() -> None:
    emojis = TierEmojis()
    loaded = emojis.load([_FakeEmoji("koa_lol_gold"), _FakeEmoji("party_blob")])
    assert loaded == 1
    assert emojis.markup(GAME_LOL, "GOLD") == "<:koa_lol_gold:1>"
    assert emojis.markup(GAME_LOL, "SILVER") == ""


def test_valorant_emoji_is_picked_per_division() -> None:
    emojis = TierEmojis()
    emojis.load(
        [
            _FakeEmoji("koa_valorant_gold_1", 1),
            _FakeEmoji("koa_valorant_gold_2", 2),
            _FakeEmoji("koa_valorant_gold_3", 3),
        ]
    )
    assert emojis.markup(GAME_VALORANT, "GOLD", "2") == "<:koa_valorant_gold_2:2>"


def test_summary_falls_back_to_the_lowest_division_icon() -> None:
    """구성 요약은 단계를 접으므로 대표 그림 하나가 필요하다."""
    emojis = TierEmojis()
    emojis.load(
        [_FakeEmoji("koa_valorant_gold_2", 2), _FakeEmoji("koa_valorant_gold_1", 1)]
    )
    assert emojis.markup(GAME_VALORANT, "GOLD") == "<:koa_valorant_gold_1:1>"


def test_badge_falls_back_when_that_division_is_missing() -> None:
    """일부만 올라간 상태에서도 뱃지가 비어 보이면 안 된다."""
    emojis = TierEmojis()
    emojis.load([_FakeEmoji("koa_valorant_gold_1", 1)])
    snapshot = TierSnapshot(
        game=GAME_VALORANT,
        tier="GOLD",
        division="3",
        label="골드 3",
        weight=4,
        fetched_at=0.0,
    )
    assert format_badge(snapshot, emojis=emojis) == "<:koa_valorant_gold_1:1> 골드 3"


def test_valorant_badge_uses_the_division_emoji_end_to_end() -> None:
    emojis = TierEmojis()
    emojis.load([_FakeEmoji("koa_valorant_ascendant_3", 7)])
    snapshot = valorant_snapshot_from_mmr(
        {"current": {"tier": {"name": "Ascendant 3"}}}
    )
    assert (
        format_badge(snapshot, emojis=emojis) == "<:koa_valorant_ascendant_3:7> 초월자 3"
    )


# ─────────────────────────── 캐시 ───────────────────────────


def test_cache_roundtrips_through_disk(tmp_path) -> None:
    path = tmp_path / "tier.json"
    store = TierStore(path, ttl_sec=3600)
    snapshot = _snap(GAME_LOL, "GOLD", "골드 2", 4)

    import asyncio

    asyncio.run(store.set(1, 2, snapshot))
    restored = TierStore(path, ttl_sec=3600).get_sync(1, 2, GAME_LOL)
    assert restored is not None
    assert (restored.tier, restored.label) == ("GOLD", "골드 2")


def test_cache_is_guild_isolated(tmp_path) -> None:
    store = TierStore(tmp_path / "tier.json", ttl_sec=3600)

    import asyncio

    asyncio.run(store.set(1, 2, _snap(GAME_LOL, "GOLD", "골드 2", 4)))
    assert store.get_sync(1, 2, GAME_LOL) is not None
    assert store.get_sync(99, 2, GAME_LOL) is None


def test_cache_freshness_follows_the_ttl(tmp_path) -> None:
    store = TierStore(tmp_path / "tier.json", ttl_sec=100)
    snapshot = TierSnapshot(
        game=GAME_LOL,
        tier="GOLD",
        division="2",
        label="골드 2",
        weight=4,
        fetched_at=1000.0,
    )
    assert store.is_fresh(snapshot, now=1050.0)
    assert not store.is_fresh(snapshot, now=1200.0)


def test_corrupt_cache_starts_empty_instead_of_raising(tmp_path) -> None:
    path = tmp_path / "tier.json"
    path.write_text("{not json", encoding="utf-8")
    assert TierStore(path).get_sync(1, 2, GAME_LOL) is None


def test_malformed_entry_is_a_cache_miss(tmp_path) -> None:
    path = tmp_path / "tier.json"
    path.write_text(json.dumps({"1": {"2": {GAME_LOL: {"tier": "GOLD"}}}}), "utf-8")
    # label 이 없으면 되살릴 수 없다. 예외 대신 미스로 떨어져야 한다.
    assert TierStore(path).get_sync(1, 2, GAME_LOL) is None


# ─────────────────────────── 서비스 ───────────────────────────


class _FakeRegistrationStore:
    def __init__(self, entries: dict[tuple[int, int], dict] | None = None) -> None:
        self._entries = entries or {}

    async def get(self, guild_id: int, user_id: int):
        entry = self._entries.get((guild_id, user_id))
        return dict(entry) if entry else None


class _NoWaitGate:
    async def wait(self) -> None:
        return None


def _service(tmp_path, *, lol_entries=None, valorant_entries=None) -> TierService:
    return TierService(
        tier_store=TierStore(tmp_path / "tier.json", ttl_sec=3600),
        lol_store=_FakeRegistrationStore(lol_entries),
        valorant_store=_FakeRegistrationStore(valorant_entries),
        gate=_NoWaitGate(),
    )


async def test_badges_skip_users_who_unregistered(tmp_path) -> None:
    """등록을 지우면 캐시가 남아 있어도 뱃지가 사라져야 한다."""
    service = _service(tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1"}})
    await service.cache.set(1, 10, _snap(GAME_LOL, "GOLD", "골드 2", 4))
    await service.cache.set(1, 11, _snap(GAME_LOL, "SILVER", "실버 1", 3))

    badges = await service.badges_for(1, [10, 11], GAME_LOL)
    assert set(badges) == {10}


async def test_for_party_without_a_game_returns_nothing(tmp_path) -> None:
    service = _service(tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1"}})
    await service.cache.set(1, 10, _snap(GAME_LOL, "GOLD", "골드 2", 4))

    result = await service.for_party(1, "저녁 먹자", [10])
    assert result == PartyBadges(None, {}, "")
    assert not result


async def test_for_party_builds_badges_and_a_summary(tmp_path) -> None:
    service = _service(
        tmp_path,
        lol_entries={
            (1, 10): {"name": "a", "tag": "kr1"},
            (1, 11): {"name": "b", "tag": "kr1"},
        },
    )
    await service.cache.set(1, 10, _snap(GAME_LOL, "GOLD", "골드 2", 4))
    await service.cache.set(1, 11, _snap(GAME_LOL, "GOLD", "골드 4", 4))

    result = await service.for_party(1, "롤 듀오", [10, 11])
    assert result.game == GAME_LOL
    assert result.badges == {10: "🥇 골드 2", 11: "🥇 골드 4"}
    assert result.summary == "🥇골드 2"


async def test_refresh_skips_a_fresh_cache_entry(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1"}})
    fresh = TierSnapshot(
        game=GAME_LOL,
        tier="GOLD",
        division="2",
        label="골드 2",
        weight=4,
        fetched_at=time.time(),
    )
    await service.cache.set(1, 10, fresh)

    def _boom(*args, **kwargs):
        raise AssertionError("상류를 부르면 안 된다")

    monkeypatch.setattr(tier_badge.lol_api, "get_account", _boom)
    assert await service.refresh(1, 10, GAME_LOL) is False


async def test_refresh_skips_unregistered_users(tmp_path) -> None:
    service = _service(tmp_path)
    assert await service.refresh(1, 10, GAME_LOL) is False


async def test_refresh_reuses_the_cached_puuid(tmp_path, monkeypatch) -> None:
    """티어는 상해도 puuid 는 안 상한다. account 조회 한 번을 아낀다."""
    service = _service(
        tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1", "platform": "kr"}}
    )
    stale = TierSnapshot(
        game=GAME_LOL,
        tier="SILVER",
        division="1",
        label="실버 1",
        weight=3,
        fetched_at=0.0,
        puuid="cached-puuid",
    )
    await service.cache.set(1, 10, stale)

    async def _no_account(*args, **kwargs):
        raise AssertionError("puuid 가 있으면 account 를 다시 부르면 안 된다")

    seen: dict[str, str] = {}

    async def _entries(platform: str, puuid: str):
        seen["platform"], seen["puuid"] = platform, puuid
        return [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "III"}]

    monkeypatch.setattr(tier_badge.lol_api, "get_account", _no_account)
    monkeypatch.setattr(tier_badge.lol_api, "get_league_entries", _entries)

    assert await service.refresh(1, 10, GAME_LOL) is True
    assert seen == {"platform": "kr", "puuid": "cached-puuid"}
    stored = service.cache.get_sync(1, 10, GAME_LOL)
    assert stored is not None
    assert stored.label == "골드 3"
    assert stored.puuid == "cached-puuid"


async def test_refresh_falls_back_to_the_default_platform(tmp_path, monkeypatch) -> None:
    service = _service(
        tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1", "platform": "xx"}}
    )
    seen: dict[str, str] = {}

    async def _account(regional: str, name: str, tag: str):
        seen["regional"] = regional
        return {"puuid": "p-9"}

    async def _entries(platform: str, puuid: str):
        seen["platform"] = platform
        return []

    monkeypatch.setattr(tier_badge.lol_api, "get_account", _account)
    monkeypatch.setattr(tier_badge.lol_api, "get_league_entries", _entries)

    assert await service.refresh(1, 10, GAME_LOL) is True
    assert seen["platform"] == tier_badge.lol_api.default_platform()
    assert seen["regional"] == "asia"


async def test_refresh_stores_the_valorant_rank(tmp_path, monkeypatch) -> None:
    service = _service(
        tmp_path,
        valorant_entries={
            (1, 10): {"name": "a", "tag": "kr1", "region": "kr", "platform": "pc"}
        },
    )

    async def _mmr(region: str, name: str, tag: str, *, platform: str):
        assert (region, platform) == ("kr", "pc")
        return {"current": {"tier": {"name": "Ascendant 3"}}}

    monkeypatch.setattr(tier_badge.valorant_api, "get_mmr", _mmr)

    assert await service.refresh(1, 10, GAME_VALORANT) is True
    stored = service.cache.get_sync(1, 10, GAME_VALORANT)
    assert stored is not None
    assert stored.label == "초월자 3"


async def test_refresh_lets_upstream_errors_through(tmp_path, monkeypatch) -> None:
    """삼킬지 말지는 호출 측(party_cog)이 정한다."""
    service = _service(tmp_path, lol_entries={(1, 10): {"name": "a", "tag": "kr1"}})

    async def _boom(*args, **kwargs):
        raise tier_badge.lol_api.LolRateLimited("rate limited")

    monkeypatch.setattr(tier_badge.lol_api, "get_account", _boom)
    with pytest.raises(tier_badge.lol_api.LolRateLimited):
        await service.refresh(1, 10, GAME_LOL)
