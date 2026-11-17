"""Database connection (SQLAlchemy 2.0). Only db/tables.py and db/repo.py import this.

  DATABASE_URL set    Neon Postgres in production, e.g. postgres://user:pw@host/db?sslmode=require.
                      "postgres://" and "postgresql://" are rewritten to "postgresql+psycopg://" (psycopg 3 driver).
  DATABASE_URL empty  a SQLite file, backend/data/app.db, for local runs. The folder is created here.

pool_pre_ping=True: Neon closes idle connections; each checkout is tested first so a stale one is replaced
instead of failing a request. The URL (it holds the password) is never logged.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.common.paths import BACKEND_DIR

SQLITE_PATH = BACKEND_DIR / "data" / "app.db"


def database_url(raw: str | None = None) -> str:
    """The SQLAlchemy URL to use. raw defaults to the DATABASE_URL environment variable."""
    url = (os.getenv("DATABASE_URL", "") if raw is None else raw).strip()
    if not url:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{SQLITE_PATH.as_posix()}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """One engine per process (cached). Tests call reset_engine() after changing DATABASE_URL."""
    url = database_url()
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # foreign keys are off by default in SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def reset_engine() -> None:
    """Forget the cached engine (tests, or after DATABASE_URL changes)."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    _session_factory.cache_clear()


@contextmanager
def get_session() -> Iterator[Session]:
    """A session that commits when the block ends normally and rolls back on any error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
