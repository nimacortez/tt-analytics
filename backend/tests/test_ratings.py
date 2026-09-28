"""Tests for ratings.py: p_win math, the Elo update inside rebuild(), rating_as_of leakage."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import PlayerRating
from app.ratings import DEFAULT_RATING, p_win, rating_as_of, rebuild
from tests.helpers import NOW, make_league, make_match, make_player

P1_WINS = [(11, 7), (11, 8), (11, 9)]
P2_WINS = [(7, 11), (8, 11), (9, 11)]


# ---------------------------------------------------------------------------
# p_win
# ---------------------------------------------------------------------------

def test_p_win_equal():
    assert p_win(1500.0, 1500.0) == pytest.approx(0.5)


def test_p_win_400_points_is_10_to_1():
    """By definition, a 400-point gap means 10:1 odds."""
    assert p_win(1900.0, 1500.0) == pytest.approx(10 / 11)


def test_p_win_symmetry():
    assert p_win(1600.0, 1400.0) + p_win(1400.0, 1600.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Elo update math, exercised through rebuild() (not re-derived in the test)
# ---------------------------------------------------------------------------

def _rows(session, match_id):
    return {r.player_id: r for r in session.scalars(
        select(PlayerRating).where(PlayerRating.match_id == match_id))}


def test_rebuild_single_match_moves_16_points(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    m = make_match(session, lg, a, b, NOW, P1_WINS)

    final = rebuild(session, k=32.0)

    rows = _rows(session, m.id)
    assert rows[a.id].rating_before == DEFAULT_RATING
    assert rows[a.id].rating_after == pytest.approx(1516.0)
    assert rows[b.id].rating_after == pytest.approx(1484.0)
    assert final == {a.id: pytest.approx(1516.0), b.id: pytest.approx(1484.0)}


def test_rebuild_second_match_uses_first_matchs_rating(session):
    """Upset: the 1484 player beats the 1516 player. Gain = K * (1 - E) with E < 0.5."""
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW, P1_WINS)
    m2 = make_match(session, lg, a, b, NOW + timedelta(hours=1), P2_WINS)

    rebuild(session, k=32.0)

    rows = _rows(session, m2.id)
    e_b = p_win(1484.0, 1516.0)
    assert rows[b.id].rating_before == pytest.approx(1484.0)
    assert rows[b.id].rating_after == pytest.approx(1484.0 + 32.0 * (1 - e_b))
    assert 32.0 * (1 - e_b) > 16.0  # upsets pay more than even matches


def test_rebuild_is_zero_sum(session):
    lg = make_league(session)
    ps = [make_player(session, f"p{i}") for i in range(4)]
    pairs = [(0, 1), (2, 3), (0, 2), (1, 3), (3, 0), (1, 2)]
    for i, (h, a) in enumerate(pairs):
        make_match(session, lg, ps[h], ps[a], NOW + timedelta(minutes=25 * i),
                   P1_WINS if i % 2 else P2_WINS)

    final = rebuild(session)

    assert sum(final.values()) == pytest.approx(DEFAULT_RATING * 4)


def test_rebuild_processes_in_time_order_not_insert_order(session):
    """Insert the later match first; rebuild must still apply the earlier one first."""
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    later = make_match(session, lg, a, b, NOW + timedelta(hours=1), P2_WINS)
    earlier = make_match(session, lg, a, b, NOW, P1_WINS)

    rebuild(session)

    assert _rows(session, earlier.id)[a.id].rating_before == DEFAULT_RATING
    assert _rows(session, later.id)[a.id].rating_before == pytest.approx(1516.0)


@pytest.mark.parametrize("status", ["retired", "walkover", "scheduled", "cancelled"])
def test_rebuild_ignores_non_finished(session, status):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    m = make_match(session, lg, a, b, NOW, P1_WINS, status=status)

    final = rebuild(session)

    assert final == {}
    assert _rows(session, m.id) == {}


def test_rebuild_is_idempotent(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW, P1_WINS)

    rebuild(session)
    first = session.scalars(select(PlayerRating.rating_after)).all()
    rebuild(session)
    second = session.scalars(select(PlayerRating.rating_after)).all()

    assert sorted(first) == sorted(second)
    assert len(second) == 2


# ---------------------------------------------------------------------------
# rating_as_of leakage
# ---------------------------------------------------------------------------

def _two_match_history(session):
    """a beats b at NOW-2h, then b beats a at NOW+1h. Ratings built by rebuild()."""
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=2), P1_WINS)
    make_match(session, lg, a, b, NOW + timedelta(hours=1), P2_WINS)
    rebuild(session)
    return a, b


def test_rating_as_of_excludes_future(session):
    a, _ = _two_match_history(session)
    assert rating_as_of(session, a.id, NOW) == pytest.approx(1516.0)


def test_rating_as_of_excludes_match_at_exactly_as_of(session):
    """Strict <: a match starting at as_of hasn't happened yet from as_of's viewpoint."""
    a, _ = _two_match_history(session)
    assert rating_as_of(session, a.id, NOW + timedelta(hours=1)) == pytest.approx(1516.0)
    assert rating_as_of(session, a.id, NOW + timedelta(hours=1, seconds=1)) < 1516.0


def test_rating_as_of_before_any_match_is_default(session):
    a, _ = _two_match_history(session)
    assert rating_as_of(session, a.id, NOW - timedelta(days=1)) == DEFAULT_RATING


def test_rating_as_of_no_history(session):
    p = make_player(session, "ghost")
    assert rating_as_of(session, p.id, NOW) == DEFAULT_RATING
