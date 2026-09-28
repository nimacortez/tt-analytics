"""Tests for predict.py: match-winner logging uses point-in-time ratings and is idempotent."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Prediction
from app.predict import log_match_winner
from app.ratings import p_win, rebuild
from tests.helpers import NOW, make_league, make_match, make_player

P1_WINS = [(11, 7), (11, 8), (11, 9)]


def test_log_match_winner_uses_ratings_before_the_match(session):
    """The prediction for m2 must see m1's result but not m2's own."""
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    make_match(session, lg, a, b, NOW - timedelta(hours=1), P1_WINS)
    m2 = make_match(session, lg, a, b, NOW, P1_WINS)
    rebuild(session)

    assert log_match_winner(session, m2, as_of=m2.scheduled_at) is True

    pred = session.scalar(select(Prediction).where(Prediction.match_id == m2.id))
    assert pred.inputs == {"home_rating": pytest.approx(1516.0),
                           "away_rating": pytest.approx(1484.0)}
    assert pred.probability == pytest.approx(p_win(1516.0, 1484.0))
    assert pred.outcome is None


def test_log_match_winner_is_idempotent(session):
    lg = make_league(session)
    a, b = make_player(session, "a"), make_player(session, "b")
    m = make_match(session, lg, a, b, NOW, P1_WINS)

    assert log_match_winner(session, m, as_of=NOW) is True
    assert log_match_winner(session, m, as_of=NOW) is False
    assert session.scalar(select(func.count()).select_from(Prediction)) == 1
