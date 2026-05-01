"""Phase 4 — Azure Speech 한국어 음성 합성.

호출 측에서 사용 후 임시 오디오 파일 삭제 책임 (큐 worker 가 재생 후 unlink).
일시적 네트워크 오류는 1회 retry, 그 외 예외는 즉시 raise (Rule 03 의
"single-message failure must not stop the worker" 정책에 따라 worker 가 catch).

출력 포맷은 `raw-48khz-16bit-mono-pcm` — Discord 의 native sample rate (48kHz)
와 일치하므로 FFmpeg 의 mp3 디코드 단계를 우회할 수 있다. 벤치마크 결과
mp3 24kHz/48kbps 대비 TTFB 중앙값 약 25ms, TOTAL 중앙값 약 23ms 단축
(`bench_azure_formats.py` 참조).

기본 백엔드는 Azure WebSocket 이다. 연결을 pre-connect/reuse 해서 idle 이후
첫 합성에서 TCP/TLS/HTTP upgrade 비용이 반복되지 않도록 한다. 장애 비교용으로
`TTS_BACKEND=rest` 를 지정하면 기존 REST 경로를 사용할 수 있다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_VOICE = "ko-KR-SunHiNeural"
_OUTPUT_FORMAT = "raw-48khz-16bit-mono-pcm"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_WS_RECEIVE_TIMEOUT_SEC = 15.0
_TOKEN_TTL_SEC = 600
_TOKEN_REFRESH_MARGIN_SEC = 60
_BACKEND = os.getenv("TTS_BACKEND", "ws").lower()
_KEEPALIVE_INTERVAL_SEC = int(os.getenv("TTS_KEEPALIVE_INTERVAL_SEC", "15"))
_KEEPALIVE_CHECK_INTERVAL_SEC = 5
_KEEPALIVE_TEXT = os.getenv("TTS_KEEPALIVE_TEXT", ".")

_RETRYABLE: tuple[type[BaseException], ...] = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    RuntimeError,
)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()
_ws: aiohttp.ClientWebSocketResponse | None = None
_ws_lock = asyncio.Lock()
_speech_config_sent = False
_token: str | None = None
_token_expires_at = 0.0
_keepalive_task: asyncio.Task | None = None
_last_user_synthesis_at = 0.0
_last_keepalive_at = 0.0


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
    return _session


async def close_session() -> None:
    global _session, _ws, _speech_config_sent, _token, _token_expires_at, _keepalive_task
    if _keepalive_task is not None:
        _keepalive_task.cancel()
        try:
            await _keepalive_task
        except asyncio.CancelledError:
            pass
        _keepalive_task = None
    if _ws is not None and not _ws.closed:
        await _ws.close()
    _ws = None
    _speech_config_sent = False
    _token = None
    _token_expires_at = 0.0
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _make_temp_audio() -> Path:
    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".pcm", delete=False)
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


def _ws_endpoint(region: str) -> str:
    return f"wss://{region}.tts.speech.microsoft.com/cognitiveservices/websocket/v1"


def _token_endpoint(region: str) -> str:
    return f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"


def _require_azure_env() -> tuple[str, str]:
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise RuntimeError("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION 미설정 — .env 확인")
    return key, region


async def _get_token() -> str:
    global _token, _token_expires_at
    key, region = _require_azure_env()
    now = time.monotonic()
    if _token and now < _token_expires_at - _TOKEN_REFRESH_MARGIN_SEC:
        return _token

    session = await _get_session()
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Length": "0"}
    async with session.post(_token_endpoint(region), headers=headers) as resp:
        resp.raise_for_status()
        _token = await resp.text()
    _token_expires_at = now + _TOKEN_TTL_SEC
    return _token


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_ws_frame(path: str, content_type: str, body: str, request_id: str) -> str:
    return (
        f"Path:{path}\r\n"
        f"X-RequestId:{request_id}\r\n"
        f"X-Timestamp:{_now_iso()}\r\n"
        f"Content-Type:{content_type}\r\n"
        f"\r\n"
        f"{body}"
    )


def _parse_ws_text(data: str) -> dict[str, str]:
    head = data.split("\r\n\r\n", 1)[0]
    headers: dict[str, str] = {}
    for line in head.split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers


def _parse_ws_binary(data: bytes) -> tuple[dict[str, str], bytes]:
    if len(data) < 2:
        raise RuntimeError("invalid websocket audio frame: missing header length")
    header_len = int.from_bytes(data[:2], "big")
    if header_len > len(data) - 2:
        raise RuntimeError("invalid websocket audio frame: header too long")
    head = data[2 : 2 + header_len].decode("ascii", errors="replace")
    headers: dict[str, str] = {}
    for line in head.strip().split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers, data[2 + header_len :]


def _speech_config_body() -> str:
    return json.dumps(
        {
            "context": {
                "system": {
                    "name": "SpeechSDK",
                    "version": "1.0.0",
                    "build": "nothing-tts-bot",
                },
                "os": {"platform": "Python", "name": "asyncio"},
            }
        }
    )


def _synthesis_context_body() -> str:
    return json.dumps(
        {
            "synthesis": {
                "audio": {
                    "metadataOptions": {
                        "bookmarkEnabled": False,
                        "punctuationBoundaryEnabled": False,
                        "sentenceBoundaryEnabled": False,
                        "sessionEndEnabled": True,
                        "visemeEnabled": False,
                        "wordBoundaryEnabled": False,
                    },
                    "outputFormat": _OUTPUT_FORMAT,
                },
                "language": {"autoDetection": False},
            }
        }
    )


async def _close_ws() -> None:
    global _ws, _speech_config_sent
    if _ws is not None and not _ws.closed:
        await _ws.close()
    _ws = None
    _speech_config_sent = False


async def _ensure_ws() -> aiohttp.ClientWebSocketResponse:
    global _ws
    key, region = _require_azure_env()
    if _ws is not None and not _ws.closed:
        return _ws

    token = await _get_token()
    session = await _get_session()
    conn_id = uuid.uuid4().hex
    url = (
        f"{_ws_endpoint(region)}"
        f"?Authorization=bearer%20{token}"
        f"&X-ConnectionId={conn_id}"
    )
    _ws = await session.ws_connect(
        url,
        headers={"Ocp-Apim-Subscription-Key": key},
        heartbeat=30.0,
        autoping=True,
        max_msg_size=0,
    )
    log.info("azure tts websocket connected")
    return _ws


async def _request_rest_once(text: str, voice: str, path: Path) -> None:
    key, region = _require_azure_env()

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


async def _stream_rest_once(text: str, voice: str) -> AsyncIterator[bytes]:
    key, region = _require_azure_env()

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
        async for chunk in resp.content.iter_any():
            if chunk:
                yield chunk


async def _request_ws_once(text: str, voice: str, path: Path) -> None:
    global _speech_config_sent
    async with _ws_lock:
        ws = await _ensure_ws()
        request_id = uuid.uuid4().hex
        if not _speech_config_sent:
            await ws.send_str(
                _build_ws_frame(
                    "speech.config",
                    "application/json",
                    _speech_config_body(),
                    request_id,
                )
            )
            _speech_config_sent = True

        await ws.send_str(
            _build_ws_frame(
                "synthesis.context",
                "application/json",
                _synthesis_context_body(),
                request_id,
            )
        )
        await ws.send_str(
            _build_ws_frame(
                "ssml",
                "application/ssml+xml",
                _build_ssml(text, voice),
                request_id,
            )
        )

        audio_received = False
        with path.open("wb") as f:
            while True:
                msg = await ws.receive(timeout=_WS_RECEIVE_TIMEOUT_SEC)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    headers = _parse_ws_text(msg.data)
                    path_name = headers.get("Path", "")
                    if path_name == "turn.end":
                        if not audio_received:
                            raise RuntimeError("azure tts websocket returned no audio")
                        return
                    if path_name in {"turn.start", "response", "audio.metadata"}:
                        continue
                    raise RuntimeError(f"unexpected azure tts websocket text path: {path_name}")

                if msg.type == aiohttp.WSMsgType.BINARY:
                    headers, payload = _parse_ws_binary(msg.data)
                    if headers.get("Path") != "audio":
                        raise RuntimeError("unexpected azure tts websocket binary path")
                    if payload:
                        f.write(payload)
                        audio_received = True
                    continue

                if msg.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                }:
                    await _close_ws()
                    raise RuntimeError(f"azure tts websocket closed: {msg.type}")


async def _stream_ws_once(text: str, voice: str) -> AsyncIterator[bytes]:
    global _speech_config_sent
    async with _ws_lock:
        ws = await _ensure_ws()
        request_id = uuid.uuid4().hex
        if not _speech_config_sent:
            await ws.send_str(
                _build_ws_frame(
                    "speech.config",
                    "application/json",
                    _speech_config_body(),
                    request_id,
                )
            )
            _speech_config_sent = True

        await ws.send_str(
            _build_ws_frame(
                "synthesis.context",
                "application/json",
                _synthesis_context_body(),
                request_id,
            )
        )
        await ws.send_str(
            _build_ws_frame(
                "ssml",
                "application/ssml+xml",
                _build_ssml(text, voice),
                request_id,
            )
        )

        audio_received = False
        while True:
            msg = await ws.receive(timeout=_WS_RECEIVE_TIMEOUT_SEC)
            if msg.type == aiohttp.WSMsgType.TEXT:
                headers = _parse_ws_text(msg.data)
                path_name = headers.get("Path", "")
                if path_name == "turn.end":
                    if not audio_received:
                        raise RuntimeError("azure tts websocket returned no audio")
                    return
                if path_name in {"turn.start", "response", "audio.metadata"}:
                    continue
                raise RuntimeError(f"unexpected azure tts websocket text path: {path_name}")

            if msg.type == aiohttp.WSMsgType.BINARY:
                headers, payload = _parse_ws_binary(msg.data)
                if headers.get("Path") != "audio":
                    raise RuntimeError("unexpected azure tts websocket binary path")
                if payload:
                    audio_received = True
                    yield payload
                continue

            if msg.type in {
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            }:
                await _close_ws()
                raise RuntimeError(f"azure tts websocket closed: {msg.type}")


async def _request_once(text: str, voice: str, path: Path) -> None:
    if _BACKEND == "rest":
        await _request_rest_once(text, voice, path)
        return
    if _BACKEND != "ws":
        raise RuntimeError(f"unsupported TTS_BACKEND: {_BACKEND}")

    try:
        await _request_ws_once(text, voice, path)
    except _RETRYABLE:
        await _close_ws()
        raise


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Path:
    global _last_user_synthesis_at
    if not text:
        raise ValueError("empty text")

    _last_user_synthesis_at = time.monotonic()
    path = _make_temp_audio()
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


async def stream_synthesize(
    text: str, voice: str = DEFAULT_VOICE
) -> AsyncIterator[bytes]:
    """Yield raw 48kHz 16-bit mono PCM chunks as Azure returns them."""
    global _last_user_synthesis_at
    if not text:
        raise ValueError("empty text")

    _last_user_synthesis_at = time.monotonic()
    for attempt in (1, 2):
        yielded_audio = False
        try:
            if _BACKEND == "rest":
                async for chunk in _stream_rest_once(text, voice):
                    yielded_audio = True
                    yield chunk
                return
            if _BACKEND != "ws":
                raise RuntimeError(f"unsupported TTS_BACKEND: {_BACKEND}")

            try:
                async for chunk in _stream_ws_once(text, voice):
                    yielded_audio = True
                    yield chunk
                return
            except _RETRYABLE:
                await _close_ws()
                raise
        except _RETRYABLE as e:
            if yielded_audio or attempt == 2:
                raise
            log.warning("tts stream attempt %d failed (%s), retrying", attempt, e.__class__.__name__)


async def warm_up(voice: str = DEFAULT_VOICE) -> None:
    """Open the Azure/TLS path once and discard the generated audio."""
    path = await synthesize(_KEEPALIVE_TEXT, voice)
    path.unlink(missing_ok=True)


async def _warm_up_keepalive(voice: str = DEFAULT_VOICE) -> None:
    """Warm the Azure path without competing with user-triggered synthesis."""
    global _last_keepalive_at
    if _ws_lock.locked():
        return
    _last_keepalive_at = time.monotonic()
    path = _make_temp_audio()
    try:
        await _request_once(_KEEPALIVE_TEXT, voice, path)
    finally:
        path.unlink(missing_ok=True)


def start_keepalive(voice: str = DEFAULT_VOICE) -> None:
    """Keep the Azure TTS connection warm in the background."""
    global _keepalive_task
    if _KEEPALIVE_INTERVAL_SEC <= 0:
        return
    if _keepalive_task is not None and not _keepalive_task.done():
        return
    _keepalive_task = asyncio.create_task(
        _keepalive_loop(voice), name="tts-keepalive"
    )


async def _keepalive_loop(voice: str) -> None:
    while True:
        await asyncio.sleep(_KEEPALIVE_CHECK_INTERVAL_SEC)
        try:
            last_activity_at = max(_last_user_synthesis_at, _last_keepalive_at)
            if time.monotonic() - last_activity_at < _KEEPALIVE_INTERVAL_SEC:
                continue
            await _warm_up_keepalive(voice)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("tts keepalive failed", exc_info=True)
