"""
Log predictions.
  python -m app.predict               # log for all scheduled matches
  python -m app.predict --backfill    # generate predictions for last 30 days of finished matches
"""
import argparse
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Match, Prediction
from app.points import matchup
from app.ratings import rating_as_of, p_win

MODEL_NAME = "elo"
MODEL_VERSION = "v1"
BURN_IN = 10  # skip each player's first N finished matches as burn-in

POINTS_MODEL_NAME = "points_model"   # app/points.py, exact iid-points distribution
POINTS_MODEL_VERSION = "v1"
DEFAULT_TOTAL_LINE = 74.5  # near the mock league median; real lines come with odds


def log_match_winner(
    session: Session,
    match: Match,
    as_of: datetime,
    ratings_cache: dict[int, float] | None = None,
) -> bool:
    """Log an Elo match-winner prediction. Returns True if a new row was inserted."""
    existing = session.scalar(
        select(Prediction).where(
            Prediction.match_id == match.id,
            Prediction.market == "match_winner",
            Prediction.model_version == MODEL_VERSION,
        )
    )
    if existing:
        return False

    h_id = match.home_player_id
    a_id = match.away_player_id

    if ratings_cache is not None:
        h_rating = ratings_cache.get(h_id, 1500.0)
        a_rating = ratings_cache.get(a_id, 1500.0)
    else:
        h_rating = rating_as_of(session, h_id, as_of)
        a_rating = rating_as_of(session, a_id, as_of)

    prob = p_win(h_rating, a_rating)

    pred = Prediction(
        match_id=match.id,
        market="match_winner",
        line=-1.0,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        probability=prob,
        as_of=as_of,
        inputs={"home_rating": h_rating, "away_rating": a_rating},
    )
    try:
        session.add(pred)
        session.flush()
        return True
    except IntegrityError:
        session.rollback()
        return False


def log_total_over(
    session: Session,
    match: Match,
    line: float,
    as_of: datetime,
    n: int = 20,
) -> bool:
    """Log a points-model total_over prediction. Returns True if inserted."""
    existing = session.scalar(
        select(Prediction).where(
            Prediction.match_id == match.id,
            Prediction.market == "total_over",
            Prediction.line == line,
            Prediction.model_version == POINTS_MODEL_VERSION,
        )
    )
    if existing:
        return False

    dist, inputs = matchup(session, match.home_player_id, match.away_player_id, as_of, n)

    pred = Prediction(
        match_id=match.id,
        market="total_over",
        line=line,
        model_name=POINTS_MODEL_NAME,
        model_version=POINTS_MODEL_VERSION,
        probability=dist.p_over(line),
        as_of=as_of,
        inputs=inputs,
    )
    try:
        session.add(pred)
        session.flush()
        return True
    except IntegrityError:
        session.rollback()
        return False


def _backfill(session: Session) -> None:
    """Generate Elo + total_over predictions for all finished matches (with burn-in)."""
    matches = session.scalars(
        select(Match)
        .where(Match.status == "finished")
        .order_by(Match.scheduled_at.asc())
    ).all()

    appearances: dict[int, int] = {}
    elo_logged = 0
    total_logged = 0

    for m in matches:
        h_id = m.home_player_id
        a_id = m.away_player_id

        h_count = appearances.get(h_id, 0)
        a_count = appearances.get(a_id, 0)

        appearances[h_id] = h_count + 1
        appearances[a_id] = a_count + 1

        if h_count < BURN_IN or a_count < BURN_IN:
            continue

        as_of = m.scheduled_at
        if log_match_winner(session, m, as_of):
            elo_logged += 1
        if log_total_over(session, m, DEFAULT_TOTAL_LINE, as_of):
            total_logged += 1

    session.commit()
    print(f"Backfilled {elo_logged} match_winner predictions")
    print(f"Backfilled {total_logged} total_over predictions (line={DEFAULT_TOTAL_LINE})")


def _log_scheduled(session: Session) -> None:
    """Log predictions for all currently scheduled matches."""
    now = datetime.now(timezone.utc)
    matches = session.scalars(
        select(Match).where(Match.status == "scheduled")
    ).all()

    elo_logged = total_logged = 0
    for m in matches:
        if log_match_winner(session, m, now):
            elo_logged += 1
        if log_total_over(session, m, DEFAULT_TOTAL_LINE, now):
            total_logged += 1

    session.commit()
    print(f"Logged {elo_logged} new match_winner predictions for scheduled matches")
    print(f"Logged {total_logged} new total_over predictions for scheduled matches")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log predictions")
    parser.add_argument("--backfill", action="store_true",
                        help="Generate predictions for finished matches")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.backfill:
            _backfill(session)
        else:
            _log_scheduled(session)
