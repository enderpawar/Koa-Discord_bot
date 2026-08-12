"""최근 경기 성과를 코아 말투의 짧은 평가로 바꾼다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["win", "loss", "draw", "unknown"]


@dataclass(frozen=True)
class RecentPerformance:
    outcome: Outcome
    kills: int = 0
    deaths: int = 0
    assists: int = 0


def stat_int(value: object) -> int:
    """외부 API의 숫자 필드를 음수가 아닌 정수로 정규화한다."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def cute_recent_reaction(games: list[RecentPerformance]) -> str | None:
    """승패와 합산 KDA로 결정적인 한 줄 평가를 만든다.

    낮은 점수여도 사용자를 모욕하지 않고, 연패 중 개인 지표가 좋으면 별도 칭찬한다.
    """
    if not games:
        return None

    decided = [game for game in games if game.outcome in {"win", "loss"}]
    wins = sum(game.outcome == "win" for game in decided)
    losses = sum(game.outcome == "loss" for game in decided)
    takedowns = sum(game.kills + game.assists for game in games)
    deaths = sum(game.deaths for game in games)
    kda = takedowns / max(1, deaths)
    average_deaths = deaths / len(games)

    if wins >= 2 and losses == 0 and kda >= 4.0:
        return (
            "오! 최고예요!! 오늘 완전 캐리 머신인데요? "
            "코아도 버스 태워 주세요! ✨"
        )
    if wins >= 2 and losses == 0:
        return "연승 냄새가 솔솔 나요! 코아가 응원봉부터 챙길게요! 🎉"
    if decided and wins / len(decided) >= 2 / 3 and kda >= 2.5:
        return "우와, 완전 잘했어요! 오늘 손에 꿀 발랐나요? 🍯"
    if losses >= 2 and losses > wins and kda >= 3.5:
        return (
            "졌는데 혼자 왜 이렇게 잘했어요? "
            "코아가 몰래 MVP 도장 찍어 줄게요! 🏅"
        )
    if losses >= 2 and wins == 0 and kda < 1.0:
        return (
            "우웩... 오늘은 스킬보다 회색 화면을 더 많이 봤어요. "
            "물 한 잔 마시고 다시 가요! 🤢"
        )
    if average_deaths >= 8 and kda < 1.5:
        return (
            "앗... 회색 화면이랑 너무 친해졌어요! "
            "오늘만큼은 잠깐 거리 두기 해요! 🫣"
        )
    if losses >= 2 and wins == 0:
        return (
            "으앙... 오늘 게임이 너무 매워요. "
            "다음 판엔 코아가 응원 버프 줄게요! 🌶️"
        )
    if kda >= 3.0:
        return "오호, 폼이 꽤 좋은데요? 다음 판엔 진짜 캐리 각이에요! 😎"
    if wins > losses:
        return "좋아요~ 승리 저금통이 차곡차곡 쌓이고 있어요! 🐷"
    if kda >= 1.5:
        return "음~ 슬슬 손이 풀리고 있어요! 다음 판엔 더 잘할 수 있겠죠? 👀"
    return "흐음... 아직 예열 중인가 봐요. 다음 판은 코아 믿고 가는 거예요! 🔥"
