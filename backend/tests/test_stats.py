"""Tests for stats.py: wilson_interval, shrunk_rate, over_hit_rate leakage."""
from datetime import datetime, timezone, timedelta

import pytest

from app.stats import wilson_interval, shrunk_rate, over_hit_rate
from app.models import League, Player, Match, MatchSet


# ---------------------------------------------------------------------------
# wilson_interval
# ---------------------------------------------------------------------------

def test_wilson_zero_n():
    low, high = wilson_interval(0, 0)
    assert low == 0.0
    assert high == 1.0


def test_wilson_all_hits():
    low, high = wilson_interval(10, 10)
    assert low > 0.7
    assert high == 1.0


def test_wilson_half():
    low, high = wilson_interval(50, 100)
    assert low < 0.5 < high
    assert (high - low) < 0.2


# ---------------------------------------------------------------------------
# shrunk_rate
# ---------------------------------------------------------------------------

def test_shrunk_rate_no_data():
    """n=0: result should just be the baseline (fully prior-weighted)."""
    baseline = 0.55
    result = shrunk_rate(0, 0, baseline, prior_strength=10)
    assert abs(result - baseline) < 1e-9


def test_shrunk_rate_large_n():
    """n=1000: result should be very close to the raw rate."""
    hits = 600
    n = 1000
    raw = hits / n
    result = shrunk_rate(hits, n, baseline=0.5, prior_strength=10)
    assert abs(result - raw) < 0.01


# ---------------------------------------------------------------------------
# Leakage test
# ---------------------------------------------------------------------------

def _make_league(session) -> League:
    lg = League(api_id="test-league", name="Test League")
    session.add(lg)
    session.flush()
    return lg


def _make_player(session, api_id: str) -> Player:
    p = Player(api_id=api_id, name=f"Player {api_id}")
    session.add(p)
    session.flush()
    return p


def _make_match(session, league, home, away, scheduled_at, status="finished",
                sets_data=None):
    """Helper: create a Match + MatchSet rows."""
    if sets_data is None:
        sets_data = [(11, 7), (11, 8), (11, 9)]  # 3-0 sweep, total 57 points
    home_won = sum(1 for h, a in sets_data if h > a)
    away_won = sum(1 for h, a in sets_data if a > h)
    m = Match(
        api_id=f"test-{scheduled_at.isoformat()}",
        league_id=league.id,
        home_player_id=home.id,
        away_player_id=away.id,
        scheduled_at=scheduled_at,
        status=status,
        home_sets_won=home_won,
        away_sets_won=away_won,
    )
    session.add(m)
    session.flush()
    for i, (hp, ap) in enumerate(sets_data, 1):
        session.add(MatchSet(match_id=m.id, set_number=i, home_points=hp, away_points=ap))
    session.flush()
    return m


def test_leakage(session):
    """over_hit_rate must only count matches scheduled BEFORE as_of."""
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    lg = _make_league(session)
    home = _make_player(session, "p1")
    away = _make_player(session, "p2")

    # 2 past matches (total ~57 pts each, well under line 200 so hits=0, but n=2)
    past1 = now - timedelta(hours=3)
    past2 = now - timedelta(hours=1)
    _make_match(session, lg, home, away, past1)
    _make_match(session, lg, home, away, past2)

    # 1 future match — should be excluded
    future = now + timedelta(hours=1)
    _make_match(session, lg, home, away, future)

    # Run with a generous line (all matches "miss") to focus on sample size
    hits, n = over_hit_rate(session, home.id, line=200.0, n=100, as_of=now)

    # Only the 2 past matches should be counted
    assert n == 2, f"Expected n=2 (past matches only), got n={n}"
    assert hits == 0
