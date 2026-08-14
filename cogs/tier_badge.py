"""파티 모집 임베드에 붙는 티어 뱃지 — 게임 판별, 조회, 표기.

`/롤 등록` · `/발로란트 등록` 으로 이미 라이엇ID 가 묶여 있는 사람들이 파티에
모이는데, 지금까지 두 기능은 서로를 몰랐다. 여기서 둘을 잇는다.

**어떤 게임의 티어를 보여줄지는 모집 제목으로 판별한다.** 파티에는 게임 필드가
없고(제목·시작·정원만 받는다), 모집 제목의 대부분이 "발로 3인" · "롤 듀오"
처럼 게임 이름으로 시작한다. 제목에서 게임을 못 찾으면 뱃지를 아예 붙이지
않는다 — "저녁 먹자" 같은 모집에 랭크가 뜨는 게 더 이상하고, 덤으로 API 호출도
그만큼 줄어든다.

롤은 **솔로 랭크만** 본다. 자유 랭크를 섞으면 "골드 2명" 같은 요약이 어느 큐
얘기인지 알 수 없어진다.

뱃지 그림은 라이엇 공식 티어 엠블럼을 애플리케이션 이모지로 올려서 쓴다
(`scripts/sync_tier_emojis.py`). 아직 안 올렸거나 조회에 실패하면 유니코드
이모지로 조용히 떨어지므로, 이모지 업로드는 기능의 전제 조건이 아니다.

조회는 언제나 best-effort 다 (Rule 03). API 가 죽거나 느려도 파티 참가 버튼은
정상 동작해야 하므로, 호출 측이 예외를 삼키고 뱃지만 빠진 임베드를 그린다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from cogs import lol_api, valorant_api
from cogs.lol_store import LolStore
from cogs.tier_store import TierSnapshot, TierStore
from cogs.valorant_store import ValorantStore

log = logging.getLogger(__name__)

GAME_LOL = "lol"
GAME_VALORANT = "valorant"

# 개발 키 한도 보호. 갱신은 사람이 버튼을 누를 때만 일어나고 TTL 이 하루라
# 정상 사용에서는 여기 걸릴 일이 거의 없다. 여러 명이 동시에 참가를 눌러
# 갱신이 겹치는 순간만 직렬로 늘어뜨린다.
_MIN_REFRESH_INTERVAL_SEC = 1.5
# 참가 버튼 응답 뒤에 도는 갱신이라 사용자를 기다리게 하지는 않지만,
# 상류가 멈췄을 때 태스크가 남지 않도록 상한을 둔다.
REFRESH_TIMEOUT_SEC = 8.0

_UNRANKED = "UNRANKED"
_UNRANKED_LABEL = "언랭"
_UNRANKED_EMOJI = "▫️"

# 제목에서 게임을 찾는 키워드. 한글 표기 흔들림("발로란트"/"발로"/"발로란뜨")과
# 영문·모드 이름을 함께 받는다. 긴 키워드가 먼저 걸리도록 정렬해 둔다.
_GAME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        GAME_VALORANT,
        ("발로란트", "발로란뜨", "발로", "valorant", "valo", "vlr", "격전"),
    ),
    (
        GAME_LOL,
        (
            "리그오브레전드",
            "league of legends",
            "칼바람",
            "협곡",
            "솔랭",
            "자랭",
            "소환사",
            "롤체",  # 전략적 팀 전투. 롤 랭크와 무관하므로 아래에서 제외한다.
            "롤",
            "lol",
            "aram",
        ),
    ),
)
# 문자열에는 "롤" 이 들어가지만 소환사 랭크와 상관없는 모집. 오검출을 막는다.
_LOL_FALSE_POSITIVES = ("롤체", "롤토체스", "tft")

_LOL_TIERS: tuple[tuple[str, str, str], ...] = (
    ("IRON", "아이언", "⚙️"),
    ("BRONZE", "브론즈", "🥉"),
    ("SILVER", "실버", "🥈"),
    ("GOLD", "골드", "🥇"),
    ("PLATINUM", "플래티넘", "💠"),
    ("EMERALD", "에메랄드", "💚"),
    ("DIAMOND", "다이아", "💎"),
    ("MASTER", "마스터", "🔮"),
    ("GRANDMASTER", "그랜드마스터", "👑"),
    ("CHALLENGER", "챌린저", "☀️"),
)
_VALORANT_TIERS: tuple[tuple[str, str, str], ...] = (
    ("IRON", "아이언", "⚙️"),
    ("BRONZE", "브론즈", "🥉"),
    ("SILVER", "실버", "🥈"),
    ("GOLD", "골드", "🥇"),
    ("PLATINUM", "플래티넘", "💠"),
    ("DIAMOND", "다이아", "💎"),
    ("ASCENDANT", "초월자", "🌿"),
    ("IMMORTAL", "불멸", "🔮"),
    ("RADIANT", "레디언트", "☀️"),
)


def _tier_table(game: str) -> dict[str, tuple[int, str, str]]:
    """티어 키 → (서열, 한국어 표기, 이모지). 언랭이 0 이라 서열은 1부터 센다."""
    rows = _VALORANT_TIERS if game == GAME_VALORANT else _LOL_TIERS
    table = {key: (index, korean, emoji) for index, (key, korean, emoji) in enumerate(rows, start=1)}
    table[_UNRANKED] = (0, _UNRANKED_LABEL, _UNRANKED_EMOJI)
    return table


_TIER_TABLES = {game: _tier_table(game) for game in (GAME_LOL, GAME_VALORANT)}

# 단계가 없는 최상위 티어. 롤 마스터 이상은 rank 필드가 "I" 로 오지만 표기하지
# 않고, 발로란트 레디언트도 마찬가지다.
_NO_DIVISION_TIERS = frozenset(
    {"MASTER", "GRANDMASTER", "CHALLENGER", "RADIANT", _UNRANKED}
)

_ROMAN_TO_ARABIC = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}
_TRAILING_NUMBER_RE = re.compile(r"^(?P<name>.+?)\s+(?P<division>\d)$")

# CommunityDragon 은 패치 버전 없이 최신 에셋을 주므로 Data Dragon 처럼 버전을
# 먼저 조회할 필요가 없다 (lol_api.profile_icon_url 과 같은 이유로 고른 CDN).
_CDRAGON_IMAGES = (
    "https://raw.communitydragon.org/latest/plugins"
    "/rcp-fe-lol-static-assets/global/default/images"
)
# 언랭은 `ranked-emblem` 세트에 없다. `ranked-mini-crests` 에 있긴 하지만 48×48
# 이라 128px 로 올라가는 다른 티어 옆에서 뭉개진다. 이쪽 500×500 을 쓴다.
_CDRAGON_SHARED = (
    "https://raw.communitydragon.org/latest/plugins"
    "/rcp-fe-lol-shared-components/global/default"
)
# 애플리케이션 이모지 이름. Discord 는 [A-Za-z0-9_] 만 받는다.
EMOJI_NAME_PREFIX = "koa_"

# 이모지를 티어 단위로 올릴지 단계 단위로 올릴지는 게임의 아트가 정한다.
# 롤은 골드 1~4 가 엠블럼 하나를 공유하지만, 발로란트는 단계마다 화살표 수가
# 달라 그림이 전부 다르다. 그래서 롤은 티어 10종, 발로란트는 단계까지 26종이다.
_PER_DIVISION_GAMES = frozenset({GAME_VALORANT})


def emoji_name(game: str, tier: str, division: str = "") -> str:
    suffix = f"_{division}" if division else ""
    return f"{EMOJI_NAME_PREFIX}{game}_{tier.lower()}{suffix}"


def tier_slots(game: str) -> tuple[tuple[str, str], ...]:
    """그 게임에서 이모지를 올릴 `(티어, 단계)` 전체 목록.

    업로드 스크립트가 대상 목록을 만들 때, `TierEmojis` 가 이름표를 만들 때
    같은 함수를 본다. 둘이 어긋나면 올려도 안 붙는다.
    """
    rows = _VALORANT_TIERS if game == GAME_VALORANT else _LOL_TIERS
    if game not in _PER_DIVISION_GAMES:
        return tuple((tier, "") for tier, _, _ in rows) + ((_UNRANKED, ""),)
    slots: list[tuple[str, str]] = []
    for tier, _, _ in rows:
        if tier in _NO_DIVISION_TIERS:
            slots.append((tier, ""))
            continue
        slots.extend((tier, str(step)) for step in (1, 2, 3))
    slots.append((_UNRANKED, ""))
    return tuple(slots)


def is_known_tier(game: str, tier: str) -> bool:
    return (tier or "").strip().upper() in _TIER_TABLES.get(game, {})


def parse_valorant_tier_name(name: str) -> tuple[str, str] | None:
    """`"Gold 2"` · `"RADIANT"` → `("GOLD", "2")` · `("RADIANT", "")`.

    mmr 응답과 valorant-api.com 의 티어 표가 같은 표기를 쓰므로 둘 다 이걸로
    판다. 아는 티어인지까지는 보지 않는다 — 호출 측이 `is_known_tier` 로
    거른다. 새 티어가 생겼을 때 조용히 언랭으로 눕히지 않기 위해서다.
    빈 문자열만 None.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return None
    matched = _TRAILING_NUMBER_RE.match(cleaned)
    tier, division = (
        (matched.group("name"), matched.group("division"))
        if matched
        else (cleaned, "")
    )
    key = tier.strip().upper()
    return key, "" if key in _NO_DIVISION_TIERS else division


def lol_emblem_url(tier: str) -> str | None:
    """티어 → 라이엇 공식 엠블럼 PNG. 없는 티어면 None.

    랭크 티어는 `ranked-emblem` 의 전체 엠블럼을 쓴다. 작게 그려진
    `ranked-mini-crests` 쪽이 축소 화질은 낫지만 에메랄드가 SVG 로만 있어
    (Discord 이모지는 SVG 를 안 받는다) 세트가 비므로 쓰지 않는다.
    언랭크만 그 세트에 없어서 shared-components 쪽에서 가져온다.
    """
    key = (tier or "").strip().upper()
    if key == _UNRANKED:
        return f"{_CDRAGON_SHARED}/unranked.png"
    if key not in {name for name, _, _ in _LOL_TIERS}:
        return None
    return f"{_CDRAGON_IMAGES}/ranked-emblem/emblem-{key.lower()}.png"


def lol_emblem_urls() -> dict[str, str]:
    """업로드 스크립트가 쓰는 `{이모지 이름: PNG 주소}`.

    발로란트 쪽은 에셋 주소에 시즌마다 바뀌는 UUID 가 들어가 여기서 만들 수
    없다. 스크립트가 valorant-api.com 을 조회해 채운다.
    """
    return {
        emoji_name(GAME_LOL, tier, division): url
        for tier, division in tier_slots(GAME_LOL)
        if (url := lol_emblem_url(tier)) is not None
    }


class TierEmojis:
    """업로드된 애플리케이션 이모지 마크업 표.

    비어 있으면 모든 조회가 빈 문자열을 돌려주고, 호출 측이 유니코드 이모지로
    떨어진다. 이모지를 안 올린 봇에서도 기능이 그대로 동작해야 하기 때문이다.
    """

    def __init__(self) -> None:
        self._markup: dict[tuple[str, str, str], str] = {}
        # 티어만 아는 자리(구성 요약)에서 쓸 대표 그림. 발로란트처럼 단계별로
        # 그림이 나뉘는 게임은 가장 낮은 단계를 대표로 세운다.
        self._by_tier: dict[tuple[str, str], str] = {}

    def load(self, emojis: Iterable[Any]) -> int:
        """`fetch_application_emojis()` 결과에서 티어 이모지만 추려 담는다."""
        wanted = {
            emoji_name(game, tier, division): (game, tier, division)
            for game in _TIER_TABLES
            for tier, division in tier_slots(game)
        }
        found: dict[tuple[str, str, str], str] = {}
        for emoji in emojis:
            key = wanted.get(str(getattr(emoji, "name", "")))
            if key is not None:
                found[key] = str(emoji)
        self._markup = found
        self._by_tier = {}
        for (game, tier, division), markup in sorted(found.items()):
            self._by_tier.setdefault((game, tier), markup)
            if not division:
                self._by_tier[(game, tier)] = markup
        return len(found)

    def markup(self, game: str, tier: str, division: str = "") -> str:
        exact = self._markup.get((game, tier, division))
        if exact:
            return exact
        # 단계별 이모지를 아직 안 올렸거나 요약처럼 단계를 모르는 자리.
        return self._by_tier.get((game, tier), "")

    def __len__(self) -> int:
        return len(self._markup)


def detect_game(title: str) -> str | None:
    """모집 제목에서 게임을 판별한다. 못 찾으면 None (= 뱃지 없음)."""
    lowered = " ".join(title.split()).lower()
    if not lowered:
        return None
    for game, keywords in _GAME_KEYWORDS:
        for keyword in keywords:
            if keyword not in lowered:
                continue
            if game == GAME_LOL and any(
                bad in lowered for bad in _LOL_FALSE_POSITIVES
            ):
                return None
            return game
    return None


def _snapshot(
    game: str, tier: str, division: str, *, puuid: str = "", now: float | None = None
) -> TierSnapshot:
    table = _TIER_TABLES[game]
    key = (tier or "").strip().upper()
    # 그림은 렌더 시점에 _tier_icon 이 고른다 (업로드된 엠블럼이 있으면 그쪽이
    # 우선이라 스냅샷에 굳히면 안 된다). 여기서는 서열과 한국어 표기만 쓴다.
    weight, korean, _ = table.get(key, table[_UNRANKED])
    if key not in table:
        # 라이엇이 티어를 추가하면 여기로 떨어진다. 언랭으로 눕히지 말고
        # 원문을 그대로 보여주되 서열만 모른다고 처리한다.
        log.info("unknown %s tier %r — showing raw label", game, tier)
        korean, weight = key.title(), 0
        division = ""
    if key in _NO_DIVISION_TIERS:
        division = ""
    label = f"{korean} {division}".strip()
    return TierSnapshot(
        game=game,
        tier=key or _UNRANKED,
        division=division,
        label=label,
        weight=weight,
        fetched_at=time.time() if now is None else now,
        puuid=puuid,
    )


def unranked_snapshot(game: str, *, puuid: str = "", now: float | None = None) -> TierSnapshot:
    return _snapshot(game, _UNRANKED, "", puuid=puuid, now=now)


def lol_snapshot_from_entries(
    entries: Sequence[dict], *, puuid: str = "", now: float | None = None
) -> TierSnapshot:
    """league-v4 응답에서 솔로 랭크만 뽑아 스냅샷으로 만든다."""
    solo = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("queueType") == "RANKED_SOLO_5x5"
        ),
        None,
    )
    if solo is None:
        return unranked_snapshot(GAME_LOL, puuid=puuid, now=now)
    rank = str(solo.get("rank", "")).strip().upper()
    return _snapshot(
        GAME_LOL,
        str(solo.get("tier", "")),
        _ROMAN_TO_ARABIC.get(rank, ""),
        puuid=puuid,
        now=now,
    )


def valorant_snapshot_from_mmr(mmr: dict, *, now: float | None = None) -> TierSnapshot:
    """mmr v3 응답의 `current.tier.name`("Gold 2", "Radiant")을 스냅샷으로."""
    current = mmr.get("current") if isinstance(mmr, dict) else None
    tier_name = ""
    if isinstance(current, dict):
        tier = current.get("tier")
        if isinstance(tier, dict):
            tier_name = str(tier.get("name") or "").strip()
    parsed = parse_valorant_tier_name(tier_name)
    if parsed is None:
        return unranked_snapshot(GAME_VALORANT, now=now)
    return _snapshot(GAME_VALORANT, parsed[0], parsed[1], now=now)


def _tier_icon(
    game: str, tier: str, division: str, emojis: TierEmojis | None
) -> str:
    """티어 그림. 업로드된 엠블럼 이모지가 있으면 그걸, 없으면 유니코드."""
    if emojis is not None:
        markup = emojis.markup(game, tier, division)
        if markup:
            return markup
    table = _TIER_TABLES.get(game, _TIER_TABLES[GAME_LOL])
    return table.get(tier, (0, "", "🎮"))[2]


def format_badge(snapshot: TierSnapshot, *, emojis: TierEmojis | None = None) -> str:
    """참가자 줄 뒤에 붙는 표기. 예: `🥇 골드 2`."""
    icon = _tier_icon(snapshot.game, snapshot.tier, snapshot.division, emojis)
    return f"{icon} {snapshot.label}".strip()


def summarize(
    snapshots: Iterable[TierSnapshot],
    *,
    top: int = 3,
    emojis: TierEmojis | None = None,
) -> str:
    """참가자 구성 한 줄 요약. 예: `🥇골드 2 · 🥈실버 1`.

    단계는 무시하고 티어로만 묶는다. "골드 2가 1명, 골드 4가 2명" 보다
    "골드 3명" 이 한눈에 들어온다.
    """
    counts: dict[str, int] = {}
    order: dict[str, tuple[int, str]] = {}
    for snapshot in snapshots:
        table = _TIER_TABLES.get(snapshot.game, _TIER_TABLES[GAME_LOL])
        weight, korean, _ = table.get(snapshot.tier, (0, snapshot.tier.title(), "🎮"))
        # 요약은 단계를 접어 티어로만 묶으므로 대표 그림을 쓴다.
        icon = _tier_icon(snapshot.game, snapshot.tier, "", emojis)
        counts[snapshot.tier] = counts.get(snapshot.tier, 0) + 1
        order[snapshot.tier] = (weight, f"{icon}{korean}")
    if not counts:
        return ""
    ranked = sorted(counts.items(), key=lambda item: (-order[item[0]][0], item[0]))
    shown = ranked[: max(1, top)]
    parts = [f"{order[tier][1]} {count}" for tier, count in shown]
    hidden = sum(count for _, count in ranked[len(shown) :])
    if hidden:
        parts.append(f"외 {hidden}")
    return " · ".join(parts)


@dataclass(frozen=True)
class PartyBadges:
    """한 파티에 대해 계산된 뱃지 묶음. 게임을 못 찾으면 전부 비어 있다."""

    game: str | None
    badges: dict[int, str]
    summary: str

    def __bool__(self) -> bool:
        return bool(self.badges)


class _RefreshGate:
    """갱신 호출 사이의 최소 간격을 강제한다. 상류 레이트 리밋 보호용."""

    def __init__(self, interval: float = _MIN_REFRESH_INTERVAL_SEC) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._interval - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class TierService:
    """등록 정보 + 티어 캐시 + 상류 API 를 묶어 뱃지를 만들어 준다."""

    def __init__(
        self,
        *,
        tier_store: TierStore | None = None,
        lol_store: LolStore | None = None,
        valorant_store: ValorantStore | None = None,
        gate: _RefreshGate | None = None,
    ) -> None:
        self.cache = tier_store if tier_store is not None else TierStore()
        self.lol = lol_store if lol_store is not None else LolStore()
        self.valorant = valorant_store if valorant_store is not None else ValorantStore()
        self.emojis = TierEmojis()
        self._gate = gate if gate is not None else _RefreshGate()

    async def load_emojis(self, bot: Any) -> int:
        """봇에 올라간 티어 엠블럼 이모지를 읽어 온다.

        아직 안 올렸으면 0 이고, 그 경우 뱃지는 유니코드 이모지로 나간다.
        """
        fetch = getattr(bot, "fetch_application_emojis", None)
        if fetch is None:
            return 0
        return self.emojis.load(await fetch())

    async def _registration(
        self, game: str, guild_id: int, user_id: int
    ) -> dict[str, Any] | None:
        store = self.lol if game == GAME_LOL else self.valorant
        return await store.get(guild_id, user_id)

    async def snapshots_for(
        self, guild_id: int, user_ids: Iterable[int], game: str
    ) -> dict[int, TierSnapshot]:
        """캐시에 있는 것만 모은다. 여기서는 절대 API 를 부르지 않는다.

        등록을 지운 사람은 캐시가 남아 있어도 뱃지를 떼야 하므로 등록 여부를
        같이 확인한다. 두 조회 모두 인메모리라 임베드 경로에서 IO 가 없다.
        """
        result: dict[int, TierSnapshot] = {}
        for user_id in user_ids:
            snapshot = self.cache.get_sync(guild_id, user_id, game)
            if snapshot is None:
                continue
            if await self._registration(game, guild_id, user_id) is None:
                continue
            result[user_id] = snapshot
        return result

    async def badges_for(
        self, guild_id: int, user_ids: Iterable[int], game: str
    ) -> dict[int, str]:
        snapshots = await self.snapshots_for(guild_id, user_ids, game)
        return {
            user_id: format_badge(snap, emojis=self.emojis)
            for user_id, snap in snapshots.items()
        }

    def summarize_snapshots(self, snapshots: Iterable[TierSnapshot]) -> str:
        return summarize(snapshots, emojis=self.emojis)

    async def for_party(
        self, guild_id: int, title: str, user_ids: Sequence[int]
    ) -> "PartyBadges":
        """제목으로 게임을 판별하고, 캐시에 있는 만큼만 뱃지를 만든다."""
        game = detect_game(title)
        if game is None:
            return PartyBadges(None, {}, "")
        snapshots = await self.snapshots_for(guild_id, user_ids, game)
        badges = {
            user_id: format_badge(snap, emojis=self.emojis)
            for user_id, snap in snapshots.items()
        }
        return PartyBadges(game, badges, self.summarize_snapshots(snapshots.values()))

    async def refresh(self, guild_id: int, user_id: int, game: str) -> bool:
        """한 사람의 티어를 상류에서 다시 읽는다.

        캐시가 아직 신선하거나 등록이 없으면 호출하지 않고 False.
        갱신에 성공하면 True — 호출 측이 그때만 메시지를 다시 그리면 된다.
        예외는 그대로 올린다. 삼킬지 말지는 호출 측이 정한다.
        """
        cached = self.cache.get_sync(guild_id, user_id, game)
        if cached is not None and self.cache.is_fresh(cached):
            return False
        registration = await self._registration(game, guild_id, user_id)
        if registration is None:
            return False
        await self._gate.wait()
        if game == GAME_LOL:
            snapshot = await self._fetch_lol(registration, cached)
        else:
            snapshot = await self._fetch_valorant(registration)
        await self.cache.set(guild_id, user_id, snapshot)
        return True

    async def _fetch_lol(
        self, registration: dict[str, Any], cached: TierSnapshot | None
    ) -> TierSnapshot:
        platform = str(registration.get("platform") or "").strip().lower()
        if platform not in lol_api.VALID_PLATFORMS:
            platform = lol_api.default_platform()
        # 티어는 하루면 상하지만 puuid 는 계정이 살아 있는 한 그대로다.
        # 굳어 있으면 account 조회 한 번을 통째로 건너뛴다.
        puuid = cached.puuid if cached is not None else ""
        if not puuid:
            account = await lol_api.get_account(
                lol_api.regional_for(platform),
                str(registration.get("name", "")),
                str(registration.get("tag", "")),
            )
            puuid = str(account.get("puuid") or "")
            if not puuid:
                raise lol_api.LolApiError("account response has no puuid")
        entries = await lol_api.get_league_entries(platform, puuid)
        return lol_snapshot_from_entries(entries, puuid=puuid)

    async def _fetch_valorant(self, registration: dict[str, Any]) -> TierSnapshot:
        region = str(registration.get("region") or "").strip().lower()
        if region not in valorant_api.VALID_REGIONS:
            region = valorant_api.default_region()
        platform = str(registration.get("platform") or "").strip().lower()
        if platform not in valorant_api.VALID_PLATFORMS:
            platform = valorant_api.DEFAULT_PLATFORM
        mmr = await valorant_api.get_mmr(
            region,
            str(registration.get("name", "")),
            str(registration.get("tag", "")),
            platform=platform,
        )
        return valorant_snapshot_from_mmr(mmr)
