"""
Grade predictions and report Brier score + calibration.
  python -m app.grade
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models import Match, Prediction


def outcome_for(pred: Prediction, match: Match) -> bool | None:
    """Did the predicted event happen? None = can't grade this market.

    match_winner: probability is P(home wins).
    total_over:   probability is P(total points > line).
    """
    if pred.market == "match_winner":
        return (match.home_sets_won or 0) > (match.away_sets_won or 0)
    if pred.market == "total_over":
        return sum(s.home_points + s.away_points for s in match.sets) > pred.line
    return None


def grade_predictions(session: Session) -> int:
    """Set outcome on ungraded predictions whose match is now finished.

    Walkovers and retirements never get graded (as per design rules).
    Returns count of newly graded rows.
    """
    rows = session.execute(
        select(Prediction, Match)
        .join(Match, Match.id == Prediction.match_id)
        .where(Prediction.outcome.is_(None))
        .where(Match.status == "finished")
        .where(Match.home_sets_won.is_not(None))
        .where(Match.away_sets_won.is_not(None))
        .options(selectinload(Match.sets))
    ).all()

    now = datetime.now(timezone.utc)
    count = 0
    for pred, match in rows:
        outcome = outcome_for(pred, match)
        if outcome is None:
            continue
        pred.outcome = outcome
        pred.graded_at = now
        count += 1

    session.commit()
    return count


def brier_score(session: Session, market: str, model_version: str) -> tuple[float, int]:
    """Brier score for all graded predictions of this market+model.
    Returns (score, n). Lower is better; 0.25 = always predicting 0.5.
    """
    preds = session.scalars(
        select(Prediction)
        .where(Prediction.market == market)
        .where(Prediction.model_version == model_version)
        .where(Prediction.outcome.is_not(None))
    ).all()

    if not preds:
        return 0.0, 0

    total = sum((p.probability - float(p.outcome)) ** 2 for p in preds)
    return total / len(preds), len(preds)


def calibration_table(
    session: Session, market: str, model_version: str, bins: int = 10
) -> list[dict]:
    """Divide predictions into bins equal-width probability buckets.
    Returns list of {bin_low, bin_high, n, mean_prob, actual_rate}.
    """
    preds = session.scalars(
        select(Prediction)
        .where(Prediction.market == market)
        .where(Prediction.model_version == model_version)
        .where(Prediction.outcome.is_not(None))
    ).all()

    bin_width = 1.0 / bins
    result = []
    for i in range(bins):
        low = i * bin_width
        high = (i + 1) * bin_width
        last = i == bins - 1  # include p == 1.0 in the top bin
        bucket = [p for p in preds
                  if low <= p.probability and (p.probability < high or last)]
        if not bucket:
            result.append({
                "bin_low": round(low, 3), "bin_high": round(high, 3),
                "n": 0, "mean_prob": None, "actual_rate": None,
            })
        else:
            mean_prob = sum(p.probability for p in bucket) / len(bucket)
            actual_rate = sum(float(p.outcome) for p in bucket) / len(bucket)
            result.append({
                "bin_low": round(low, 3), "bin_high": round(high, 3),
                "n": len(bucket),
                "mean_prob": round(mean_prob, 3),
                "actual_rate": round(actual_rate, 3),
            })
    return result


def _oracle_brier(session: Session) -> tuple[float, int]:
    """Compute oracle Brier score using mock true_skill via p_win.
    This is a lower bound achievable with perfect knowledge of skills.
    """
    from app.providers.mock import MockProvider
    from app.models import Player
    from app.ratings import p_win

    provider = MockProvider()
    players = session.scalars(select(Player)).all()
    pid_to_api = {p.id: p.api_id for p in players}

    preds = session.scalars(
        select(Prediction)
        .where(Prediction.market == "match_winner")
        .where(Prediction.model_version == "v1")
        .where(Prediction.outcome.is_not(None))
    ).all()

    if not preds:
        return 0.0, 0

    # Pre-load the match home/away for each prediction
    match_ids = {p.match_id for p in preds}
    matches = {
        m.id: m for m in session.scalars(
            select(Match).where(Match.id.in_(match_ids))
        ).all()
    }

    total = 0.0
    counted = 0
    for pred in preds:
        m = matches[pred.match_id]
        h_api = pid_to_api.get(m.home_player_id, "")
        a_api = pid_to_api.get(m.away_player_id, "")
        try:
            h_skill = provider.true_skill(h_api)
            a_skill = provider.true_skill(a_api)
        except KeyError:
            continue
        # Convert skills to win probability using same Bradley-Terry as mock
        import math
        p_home_point = 1 / (1 + math.exp(-0.12 * (h_skill - a_skill)))
        # Use p_win with ratings derived from skills (scale to Elo range)
        oracle_prob = p_home_point  # direct skill-based prob (not Elo-derived)
        total += (oracle_prob - float(pred.outcome)) ** 2
        counted += 1

    return total / counted if counted else 0.0, counted


def _historical_over_brier(session: Session, line: float = 74.5) -> tuple[float, int]:
    """Historical over-rate baseline Brier for total_over predictions.

    Uses each match's league over rate as_of match time as the predicted probability.
    """
    from app.stats import league_over_rate
    from app.models import League

    preds = session.scalars(
        select(Prediction)
        .where(Prediction.market == "total_over")
        .where(Prediction.model_version == "v1")
        .where(Prediction.outcome.is_not(None))
    ).all()

    if not preds:
        return 0.0, 0

    match_ids = {p.match_id for p in preds}
    matches = {
        m.id: m for m in session.scalars(
            select(Match).where(Match.id.in_(match_ids))
        ).all()
    }

    total = 0.0
    counted = 0
    for pred in preds:
        m = matches.get(pred.match_id)
        if not m:
            continue
        lg_hits, lg_total = league_over_rate(session, m.league_id, line, pred.as_of)
        hist_prob = lg_hits / lg_total if lg_total else 0.5
        total += (hist_prob - float(pred.outcome)) ** 2
        counted += 1

    return total / counted if counted else 0.0, counted


def report(session: Session) -> None:
    """Print Brier scores and calibration tables for all markets."""
    graded = grade_predictions(session)
    if graded:
        print(f"Graded {graded} new predictions")

    print("\n=== Brier Scores (match_winner) ===")
    elo_score, elo_n = brier_score(session, "match_winner", "v1")
    baseline = 0.25
    oracle_score, oracle_n = _oracle_brier(session)

    print(f"  Always-0.5 baseline:  {baseline:.4f}")
    print(f"  Elo v1:               {elo_score:.4f}  (n={elo_n})")
    print(f"  Oracle (true_skill):  {oracle_score:.4f}  (n={oracle_n})")

    if elo_n > 0 and elo_score > baseline:
        print("  *** WARNING: Elo Brier score is ABOVE the always-0.5 baseline! ***")

    print("\n=== Calibration (match_winner, elo v1) ===")
    print(f"  {'bin':>12}  {'n':>5}  {'mean_p':>7}  {'actual':>7}")
    for row in calibration_table(session, "match_winner", "v1"):
        if row["n"] == 0:
            continue
        print(f"  {row['bin_low']:.2f}–{row['bin_high']:.2f}  "
              f"{row['n']:>5}  {row['mean_prob']:>7.3f}  {row['actual_rate']:>7.3f}")

    print("\n=== Brier Scores (total_over, line=74.5) ===")
    pts_score, pts_n = brier_score(session, "total_over", "v1")
    hist_score, hist_n = _historical_over_brier(session)

    print(f"  Historical baseline:  {hist_score:.4f}  (n={hist_n})")
    print(f"  Points model v1:      {pts_score:.4f}  (n={pts_n})")

    if pts_n > 0 and hist_n > 0 and pts_score > hist_score:
        print("  *** WARNING: Points model Brier is ABOVE historical baseline! ***")

    print("\n=== Calibration (total_over, points_model v1) ===")
    print(f"  {'bin':>12}  {'n':>5}  {'mean_p':>7}  {'actual':>7}")
    for row in calibration_table(session, "total_over", "v1"):
        if row["n"] == 0:
            continue
        print(f"  {row['bin_low']:.2f}–{row['bin_high']:.2f}  "
              f"{row['n']:>5}  {row['mean_prob']:>7.3f}  {row['actual_rate']:>7.3f}")


if __name__ == "__main__":
    with SessionLocal() as session:
        report(session)
