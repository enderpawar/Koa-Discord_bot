"""대시보드 공개 주소 해석.

우선순위:
1. `ADMIN_WEB_PUBLIC_URL` — 고정 주소(도메인 + TLS 구성). 있으면 그대로 쓴다.
2. `CLOUDFLARED_METRICS_URL` — Cloudflare 임시 터널(trycloudflare)의 metrics
   엔드포인트. `/quicktunnel` 이 `{"hostname": "...trycloudflare.com"}` 을 준다.

임시 터널 주소는 cloudflared 가 재시작할 때마다 바뀐다. 그래서 배포 시점에
`.env` 로 박아 넣지 않고 런타임에 물어본다 — 터널이 새 주소를 받아도 봇을
재시작할 필요가 없고, 그 뒤에 발급되는 키 DM 은 살아 있는 주소를 담는다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import aiohttp

log = logging.getLogger(__name__)

_QUICK_TUNNEL_PATH = "/quicktunnel"
_TIMEOUT = aiohttp.ClientTimeout(total=5)
# 터널 주소는 자주 바뀌지 않는다. 매번 물어보면 DM 발송이 느려진다.
_CACHE_TTL_SEC = 60.0

_cached: str | None = None
_cached_at: float = 0.0
_lock = asyncio.Lock()


def _static_url() -> str | None:
    value = os.getenv("ADMIN_WEB_PUBLIC_URL", "").strip()
    return value.rstrip("/") if value else None


def _metrics_url() -> str | None:
    value = os.getenv("CLOUDFLARED_METRICS_URL", "").strip()
    return value.rstrip("/") if value else None


def cached_url() -> str | None:
    """마지막으로 확인된 주소. 동기 경로(임베드 조립)에서 쓴다.

    한 번도 해석되지 않았으면 None 이다. 네트워크를 타지 않는다.
    """
    return _static_url() or _cached


async def resolve_url() -> str | None:
    """공개 주소를 확인한다. 필요하면 cloudflared 에 물어본다."""
    global _cached, _cached_at

    static = _static_url()
    if static:
        return static

    metrics = _metrics_url()
    if not metrics:
        return None

    async with _lock:
        now = time.monotonic()
        if _cached and now - _cached_at < _CACHE_TTL_SEC:
            return _cached
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(metrics + _QUICK_TUNNEL_PATH) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            # 터널이 아직 안 올라왔거나 잠시 죽은 상태. 직전 값이 있으면 그걸 쓴다
            # (Rule 03: 여기서 예외를 올려 명령 전체를 죽이지 않는다).
            log.warning("cloudflared quick tunnel lookup failed: %s", metrics)
            return _cached

        hostname = str((payload or {}).get("hostname", "")).strip()
        if not hostname:
            return _cached
        _cached = f"https://{hostname}"
        _cached_at = now
        log.info("dashboard public url resolved: %s", _cached)
        return _cached


def reset_cache_for_tests() -> None:
    global _cached, _cached_at
    _cached = None
    _cached_at = 0.0
