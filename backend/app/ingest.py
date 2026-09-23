"""Pull events from the provider and upsert them into Postgres.

Upsert = insert, or update if the api_id already exists. That makes ingestion
safe to re-run: a match you saw as 'scheduled' yesterday gets updated to
'finished' today instead of duplicated.

Run: python -m app.ingest --days 30
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import League, Match, MatchSet, Player, RawEvent
from app.providers import get_provider
from app.providers.base import ProviderEvent


def upsert(session: Session, model, values: dict, extra_updates: dict | None = None) -> int:
    """Insert a row keyed on api_id, or update it. Returns our internal id."""
    stmt = insert(model).values(**values)
    updates = {col: stmt.excluded[col] for col in values if col != "api_id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["api_id"], set_={**updates, **(extra_updates or {})}
    ).returning(model.id)
    return session.execute(stmt).scalar_one()


def ingest_event(session: Session, source: str, ev: ProviderEvent) -> None:
    session.add(RawEvent(source=source, api_id=ev.api_id, payload=ev.raw))

    league_id = upsert(session, League, {"api_id": ev.league.api_id, "name": ev.league.name})
    home_id = upsert(session, Player, {"api_id": ev.home.api_id, "name": ev.home.name})
    away_id = upsert(session, Player, {"api_id": ev.away.api_id, "name": ev.away.name})

    match_id = upsert(
        session,
        Match,
        {
            "api_id": ev.api_id,
            "league_id": league_id,
            "home_player_id": home_id,
            "away_player_id": away_id,
            "scheduled_at": ev.scheduled_at,
            "status": ev.status,
            "home_sets_won": ev.home_sets_won,
            "away_sets_won": ev.away_sets_won,
        },
        extra_updates={"updated_at": func.now()},
    )

    # Simplest correct way to sync sets: wipe and rewrite this match's sets.
    session.execute(delete(MatchSet).where(MatchSet.match_id == match_id))
    if ev.sets:
        session.execute(
            insert(MatchSet), [{"match_id": match_id, **s.model_dump()} for s in ev.sets]
        )


def run(days: int) -> None:
    provider = get_provider()
    today = datetime.now(timezone.utc).date()

    with SessionLocal() as session:
        for league in provider.get_leagues():
            count = 0
            for back in range(days, -1, -1):
                for ev in provider.get_ended(league.api_id, today - timedelta(days=back)):
                    ingest_event(session, provider.name, ev)
                    count += 1
            for ev in provider.get_upcoming(league.api_id):
                ingest_event(session, provider.name, ev)
                count += 1
            session.commit()  # one transaction per league
            print(f"{league.name}: {count} events")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="days of history to backfill")
    run(parser.parse_args().days)
