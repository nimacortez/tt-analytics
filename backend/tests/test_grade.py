"""Tests for grade.py: market-aware grading, Brier score, calibration bins."""
from datetime import timedelta

import pytest

from app.grade import brier_score, calibration_table, grade_predictions
from app.models import Prediction
from tests.helpers import NOW, make_league, make_match, make_player

HOME_SWEEP_57 = [(11, 7), (11, 8), (11, 9)]
AWAY_WINS_101 = [(11, 9), (9, 11), (12, 10), (10, 12), (6, 11)]


def _pred(session, match, market="match_winner", line=-1.0, probability=0.5,
          outcome=None, version="v1") -> Prediction:
    p = Prediction(match_id=match.id, market=market, line=line, model_name="test",
                   model_version=version, probability=probability,
                   as_of=match.scheduled_at, inputs={}, outcome=outcome)
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def two_matches(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    m1 = make_match(session, lg, a, b, NOW - timedelta(hours=2), HOME_SWEEP_57)
    m2 = make_match(session, lg, a, b, NOW - timedelta(hours=1), AWAY_WINS_101)
    return lg, a, b, m1, m2


# ---------------------------------------------------------------------------
# grade_predictions
# ---------------------------------------------------------------------------

def test_grade_match_winner(session, two_matches):
    *_, m1, m2 = two_matches
    p1, p2 = _pred(session, m1), _pred(session, m2)

    assert grade_predictions(session) == 2
    assert p1.outcome is True     # home won
    assert p2.outcome is False    # away won
    assert p1.graded_at is not None


def test_grade_total_over_uses_points_not_winner(session, two_matches):
    """Regression: total_over used to be graded as 'did home win'."""
    *_, m1, m2 = two_matches
    under = _pred(session, m1, market="total_over", line=74.5)   # 57 pts, home won
    over = _pred(session, m2, market="total_over", line=74.5)    # 101 pts, away won

    grade_predictions(session)

    assert under.outcome is False
    assert over.outcome is True


def test_grade_total_over_respects_line(session, two_matches):
    *_, m1, _ = two_matches
    low = _pred(session, m1, market="total_over", line=56.5)
    high = _pred(session, m1, market="total_over", line=57.5)

    grade_predictions(session)

    assert low.outcome is True
    assert high.outcome is False


@pytest.mark.parametrize("status", ["retired", "walkover", "scheduled"])
def test_grade_skips_non_finished(session, status):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    m = make_match(session, lg, a, b, NOW, HOME_SWEEP_57, status=status)
    p = _pred(session, m)

    assert grade_predictions(session) == 0
    assert p.outcome is None


def test_grade_skips_unknown_market(session, two_matches):
    *_, m1, _ = two_matches
    p = _pred(session, m1, market="mystery")

    assert grade_predictions(session) == 0
    assert p.outcome is None


def test_grade_is_idempotent(session, two_matches):
    *_, m1, _ = two_matches
    _pred(session, m1)
    assert grade_predictions(session) == 1
    assert grade_predictions(session) == 0


# ---------------------------------------------------------------------------
# brier_score / calibration_table
# ---------------------------------------------------------------------------

def test_brier_perfect(session, two_matches):
    *_, m1, m2 = two_matches
    _pred(session, m1, probability=1.0, outcome=True)
    _pred(session, m2, probability=0.0, outcome=False)
    assert brier_score(session, "match_winner", "v1") == (pytest.approx(0.0), 2)


def test_brier_coin_flip_is_quarter(session, two_matches):
    *_, m1, m2 = two_matches
    _pred(session, m1, probability=0.5, outcome=True)
    _pred(session, m2, probability=0.5, outcome=False)
    assert brier_score(session, "match_winner", "v1") == (pytest.approx(0.25), 2)


def test_brier_filters_market_version_and_ungraded(session, two_matches):
    *_, m1, m2 = two_matches
    _pred(session, m1, probability=0.8, outcome=True)                 # counted: 0.04
    _pred(session, m2, probability=0.9, outcome=None)                 # ungraded
    _pred(session, m2, probability=0.0, outcome=True, version="v2")   # other version
    _pred(session, m1, market="total_over", line=74.5, probability=1.0, outcome=False)

    assert brier_score(session, "match_winner", "v1") == (pytest.approx(0.04), 1)


def test_brier_no_predictions(session):
    assert brier_score(session, "match_winner", "v1") == (0.0, 0)


def test_calibration_top_bin_includes_probability_one(session, two_matches):
    *_, m1, m2 = two_matches
    _pred(session, m1, probability=1.0, outcome=True)
    _pred(session, m2, probability=0.95, outcome=False)

    top = calibration_table(session, "match_winner", "v1")[-1]

    assert top["n"] == 2
    assert top["actual_rate"] == 0.5
