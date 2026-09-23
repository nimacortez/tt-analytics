"""
Elo ratings. Run: python -m app.ratings [--k 32] [--check]
--check also prints Spearman rank correlation vs mock true_skill().
"""
import argparse
from datetime import datetime

from scipy.stats import spearmanr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Match, Player, PlayerRating

DEFAULT_RATING = 1500.0


def p_win(rating_a: float, rating_b: float) -> float:
    """Standard Elo win probability: P(a beats b)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def rebuild(session: Session, k: float = 32.0) -> dict[int, float]:
    """Wipe player_ratings and rebuild from scratch in chronological order.

    Returns {player_id: final_rating}.
    """
    session.execute(delete(PlayerRating))

    matches = session.scalars(
        select(Match)
        .where(Match.status == "finished")
        .order_by(Match.scheduled_at.asc())
    ).all()

    ratings: dict[int, float] = {}
    rows: list[PlayerRating] = []

    for m in matches:
        h_id = m.home_player_id
        a_id = m.away_player_id
        h_before = ratings.get(h_id, DEFAULT_RATING)
        a_before = ratings.get(a_id, DEFAULT_RATING)

        home_won = (m.home_sets_won or 0) > (m.away_sets_won or 0)
        h_score = 1.0 if home_won else 0.0

        e_home = p_win(h_before, a_before)
        h_after = h_before + k * (h_score - e_home)
        a_after = a_before + k * ((1.0 - h_score) - (1.0 - e_home))

        ratings[h_id] = h_after
        ratings[a_id] = a_after

        rows.append(PlayerRating(player_id=h_id, match_id=m.id,
                                 rating_before=h_before, rating_after=h_after))
        rows.append(PlayerRating(player_id=a_id, match_id=m.id,
                                 rating_before=a_before, rating_after=a_after))

    session.add_all(rows)
    session.commit()
    print(f"Processed {len(matches)} matches, rated {len(ratings)} players "
          f"({len(rows)} rating rows)")
    return ratings


def rating_as_of(session: Session, player_id: int, as_of: datetime) -> float:
    """Return the rating_after from the most recent match before as_of (strict <).

    Returns DEFAULT_RATING if no qualifying rows exist.
    """
    row = session.scalars(
        select(PlayerRating)
        .join(Match, Match.id == PlayerRating.match_id)
        .where(PlayerRating.player_id == player_id)
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(1)
    ).first()
    return row.rating_after if row else DEFAULT_RATING


def check_vs_true_skill(ratings: dict[int, float]) -> None:
    """Print per-league Spearman ρ between final Elo and mock true_skill."""
    from app.providers.mock import MockProvider
    provider = MockProvider()

    with SessionLocal() as session:
        players = session.scalars(select(Player)).all()
        pid_to_api = {p.id: p.api_id for p in players}

    by_league: dict[str, list[tuple[float, float]]] = {}
    for pid, elo in ratings.items():
        api_id = pid_to_api.get(pid, "")
        parts = api_id.rsplit("-p", 1)
        if len(parts) != 2:
            continue
        league_api_id = parts[0]
        try:
            skill = provider.true_skill(api_id)
        except KeyError:
            continue
        by_league.setdefault(league_api_id, []).append((elo, skill))

    print("\nSpearman rank correlation: Elo vs true_skill")
    print(f"{'League':<30} {'n':>4}  {'rho':>6}  {'p':>8}")
    print("-" * 55)
    all_elo, all_skill = [], []
    for league_api_id, pairs in sorted(by_league.items()):
        elos, skills = zip(*pairs)
        rho, p = spearmanr(elos, skills)
        all_elo.extend(elos)
        all_skill.extend(skills)
        print(f"{league_api_id:<30} {len(pairs):>4}  {rho:>6.3f}  {p:>8.4f}")
    rho_all, p_all = spearmanr(all_elo, all_skill)
    print("-" * 55)
    print(f"{'ALL':<30} {len(all_elo):>4}  {rho_all:>6.3f}  {p_all:>8.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild Elo ratings")
    parser.add_argument("--k", type=float, default=32.0, help="K-factor (default 32)")
    parser.add_argument("--check", action="store_true",
                        help="print Spearman correlation vs mock true_skill")
    args = parser.parse_args()

    with SessionLocal() as session:
        final_ratings = rebuild(session, k=args.k)

    if args.check:
        check_vs_true_skill(final_ratings)
