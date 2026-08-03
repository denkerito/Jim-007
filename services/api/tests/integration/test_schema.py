from pathlib import Path

from alembic import command
from alembic.config import Config


def test_alembic_schema_matches_orm_metadata(database_url: str) -> None:
    del database_url
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.check(config)
