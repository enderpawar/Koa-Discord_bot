from cogs.game_reactions import RecentPerformance, cute_recent_reaction, stat_int


def _game(outcome: str, k: int, d: int, a: int) -> RecentPerformance:
    return RecentPerformance(outcome=outcome, kills=k, deaths=d, assists=a)


def test_reaction_praises_dominant_win_streak():
    reaction = cute_recent_reaction(
        [_game("win", 12, 2, 8), _game("win", 9, 1, 11), _game("win", 7, 2, 9)]
    )
    assert reaction is not None
    assert "최고예요" in reaction
    assert "버스" in reaction


def test_reaction_uses_requested_uwuek_for_genuinely_bad_losses():
    reaction = cute_recent_reaction(
        [_game("loss", 1, 10, 2), _game("loss", 0, 8, 1), _game("loss", 2, 9, 1)]
    )
    assert reaction is not None
    assert reaction.startswith("우웩...")


def test_reaction_praises_good_personal_score_during_losses():
    reaction = cute_recent_reaction(
        [_game("loss", 14, 3, 8), _game("loss", 10, 2, 9), _game("win", 8, 2, 7)]
    )
    assert reaction is not None
    assert "MVP" in reaction
    assert "우웩" not in reaction


def test_reaction_handles_draws_and_unknown_results():
    reaction = cute_recent_reaction(
        [_game("draw", 8, 4, 7), _game("unknown", 5, 3, 6)]
    )
    assert reaction is not None


def test_reaction_returns_none_without_matches():
    assert cute_recent_reaction([]) is None


def test_stat_int_safely_normalizes_upstream_values():
    assert stat_int("7") == 7
    assert stat_int(-2) == 0
    assert stat_int(None) == 0
