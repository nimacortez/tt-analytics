import math
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Match, MatchSet

def over_hit_rate(session: Session, player_id: int, line: float, n: int,
                  as_of: datetime) -> tuple[int, int]:
    """Of the player's last n finished matches before as_of,
    how many had total points over `line`?
    Returns (hits, sample_size)."""
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")      # skip walkovers/retirements
        .where(Match.scheduled_at < as_of)      # no peeking at the future
        .order_by(Match.scheduled_at.desc())    # newest first
        .limit(n)
        .options(selectinload(Match.sets))      # load each match's sets too
    )
    matches = session.scalars(stmt).all()

    hits = 0
    for m in matches:
        total = sum(s.home_points + s.away_points for s in m.sets)
        if total > line:
            hits += 1

    return hits, len(matches)

def league_over_rate(session: Session, league_id: int, line: float,
                     as_of: datetime) -> tuple[int, int]:
    """League-wide baseline: how many finished matches before as_of went over `line`.
    Returns (hits, sample_size). Done in SQL so it doesn't load thousands of matches."""
    totals = (
        select(MatchSet.match_id,
               func.sum(MatchSet.home_points + MatchSet.away_points).label("total"))
        .group_by(MatchSet.match_id)
        .subquery()
    )
    stmt = (
        select(func.count(), func.count().filter(totals.c.total > line))
        .select_from(Match)
        .join(totals, totals.c.match_id == Match.id)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
    )
    total, hits = session.execute(stmt).one()
    return hits, total


def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a hit rate. Behaves well with small samples."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def shrunk_rate(hits: int, n: int, baseline: float, prior_strength: float = 10) -> float:
    """Blend the player's rate with the league baseline, weighted by sample size."""
    return (hits + prior_strength * baseline) / (n + prior_strength)


SMALL_SAMPLE = 10


def over_summary(session: Session, player_id: int, league_id: int, line: float,
                 n: int, as_of: datetime) -> dict:
    hits, sample = over_hit_rate(session, player_id, line, n, as_of)
    lg_hits, lg_total = league_over_rate(session, league_id, line, as_of)
    baseline = lg_hits / lg_total if lg_total else 0.5
    low, high = wilson_interval(hits, sample)
    return {
        "hits": hits,
        "sample": sample,
        "raw_rate": round(hits / sample, 3) if sample else None,
        "ci_95": (round(low, 3), round(high, 3)),
        "league_rate": round(baseline, 3),
        "shrunk_rate": round(shrunk_rate(hits, sample, baseline), 3),
        "small_sample": sample < SMALL_SAMPLE,
    }