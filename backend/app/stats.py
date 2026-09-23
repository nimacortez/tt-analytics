from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Match


def over_hit_rate(session: Session, player_id: int, line: float, n: int,
                  as_of: datetime) -> tuple[int, int]:
    """Of the player's last n finished matches before as_of,
    how many had total points over `line`?
    Returns (hits, sample_size)."""
    # 1. Select matches where the player is home OR away
    # 2. Only status == "finished"
    # 3. Only scheduled_at < as_of
    # 4. Newest first, take n
    # 5. For each match, total = sum of all set points; count how many are > line