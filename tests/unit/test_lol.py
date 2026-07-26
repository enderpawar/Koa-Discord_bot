"""리그 오브 레전드 전적 조회 단위 테스트.

순수 함수(파싱/랭크/임베드/라우팅)와 저장소 왕복, API 클라이언트의 URL 구성/상태
코드 매핑을 다룬다. 실제 HTTP 는 가짜 세션으로 대체한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cogs import lol_api as api
from cogs import lol_cog as lc
from cogs.game_reactions import RecentPerformance
from cogs.lol_store import LolStore


def test_slash_group_exposes_complete_command_set():
    assert lc.LolCog.lol.name == "롤"
    assert {command.name for command in lc.LolCog.lol.commands} == {
        "등록",
        "전적",
        "검색",
        "등록해제",
    }


# ---- 라우팅 ---------------------------------------------------------------


def test_regional_for_platform():
    assert api.regional_for("kr") == "asia"
    assert api.regional_for("jp1") == "asia"
    assert api.regional_for("na1") == "americas"
    assert api.regional_for("euw1") == "europe"
    assert api.regional_for("sg2") == "sea"
    assert api.regional_for("unknown") == "asia"  # 안전한 기본값


def test_default_platform(monkeypatch):
    monkeypatch.delenv("LOL_DEFAULT_PLATFORM", raising=False)
    assert api.default_platform() == "kr"
    monkeypatch.setenv("LOL_DEFAULT_PLATFORM", "NA1")
    assert api.default_platform() == "na1"
    monkeypatch.setenv("LOL_DEFAULT_PLATFORM", "bogus")
    assert api.default_platform() == "kr"


# ---- _parse_riot_id -------------------------------------------------------


def test_parse_riot_id_valid():
    assert lc._parse_riot_id("Hide on bush#KR1") == ("Hide on bush", "KR1")
    assert lc._parse_riot_id("  a#b ") == ("a", "b")


@pytest.mark.parametrize("raw", ["nohash", "#tag", "name#", "#"])
def test_parse_riot_id_invalid(raw):
    assert lc._parse_riot_id(raw) is None


# ---- 랭크 표기 ------------------------------------------------------------


def test_entry_for_queue():
    entries = [
        {"queueType": "RANKED_FLEX_SR", "tier": "SILVER"},
        {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD"},
    ]
    assert lc._entry_for_queue(entries, lc._SOLO_QUEUE)["tier"] == "GOLD"
    assert lc._entry_for_queue(entries, lc._FLEX_QUEUE)["tier"] == "SILVER"
    assert lc._entry_for_queue([], lc._SOLO_QUEUE) is None


def test_rank_value_unranked():
    assert lc._rank_value(None) == "언랭크"


def test_rank_value_formats_tier_and_winrate():
    entry = {
        "tier": "GOLD",
        "rank": "II",
        "leaguePoints": 42,
        "wins": 30,
        "losses": 20,
    }
    value = lc._rank_value(entry)
    assert "Gold II" in value
    assert "42 LP" in value
    assert "30승 20패" in value
    assert "60%" in value  # 30/50


def test_rank_value_zero_games_no_div_by_zero():
    entry = {"tier": "IRON", "rank": "IV", "leaguePoints": 0, "wins": 0, "losses": 0}
    value = lc._rank_value(entry)
    assert "0%" in value


def test_tier_emoji():
    assert lc._tier_emoji("CHALLENGER") == "☀️"
    assert lc._tier_emoji("emerald") == "💚"
    assert lc._tier_emoji("") == "🎮"


# ---- _match_line ----------------------------------------------------------


def _match(puuid, *, win=True, champ="Ahri", k=8, d=2, a=10):
    return {
        "info": {
            "participants": [
                {"puuid": "other", "championName": "Zed"},
                {
                    "puuid": puuid,
                    "championName": champ,
                    "kills": k,
                    "deaths": d,
                    "assists": a,
                    "win": win,
                },
            ]
        }
    }


def test_match_line_win_and_loss():
    assert lc._match_line(_match("p1", win=True), "p1").startswith("✅")
    assert lc._match_line(_match("p1", win=False), "p1").startswith("❌")


def test_match_line_contains_champ_and_kda():
    line = lc._match_line(_match("p1", champ="LeeSin", k=5, d=5, a=7), "p1")
    assert "LeeSin" in line
    assert "5/5/7" in line


def test_match_line_missing_participant_returns_none():
    assert lc._match_line(_match("p1"), "not-in-match") is None
    assert lc._match_line({}, "p1") is None


def test_match_performance_extracts_outcome_and_kda():
    performance = lc._match_performance(
        _match("p1", win=False, k=3, d=7, a=4), "p1"
    )
    assert performance == RecentPerformance(
        outcome="loss", kills=3, deaths=7, assists=4
    )


# ---- _profile_embed -------------------------------------------------------


def test_profile_embed_fields():
    summoner = {"summonerLevel": 321, "profileIconId": 29}
    entries = [
        {
            "queueType": "RANKED_SOLO_5x5",
            "tier": "PLATINUM",
            "rank": "I",
            "leaguePoints": 12,
            "wins": 10,
            "losses": 5,
        }
    ]
    embed = lc._profile_embed(
        "nick",
        "KR1",
        "kr",
        summoner,
        entries,
        ["✅ `Ahri` 8/2/10"],
        [RecentPerformance("win", 8, 2, 10)],
    )
    assert embed.title == "nick#KR1"
    assert embed.thumbnail.url == api.profile_icon_url(29)
    names = [f.name for f in embed.fields]
    assert "솔로 랭크" in names
    assert "자유 랭크" in names
    assert "최근 경기" in names
    recent = next(f for f in embed.fields if f.name == "최근 경기")
    assert "나띵이" in recent.value
    solo = next(f for f in embed.fields if f.name == "솔로 랭크")
    assert "Platinum I" in solo.value
    flex = next(f for f in embed.fields if f.name == "자유 랭크")
    assert flex.value == "언랭크"


def test_profile_embed_no_matches_omits_field():
    embed = lc._profile_embed("n", "t", "na1", {}, [], [])
    assert "최근 경기" not in [f.name for f in embed.fields]


# ---- _Cooldown ------------------------------------------------------------


def test_cooldown(monkeypatch):
    cd = lc._Cooldown(interval=5.0)
    now = [1000.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: now[0])
    assert cd.retry_after(1) == 0.0
    cd.stamp(1)
    assert cd.retry_after(1) > 0
    assert cd.retry_after(2) == 0.0
    now[0] += 6
    assert cd.retry_after(1) == 0.0


# ---- LolStore -------------------------------------------------------------


async def test_store_roundtrip_and_persistence(tmp_path: Path):
    path = tmp_path / "lol.json"
    store = LolStore(path)
    await store.set(7, name="nick", tag="KR1", platform="kr")
    assert await store.get(7) == {"name": "nick", "tag": "KR1", "platform": "kr"}
    assert await store.get(8) is None

    reloaded = LolStore(path)
    assert (await reloaded.get(7))["platform"] == "kr"

    assert await store.remove(7) is True
    assert await store.remove(7) is False


async def test_store_recovers_from_corrupt(tmp_path: Path):
    path = tmp_path / "lol.json"
    path.write_text("broken", encoding="utf-8")
    store = LolStore(path)
    assert await store.get(1) is None
    assert path.with_suffix(".json.corrupt").exists()


# ---- lol_api HTTP ---------------------------------------------------------


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("RIOT_API_KEY", raising=False)
    with pytest.raises(api.LolConfigError):
        api._api_key()


@pytest.mark.parametrize(
    "status,exc",
    [
        (404, api.LolNotFound),
        (429, api.LolRateLimited),
        (401, api.LolConfigError),
        (403, api.LolConfigError),
        (500, api.LolApiError),
    ],
)
def test_raise_for_status(status, exc):
    with pytest.raises(exc):
        api._raise_for_status(status, "/x")


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, status, payload, capture):
        self._status = status
        self._payload = payload
        self._capture = capture

    def get(self, url, headers=None):
        self._capture["url"] = url
        self._capture["headers"] = headers
        return _FakeResp(self._status, self._payload)


def _patch_session(monkeypatch, status, payload, capture):
    monkeypatch.setenv("RIOT_API_KEY", "test-key")

    async def _fake_get_session():
        return _FakeSession(status, payload, capture)

    monkeypatch.setattr(api, "_get_session", _fake_get_session)


async def test_get_account_uses_regional_host_and_auth(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, {"puuid": "abc"}, capture)

    data = await api.get_account("asia", "Hide on bush", "KR1")

    assert data == {"puuid": "abc"}
    assert capture["url"] == (
        "https://asia.api.riotgames.com/riot/account/v1/accounts/"
        "by-riot-id/Hide%20on%20bush/KR1"
    )
    assert capture["headers"] == {
        "X-Riot-Token": "test-key",
        "User-Agent": "NothingBot/1.0",
    }


async def test_get_league_entries_uses_platform_host(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, [{"queueType": "RANKED_SOLO_5x5"}], capture)

    entries = await api.get_league_entries("kr", "puuid-1")

    assert isinstance(entries, list) and entries[0]["queueType"] == "RANKED_SOLO_5x5"
    assert capture["url"] == (
        "https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/puuid-1"
    )


async def test_get_match_raises_not_found(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 404, {}, capture)
    with pytest.raises(api.LolNotFound):
        await api.get_match("asia", "KR_123")


async def test_get_recent_match_ids_encodes_puuid_and_clamps_count(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, ["KR_1"], capture)

    match_ids = await api.get_recent_match_ids("asia", "puuid/1", count=99)

    assert match_ids == ["KR_1"]
    assert capture["url"].endswith(
        "/lol/match/v5/matches/by-puuid/puuid%2F1/ids?start=0&count=10"
    )


async def test_unexpected_account_shape_raises_api_error(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, [], capture)

    with pytest.raises(api.LolApiError):
        await api.get_account("asia", "nick", "tag")
