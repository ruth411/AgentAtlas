from collections.abc import Generator
from os import environ

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

DEFAULT_DATABASE_URL = "sqlite:///./agentatlas.db"
DATABASE_URL = environ.get("AGENTATLAS_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str = DEFAULT_DATABASE_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(target_engine: Engine = engine) -> None:
    Base.metadata.create_all(bind=target_engine)


def get_db_session() -> Generator[Session]:
    with SessionLocal() as session:
        yield session
