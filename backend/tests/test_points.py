"""Tests for points.py: exact set/match distributions, log5, point-in-time point rates."""
import math
import random
from datetime import timedelta
from math import comb

import pytest

from app.points import (
    match_distribution, matchup, matchup_point_prob, player_point_win_rate, set_outcomes,
)
from app.providers.mock import POINT_SCALE, simulate_match
from tests.helpers import NOW, make_league, make_match, make_player


# ---------------------------------------------------------------------------
# Exact distributions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("p", [0.3, 0.5, 0.56, 0.8])
def test_set_outcomes_sum_to_one(p):
    home, away = set_outcomes(p)
    assert sum(home.values()) + sum(away.values()) == pytest.approx(1.0, abs=1e-12)


def test_set_outcomes_known_values_at_half():
    home, away = set_outcomes(0.5)
    assert home[11] == pytest.approx(0.5**11)                 # 11-0
    assert home[20] == pytest.approx(comb(19, 9) * 0.5**20)   # 11-9
    reach_deuce = comb(20, 10) * 0.5**20                      # ~0.176
    deuce_total = sum(v for t, v in home.items() if t >= 22) * 2
    assert deuce_total == pytest.approx(reach_deuce)
    assert home == away


@pytest.mark.parametrize("p", [0.3, 0.5, 0.56, 0.8])
def test_match_distribution_sums_to_one(p):
    d = match_distribution(p)
    assert sum(d.totals.values()) == pytest.approx(1.0, abs=1e-9)
    assert min(d.totals) == 33       # 3 sets of 11-0


def test_match_distribution_even_matchup():
    d = match_distribution(0.5)
    assert d.p_home_win == pytest.approx(0.5)
    assert d.p_over(0) == pytest.approx(1.0)
    assert d.p_over(10_000) == pytest.approx(0.0)


def test_match_win_amplifies_point_edge():
    """A 55% point edge is worth far more than 55% of matches."""
    d = match_distribution(0.55)
    assert d.p_home_win > 0.75
    assert match_distribution(0.45).p_home_win == pytest.approx(1 - d.p_home_win)


def test_bigger_mismatch_means_fewer_points():
    assert match_distribution(0.65).mean() < match_distribution(0.5).mean()


def test_p_over_decreases_with_line():
    d = match_distribution(0.52)
    probs = [d.p_over(line) for line in (60.5, 70.5, 74.5, 80.5, 90.5)]
    assert probs == sorted(probs, reverse=True)


def test_exact_model_matches_mock_simulator():
    """Brute-force the mock's own simulate_match and compare. This is the check
    that the closed-form math is right (same rules: 11, win by 2, best of 5)."""
    skill_gap = 2.0
    p = 1 / (1 + math.exp(-POINT_SCALE * skill_gap))
    rng = random.Random(7)
    totals, home_wins = [], 0
    while len(totals) < 20_000:
        status, sets = simulate_match(rng, skill_gap, 0.0)
        if status != "finished":
            continue
        totals.append(sum(s.home_points + s.away_points for s in sets))
        home_wins += sum(s.home_points > s.away_points for s in sets) == 3

    d = match_distribution(p)
    assert sum(totals) / len(totals) == pytest.approx(d.mean(), abs=0.3)
    assert sum(t > 74.5 for t in totals) / len(totals) == pytest.approx(d.p_over(74.5), abs=0.012)
    assert home_wins / len(totals) == pytest.approx(d.p_home_win, abs=0.012)


# ---------------------------------------------------------------------------
# log5
# ---------------------------------------------------------------------------

def test_matchup_point_prob():
    assert matchup_point_prob(0.5, 0.5) == pytest.approx(0.5)
    assert matchup_point_prob(0.55, 0.5) == pytest.approx(0.55)   # vs average = own rate
    assert matchup_point_prob(0.55, 0.45) > 0.55
    assert matchup_point_prob(0.55, 0.45) + matchup_point_prob(0.45, 0.55) == pytest.approx(1)


# ---------------------------------------------------------------------------
# player_point_win_rate: counts and leakage
# ---------------------------------------------------------------------------

def test_player_point_win_rate_counts_both_sides(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=2), [(11, 9)] * 3)   # a: 33 of 60
    make_match(session, lg, b, a, NOW - timedelta(hours=1), [(11, 5)] * 3)   # a: 15 of 48

    rate, played = player_point_win_rate(session, a.id, 10, NOW, prior_strength=0)

    assert played == 108
    assert rate == pytest.approx(48 / 108)


def test_player_point_win_rate_ignores_boundary_future_retired(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=1), [(11, 9)] * 3)
    make_match(session, lg, a, b, NOW, [(11, 0)] * 3)
    make_match(session, lg, a, b, NOW + timedelta(hours=1), [(11, 0)] * 3)
    make_match(session, lg, a, b, NOW - timedelta(hours=2), [(11, 0)], status="retired")

    rate, played = player_point_win_rate(session, a.id, 10, NOW, prior_strength=0)

    assert (rate, played) == (pytest.approx(33 / 60), 60)


def test_player_point_win_rate_no_history_is_prior(session):
    p = make_player(session, "new")
    assert player_point_win_rate(session, p.id, 10, NOW) == (0.5, 0)


def test_matchup_logs_inputs(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=1), [(11, 9)] * 3)

    dist, inputs = matchup(session, a.id, b.id, NOW)

    assert inputs["home_points_seen"] == inputs["away_points_seen"] == 60
    assert inputs["p_home_point"] > 0.5
    assert dist.p_home_win > 0.5
