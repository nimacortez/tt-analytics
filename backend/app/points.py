"""Points-total distribution model: P(total points > line) at any line.

Model: each point is won by home with a fixed probability p (independent points).
Inputs are set-level only: p comes from each player's share of points in their
recent sets. From p we get the exact distribution of match total points:

  set to 11, win by 2:   P(home wins 11-k) = C(10+k, k) p^11 q^k        k = 0..9
  from 10-10 (deuce):    P(reach 10-10) = C(20, 10) (pq)^10, then each pair of
                         points either ends it (pp or qq) or returns to deuce (2pq)
  best of 5:             DP over set score (home_sets, away_sets) -> total points

Exact and deterministic (no Monte Carlo noise), ~1ms per matchup.

Caveat: the mock generates matches with exactly this model, so it will look
better on mock data than on real data (real points have streaks/momentum).
"""
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from math import comb

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Match
from app.stats import shrunk_rate

MAX_DEUCE_ROUNDS = 40   # (2pq)^40 <= 1e-12: truncation error is negligible
POINT_PRIOR_STRENGTH = 50  # in points; a match is ~75, so this is light (a guess)


@dataclass(frozen=True)
class MatchDistribution:
    totals: dict[int, float]  # total points -> probability
    p_home_win: float

    def p_over(self, line: float) -> float:
        return sum(prob for total, prob in self.totals.items() if total > line)

    def mean(self) -> float:
        return sum(t * prob for t, prob in self.totals.items())


def set_outcomes(p: float) -> tuple[dict[int, float], dict[int, float]]:
    """Distribution of one set's total points, split by winner.
    Returns (home_wins: {total: prob}, away_wins: {total: prob})."""
    q = 1.0 - p
    home: dict[int, float] = {}
    away: dict[int, float] = {}
    for k in range(10):
        ways = comb(10 + k, k)
        home[11 + k] = ways * p**11 * q**k
        away[11 + k] = ways * q**11 * p**k
    deuce = comb(20, 10) * (p * q) ** 10
    for j in range(MAX_DEUCE_ROUNDS):
        reach = deuce * (2 * p * q) ** j
        home[22 + 2 * j] = reach * p * p
        away[22 + 2 * j] = reach * q * q
    return home, away


@lru_cache(maxsize=4096)
def _match_distribution(p_rounded: float) -> MatchDistribution:
    home_set, away_set = set_outcomes(p_rounded)
    # state (home_sets, away_sets) -> {points so far: prob}
    states: dict[tuple[int, int], dict[int, float]] = {(0, 0): {0: 1.0}}
    totals: dict[int, float] = {}
    p_home_win = 0.0

    for _ in range(5):  # at most 5 sets
        nxt: dict[tuple[int, int], dict[int, float]] = {}
        for (h, a), dist in states.items():
            for won, set_dist in ((True, home_set), (False, away_set)):
                score = (h + 1, a) if won else (h, a + 1)
                bucket = nxt.setdefault(score, {})
                for so_far, p1 in dist.items():
                    for pts, p2 in set_dist.items():
                        bucket[so_far + pts] = bucket.get(so_far + pts, 0.0) + p1 * p2
        states = {}
        for (h, a), dist in nxt.items():
            if h == 3 or a == 3:
                for t, prob in dist.items():
                    totals[t] = totals.get(t, 0.0) + prob
                if h == 3:
                    p_home_win += sum(dist.values())
            else:
                states[(h, a)] = dist

    return MatchDistribution(totals=totals, p_home_win=p_home_win)


def match_distribution(p_home_point: float) -> MatchDistribution:
    """Exact distribution of match total points for a given P(home wins a point)."""
    return _match_distribution(round(min(max(p_home_point, 0.0), 1.0), 4))


def matchup_point_prob(rate_home: float, rate_away: float) -> float:
    """log5 / Bradley-Terry: P(home wins a point) from each player's point-win rate
    against the field."""
    num = rate_home * (1.0 - rate_away)
    denom = num + rate_away * (1.0 - rate_home)
    return num / denom if denom else 0.5


def player_point_win_rate(session: Session, player_id: int, n: int, as_of: datetime,
                          prior: float = 0.5,
                          prior_strength: float = POINT_PRIOR_STRENGTH) -> tuple[float, int]:
    """Share of points the player won over their last n finished matches before as_of,
    shrunk toward `prior`. Returns (rate, points_played)."""
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
        .options(selectinload(Match.sets))
    )
    won = played = 0
    for m in session.scalars(stmt):
        is_home = m.home_player_id == player_id
        for s in m.sets:
            won += s.home_points if is_home else s.away_points
            played += s.home_points + s.away_points
    return shrunk_rate(won, played, prior, prior_strength), played


def matchup(session: Session, home_id: int, away_id: int, as_of: datetime,
            n: int = 20) -> tuple[MatchDistribution, dict]:
    """Model distribution for a matchup plus the inputs used (for prediction logging)."""
    rate_h, pts_h = player_point_win_rate(session, home_id, n, as_of)
    rate_a, pts_a = player_point_win_rate(session, away_id, n, as_of)
    p = matchup_point_prob(rate_h, rate_a)
    inputs = {"home_point_rate": rate_h, "away_point_rate": rate_a,
              "home_points_seen": pts_h, "away_points_seen": pts_a,
              "p_home_point": p, "n_matches": n}
    return match_distribution(p), inputs
