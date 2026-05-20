from logging.config import fileConfig
from os import environ

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` keeps the application's own
    # loggers (notably `agentatlas.request`) usable after alembic
    # configures its own. The default `True` would otherwise mark
    # every pre-existing logger as `disabled=True`, silently dropping
    # request log lines in any test that runs migrations before
    # exercising the FastAPI app.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

database_url = environ.get("AGENTATLAS_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
