"""Riot Games 공식 League of Legends API 클라이언트.

발로란트(HenrikDev, 비공식)와 달리 롤은 라이엇 공식 API 를 쓴다. 두 가지 라우팅이
섞여 있어 주의: 계정/전적은 대륙(regional: asia/americas/europe) 호스트, 소환사/랭크는
플랫폼(kr/na1/euw1/jp1) 호스트로 호출한다.

키는 `RIOT_API_KEY` 환경변수로만 주입한다. 개발용 키는 24시간마다 갱신되고
분당 100회(초당 20회) 제한이 있으므로, cog 가 유저별 쿨다운으로 보호한다.

2025-06 라이엇이 summonerId 기반 엔드포인트를 제거해, 랭크 조회는 puuid 기반
`league/v4/entries/by-puuid` 를 쓴다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
_USER_AGENT = os.getenv("RIOT_API_USER_AGENT", "NothingBot/1.0").strip() or "NothingBot/1.0"

# 플랫폼(소환사/랭크 호스트) → 대륙(계정/전적 호스트) 매핑.
_PLATFORM_TO_REGIONAL = {
    "kr": "asia",
    "jp1": "asia",
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}
VALID_PLATFORMS = tuple(_PLATFORM_TO_REGIONAL)


def default_platform() -> str:
    platform = os.getenv("LOL_DEFAULT_PLATFORM", "kr").strip().lower()
    return platform if platform in _PLATFORM_TO_REGIONAL else "kr"


def regional_for(platform: str) -> str:
    return _PLATFORM_TO_REGIONAL.get(platform, "asia")


def profile_icon_url(icon_id: int) -> str:
    # CommunityDragon 은 패치 버전 없이 최신 아이콘을 제공해 Data Dragon 버전 조회를
    # 생략할 수 있다.
    return f"https://cdn.communitydragon.org/latest/profile-icon/{icon_id}"


class LolConfigError(RuntimeError):
    """RIOT_API_KEY 미설정 또는 키 거부(401/403). 개발 키 만료(24h) 포함."""


class LolNotFound(RuntimeError):
    """해당 라이엇ID/소환사/전적을 찾지 못함(404)."""


class LolRateLimited(RuntimeError):
    """레이트 리밋 초과(429)."""


class LolApiError(RuntimeError):
    """그 외 상류 오류(5xx, 네트워크, 파싱)."""


_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def _api_key() -> str:
    key = os.getenv("RIOT_API_KEY", "").strip()
    if not key:
        raise LolConfigError("RIOT_API_KEY 미설정 — .env 확인")
    return key


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _raise_for_status(status: int, path: str) -> None:
    if status == 404:
        raise LolNotFound(f"not found: {path}")
    if status == 429:
        raise LolRateLimited("rate limited")
    if status in (401, 403):
        raise LolConfigError(f"API 키 거부({status}) — 키 만료/오류 확인")
    if status >= 400:
        raise LolApiError(f"upstream {status}: {path}")


async def _get(host: str, path: str):
    """`https://{host}.api.riotgames.com{path}` 를 GET 하고 JSON 을 반환한다."""
    key = _api_key()
    session = await _get_session()
    url = f"https://{host}.api.riotgames.com{path}"
    try:
        async with session.get(
            url,
            headers={
                "X-Riot-Token": key,
                # Riot's edge rejects generic Python clients on some networks.
                # Use an identifiable, stable product agent for every request.
                "User-Agent": _USER_AGENT,
            },
        ) as resp:
            _raise_for_status(resp.status, path)
            return await resp.json()
    except (LolConfigError, LolNotFound, LolRateLimited, LolApiError):
        raise
    except aiohttp.ClientError as exc:
        raise LolApiError(f"network error: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise LolApiError("timeout") from exc


async def get_account(regional: str, name: str, tag: str) -> dict:
    """라이엇ID → puuid. account-v1 (대륙 호스트)."""
    data = await _get(
        regional,
        f"/riot/account/v1/accounts/by-riot-id/{quote(name, safe='')}/{quote(tag, safe='')}",
    )
    if not isinstance(data, dict):
        raise LolApiError("unexpected account response shape")
    return data


async def get_summoner(platform: str, puuid: str) -> dict:
    """레벨/프로필 아이콘. summoner-v4 (플랫폼 호스트)."""
    data = await _get(
        platform, f"/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}"
    )
    if not isinstance(data, dict):
        raise LolApiError("unexpected summoner response shape")
    return data


async def get_league_entries(platform: str, puuid: str) -> list[dict]:
    """솔로/자유 랭크 목록. league-v4 by-puuid (플랫폼 호스트)."""
    data = await _get(
        platform, f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}"
    )
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise LolApiError("unexpected league response shape")
    return data


async def get_recent_match_ids(regional: str, puuid: str, count: int = 3) -> list[str]:
    """최근 매치 ID 목록. match-v5 (대륙 호스트)."""
    count = max(1, min(count, 10))
    data = await _get(
        regional,
        f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}"
        f"/ids?start=0&count={count}",
    )
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        raise LolApiError("unexpected match id response shape")
    return data


async def get_match(regional: str, match_id: str) -> dict:
    """매치 상세. match-v5 (대륙 호스트)."""
    data = await _get(
        regional, f"/lol/match/v5/matches/{quote(match_id, safe='')}"
    )
    if not isinstance(data, dict):
        raise LolApiError("unexpected match response shape")
    return data
