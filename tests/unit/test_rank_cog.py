from __future__ import annotations

from cogs.rank_cog import _format_duration, _format_score, _rank_icon


def test_format_duration() -> None:
    assert _format_duration(4) == "4초"
    assert _format_duration(65) == "1분 5초"
    assert _format_duration(3660) == "1시간 1분"


def test_format_score() -> None:
    assert _format_score(7000) == "70.00점"
    assert _format_score(10000) == "100.00점"


def test_rank_icon() -> None:
    assert _rank_icon(1) == "🥇"
    assert _rank_icon(2) == "🥈"
    assert _rank_icon(3) == "🥉"
    assert _rank_icon(4) == "`4`"
