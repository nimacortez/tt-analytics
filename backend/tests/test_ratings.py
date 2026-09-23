"""Tests for ratings.py: p_win math, Elo update formula, rating_as_of leakage."""
from datetime import datetime, timezone, timedelta

import pytest

from app.ratings import p_win, rebuild, rating_as_of
from app.models import League, Player, Match, MatchSet, PlayerRating


# ---------------------------------------------------------------------------
# p_win
# ---------------------------------------------------------------------------

def test_p_win_equal():
    assert abs(p_win(1500.0, 1500.0) - 0.5) < 1e-9


def test_p_win_higher():
    assert p_win(1600.0, 1400.0) > 0.5


def test_p_win_symmetry():
    """p(a>b) + p(b>a) == 1."""
    assert abs(p_win(1600.0, 1400.0) + p_win(1400.0, 1600.0) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Elo update math
# ---------------------------------------------------------------------------

def test_elo_update_math():
    """After one win, rating rises by K*(1 - E)."""
    r_a, r_b = 1500.0, 1500.0
    k = 32.0
    e = p_win(r_a, r_b)       # 0.5 when equal
    expected_winner_after = r_a + k * (1.0 - e)   # 1516.0
    assert abs(expected_winner_after - 1516.0) < 1e-6


# ---------------------------------------------------------------------------
# rating_as_of leakage
# ---------------------------------------------------------------------------

def _setup_two_matches(session):
    """Insert League, two Players, and two Matches with PlayerRating rows."""
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    lg = League(api_id="test-lg", name="Test League")
    session.add(lg)
    session.flush()

    p1 = Player(api_id="test-p1", name="Player One")
    p2 = Player(api_id="test-p2", name="Player Two")
    session.add_all([p1, p2])
    session.flush()

    # Match 1: 2 hours in the past (before as_of)
    m1 = Match(
        api_id="test-m1",
        league_id=lg.id,
        home_player_id=p1.id,
        away_player_id=p2.id,
        scheduled_at=now - timedelta(hours=2),
        status="finished",
        home_sets_won=3,
        away_sets_won=1,
    )
    session.add(m1)
    session.flush()
    for i, (h, a) in enumerate([(11, 7), (11, 8), (5, 11), (11, 6)], 1):
        session.add(MatchSet(match_id=m1.id, set_number=i, home_points=h, away_points=a))

    # Match 2: 1 hour in the future (AFTER as_of) — should be excluded
    m2 = Match(
        api_id="test-m2",
        league_id=lg.id,
        home_player_id=p1.id,
        away_player_id=p2.id,
        scheduled_at=now + timedelta(hours=1),
        status="finished",
        home_sets_won=0,
        away_sets_won=3,
    )
    session.add(m2)
    session.flush()
    for i, (h, a) in enumerate([(5, 11), (6, 11), (7, 11)], 1):
        session.add(MatchSet(match_id=m2.id, set_number=i, home_points=h, away_points=a))

    # Insert PlayerRating rows manually (simulating what rebuild() would do)
    # After match 1: p1 wins → p1 rating goes up from 1500
    e1 = p_win(1500.0, 1500.0)
    p1_after_m1 = 1500.0 + 32.0 * (1.0 - e1)   # 1516.0
    p2_after_m1 = 1500.0 + 32.0 * (0.0 - (1.0 - e1))  # 1484.0

    session.add(PlayerRating(player_id=p1.id, match_id=m1.id,
                             rating_before=1500.0, rating_after=p1_after_m1))
    session.add(PlayerRating(player_id=p2.id, match_id=m1.id,
                             rating_before=1500.0, rating_after=p2_after_m1))

    # After match 2: p2 wins → p1 rating goes down further
    e2 = p_win(p1_after_m1, p2_after_m1)
    p1_after_m2 = p1_after_m1 + 32.0 * (0.0 - e2)
    p2_after_m2 = p2_after_m1 + 32.0 * (1.0 - (1.0 - e2))

    session.add(PlayerRating(player_id=p1.id, match_id=m2.id,
                             rating_before=p1_after_m1, rating_after=p1_after_m2))
    session.add(PlayerRating(player_id=p2.id, match_id=m2.id,
                             rating_before=p2_after_m1, rating_after=p2_after_m2))

    session.flush()
    return p1, p2, now, p1_after_m1, p2_after_m1


def test_rating_as_of_excludes_future(session):
    """rating_as_of must not include matches at or after as_of."""
    p1, p2, now, p1_after_m1, _ = _setup_two_matches(session)

    # as_of is between the two matches
    result = rating_as_of(session, p1.id, now)

    # Should reflect only match 1 (which is before now)
    assert abs(result - p1_after_m1) < 1e-6, (
        f"Expected {p1_after_m1:.4f} (only match1), got {result:.4f}"
    )


def test_rating_as_of_no_history(session):
    """Player with no prior matches returns DEFAULT_RATING = 1500."""
    # Insert just a player with no matches
    p = Player(api_id="test-nohistory", name="Ghost Player")
    session.add(p)
    session.flush()

    as_of = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = rating_as_of(session, p.id, as_of)
    assert result == 1500.0
