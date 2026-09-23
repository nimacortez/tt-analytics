"""Tests for grade.py: brier_score edge cases."""
from datetime import datetime, timezone

import pytest

from app.grade import brier_score
from app.models import League, Player, Match, Prediction


def _make_graded_prediction(session, match_id, probability, outcome):
    """Insert a graded Prediction row."""
    now = datetime.now(timezone.utc)
    pred = Prediction(
        match_id=match_id,
        market="match_winner",
        line=-1.0,
        model_name="elo",
        model_version="v1",
        probability=probability,
        as_of=now,
        inputs={},
        outcome=outcome,
        graded_at=now,
    )
    session.add(pred)
    session.flush()
    return pred


def _make_match(session):
    """Minimal match + players for FK requirements."""
    lg = League(api_id="grade-test-lg", name="Grade Test League")
    session.add(lg)
    session.flush()

    p1 = Player(api_id="grade-p1", name="P1")
    p2 = Player(api_id="grade-p2", name="P2")
    session.add_all([p1, p2])
    session.flush()

    m = Match(
        api_id="grade-m1",
        league_id=lg.id,
        home_player_id=p1.id,
        away_player_id=p2.id,
        scheduled_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status="finished",
        home_sets_won=3,
        away_sets_won=1,
    )
    session.add(m)
    session.flush()
    return m


def test_brier_perfect(session):
    """All correct predictions at probability 1.0 → Brier = 0.0."""
    m = _make_match(session)
    _make_graded_prediction(session, m.id, probability=1.0, outcome=True)
    score, n = brier_score(session, "match_winner", "v1")
    assert n == 1
    assert abs(score - 0.0) < 1e-9


def test_brier_half(session):
    """All predictions at 0.5 regardless of outcome → Brier = 0.25."""
    m = _make_match(session)
    _make_graded_prediction(session, m.id, probability=0.5, outcome=True)

    # Add a second match for away win
    lg2 = League(api_id="grade-test-lg2", name="Grade Test League 2")
    session.add(lg2)
    session.flush()
    p3 = Player(api_id="grade-p3", name="P3")
    p4 = Player(api_id="grade-p4", name="P4")
    session.add_all([p3, p4])
    session.flush()
    m2 = Match(
        api_id="grade-m2",
        league_id=lg2.id,
        home_player_id=p3.id,
        away_player_id=p4.id,
        scheduled_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        status="finished",
        home_sets_won=0,
        away_sets_won=3,
    )
    session.add(m2)
    session.flush()

    now = datetime.now(timezone.utc)
    pred2 = Prediction(
        match_id=m2.id,
        market="match_winner",
        line=-1.0,
        model_name="elo",
        model_version="v1",
        probability=0.5,
        as_of=now,
        inputs={},
        outcome=False,
        graded_at=now,
    )
    session.add(pred2)
    session.flush()

    score, n = brier_score(session, "match_winner", "v1")
    assert n == 2
    assert abs(score - 0.25) < 1e-9
