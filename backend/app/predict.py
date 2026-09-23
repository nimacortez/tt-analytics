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
from app.ratings import rating_as_of, p_win

MODEL_NAME = "elo"
MODEL_VERSION = "v1"
BURN_IN = 10  # skip each player's first N finished matches as burn-in


def _player_match_count(session: Session, player_id: int, before: datetime) -> int:
    """Count finished matches for a player before the given time."""
    from app.models import Match as M
    from sqlalchemy import or_, func as sqlfunc
    return session.scalar(
        select(sqlfunc.count()).select_from(M)
        .where(or_(M.home_player_id == player_id, M.away_player_id == player_id))
        .where(M.status == "finished")
        .where(M.scheduled_at < before)
    ) or 0


def log_match_winner(
    session: Session,
    match: Match,
    as_of: datetime,
    ratings_cache: dict[int, float] | None = None,
) -> bool:
    """Log an Elo match-winner prediction. Returns True if a new row was inserted."""
    # Skip if prediction already exists for this match + model version
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


def _backfill(session: Session) -> None:
    """Generate Elo predictions for all finished matches (with burn-in)."""
    matches = session.scalars(
        select(Match)
        .where(Match.status == "finished")
        .order_by(Match.scheduled_at.asc())
    ).all()

    # Per-player appearance counter (both home and away)
    appearances: dict[int, int] = {}
    logged = 0

    for m in matches:
        h_id = m.home_player_id
        a_id = m.away_player_id

        # Burn-in: count appearances *before* this match
        h_count = appearances.get(h_id, 0)
        a_count = appearances.get(a_id, 0)

        # Update appearance counts (this match counts for future burn-in)
        appearances[h_id] = h_count + 1
        appearances[a_id] = a_count + 1

        # Skip if either player hasn't finished their burn-in
        if h_count < BURN_IN or a_count < BURN_IN:
            continue

        as_of = m.scheduled_at
        if log_match_winner(session, m, as_of):
            logged += 1

    session.commit()
    print(f"Backfilled {logged} match_winner predictions")


def _log_scheduled(session: Session) -> None:
    """Log predictions for all currently scheduled matches."""
    now = datetime.now(timezone.utc)
    matches = session.scalars(
        select(Match).where(Match.status == "scheduled")
    ).all()

    logged = 0
    for m in matches:
        if log_match_winner(session, m, now):
            logged += 1

    session.commit()
    print(f"Logged {logged} new match_winner predictions for scheduled matches")


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
