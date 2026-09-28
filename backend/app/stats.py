import math
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Match, MatchSet


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

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


def summarize(hits: int, n: int, baseline: float, prior_strength: float = 10) -> dict:
    """Wilson CI, shrinkage, and small-sample flag for any (hits, n) pair."""
    low, high = wilson_interval(hits, n)
    return {
        "hits": hits,
        "n": n,
        "rate": round(hits / n, 3) if n else None,
        "ci_95": [round(low, 3), round(high, 3)],
        "shrunk_rate": round(shrunk_rate(hits, n, baseline, prior_strength), 3),
        "small_sample": n < SMALL_SAMPLE,
    }


# ---------------------------------------------------------------------------
# Over/under
# ---------------------------------------------------------------------------

def over_hit_rate(session: Session, player_id: int, line: float, n: int,
                  as_of: datetime) -> tuple[int, int]:
    """Of the player's last n finished matches before as_of,
    how many had total points over `line`?
    Returns (hits, sample_size)."""
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
        .options(selectinload(Match.sets))
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


def over_summary(session: Session, player_id: int, league_id: int, line: float,
                 n: int, as_of: datetime) -> dict:
    hits, sample = over_hit_rate(session, player_id, line, n, as_of)
    lg_hits, lg_total = league_over_rate(session, league_id, line, as_of)
    baseline = lg_hits / lg_total if lg_total else 0.5
    result = summarize(hits, sample, baseline)
    result["league_rate"] = round(baseline, 3)
    return result


# ---------------------------------------------------------------------------
# Set-sequence stats: sweeps, set conditionals, splits
#
# Every stat here is a rule over one player's view of a finished match:
#   sets = [won set 1?, won set 2?, ...], won = won the match?
# The rule returns None if the match doesn't qualify (not in the denominator),
# else True/False for hit/miss.
#
# Player stat: last n finished matches before as_of, then keep the qualifying
# ones, so conditional stats have n_qualifying <= n. Same window for every stat.
# League baseline: every finished league match before as_of, seen from BOTH
# players' sides, so its n counts player-matches (up to 2x matches).
# ---------------------------------------------------------------------------

SetRule = Callable[[list[bool], bool], bool | None]


def _two(sets: list[bool]) -> bool:
    return len(sets) >= 2


SET_STATS: dict[str, SetRule] = {
    # Match ended 3-0 either way.
    "sweep": lambda s, won: len(s) == 3,
    # Won the match after winning / losing set 1.
    "win_after_set1_win": lambda s, won: won if s[0] else None,
    "win_after_set1_loss": lambda s, won: won if not s[0] else None,
    # Led 1-0, got pegged back to 1-1: won the match anyway?
    "win_after_1_1_from_1_0": lambda s, won: won if _two(s) and s[0] and not s[1] else None,
    # Up 2-0: took set 3 (i.e. closed out the sweep)?
    "set3_win_when_up_2_0": lambda s, won: s[2] if len(s) >= 3 and s[0] and s[1] else None,
    # Went to a decider at 2-2: won set 5?
    "set5_win": lambda s, won: won if len(s) == 5 else None,
    # Split = the loser of set 1 wins set 2 (1-1 after two sets).
    "split": lambda s, won: s[0] != s[1] if _two(s) else None,
    # Player lost set 1, won set 2.
    "split_after_losing_set1": lambda s, won: s[1] if _two(s) and not s[0] else None,
    # Player won set 1, lost set 2.
    "split_allowed_after_winning_set1": lambda s, won: not s[1] if _two(s) and s[0] else None,
}


def _set_wins(match: Match, player_id: int) -> list[bool]:
    """Did the player win each set, in set order?"""
    is_home = match.home_player_id == player_id
    return [(s.home_points > s.away_points) == is_home
            for s in sorted(match.sets, key=lambda x: x.set_number)]


def _player_won_match(match: Match, player_id: int) -> bool:
    home_won = (match.home_sets_won or 0) > (match.away_sets_won or 0)
    return home_won == (match.home_player_id == player_id)


def _player_matches(session: Session, player_id: int, n: int,
                    as_of: datetime) -> list[Match]:
    """The player's last n finished matches before as_of, sets preloaded."""
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
        .options(selectinload(Match.sets))
    )
    return list(session.scalars(stmt).all())


def _league_matches(session: Session, league_id: int, as_of: datetime) -> list[Match]:
    stmt = (
        select(Match)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .options(selectinload(Match.sets))
    )
    return list(session.scalars(stmt).all())


def _apply(rule: SetRule, views: list[tuple[list[bool], bool]]) -> tuple[int, int]:
    hits = n = 0
    for sets, won in views:
        if not sets:
            continue
        result = rule(sets, won)
        if result is None:
            continue
        n += 1
        hits += bool(result)
    return hits, n


def player_set_stat(session: Session, stat: str, player_id: int, n: int,
                    as_of: datetime) -> tuple[int, int]:
    """(hits, qualifying matches) for SET_STATS[stat] over the player's last n matches."""
    views = [(_set_wins(m, player_id), _player_won_match(m, player_id))
             for m in _player_matches(session, player_id, n, as_of)]
    return _apply(SET_STATS[stat], views)


def _league_views(session: Session, league_id: int,
                  as_of: datetime) -> list[tuple[list[bool], bool]]:
    return [(_set_wins(m, pid), _player_won_match(m, pid))
            for m in _league_matches(session, league_id, as_of)
            for pid in (m.home_player_id, m.away_player_id)]


def league_set_stat(session: Session, stat: str, league_id: int,
                    as_of: datetime) -> tuple[int, int]:
    """(hits, qualifying player-matches) for SET_STATS[stat], league-wide baseline."""
    return _apply(SET_STATS[stat], _league_views(session, league_id, as_of))


def _summary_with_baseline(hits: int, sample: int, lg_hits: int, lg_n: int) -> dict:
    baseline = lg_hits / lg_n if lg_n else 0.5
    result = summarize(hits, sample, baseline)
    result["league_rate"] = round(baseline, 3)
    return result


def set_stat_summary(session: Session, stat: str, player_id: int, league_id: int,
                     n: int, as_of: datetime) -> dict:
    hits, sample = player_set_stat(session, stat, player_id, n, as_of)
    return _summary_with_baseline(hits, sample, *league_set_stat(session, stat, league_id, as_of))


def all_set_stat_summaries(session: Session, player_ids: list[int], league_id: int,
                           n: int, as_of: datetime) -> dict[int, dict[str, dict]]:
    """Every SET_STATS summary for several players, loading the league only once.
    Returns {player_id: {stat: summary}}."""
    views = _league_views(session, league_id, as_of)
    league = {stat: _apply(rule, views) for stat, rule in SET_STATS.items()}
    out: dict[int, dict[str, dict]] = {}
    for pid in player_ids:
        mine = [(_set_wins(m, pid), _player_won_match(m, pid))
                for m in _player_matches(session, pid, n, as_of)]
        out[pid] = {stat: _summary_with_baseline(*_apply(rule, mine), *league[stat])
                    for stat, rule in SET_STATS.items()}
    return out


# ---------------------------------------------------------------------------
# Head to head
# ---------------------------------------------------------------------------

def h2h_record(session: Session, player_a_id: int, player_b_id: int, n: int,
               as_of: datetime) -> tuple[int, int]:
    """Of the last n finished meetings before as_of, how many did player_a win?
    Returns (wins_a, meetings). Baseline for shrinkage is 0.5 (or an Elo p_win)."""
    stmt = (
        select(Match)
        .where(or_(
            (Match.home_player_id == player_a_id) & (Match.away_player_id == player_b_id),
            (Match.home_player_id == player_b_id) & (Match.away_player_id == player_a_id),
        ))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
    )
    matches = session.scalars(stmt).all()
    return sum(_player_won_match(m, player_a_id) for m in matches), len(matches)


# ---------------------------------------------------------------------------
# Average total points (a mean, not a rate)
# ---------------------------------------------------------------------------

def _match_total(m: Match) -> int:
    return sum(s.home_points + s.away_points for s in m.sets)


def avg_total_points(session: Session, player_id: int, n: int,
                     as_of: datetime) -> tuple[int, int]:
    """(sum of total points, matches) over the player's last n finished matches."""
    matches = _player_matches(session, player_id, n, as_of)
    return sum(_match_total(m) for m in matches), len(matches)


def league_avg_total_points(session: Session, league_id: int,
                            as_of: datetime) -> tuple[int, int]:
    """(sum of total points, matches) league-wide before as_of."""
    totals = (
        select(MatchSet.match_id,
               func.sum(MatchSet.home_points + MatchSet.away_points).label("total"))
        .group_by(MatchSet.match_id)
        .subquery()
    )
    stmt = (
        select(func.coalesce(func.sum(totals.c.total), 0), func.count())
        .select_from(Match)
        .join(totals, totals.c.match_id == Match.id)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
    )
    total, count = session.execute(stmt).one()
    return int(total), count


def avg_total_summary(session: Session, player_id: int, league_id: int, n: int,
                      as_of: datetime, prior_strength: float = 10) -> dict:
    """Mean total points, shrunk toward the league mean like the rates are.
    No Wilson CI: that's for proportions, not means."""
    total, sample = avg_total_points(session, player_id, n, as_of)
    lg_total, lg_n = league_avg_total_points(session, league_id, as_of)
    baseline = lg_total / lg_n if lg_n else 0.0
    return {
        "n": sample,
        "mean": round(total / sample, 2) if sample else None,
        "shrunk_mean": round(shrunk_rate(total, sample, baseline, prior_strength), 2),
        "league_mean": round(baseline, 2),
        "small_sample": sample < SMALL_SAMPLE,
    }
