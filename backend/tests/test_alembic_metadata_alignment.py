from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.models import Base
from tests.helpers import _normalize_db_type


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_alembic_head_matches_sqlalchemy_metadata(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'alembic-head.db'}"
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    expected_tables = set(Base.metadata.tables.keys())

    assert actual_tables == expected_tables

    for table_name in expected_tables:
        actual_columns = {
            column["name"]: {
                "type": _normalize_db_type(column["type"]),
                "nullable": column["nullable"],
            }
            for column in inspector.get_columns(table_name)
        }
        expected_columns = {
            column.name: {
                "type": _normalize_db_type(column.type),
                "nullable": column.nullable,
            }
            for column in Base.metadata.tables[table_name].columns
        }
        assert actual_columns == expected_columns, (
            f"Column drift in '{table_name}': "
            f"alembic={actual_columns} metadata={expected_columns}"
        )
