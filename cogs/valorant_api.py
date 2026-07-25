"""HenrikDev(비공식 발로란트) API 클라이언트.

라이엇 공식 VAL 엔드포인트는 개인 키로 사실상 쓸 수 없어, 커뮤니티 표준인
HenrikDev API(https://docs.henrikdev.xyz)를 사용한다. Basic 키는 즉시 발급되며
분당 30회 제한이 있다. 키는 공개 저장소에 박지 않고 `VALORANT_API_KEY` 환경변수
로만 주입한다 (미설정 시 명령이 '설정 필요'로 응답).

tts_engine 과 동일하게 module-level `aiohttp.ClientSession` 을 재사용한다.
호출 측(cog)이 예외 타입으로 사용자 메시지를 분기한다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

_BASE_URL = "https://api.henrikdev.xyz"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# HenrikDev 가 지원하는 리전 코드. platform 은 pc/console.
VALID_REGIONS = ("eu", "na", "ap", "kr", "latam", "br")
VALID_PLATFORMS = ("pc", "console")
DEFAULT_PLATFORM = "pc"


def default_region() -> str:
    region = os.getenv("VALORANT_DEFAULT_REGION", "kr").strip().lower()
    return region if region in VALID_REGIONS else "kr"


class ValorantConfigError(RuntimeError):
    """VALORANT_API_KEY 미설정 또는 키 거부(401/403)."""


class ValorantNotFound(RuntimeError):
    """해당 라이엇ID/전적을 찾지 못함(404)."""


class ValorantRateLimited(RuntimeError):
    """레이트 리밋 초과(429)."""


class ValorantApiError(RuntimeError):
    """그 외 상류 오류(5xx, 네트워크, 파싱)."""


_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def _api_key() -> str:
    key = os.getenv("VALORANT_API_KEY", "").strip()
    if not key:
        raise ValorantConfigError("VALORANT_API_KEY 미설정 — .env 확인")
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
        raise ValorantNotFound(f"not found: {path}")
    if status == 429:
        raise ValorantRateLimited("rate limited")
    if status in (401, 403):
        raise ValorantConfigError(f"API 키 거부({status}) — 키 확인")
    if status >= 400:
        raise ValorantApiError(f"upstream {status}: {path}")


async def _get(path: str) -> Any:
    """`path`(선행 슬래시 포함)를 GET 하고 JSON 의 `data` 를 반환한다."""
    key = _api_key()
    session = await _get_session()
    url = f"{_BASE_URL}{path}"
    try:
        async with session.get(url, headers={"Authorization": key}) as resp:
            _raise_for_status(resp.status, path)
            payload = await resp.json()
    except (ValorantConfigError, ValorantNotFound, ValorantRateLimited, ValorantApiError):
        raise
    except aiohttp.ClientError as exc:
        raise ValorantApiError(f"network error: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise ValorantApiError("timeout") from exc

    if not isinstance(payload, dict):
        raise ValorantApiError("unexpected response shape")
    embedded_status = payload.get("status")
    if isinstance(embedded_status, int) and embedded_status >= 400:
        _raise_for_status(embedded_status, path)
    if "data" not in payload:
        return payload
    data = payload["data"]
    if data is None:
        raise ValorantApiError("response data is missing")
    return data


def _encode(segment: str) -> str:
    return quote(segment, safe="")


def _expect_dict(data: Any, resource: str) -> dict:
    if not isinstance(data, dict):
        raise ValorantApiError(f"unexpected {resource} response shape")
    return data


def _expect_list(data: Any, resource: str) -> list[dict]:
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValorantApiError(f"unexpected {resource} response shape")
    return data


async def get_account(name: str, tag: str) -> dict:
    """라이엇ID 존재 확인 + puuid/레벨/카드 이미지. account v2."""
    data = await _get(f"/valorant/v2/account/{_encode(name)}/{_encode(tag)}")
    return _expect_dict(data, "account")


async def get_mmr(region: str, name: str, tag: str, *, platform: str = DEFAULT_PLATFORM) -> dict:
    """현재/최고 랭크. mmr v3."""
    data = await _get(
        f"/valorant/v3/mmr/{region}/{platform}/{_encode(name)}/{_encode(tag)}"
    )
    return _expect_dict(data, "mmr")


async def get_recent_matches(region: str, puuid: str, *, count: int = 5) -> list[dict]:
    """PUUID 기준 저장된 최근 경쟁전 요약.

    stored-matches 는 HenrikDev 서버에 수집된 경기만 반환하므로 호출 측에서
    best-effort 정보로 취급한다.
    """
    size = max(1, min(count, 10))
    data = await _get(
        f"/valorant/v1/by-puuid/stored-matches/{region}/{_encode(puuid)}"
        f"?mode=competitive&size={size}"
    )
    return _expect_list(data, "matches")
