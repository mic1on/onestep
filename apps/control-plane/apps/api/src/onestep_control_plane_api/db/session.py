from __future__ import annotations

from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.base import Base


def create_engine_from_url(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = sa.create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=not is_sqlite,
    )

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = create_engine_from_url(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db(bind: Engine | None = None) -> None:
    import onestep_control_plane_api.db.models  # noqa: F401

    Base.metadata.create_all(bind or engine)


def get_db_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
