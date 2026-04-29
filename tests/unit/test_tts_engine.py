"""
Phase 4 — TTS Engine
edge-tts 호출은 mocked. 실제 네트워크 합성은 RUN_LIVE=1 + @pytest.mark.live.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_HAS = importlib.util.find_spec("cogs.tts_engine") is not None
pytestmark = pytest.mark.skipif(_HAS is False, reason="Phase 4 not yet implemented")


async def test_synthesize_returns_path(tmp_path):
    from cogs.tts_engine import synthesize

    fake_save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"\x00\x01"))
    with patch("edge_tts.Communicate") as Comm:
        Comm.return_value.save = fake_save
        result = await synthesize("안녕하세요")
        assert isinstance(result, Path)
        assert result.exists()
        assert result.stat().st_size > 0
    result.unlink(missing_ok=True)


async def test_synthesize_uses_korean_voice():
    from cogs.tts_engine import synthesize

    captured = {}
    fake_save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"x"))

    def factory(text, voice):
        captured["voice"] = voice
        m = type("C", (), {})()
        m.save = fake_save
        return m

    with patch("edge_tts.Communicate", side_effect=factory):
        path = await synthesize("ㅎㅇ", voice="ko-KR-InJoonNeural")
        assert captured["voice"] == "ko-KR-InJoonNeural"
        path.unlink(missing_ok=True)


async def test_synthesize_rejects_empty():
    from cogs.tts_engine import synthesize
    with pytest.raises((ValueError, AssertionError)):
        await synthesize("")


@pytest.mark.live
async def test_synthesize_live_smoke(tmp_path):
    """실제 edge-tts에 도달 — RUN_LIVE=1 일 때만."""
    from cogs.tts_engine import synthesize
    p = await synthesize("테스트")
    try:
        assert p.exists() and p.stat().st_size > 1024
    finally:
        p.unlink(missing_ok=True)
