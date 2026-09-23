"""Create tables. Run: python -m app.init_db  (add --reset to drop everything first)

While the schema is still changing daily, drop-and-recreate is fine (mock data
regenerates in seconds). Once real data is flowing, switch to Alembic migrations.
"""
import sys

from app import models  # noqa: F401  (importing registers the tables)
from app.db import Base, engine

if __name__ == "__main__":
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
        print("Dropped all tables")
    Base.metadata.create_all(engine)
    print("Tables ready")
