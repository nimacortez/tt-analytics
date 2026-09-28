import pytest
from sqlalchemy import create_engine, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from app.models import Base


@pytest.fixture
def session():
    # SQLite doesn't support JSONB; substitute JSON for the in-memory engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Patch JSONB -> JSON for SQLite before creating tables.
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as sess:
        yield sess
