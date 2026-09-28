"""Tests for stats.py core helpers and over/under: wilson, shrinkage, as_of leakage."""
from datetime import timedelta

import pytest

from app.stats import league_over_rate, over_hit_rate, shrunk_rate, wilson_interval
from tests.helpers import NOW, make_league, make_match, make_player

SWEEP_57 = [(11, 7), (11, 8), (11, 9)]                      # total 57
LONG_101 = [(11, 9), (9, 11), (12, 10), (10, 12), (11, 6)]    # total 101


# ---------------------------------------------------------------------------
# wilson_interval
# ---------------------------------------------------------------------------

def test_wilson_zero_n():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_all_hits():
    low, high = wilson_interval(10, 10)
    assert low > 0.7
    assert high == 1.0


def test_wilson_known_value():
    """50/100 at z=1.96 -> (0.4038, 0.5962), textbook value."""
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-4)
    assert high == pytest.approx(0.5962, abs=1e-4)


# ---------------------------------------------------------------------------
# shrunk_rate
# ---------------------------------------------------------------------------

def test_shrunk_rate_no_data_is_baseline():
    assert shrunk_rate(0, 0, 0.55, prior_strength=10) == pytest.approx(0.55)


def test_shrunk_rate_equal_weight_when_n_equals_prior():
    """n == prior_strength -> exactly halfway between raw rate and baseline."""
    assert shrunk_rate(10, 10, 0.5, prior_strength=10) == pytest.approx(0.75)


def test_shrunk_rate_large_n_approaches_raw():
    assert shrunk_rate(600, 1000, baseline=0.5, prior_strength=10) == pytest.approx(0.6, abs=0.01)


# ---------------------------------------------------------------------------
# over_hit_rate / league_over_rate
# ---------------------------------------------------------------------------

def test_over_hit_rate_counts_hits(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=2), SWEEP_57)
    make_match(session, lg, b, a, NOW - timedelta(hours=1), LONG_101)

    assert over_hit_rate(session, a.id, line=74.5, n=10, as_of=NOW) == (1, 2)


def test_over_hit_rate_last_n_is_most_recent(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=3), LONG_101)   # oldest, dropped
    make_match(session, lg, a, b, NOW - timedelta(hours=2), SWEEP_57)
    make_match(session, lg, a, b, NOW - timedelta(hours=1), SWEEP_57)

    assert over_hit_rate(session, a.id, line=74.5, n=2, as_of=NOW) == (0, 2)


def test_over_hit_rate_excludes_future_and_boundary(session):
    """Leakage: only scheduled_at < as_of counts. A match AT as_of is excluded too."""
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=1), SWEEP_57)
    make_match(session, lg, a, b, NOW, LONG_101)                        # boundary
    make_match(session, lg, a, b, NOW + timedelta(hours=1), LONG_101)   # future

    assert over_hit_rate(session, a.id, line=74.5, n=100, as_of=NOW) == (0, 1)


@pytest.mark.parametrize("status", ["retired", "walkover"])
def test_over_hit_rate_ignores_non_finished(session, status):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=1), LONG_101, status=status)

    assert over_hit_rate(session, a.id, line=74.5, n=10, as_of=NOW) == (0, 0)


def test_league_over_rate_excludes_future_and_other_leagues(session):
    lg, other = make_league(session, "lg1"), make_league(session, "lg2")
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=2), LONG_101)
    make_match(session, lg, a, b, NOW - timedelta(hours=1), SWEEP_57)
    make_match(session, lg, a, b, NOW, LONG_101)                        # boundary
    make_match(session, other, a, b, NOW - timedelta(hours=1), LONG_101)

    assert league_over_rate(session, lg.id, line=74.5, as_of=NOW) == (1, 2)
