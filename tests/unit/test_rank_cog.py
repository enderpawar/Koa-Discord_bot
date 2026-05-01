from __future__ import annotations

from cogs.rank_cog import _format_duration


def test_format_duration() -> None:
    assert _format_duration(4) == "4초"
    assert _format_duration(65) == "1분 5초"
    assert _format_duration(3660) == "1시간 1분"
