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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import aiohttp

from cogs.preprocess import TONE_CHEERFUL, TONE_EXCITED, TONE_SAD

log = logging.getLogger(__name__)

# `/목소리` 의 "여성 · 부드러움". 서버가 따로 고르기 전까지 이 목소리로 읽는다.
DEFAULT_VOICE = "ko-KR-SeoHyeonNeural"
_OUTPUT_FORMAT = "raw-48khz-16bit-mono-pcm"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_WS_RECEIVE_TIMEOUT_SEC = 15.0
_TOKEN_TTL_SEC = 600
_TOKEN_REFRESH_MARGIN_SEC = 60
_BACKEND = os.getenv("TTS_BACKEND", "ws").lower()
_KEEPALIVE_INTERVAL_SEC = float(os.getenv("TTS_KEEPALIVE_INTERVAL_SEC", "8"))
_KEEPALIVE_CHECK_INTERVAL_SEC = float(
    os.getenv("TTS_KEEPALIVE_CHECK_INTERVAL_SEC", "1")
)
_KEEPALIVE_TEXT = os.getenv("TTS_KEEPALIVE_TEXT", "\uac00")
_WS_POOL_SIZE = max(1, int(os.getenv("TTS_WS_POOL_SIZE", "2")))
_WS_POOL_WARM_ALL = os.getenv("TTS_WS_POOL_WARM_ALL", "1") == "1"
_WS_HEARTBEAT_SEC = float(os.getenv("TTS_WS_HEARTBEAT_SEC", "5"))

_RETRYABLE: tuple[type[BaseException], ...] = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    RuntimeError,
)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()
_token: str | None = None
_token_expires_at = 0.0
_keepalive_task: asyncio.Task | None = None
_last_user_synthesis_at = 0.0
_last_keepalive_at = 0.0


@dataclass
class _WSSlot:
    """단일 Azure TTS WebSocket 연결 슬롯.

    각 슬롯은 자체 lock 을 가지므로 풀 크기만큼의 합성이 동시에 진행될 수 있다.
    """
    ws: aiohttp.ClientWebSocketResponse | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    speech_config_sent: bool = False
    index: int = 0
    last_activity_at: float = 0.0


_pool: list[_WSSlot] = []
_pool_init_lock = asyncio.Lock()


async def _get_pool() -> list[_WSSlot]:
    global _pool
    if not _pool:
        async with _pool_init_lock:
            if not _pool:
                _pool = [_WSSlot(index=index) for index in range(_WS_POOL_SIZE)]
    return _pool


@asynccontextmanager
async def _acquire_slot() -> AsyncIterator[_WSSlot]:
    """첫 번째 비어 있는 슬롯을 즉시 잡고, 모두 busy 면 첫 슬롯의 락을 기다림."""
    pool = await _get_pool()
    for slot in pool:
        if not slot.lock.locked():
            await slot.lock.acquire()
            try:
                yield slot
            finally:
                slot.lock.release()
            return
    slot = pool[0]
    await slot.lock.acquire()
    try:
        yield slot
    finally:
        slot.lock.release()


@asynccontextmanager
async def _acquire_specific_slot(slot: _WSSlot) -> AsyncIterator[_WSSlot]:
    await slot.lock.acquire()
    try:
        yield slot
    finally:
        slot.lock.release()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
    return _session


async def close_session() -> None:
    global _session, _pool, _token, _token_expires_at, _keepalive_task, _voice_styles
    global _style_load_task, _style_retry_after
    if _keepalive_task is not None:
        _keepalive_task.cancel()
        try:
            await _keepalive_task
        except asyncio.CancelledError:
            pass
        _keepalive_task = None
    for slot in _pool:
        if slot.ws is not None and not slot.ws.closed:
            await slot.ws.close()
        slot.ws = None
        slot.speech_config_sent = False
    _pool = []
    _token = None
    _token_expires_at = 0.0
    _voice_styles = None
    _style_load_task = None
    _style_retry_after = 0.0
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _make_temp_audio() -> Path:
    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".pcm", delete=False)
    fd.close()
    return Path(fd.name)


def _build_ssml(text: str, voice: str, style: str | None = None) -> str:
    if style:
        # mstts 네임스페이스는 스타일을 실제로 쓸 때만 붙인다. 스타일 없는
        # SSML 을 건드리지 않아야, 카탈로그를 못 받았을 때 기존과 완전히 같은
        # 요청이 나간다.
        return (
            '<speak version="1.0" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" '
            'xml:lang="ko-KR">'
            f'<voice name="{voice}">'
            f'<mstts:express-as style={quoteattr(style)}>'
            f'{escape(text)}'
            '</mstts:express-as>'
            '</voice></speak>'
        )
    return (
        f'<speak version="1.0" xml:lang="ko-KR">'
        f'<voice name="{voice}">{escape(text)}</voice>'
        f'</speak>'
    )


# ---------- 감정 스타일 카탈로그 ----------

# 어떤 보이스가 어떤 `<mstts:express-as>` 스타일을 지원하는지는 보이스마다
# 다르고 Azure 가 수시로 바꾼다. 표를 코드에 박아 두면 지원하지 않는 스타일을
# 조용히 계속 보내게 되므로, 기동 시 voices/list 에서 받아 캐시한다.
# 못 받으면 빈 표로 두어 스타일 없이(= 기존 동작) 읽는다.
_voice_styles: dict[str, frozenset[str]] | None = None
_style_load_task: "asyncio.Task | None" = None
# 실패를 영구 캐시하면 부팅 중 네트워크가 한 번 흔들린 것만으로 재시작 전까지
# 감정이 죽는다. 실패는 캐시하지 않고 이 시각 이후에 다시 시도한다.
_style_retry_after = 0.0
_STYLE_RETRY_COOLDOWN_SEC = 300.0

# 감정 라벨 → 시도할 Azure 스타일 순서. 앞의 것을 그 보이스가 지원하지 않으면
# 뒤로 내려가고, 끝까지 없으면 스타일을 붙이지 않는다.
#
# 보이스 계열마다 스타일 어휘가 다르다. 예전 뉴럴 보이스는 `cheerful`/`friendly`
# 를 쓰지만 MAI-Voice-2 계열(ko-KR-Haena, ko-KR-Junho)은 `happy`/`joyful` 을
# 쓴다. 한쪽 어휘만 넣어 두면 감정을 지원하는 보이스인데도 매칭이 안 돼
# 조용히 평소 톤으로 읽힌다 — 실제로 그렇게 놓쳤다.
_TONE_STYLE_CHAIN: dict[str, tuple[str, ...]] = {
    TONE_CHEERFUL: ("cheerful", "happy", "joyful", "friendly"),
    TONE_SAD: ("sad", "regretful", "gentle"),
    TONE_EXCITED: ("excited", "surprised", "happy", "cheerful"),
}


def _voices_endpoint(region: str) -> str:
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"


async def load_voice_styles(*, force: bool = False) -> dict[str, frozenset[str]]:
    """보이스별 지원 스타일 목록을 Azure 에서 받아 캐시한다.

    실패하면 빈 표를 **돌려주되 캐시하지는 않는다** — 호출자 입장에서 지금 쓸 수
    있는 스타일이 없다는 뜻이고, 쿨다운이 지나면 다시 시도한다.
    """
    global _voice_styles, _style_retry_after
    if _voice_styles is not None and not force:
        return _voice_styles
    try:
        key, region = _require_azure_env()
        session = await _get_session()
        async with session.get(
            _voices_endpoint(region),
            headers={"Ocp-Apim-Subscription-Key": key},
        ) as resp:
            resp.raise_for_status()
            catalog = await resp.json()
    except Exception:
        # 감정은 부가 기능이다. 카탈로그를 못 받아도 읽기 자체는 멀쩡해야 한다 (Rule 03).
        _style_retry_after = time.monotonic() + _STYLE_RETRY_COOLDOWN_SEC
        log.warning(
            "voice style catalog unavailable; reading without emotion styles "
            "(retrying in %ds)",
            int(_STYLE_RETRY_COOLDOWN_SEC),
        )
        log.debug("voice style catalog fetch failed", exc_info=True)
        return {}

    styles: dict[str, frozenset[str]] = {}
    for entry in catalog if isinstance(catalog, list) else []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("ShortName")
        if not name:
            continue
        raw = entry.get("StyleList") or []
        styles[str(name)] = frozenset(str(item) for item in raw if item)
    _voice_styles = styles
    log.info(
        "voice style catalog loaded: voices=%d with_styles=%d",
        len(styles),
        sum(1 for entry in styles.values() if entry),
    )
    return styles


def ensure_voice_styles_loading() -> None:
    """카탈로그가 없으면 백그라운드로 받아온다. 합성을 막지 않는다.

    기동 시 한 번만 읽던 구조에서는 그 한 번이 실패하거나 태스크가 거기까지
    가지 못하면 재시작 전까지 감정이 조용히 죽어 있었다. 합성 경로에서 매번
    확인해 스스로 복구한다 — 이미 읽었으면 전역 하나 비교로 끝난다.
    """
    global _style_load_task
    if _voice_styles is not None:
        return
    if time.monotonic() < _style_retry_after:
        return
    if _style_load_task is not None and not _style_load_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _style_load_task = loop.create_task(load_voice_styles(), name="tts-voice-styles")


def voice_style_status(voice: str) -> tuple[str, tuple[str, ...]]:
    """`/상태` 진단용. (상태, 지원 스타일).

    상태: `unloaded` 카탈로그 미확인 / `unknown_voice` 목록에 없음 /
    `unsupported` 감정 미지원 / `ready` 사용 가능.
    """
    if _voice_styles is None:
        return "unloaded", ()
    supported = _voice_styles.get(voice)
    if supported is None:
        return "unknown_voice", ()
    if not supported:
        return "unsupported", ()
    return "ready", tuple(sorted(supported))


def style_for(voice: str, tone: str | None) -> str | None:
    """그 보이스가 실제로 지원하는 스타일만 돌려준다. 없으면 None.

    카탈로그를 아직 못 읽었거나 비어 있으면 항상 None 이라, 감정 기능이 통째로
    꺼진 것처럼 동작한다.
    """
    if not tone or not _voice_styles:
        return None
    supported = _voice_styles.get(voice)
    if not supported:
        return None
    for candidate in _TONE_STYLE_CHAIN.get(tone, (tone,)):
        if candidate in supported:
            return candidate
    return None


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
                    "build": "koa-tts-bot",
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


async def _close_slot(slot: _WSSlot) -> None:
    if slot.ws is not None and not slot.ws.closed:
        await slot.ws.close()
    slot.ws = None
    slot.speech_config_sent = False
    slot.last_activity_at = 0.0


async def _ensure_slot_ws(slot: _WSSlot) -> aiohttp.ClientWebSocketResponse:
    key, region = _require_azure_env()
    if slot.ws is not None and not slot.ws.closed:
        return slot.ws

    token = await _get_token()
    session = await _get_session()
    conn_id = uuid.uuid4().hex
    url = (
        f"{_ws_endpoint(region)}"
        f"?Authorization=bearer%20{token}"
        f"&X-ConnectionId={conn_id}"
    )
    slot.ws = await session.ws_connect(
        url,
        headers={"Ocp-Apim-Subscription-Key": key},
        heartbeat=_WS_HEARTBEAT_SEC if _WS_HEARTBEAT_SEC > 0 else None,
        autoping=True,
        max_msg_size=0,
    )
    slot.speech_config_sent = False
    log.info("azure tts websocket connected: slot=%d", slot.index)
    return slot.ws


async def _request_rest_once(
    text: str, voice: str, path: Path, *, style: str | None = None
) -> None:
    key, region = _require_azure_env()

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
        "User-Agent": "koa-tts-bot",
    }
    body = _build_ssml(text, voice, style).encode("utf-8")
    session = await _get_session()
    async with session.post(_endpoint(region), data=body, headers=headers) as resp:
        resp.raise_for_status()
        with path.open("wb") as f:
            async for chunk in resp.content.iter_any():
                f.write(chunk)


async def _stream_rest_once(
    text: str, voice: str, *, style: str | None = None
) -> AsyncIterator[bytes]:
    key, region = _require_azure_env()

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
        "User-Agent": "koa-tts-bot",
    }
    body = _build_ssml(text, voice, style).encode("utf-8")
    session = await _get_session()
    async with session.post(_endpoint(region), data=body, headers=headers) as resp:
        resp.raise_for_status()
        async for chunk in resp.content.iter_any():
            if chunk:
                yield chunk


async def _request_ws_once(
    text: str,
    voice: str,
    path: Path,
    *,
    style: str | None = None,
    preferred_slot: _WSSlot | None = None,
) -> None:
    slot_context = (
        _acquire_specific_slot(preferred_slot)
        if preferred_slot is not None
        else _acquire_slot()
    )
    async with slot_context as slot:
        try:
            ws = await _ensure_slot_ws(slot)
            request_id = uuid.uuid4().hex
            if not slot.speech_config_sent:
                await ws.send_str(
                    _build_ws_frame(
                        "speech.config",
                        "application/json",
                        _speech_config_body(),
                        request_id,
                    )
                )
                slot.speech_config_sent = True

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
                    _build_ssml(text, voice, style),
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
                            slot.last_activity_at = time.monotonic()
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
                        raise RuntimeError(f"azure tts websocket closed: {msg.type}")
        except asyncio.CancelledError:
            await _close_slot(slot)
            raise
        except _RETRYABLE:
            await _close_slot(slot)
            raise


async def _stream_ws_once(
    text: str, voice: str, *, style: str | None = None
) -> AsyncIterator[bytes]:
    async with _acquire_slot() as slot:
        turn_completed = False
        try:
            ws = await _ensure_slot_ws(slot)
            request_id = uuid.uuid4().hex
            if not slot.speech_config_sent:
                await ws.send_str(
                    _build_ws_frame(
                        "speech.config",
                        "application/json",
                        _speech_config_body(),
                        request_id,
                    )
                )
                slot.speech_config_sent = True

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
                    _build_ssml(text, voice, style),
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
                        slot.last_activity_at = time.monotonic()
                        turn_completed = True
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
                    raise RuntimeError(f"azure tts websocket closed: {msg.type}")
        except asyncio.CancelledError:
            await _close_slot(slot)
            raise
        except _RETRYABLE:
            await _close_slot(slot)
            raise
        finally:
            # Closing an async generator injects GeneratorExit, not
            # CancelledError.  Never return a socket with unread frames from
            # the abandoned turn to the reusable pool.
            if not turn_completed:
                await _close_slot(slot)


async def _request_once(
    text: str, voice: str, path: Path, *, style: str | None = None
) -> None:
    if _BACKEND == "rest":
        await _request_rest_once(text, voice, path, style=style)
        return
    if _BACKEND != "ws":
        raise RuntimeError(f"unsupported TTS_BACKEND: {_BACKEND}")

    # _request_ws_once 가 슬롯 단위로 자체 정리한다.
    await _request_ws_once(text, voice, path, style=style)


async def synthesize(
    text: str, voice: str = DEFAULT_VOICE, *, tone: str | None = None
) -> Path:
    global _last_user_synthesis_at
    if not text:
        raise ValueError("empty text")

    ensure_voice_styles_loading()
    style = style_for(voice, tone)
    _last_user_synthesis_at = time.monotonic()
    path = _make_temp_audio()
    try:
        for attempt in (1, 2):
            try:
                await _request_once(text, voice, path, style=style)
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
    text: str, voice: str = DEFAULT_VOICE, *, tone: str | None = None
) -> AsyncIterator[bytes]:
    """Yield raw 48kHz 16-bit mono PCM chunks as Azure returns them."""
    global _last_user_synthesis_at
    if not text:
        raise ValueError("empty text")

    ensure_voice_styles_loading()
    style = style_for(voice, tone)
    _last_user_synthesis_at = time.monotonic()
    for attempt in (1, 2):
        yielded_audio = False
        try:
            if _BACKEND == "rest":
                async for chunk in _stream_rest_once(text, voice, style=style):
                    yielded_audio = True
                    yield chunk
                return
            if _BACKEND != "ws":
                raise RuntimeError(f"unsupported TTS_BACKEND: {_BACKEND}")

            # _stream_ws_once 가 슬롯 단위로 자체 정리한다.
            async for chunk in _stream_ws_once(text, voice, style=style):
                yielded_audio = True
                yield chunk
            return
        except _RETRYABLE as e:
            if yielded_audio or attempt == 2:
                raise
            log.warning("tts stream attempt %d failed (%s), retrying", attempt, e.__class__.__name__)


async def warm_up(voice: str = DEFAULT_VOICE) -> None:
    """Open the Azure/TLS path once and discard the generated audio.

    `TTS_WS_POOL_WARM_ALL=1` 이면 풀의 모든 슬롯을 동시에 워밍한다 (병렬 합성으로
    각 슬롯이 자기 ws 를 잡음).
    """
    if _BACKEND == "ws" and _WS_POOL_WARM_ALL and _WS_POOL_SIZE > 1:
        await asyncio.gather(
            *(_warm_one(voice) for _ in range(_WS_POOL_SIZE)),
            return_exceptions=True,
        )
        return
    await _warm_one(voice)


async def _warm_one(voice: str) -> None:
    path = await synthesize(_KEEPALIVE_TEXT, voice)
    path.unlink(missing_ok=True)


async def _warm_up_keepalive(
    voice: str = DEFAULT_VOICE,
    *,
    slot: _WSSlot | None = None,
) -> bool:
    """Warm the Azure path without competing with user-triggered synthesis.

    A slot is only marked warm after a successful synthesis.  A failed probe
    therefore remains immediately eligible for the next keepalive cycle.
    """
    global _last_keepalive_at
    if slot is not None and slot.lock.locked():
        return False
    if slot is None:
        pool = await _get_pool()
        if all(candidate.lock.locked() for candidate in pool):
            return False

    path = _make_temp_audio()
    try:
        if _BACKEND == "ws":
            await _request_ws_once(
                _KEEPALIVE_TEXT,
                voice,
                path,
                preferred_slot=slot,
            )
        else:
            await _request_once(_KEEPALIVE_TEXT, voice, path)
        _last_keepalive_at = time.monotonic()
        return True
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
            await _run_keepalive_cycle(voice)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("tts keepalive failed", exc_info=True)


async def _run_keepalive_cycle(voice: str = DEFAULT_VOICE) -> None:
    if _BACKEND != "ws":
        last_activity_at = max(_last_user_synthesis_at, _last_keepalive_at)
        if time.monotonic() - last_activity_at >= _KEEPALIVE_INTERVAL_SEC:
            await _warm_up_keepalive(voice)
        return

    # Warm slots sequentially.  With a pool larger than one this keeps another
    # slot available for a user request throughout the cycle.
    pool = await _get_pool()
    for slot in pool:
        if slot.lock.locked():
            continue
        if (
            slot.last_activity_at > 0
            and time.monotonic() - slot.last_activity_at
            < _KEEPALIVE_INTERVAL_SEC
        ):
            continue
        try:
            await _warm_up_keepalive(voice, slot=slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug(
                "tts keepalive failed: slot=%d",
                slot.index,
                exc_info=True,
            )
