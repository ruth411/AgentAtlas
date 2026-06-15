from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.models import (
    CapabilityRecord,
    ConstraintRecord,
    EffectRecord,
    SubjectRecord,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_TABLES = {"subjects", "capabilities", "constraints", "effects"}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_structured_record_imports_resolve() -> None:
    assert SubjectRecord.__tablename__ == "subjects"
    assert CapabilityRecord.__tablename__ == "capabilities"
    assert ConstraintRecord.__tablename__ == "constraints"
    assert EffectRecord.__tablename__ == "effects"


def test_structured_tables_roundtrip_through_latest_migration(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'structured-roundtrip.db'}"
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables_after_upgrade = set(inspector.get_table_names())
    assert STRUCTURED_TABLES <= tables_after_upgrade
    # Inferred effect / constraint rows carry their own derivation level so
    # the substrate never silently presents them as runtime-verified.
    constraint_columns = {col["name"] for col in inspector.get_columns("constraints")}
    effect_columns = {col["name"] for col in inspector.get_columns("effects")}
    assert "verification_level" in constraint_columns
    assert "verification_level" in effect_columns

    # Downgrade past the structured-table creation (0018) so the whole
    # structured surface roundtrips, not just the latest column addition.
    command.downgrade(cfg, "0017_add_claim_embedding")
    inspector = inspect(engine)
    tables_after_downgrade = set(inspector.get_table_names())
    assert STRUCTURED_TABLES.isdisjoint(tables_after_downgrade)

    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    tables_after_reupgrade = set(inspector.get_table_names())
    assert STRUCTURED_TABLES <= tables_after_reupgrade
