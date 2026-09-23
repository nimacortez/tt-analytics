"""Elo ratings, computed chronologically from finished matches.

Run:  python -m app.elo          (recomputes everything from scratch)
      python -m app.elo --check  (also prints rank correlation vs mock true_skill)

Design: full recompute, not incremental. Wipes player_ratings and rebuilds in
one pass so the table is always consistent. Fast enough for mock-scale data;
add incremental updates when real-time latency matters.
"""
import argparse
from datetime import datetime

from scipy.stats import spearmanr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Match, Player, PlayerRating

DEFAULT_RATING = 1500.0
K = 32.0


def _expected(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))


def compute_elo(session: Session) -> dict[int, float]:
    """Recompute all Elo ratings from scratch. Returns {player_id: final_rating}."""
    session.execute(delete(PlayerRating))

    matches = session.scalars(
        select(Match)
        .where(Match.status == "finished")
        .order_by(Match.scheduled_at.asc())
    ).all()

    ratings: dict[int, float] = {}
    rows: list[PlayerRating] = []

    for m in matches:
        h_id, a_id = m.home_player_id, m.away_player_id
        h_before = ratings.get(h_id, DEFAULT_RATING)
        a_before = ratings.get(a_id, DEFAULT_RATING)

        home_won = (m.home_sets_won or 0) > (m.away_sets_won or 0)
        h_score = 1.0 if home_won else 0.0

        e_home = _expected(h_before, a_before)
        h_after = h_before + K * (h_score - e_home)
        a_after = a_before + K * ((1.0 - h_score) - (1.0 - e_home))

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


def current_rating(session: Session, player_id: int, as_of: datetime) -> float:
    """Latest rating_after for this player in matches before as_of.
    Returns DEFAULT_RATING if the player has never been rated."""
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
    """Print Spearman rank correlation between final Elo and mock true_skill.
    Operates per-league so the different skill pools don't blur the signal."""
    from app.providers.mock import MockProvider
    provider = MockProvider()

    with SessionLocal() as session:
        players = session.scalars(select(Player)).all()
        pid_to_api = {p.id: p.api_id for p in players}

    by_league: dict[str, list[tuple[float, float]]] = {}
    for pid, elo in ratings.items():
        api_id = pid_to_api.get(pid, "")
        # api_id format: "mock-{league}-p{nn}"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="also print rank correlation vs mock true_skill")
    args = parser.parse_args()

    with SessionLocal() as session:
        ratings = compute_elo(session)

    if args.check:
        check_vs_true_skill(ratings)
