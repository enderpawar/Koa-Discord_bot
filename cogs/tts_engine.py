"""Phase 4 — edge-tts 한국어 음성 합성.

호출 측에서 사용 후 임시 mp3 파일 삭제 책임 (큐 worker 가 재생 후 unlink).
일시적 네트워크 오류는 1회 retry, 그 외 예외는 즉시 raise (Rule 03 의
"single-message failure must not stop the worker" 정책에 따라 worker 가 catch).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import aiohttp
import edge_tts

log = logging.getLogger(__name__)

DEFAULT_VOICE = "ko-KR-SunHiNeural"

# 일시 장애로 분류되어 1회 재시도되는 예외들. 그 외는 즉시 raise.
try:
    from edge_tts.exceptions import NoAudioReceived

    _RETRYABLE: tuple[type[BaseException], ...] = (aiohttp.ClientError, NoAudioReceived)
except (ImportError, AttributeError):
    _RETRYABLE = (aiohttp.ClientError,)


def _make_temp_mp3() -> Path:
    fd = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".mp3", delete=False)
    fd.close()
    return Path(fd.name)


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Path:
    if not text:
        raise ValueError("empty text")

    path = _make_temp_mp3()
    try:
        for attempt in (1, 2):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(path))
                return path
            except _RETRYABLE as e:
                if attempt == 2:
                    raise
                log.warning("tts attempt %d failed (%s), retrying", attempt, e.__class__.__name__)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    # 도달 불가능 (loop 내에서 return 또는 raise)
    return path
