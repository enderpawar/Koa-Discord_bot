"""Phase 4 — Azure Speech REST 한국어 음성 합성.

호출 측에서 사용 후 임시 mp3 파일 삭제 책임 (큐 worker 가 재생 후 unlink).
일시적 네트워크 오류는 1회 retry, 그 외 예외는 즉시 raise (Rule 03 의
"single-message failure must not stop the worker" 정책에 따라 worker 가 catch).

연속 합성 시 latency 를 줄이기 위해 module-level aiohttp.ClientSession 을
재사용한다 (HTTP keep-alive). 봇 종료 시 close_session() 호출 권장.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_VOICE = "ko-KR-SunHiNeural"
_OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

_RETRYABLE: tuple[type[BaseException], ...] = (aiohttp.ClientError, asyncio.TimeoutError)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


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


def _make_temp_mp3() -> Path:
    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".mp3", delete=False)
    fd.close()
    return Path(fd.name)


def _build_ssml(text: str, voice: str) -> str:
    return (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{voice}">{escape(text)}</voice>'
        f'</speak>'
    )


def _endpoint(region: str) -> str:
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"


async def _request_once(text: str, voice: str, path: Path) -> None:
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise RuntimeError("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION 미설정 — .env 확인")

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
        "User-Agent": "nothing-tts-bot",
    }
    body = _build_ssml(text, voice).encode("utf-8")
    session = await _get_session()
    async with session.post(_endpoint(region), data=body, headers=headers) as resp:
        resp.raise_for_status()
        with path.open("wb") as f:
            async for chunk in resp.content.iter_any():
                f.write(chunk)


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Path:
    if not text:
        raise ValueError("empty text")

    path = _make_temp_mp3()
    try:
        for attempt in (1, 2):
            try:
                await _request_once(text, voice, path)
                return path
            except _RETRYABLE as e:
                if attempt == 2:
                    raise
                log.warning("tts attempt %d failed (%s), retrying", attempt, e.__class__.__name__)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return path
