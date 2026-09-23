import math
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
# Match-loading helpers
# ---------------------------------------------------------------------------

def _player_matches(session: Session, player_id: int, n: int,
                    as_of: datetime) -> list[Match]:
    """Return the player's last n finished matches before as_of, sets preloaded."""
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


def _set_wins(match: Match, player_id: int) -> list[bool]:
    """Per-set boolean: did the player win each set (in set-number order)?"""
    is_home = match.home_player_id == player_id
    return [
        (s.home_points > s.away_points) if is_home else (s.away_points > s.home_points)
        for s in sorted(match.sets, key=lambda x: x.set_number)
    ]


def _player_won_match(match: Match, player_id: int) -> bool:
    if match.home_player_id == player_id:
        return (match.home_sets_won or 0) > (match.away_sets_won or 0)
    return (match.away_sets_won or 0) > (match.home_sets_won or 0)


# ---------------------------------------------------------------------------
# H2H
# ---------------------------------------------------------------------------

def h2h_record(
    session: Session, player_a_id: int, player_b_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n finished H2H matches before as_of, how many did player_a win?
    Returns (wins_a, total).
    """
    stmt = (
        select(Match)
        .where(
            or_(
                (Match.home_player_id == player_a_id) & (Match.away_player_id == player_b_id),
                (Match.home_player_id == player_b_id) & (Match.away_player_id == player_a_id),
            )
        )
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
    )
    matches = session.scalars(stmt).all()
    wins = sum(1 for m in matches if _player_won_match(m, player_a_id))
    return wins, len(matches)


# ---------------------------------------------------------------------------
# Sweep rate
# ---------------------------------------------------------------------------

def sweep_rate(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n finished matches, how many ended 3-0 (either direction)?
    Returns (sweeps, total).
    """
    matches = _player_matches(session, player_id, n, as_of)
    sweeps = sum(
        1 for m in matches
        if (m.home_sets_won == 3 and m.away_sets_won == 0)
        or (m.home_sets_won == 0 and m.away_sets_won == 3)
    )
    return sweeps, len(matches)


def league_sweep_rate(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League-wide baseline for sweep rate. Returns (sweeps, total)."""
    stmt = (
        select(
            func.count(),
            func.count().filter(
                ((Match.home_sets_won == 3) & (Match.away_sets_won == 0))
                | ((Match.home_sets_won == 0) & (Match.away_sets_won == 3))
            )
        )
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
    )
    total, sweeps = session.execute(stmt).one()
    return sweeps, total


# ---------------------------------------------------------------------------
# Set-1 conditionals
# ---------------------------------------------------------------------------

def win_after_set1_win(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n matches where player won set 1, how many did player win the match?
    Returns (match_wins, total_where_won_set1).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = wins = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if sw and sw[0]:
            total += 1
            if _player_won_match(m, player_id):
                wins += 1
    return wins, total


def win_after_set1_loss(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n matches where player lost set 1, how many did player win the match?
    Returns (match_wins, total_where_lost_set1).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = wins = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if sw and not sw[0]:
            total += 1
            if _player_won_match(m, player_id):
                wins += 1
    return wins, total


def league_win_after_set1_win(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League baseline: matches where set-1 winner won the match.
    Returns (set1_winner_also_won_match, total_finished).
    """
    matches = session.scalars(
        select(Match)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .options(selectinload(Match.sets))
    ).all()
    total = wins = 0
    for m in matches:
        if not m.sets:
            continue
        s1 = min(m.sets, key=lambda s: s.set_number)
        home_won_set1 = s1.home_points > s1.away_points
        home_won_match = (m.home_sets_won or 0) > (m.away_sets_won or 0)
        total += 1
        if home_won_set1 == home_won_match:
            wins += 1
    return wins, total


def league_win_after_set1_loss(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League baseline: matches where the set-1 loser came back to win.
    Returns (comebacks, total_finished).
    """
    matches = session.scalars(
        select(Match)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .options(selectinload(Match.sets))
    ).all()
    total = comebacks = 0
    for m in matches:
        if not m.sets:
            continue
        s1 = min(m.sets, key=lambda s: s.set_number)
        home_won_set1 = s1.home_points > s1.away_points
        home_won_match = (m.home_sets_won or 0) > (m.away_sets_won or 0)
        total += 1
        if home_won_set1 != home_won_match:
            comebacks += 1
    return comebacks, total


# ---------------------------------------------------------------------------
# After going down 1-1
# ---------------------------------------------------------------------------

def win_after_going_down_1_1(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """When player led 1-0 then lost set 2 (now 1-1), how often did they win?
    Returns (match_wins, total_where_led_1_0_then_went_1_1).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = wins = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if len(sw) >= 2 and sw[0] and not sw[1]:
            total += 1
            if _player_won_match(m, player_id):
                wins += 1
    return wins, total


# ---------------------------------------------------------------------------
# Set 3 win rate when up 2-0
# ---------------------------------------------------------------------------

def set3_win_rate_when_up_2_0(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """When player was up 2-0 (won sets 1 and 2), how often did they win set 3?
    Returns (set3_wins, total_where_up_2_0).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = wins = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if len(sw) >= 3 and sw[0] and sw[1]:
            total += 1
            if sw[2]:
                wins += 1
    return wins, total


def league_set3_win_rate_when_up_2_0(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League baseline: of matches that had a 2-0 leader going into set 3,
    how often did that leader win set 3?
    Returns (set3_wins_by_leader, total_where_had_2_0_leader).
    """
    matches = session.scalars(
        select(Match)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .options(selectinload(Match.sets))
    ).all()
    total = wins = 0
    for m in matches:
        ss = sorted(m.sets, key=lambda s: s.set_number)
        if len(ss) < 3:
            continue
        h1 = ss[0].home_points > ss[0].away_points
        h2 = ss[1].home_points > ss[1].away_points
        h3 = ss[2].home_points > ss[2].away_points
        if h1 and h2:          # home up 2-0
            total += 1
            wins += int(h3)
        elif not h1 and not h2:  # away up 2-0
            total += 1
            wins += int(not h3)
    return wins, total


# ---------------------------------------------------------------------------
# Set 5 win rate
# ---------------------------------------------------------------------------

def set5_win_rate(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n 5-set matches the player appeared in, how often did they win?
    Returns (wins, total_set5_appearances).
    """
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .where(Match.home_sets_won + Match.away_sets_won == 5)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
    )
    matches = session.scalars(stmt).all()
    wins = sum(1 for m in matches if _player_won_match(m, player_id))
    return wins, len(matches)


def league_set5_win_rate(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League baseline: home player win rate in 5-set matches (should be ~0.5).
    Returns (home_wins, total_5set_matches).
    """
    stmt = (
        select(
            func.count(),
            func.count().filter(Match.home_sets_won > Match.away_sets_won)
        )
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .where(Match.home_sets_won + Match.away_sets_won == 5)
    )
    total, home_wins = session.execute(stmt).one()
    return home_wins, total


# ---------------------------------------------------------------------------
# Average total points
# ---------------------------------------------------------------------------

def avg_total_points(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Sum of total points across last n finished matches.
    Returns (total_points_sum, total_matches). Caller divides for average.
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = sum(
        sum(s.home_points + s.away_points for s in m.sets)
        for m in matches
    )
    return total, len(matches)


# ---------------------------------------------------------------------------
# Split rate
# ---------------------------------------------------------------------------

def split_rate(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """Of last n matches with at least 2 sets, how often was it 1-1 after two sets?
    Returns (splits, total).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = splits = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if len(sw) >= 2:
            total += 1
            if sw[0] != sw[1]:
                splits += 1
    return splits, total


def split_when_lost_set1(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """When player lost set 1, how often did they win set 2 (comeback to 1-1)?
    Returns (set2_wins, total_where_lost_set1).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = wins = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if len(sw) >= 2 and not sw[0]:
            total += 1
            if sw[1]:
                wins += 1
    return wins, total


def split_allowed_after_set1_win(
    session: Session, player_id: int, n: int, as_of: datetime
) -> tuple[int, int]:
    """When player won set 1, how often did they lose set 2 (opponent back to 1-1)?
    Returns (set2_losses, total_where_won_set1).
    """
    matches = _player_matches(session, player_id, n, as_of)
    total = losses = 0
    for m in matches:
        sw = _set_wins(m, player_id)
        if len(sw) >= 2 and sw[0]:
            total += 1
            if not sw[1]:
                losses += 1
    return losses, total


def league_split_rate(
    session: Session, league_id: int, as_of: datetime
) -> tuple[int, int]:
    """League baseline for split rate (1-1 after two sets).
    Returns (splits, total).
    """
    matches = session.scalars(
        select(Match)
        .where(Match.league_id == league_id)
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .options(selectinload(Match.sets))
    ).all()
    total = splits = 0
    for m in matches:
        ss = sorted(m.sets, key=lambda s: s.set_number)
        if len(ss) >= 2:
            total += 1
            s1h = ss[0].home_points > ss[0].away_points
            s2h = ss[1].home_points > ss[1].away_points
            if s1h != s2h:
                splits += 1
    return splits, total
