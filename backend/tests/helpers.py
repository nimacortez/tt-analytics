"""Builders for hand-made test data. Sets are (home_points, away_points) tuples."""
from datetime import datetime, timezone
from itertools import count

from app.models import League, Match, MatchSet, Player

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

_ids = count()


def make_league(session, api_id: str = "test-lg") -> League:
    lg = League(api_id=api_id, name=f"League {api_id}")
    session.add(lg)
    session.flush()
    return lg


def make_player(session, api_id: str) -> Player:
    p = Player(api_id=api_id, name=f"Player {api_id}")
    session.add(p)
    session.flush()
    return p


def make_match(session, league, home, away, scheduled_at: datetime,
               sets: list[tuple[int, int]], status: str = "finished") -> Match:
    m = Match(
        api_id=f"test-m{next(_ids)}",
        league_id=league.id,
        home_player_id=home.id,
        away_player_id=away.id,
        scheduled_at=scheduled_at,
        status=status,
        home_sets_won=sum(h > a for h, a in sets),
        away_sets_won=sum(a > h for h, a in sets),
    )
    session.add(m)
    session.flush()
    for i, (hp, ap) in enumerate(sets, 1):
        session.add(MatchSet(match_id=m.id, set_number=i, home_points=hp, away_points=ap))
    session.flush()
    return m
