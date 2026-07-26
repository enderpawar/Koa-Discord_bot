"""발로란트 전적 조회 단위 테스트.

순수 함수(파싱/임베드/쿨다운)와 저장소 왕복, API 클라이언트의 URL 구성/데이터
언랩/상태코드 매핑을 다룬다. 실제 HTTP 는 가짜 세션으로 대체한다 (Rule: 외부
의존은 단위 테스트에서 mock).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cogs import valorant_api as api
from cogs import valorant_cog as vc
from cogs.valorant_store import ValorantStore


def test_slash_group_exposes_complete_command_set():
    assert vc.ValorantCog.valorant.name == "발로란트"
    assert {command.name for command in vc.ValorantCog.valorant.commands} == {
        "등록",
        "전적",
        "검색",
        "등록해제",
    }


# ---- _parse_riot_id -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hide on bush#KR1", ("Hide on bush", "KR1")),
        ("  nick#tag  ", ("nick", "tag")),
        ("name#with#hash", ("name#with", "hash")),  # 마지막 # 기준
    ],
)
def test_parse_riot_id_valid(raw, expected):
    assert vc._parse_riot_id(raw) == expected


@pytest.mark.parametrize("raw", ["noseparator", "#tagonly", "nameonly#", "   ", "#"])
def test_parse_riot_id_invalid(raw):
    assert vc._parse_riot_id(raw) is None


# ---- _tier_emoji ----------------------------------------------------------


def test_tier_emoji_known_and_unknown():
    assert vc._tier_emoji("Immortal 2") == "🔮"
    assert vc._tier_emoji("Radiant") == "☀️"
    assert vc._tier_emoji("무순위") == "🎯"
    assert vc._tier_emoji("") == "🎯"


def test_resolved_region_prefers_api_account_region():
    assert vc._resolved_region({"region": "AP"}, "kr") == "ap"
    assert vc._resolved_region({"region": "unknown"}, "kr") == "kr"
    assert vc._resolved_region({}, "eu") == "eu"


def test_card_image_url_supports_account_v1_and_v2_shapes():
    assert vc._card_image_url({"card": {"small": "https://img/small.png"}}) == (
        "https://img/small.png"
    )
    assert vc._card_image_url({"card": "https://img/card.png"}) == "https://img/card.png"
    assert vc._card_image_url({"card": "player-card-uuid"}) is None


# ---- _match_line ----------------------------------------------------------


def _match(team, red, blue, *, k=10, d=5, a=3, map_name="Ascent"):
    return {
        "meta": {"map": {"name": map_name}},
        "stats": {"team": team, "kills": k, "deaths": d, "assists": a},
        "teams": {"red": red, "blue": blue},
    }


def test_match_line_win_loss_draw():
    assert vc._match_line(_match("Red", 13, 7)).startswith("✅")
    assert vc._match_line(_match("Blue", 13, 7)).startswith("❌")
    assert vc._match_line(_match("Red", 12, 12)).startswith("➖")


def test_match_line_contains_kda_and_map():
    line = vc._match_line(_match("Red", 13, 10, k=24, d=14, a=6, map_name="Bind"))
    assert "Bind" in line
    assert "24/14/6" in line
    assert "13-10" in line


def test_match_line_missing_scores_is_safe():
    line = vc._match_line({"meta": {}, "stats": {}, "teams": {}})
    assert line.startswith("▫️")  # 승패 판정 불가여도 크래시하지 않음


def test_match_performance_extracts_outcome_and_kda():
    performance = vc._match_performance(_match("Blue", 7, 13, k=3, d=8, a=2))
    assert performance.outcome == "win"
    assert (performance.kills, performance.deaths, performance.assists) == (3, 8, 2)


# ---- _profile_embed -------------------------------------------------------


def test_profile_embed_fields():
    account = {"account_level": 152, "card": {"small": "http://img/card.png"}}
    mmr = {
        "current": {"tier": {"name": "Immortal 1"}, "rr": 42},
        "peak": {"tier": {"name": "Immortal 3"}},
    }
    matches = [_match("Red", 13, 5)]
    embed = vc._profile_embed("nick", "KR1", "kr", account, mmr, matches)

    assert embed.title == "nick#KR1"
    assert embed.thumbnail.url == "http://img/card.png"
    field_names = [f.name for f in embed.fields]
    assert "현재 랭크" in field_names
    assert "최고 랭크" in field_names
    assert "최근 경기" in field_names
    recent = next(f for f in embed.fields if f.name == "최근 경기")
    assert "나띵이" in recent.value
    current_field = next(f for f in embed.fields if f.name == "현재 랭크")
    assert "Immortal 1" in current_field.value
    assert "42 RR" in current_field.value


def test_profile_embed_handles_unranked():
    embed = vc._profile_embed("n", "t", "ap", {}, {}, [])
    current_field = next(f for f in embed.fields if f.name == "현재 랭크")
    assert "무순위" in current_field.value
    # 경기 목록이 없으면 최근 경기 필드는 생략
    assert "최근 경기" not in [f.name for f in embed.fields]


def test_profile_embed_accepts_v2_card_url():
    embed = vc._profile_embed(
        "n",
        "t",
        "kr",
        {"card": "https://img/card.png"},
        {},
        [],
    )
    assert embed.thumbnail.url == "https://img/card.png"


# ---- _Cooldown ------------------------------------------------------------


def test_cooldown_blocks_then_allows(monkeypatch):
    cd = vc._Cooldown(interval=5.0)
    now = [100.0]
    monkeypatch.setattr(vc.time, "monotonic", lambda: now[0])
    assert cd.retry_after(1) == 0.0
    cd.stamp(1)
    assert cd.retry_after(1) > 0
    assert cd.retry_after(2) == 0.0  # 유저별 격리
    now[0] += 6
    assert cd.retry_after(1) == 0.0


# ---- ValorantStore --------------------------------------------------------


async def test_store_roundtrip_and_persistence(tmp_path: Path):
    path = tmp_path / "ids.json"
    store = ValorantStore(path)
    await store.set(42, name="nick", tag="KR1", region="kr", platform="pc")

    got = await store.get(42)
    assert got == {"name": "nick", "tag": "KR1", "region": "kr", "platform": "pc"}
    assert await store.get(99) is None

    # 새 인스턴스가 디스크에서 복원해야 한다
    reloaded = ValorantStore(path)
    assert (await reloaded.get(42))["name"] == "nick"

    assert await store.remove(42) is True
    assert await store.remove(42) is False
    assert await store.get(42) is None


async def test_store_recovers_from_corrupt_file(tmp_path: Path):
    path = tmp_path / "ids.json"
    path.write_text("{ not json", encoding="utf-8")
    store = ValorantStore(path)  # 손상 파일이어도 예외 없이 빈 상태로 시작
    assert await store.get(1) is None
    assert path.with_suffix(".json.corrupt").exists()


# ---- valorant_api ---------------------------------------------------------


def test_default_region(monkeypatch):
    monkeypatch.delenv("VALORANT_DEFAULT_REGION", raising=False)
    assert api.default_region() == "kr"
    monkeypatch.setenv("VALORANT_DEFAULT_REGION", "EU")
    assert api.default_region() == "eu"
    monkeypatch.setenv("VALORANT_DEFAULT_REGION", "bogus")
    assert api.default_region() == "kr"


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("VALORANT_API_KEY", raising=False)
    with pytest.raises(api.ValorantConfigError):
        api._api_key()


@pytest.mark.parametrize(
    "status,exc",
    [
        (404, api.ValorantNotFound),
        (429, api.ValorantRateLimited),
        (401, api.ValorantConfigError),
        (403, api.ValorantConfigError),
        (500, api.ValorantApiError),
    ],
)
def test_raise_for_status(status, exc):
    with pytest.raises(exc):
        api._raise_for_status(status, "/x")


class _FakeResp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, status: int, payload: dict, capture: dict) -> None:
        self._status = status
        self._payload = payload
        self._capture = capture

    def get(self, url, headers=None):
        self._capture["url"] = url
        self._capture["headers"] = headers
        return _FakeResp(self._status, self._payload)


def _patch_session(monkeypatch, status, payload, capture):
    monkeypatch.setenv("VALORANT_API_KEY", "test-key")

    async def _fake_get_session():
        return _FakeSession(status, payload, capture)

    monkeypatch.setattr(api, "_get_session", _fake_get_session)


async def test_get_mmr_builds_url_and_unwraps_data(monkeypatch):
    capture: dict = {}
    payload = {"status": 200, "data": {"current": {"tier": {"name": "Gold 1"}}}}
    _patch_session(monkeypatch, 200, payload, capture)

    data = await api.get_mmr("kr", "nick", "KR1", platform="pc")

    assert data == {"current": {"tier": {"name": "Gold 1"}}}
    assert capture["url"] == (
        "https://api.henrikdev.xyz/valorant/v3/mmr/kr/pc/nick/KR1"
    )
    assert capture["headers"] == {"Authorization": "test-key"}


async def test_get_account_url_encodes_spaces(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, {"data": {"name": "Hide on bush"}}, capture)

    await api.get_account("Hide on bush", "KR1")

    assert capture["url"].endswith("/valorant/v2/account/Hide%20on%20bush/KR1")


async def test_get_recent_matches_uses_puuid_and_returns_list(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, {"data": [{"meta": {}}, {"meta": {}}]}, capture)

    matches = await api.get_recent_matches("kr", "puuid/1", count=2)

    assert isinstance(matches, list)
    assert len(matches) == 2
    assert capture["url"] == (
        "https://api.henrikdev.xyz/valorant/v1/by-puuid/stored-matches/"
        "kr/puuid%2F1?mode=competitive&size=2"
    )


async def test_get_raises_not_found(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 404, {"errors": [{"message": "Not found"}]}, capture)

    with pytest.raises(api.ValorantNotFound):
        await api.get_account("ghost", "0000")


async def test_embedded_error_status_is_not_treated_as_data(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, {"status": 404, "data": None}, capture)

    with pytest.raises(api.ValorantNotFound):
        await api.get_account("ghost", "0000")


async def test_unexpected_payload_shape_raises_api_error(monkeypatch):
    capture: dict = {}
    _patch_session(monkeypatch, 200, {"data": []}, capture)

    with pytest.raises(api.ValorantApiError):
        await api.get_account("nick", "tag")
