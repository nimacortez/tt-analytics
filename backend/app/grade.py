"""
Grade predictions and report Brier score + calibration.
  python -m app.grade
"""
import math
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models import Match, Player, Prediction
from app.stats import league_over_rate


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


def _brier_of(session: Session, market: str, model_version: str,
              prob_fn: Callable[[Prediction, Match], float | None]) -> tuple[float, int]:
    """Brier of an alternative probability (baseline, oracle) on the same graded
    predictions as the model, so the numbers are directly comparable."""
    rows = session.execute(
        select(Prediction, Match)
        .join(Match, Match.id == Prediction.match_id)
        .where(Prediction.market == market)
        .where(Prediction.model_version == model_version)
        .where(Prediction.outcome.is_not(None))
    ).all()
    total = 0.0
    counted = 0
    for pred, match in rows:
        prob = prob_fn(pred, match)
        if prob is None:
            continue
        total += (prob - float(pred.outcome)) ** 2
        counted += 1
    return (total / counted if counted else 0.0), counted


def _oracle(session: Session) -> Callable[[Prediction, Match], float | None]:
    """Mock-only: the true probabilities, from hidden skills through the exact points
    model (which is how the mock generates matches). The best Brier any model can get."""
    from app.points import match_distribution
    from app.providers.mock import POINT_SCALE, MockProvider

    provider = MockProvider()
    api_ids = dict(session.execute(select(Player.id, Player.api_id)).tuples().all())

    def prob(pred: Prediction, match: Match) -> float | None:
        try:
            gap = (provider.true_skill(api_ids[match.home_player_id])
                   - provider.true_skill(api_ids[match.away_player_id]))
        except KeyError:
            return None
        dist = match_distribution(1 / (1 + math.exp(-POINT_SCALE * gap)))
        if pred.market == "match_winner":
            return dist.p_home_win
        if pred.market == "total_over":
            return dist.p_over(pred.line)
        return None

    return prob


def _league_over_baseline(session: Session) -> Callable[[Prediction, Match], float]:
    """Naive totals baseline: the league's over rate at this line, as of the match."""
    def prob(pred: Prediction, match: Match) -> float:
        hits, n = league_over_rate(session, match.league_id, pred.line, pred.as_of)
        return hits / n if n else 0.5
    return prob


MODELS = [
    # (market, model_version, label, naive baseline label, naive baseline)
    ("match_winner", "v1", "Elo v1", "Always 0.5", lambda s: (lambda p, m: 0.5)),
    ("total_over", "v1", "Points model v1", "League over rate", _league_over_baseline),
]


def report(session: Session) -> None:
    """Print Brier scores (naive baseline / model / oracle) and calibration per market."""
    graded = grade_predictions(session)
    if graded:
        print(f"Graded {graded} new predictions")

    oracle = _oracle(session)
    for market, version, label, base_label, base_fn in MODELS:
        model_score, n = brier_score(session, market, version)
        print(f"\n=== {market} ({label}), n={n} ===")
        if not n:
            continue
        base_score, _ = _brier_of(session, market, version, base_fn(session))
        oracle_score, oracle_n = _brier_of(session, market, version, oracle)
        print(f"  {base_label + ':':<22}{base_score:.4f}")
        print(f"  {label + ':':<22}{model_score:.4f}")
        print(f"  {'Oracle (true skill):':<22}{oracle_score:.4f}  (n={oracle_n})")
        if model_score > base_score:
            print("  *** WARNING: model is WORSE than the naive baseline ***")

        print(f"  {'bin':>12}  {'n':>5}  {'mean_p':>7}  {'actual':>7}")
        for row in calibration_table(session, market, version):
            if row["n"]:
                print(f"  {row['bin_low']:.2f}-{row['bin_high']:.2f}  "
                      f"{row['n']:>5}  {row['mean_prob']:>7.3f}  {row['actual_rate']:>7.3f}")


if __name__ == "__main__":
    with SessionLocal() as session:
        report(session)
