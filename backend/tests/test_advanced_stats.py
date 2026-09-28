"""Tests for set-sequence stats (sweeps, set conditionals, splits), H2H, avg total points.

One fixed history between A and B, written from A's point of view
(W = A won the set). A alternates home/away so the home/away flip is exercised.

  #  A is   sets        result
  1  home   W W W       A 3-0
  2  away   L W W W     A 3-1
  3  home   W L L L     A 1-3
  4  away   W L W L W   A 3-2
  5  home   L L W W L   A 2-3
  6  away   L L L       A 0-3     (most recent)
"""
from datetime import timedelta

import pytest

from app.stats import (
    SET_STATS, avg_total_points, avg_total_summary, h2h_record, league_avg_total_points,
    league_set_stat, player_set_stat, set_stat_summary,
)
from tests.helpers import NOW, make_league, make_match, make_player

HISTORY = [
    (True, "WWW"),
    (False, "LWWW"),
    (True, "WLLL"),
    (False, "WLWLW"),
    (True, "LLWWL"),
    (False, "LLL"),
]


def _sets(a_is_home: bool, pattern: str) -> list[tuple[int, int]]:
    """A wins a set 11-7, loses it 7-11; flip to (home, away) order."""
    out = []
    for c in pattern:
        a_pts, b_pts = (11, 7) if c == "W" else (7, 11)
        out.append((a_pts, b_pts) if a_is_home else (b_pts, a_pts))
    return out


def _add(session, lg, a, b, when, a_is_home, pattern, status="finished"):
    home, away = (a, b) if a_is_home else (b, a)
    return make_match(session, lg, home, away, when, _sets(a_is_home, pattern), status)


@pytest.fixture
def hist(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    for i, (a_home, pattern) in enumerate(HISTORY):
        _add(session, lg, a, b, NOW - timedelta(hours=len(HISTORY) - i), a_home, pattern)
    return lg, a, b


# (hits, n) for player A over all six matches, worked out by hand from the table.
EXPECTED_A = {
    "sweep": (2, 6),                              # 1, 6
    "win_after_set1_win": (2, 3),                 # won set 1 in 1,3,4; won match 1,4
    "win_after_set1_loss": (1, 3),                # lost set 1 in 2,5,6; won match 2
    "win_after_1_1_from_1_0": (1, 2),             # W L start: 3 (lost), 4 (won)
    "set3_win_when_up_2_0": (1, 1),               # only 1
    "set5_win": (1, 2),                           # 4 won, 5 lost
    "split": (3, 6),                              # 2, 3, 4
    "split_after_losing_set1": (1, 3),            # lost set 1 in 2,5,6; won set 2 in 2
    "split_allowed_after_winning_set1": (2, 3),   # won set 1 in 1,3,4; lost set 2 in 3,4
}


def test_every_stat_has_an_expected_value():
    assert set(EXPECTED_A) == set(SET_STATS)


@pytest.mark.parametrize("stat", sorted(EXPECTED_A))
def test_player_set_stat(session, hist, stat):
    _, a, _ = hist
    assert player_set_stat(session, stat, a.id, n=50, as_of=NOW) == EXPECTED_A[stat]


def test_player_set_stat_from_other_side(session, hist):
    """B's view mirrors A's: B lost set 1 in 1,3,4 and won set 2 in 3,4."""
    _, _, b = hist
    assert player_set_stat(session, "split_after_losing_set1", b.id, 50, NOW) == (2, 3)
    assert player_set_stat(session, "split_allowed_after_winning_set1", b.id, 50, NOW) == (1, 3)
    assert player_set_stat(session, "sweep", b.id, 50, NOW) == (2, 6)


def test_player_set_stat_last_n_window(session, hist):
    """n=2 -> only matches 5 and 6 (both L L starts): no splits."""
    _, a, _ = hist
    assert player_set_stat(session, "split", a.id, n=2, as_of=NOW) == (0, 2)
    assert player_set_stat(session, "split_after_losing_set1", a.id, n=2, as_of=NOW) == (0, 2)


@pytest.mark.parametrize("stat,expected", [
    ("split", (6, 12)),                       # symmetric: each split counted from both sides
    ("split_after_losing_set1", (3, 6)),      # A 1/3 + B 2/3
    ("split_allowed_after_winning_set1", (3, 6)),
    ("sweep", (4, 12)),
    ("set5_win", (2, 4)),                     # always 50% league-wide by construction
])
def test_league_set_stat(session, hist, stat, expected):
    lg, _, _ = hist
    assert league_set_stat(session, stat, lg.id, NOW) == expected


def test_split_variants_share_league_rate(session, hist):
    """Every split has exactly one set-1 loser who won set 2, so league-wide
    'split after losing set 1' == 'split allowed after winning set 1' == split rate."""
    lg, _, _ = hist
    rates = [h / n for h, n in (league_set_stat(session, s, lg.id, NOW) for s in
             ("split", "split_after_losing_set1", "split_allowed_after_winning_set1"))]
    assert rates[0] == pytest.approx(rates[1]) == pytest.approx(rates[2])


@pytest.mark.parametrize("stat", sorted(SET_STATS))
def test_set_stats_ignore_future_boundary_and_non_finished(session, hist, stat):
    """Leakage: adding matches at exactly as_of, after as_of, or retired must change nothing."""
    lg, a, b = hist
    before_player = player_set_stat(session, stat, a.id, 50, NOW)
    before_league = league_set_stat(session, stat, lg.id, NOW)

    for a_home, pattern in HISTORY:
        _add(session, lg, a, b, NOW, a_home, pattern)                           # boundary
        _add(session, lg, a, b, NOW + timedelta(hours=1), a_home, pattern)      # future
        _add(session, lg, a, b, NOW - timedelta(days=1), a_home, pattern[:2],   # retired
             status="retired")

    assert player_set_stat(session, stat, a.id, 50, NOW) == before_player
    assert league_set_stat(session, stat, lg.id, NOW) == before_league


def test_set_stat_summary_shrinks_toward_league(session, hist):
    lg, a, _ = hist
    s = set_stat_summary(session, "split_after_losing_set1", a.id, lg.id, 50, NOW)

    assert (s["hits"], s["n"]) == (1, 3)
    assert s["league_rate"] == 0.5
    assert s["shrunk_rate"] == pytest.approx((1 + 10 * 0.5) / (3 + 10), abs=1e-3)
    assert s["small_sample"] is True


def test_set_stat_summary_no_history(session):
    lg = make_league(session)
    p = make_player(session, "new")
    s = set_stat_summary(session, "split", p.id, lg.id, 50, NOW)
    assert (s["hits"], s["n"], s["rate"]) == (0, 0, None)
    assert s["shrunk_rate"] == 0.5


# ---------------------------------------------------------------------------
# H2H
# ---------------------------------------------------------------------------

def test_h2h_record(session, hist):
    _, a, b = hist
    assert h2h_record(session, a.id, b.id, 50, NOW) == (3, 6)
    assert h2h_record(session, b.id, a.id, 50, NOW) == (3, 6)
    assert h2h_record(session, a.id, b.id, 2, NOW) == (0, 2)


def test_h2h_ignores_other_opponents_and_future(session, hist):
    lg, a, b = hist
    c = make_player(session, "c")
    _add(session, lg, a, c, NOW - timedelta(hours=1), True, "WWW")
    _add(session, lg, a, b, NOW, True, "WWW")
    assert h2h_record(session, a.id, b.id, 50, NOW) == (3, 6)


# ---------------------------------------------------------------------------
# Average total points
# ---------------------------------------------------------------------------

def test_avg_total_points(session, hist):
    """Every set is 11-7 = 18 points; A's six matches have 3+4+4+5+5+3 = 24 sets."""
    lg, a, _ = hist
    assert avg_total_points(session, a.id, 50, NOW) == (24 * 18, 6)
    assert avg_total_points(session, a.id, 1, NOW) == (3 * 18, 1)
    assert league_avg_total_points(session, lg.id, NOW) == (24 * 18, 6)


def test_avg_total_summary(session, hist):
    lg, a, _ = hist
    s = avg_total_summary(session, a.id, lg.id, n=1, as_of=NOW)
    league_mean = 24 * 18 / 6                       # 72
    assert s["mean"] == 54.0
    assert s["league_mean"] == league_mean
    assert s["shrunk_mean"] == pytest.approx((54 + 10 * league_mean) / 11, abs=0.01)


def test_avg_total_ignores_future(session, hist):
    lg, a, b = hist
    _add(session, lg, a, b, NOW, True, "WLWLW")
    assert avg_total_points(session, a.id, 50, NOW) == (24 * 18, 6)
    assert league_avg_total_points(session, lg.id, NOW) == (24 * 18, 6)
