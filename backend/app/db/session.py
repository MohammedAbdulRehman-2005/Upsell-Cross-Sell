"""Database engine, session factory, FastAPI dependency, and connectivity check."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy 2.x Engine from application settings."""
    active_settings = settings or get_settings()
    uri = active_settings.sqlalchemy_database_uri
    engine_kwargs: dict[str, Any] = {
        "echo": active_settings.db_echo,
        "pool_pre_ping": True,
    }
    if not uri.startswith("sqlite"):
        engine_kwargs["pool_size"] = active_settings.db_pool_size
        engine_kwargs["max_overflow"] = active_settings.db_max_overflow
    return create_engine(uri, **engine_kwargs)


engine: Engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a managed SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection(db: Session) -> tuple[bool, str | None]:
    """Execute a lightweight SELECT 1 probe to verify database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # pragma: no cover - defensive error capture
        return False, str(exc)
