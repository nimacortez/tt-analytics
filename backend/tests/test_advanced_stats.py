"""Tests for advanced stats: H2H, sweep, split functions."""
from datetime import datetime, timezone, timedelta

import pytest

from app.stats import (
    h2h_record, sweep_rate, split_rate,
    split_when_lost_set1, split_allowed_after_set1_win,
)
from app.models import League, Player, Match, MatchSet


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_setup(session):
    """Create one league and two players, return them."""
    lg = League(api_id="adv-lg", name="Advanced Test League")
    session.add(lg)
    session.flush()
    p1 = Player(api_id="adv-p1", name="Alice")
    p2 = Player(api_id="adv-p2", name="Bob")
    session.add_all([p1, p2])
    session.flush()
    return lg, p1, p2


def _make_match_with_sets(session, lg, home, away, scheduled_at, sets_data, api_id_suffix):
    """Create a Match + MatchSet rows. sets_data: list of (home_pts, away_pts)."""
    home_won = sum(1 for h, a in sets_data if h > a)
    away_won = sum(1 for h, a in sets_data if a > h)
    m = Match(
        api_id=f"adv-{api_id_suffix}",
        league_id=lg.id,
        home_player_id=home.id,
        away_player_id=away.id,
        scheduled_at=scheduled_at,
        status="finished",
        home_sets_won=home_won,
        away_sets_won=away_won,
    )
    session.add(m)
    session.flush()
    for i, (hp, ap) in enumerate(sets_data, 1):
        session.add(MatchSet(match_id=m.id, set_number=i, home_points=hp, away_points=ap))
    session.flush()
    return m


# ---------------------------------------------------------------------------
# test_h2h_record
# ---------------------------------------------------------------------------

def test_h2h_record(session):
    """Player A wins 2 of 3 H2H matches."""
    lg, p1, p2 = _make_setup(session)
    base = NOW - timedelta(days=10)

    # Match 1: p1 wins (home 3-1)
    _make_match_with_sets(session, lg, p1, p2, base,
                          [(11,7),(11,8),(5,11),(11,6)], "h2h-1")
    # Match 2: p2 wins (away 3-2)
    _make_match_with_sets(session, lg, p1, p2, base + timedelta(hours=2),
                          [(11,9),(5,11),(5,11),(11,8),(5,11)], "h2h-2")
    # Match 3: p1 wins (home 3-0)
    _make_match_with_sets(session, lg, p1, p2, base + timedelta(hours=4),
                          [(11,5),(11,6),(11,7)], "h2h-3")

    wins, total = h2h_record(session, p1.id, p2.id, 10, NOW)
    assert total == 3
    assert wins == 2


# ---------------------------------------------------------------------------
# test_sweep_rate
# ---------------------------------------------------------------------------

def test_sweep_rate(session):
    """1 of 2 matches was a 3-0 sweep."""
    lg, p1, p2 = _make_setup(session)
    base = NOW - timedelta(days=5)

    # Match 1: sweep (3-0)
    _make_match_with_sets(session, lg, p1, p2, base,
                          [(11,5),(11,6),(11,7)], "sweep-1")
    # Match 2: not a sweep (3-1)
    _make_match_with_sets(session, lg, p1, p2, base + timedelta(hours=2),
                          [(11,7),(11,8),(5,11),(11,6)], "sweep-2")

    sweeps, total = sweep_rate(session, p1.id, 10, NOW)
    assert total == 2
    assert sweeps == 1


# ---------------------------------------------------------------------------
# test_split_rate
# ---------------------------------------------------------------------------

def test_split_rate(session):
    """2 matches: one is split (1-1 after 2 sets), one is not."""
    lg, p1, p2 = _make_setup(session)
    base = NOW - timedelta(days=3)

    # Match 1: p1 wins sets 1 and 2 (no split) — home wins 3-0
    _make_match_with_sets(session, lg, p1, p2, base,
                          [(11,5),(11,6),(11,7)], "split-1")
    # Match 2: p1 wins set 1, p2 wins set 2 (split!) — ends 3-2
    _make_match_with_sets(session, lg, p1, p2, base + timedelta(hours=2),
                          [(11,7),(5,11),(11,8),(5,11),(11,9)], "split-2")

    splits, total = split_rate(session, p1.id, 10, NOW)
    assert total == 2
    assert splits == 1


# ---------------------------------------------------------------------------
# test_split_when_lost_set1
# ---------------------------------------------------------------------------

def test_split_when_lost_set1(session):
    """Two matches where p1 lost set 1; in one they won set 2."""
    lg, p1, p2 = _make_setup(session)
    base = NOW - timedelta(days=4)

    # Match 1: p1 loses set 1, wins set 2 → comeback to 1-1, then wins 3-2
    _make_match_with_sets(session, lg, p1, p2, base,
                          [(5,11),(11,8),(11,7),(5,11),(11,9)], "swls-1")
    # Match 2: p1 loses set 1, loses set 2 too → 0-2 down, loses 0-3
    _make_match_with_sets(session, lg, p2, p1, base + timedelta(hours=2),
                          [(11,5),(11,7),(11,6)], "swls-2")

    # p1 is away in match 2 (p2 is home), p1 lost sets 1 and 2
    wins, total = split_when_lost_set1(session, p1.id, 10, NOW)
    assert total == 2
    assert wins == 1


# ---------------------------------------------------------------------------
# test_split_allowed_after_set1_win
# ---------------------------------------------------------------------------

def test_split_allowed_after_set1_win(session):
    """Two matches where p1 won set 1; in one the opponent took set 2."""
    lg, p1, p2 = _make_setup(session)
    base = NOW - timedelta(days=6)

    # Match 1: p1 wins set 1, wins set 2 also (no split allowed) → sweep 3-0
    _make_match_with_sets(session, lg, p1, p2, base,
                          [(11,5),(11,6),(11,7)], "saasw-1")
    # Match 2: p1 wins set 1, loses set 2 (split allowed!) → ends 3-2 for p1
    _make_match_with_sets(session, lg, p1, p2, base + timedelta(hours=2),
                          [(11,8),(5,11),(11,9),(5,11),(11,7)], "saasw-2")

    losses, total = split_allowed_after_set1_win(session, p1.id, 10, NOW)
    assert total == 2
    assert losses == 1
