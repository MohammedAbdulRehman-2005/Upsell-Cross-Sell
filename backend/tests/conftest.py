"""Pytest configuration and isolated database fixtures for Stage 2."""
from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: E402, F401
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402


def _resolve_test_database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Use an isolated PostgreSQL test database if reachable, else isolated file-backed SQLite."""
    explicit_test_url = os.getenv("TEST_DATABASE_URL")
    if explicit_test_url:
        return explicit_test_url

    # Probe local PostgreSQL test database on localhost:5433 (or POSTGRES_PORT) if running
    pg_user = os.getenv("POSTGRES_USER", "plural_user")
    pg_password = os.getenv("POSTGRES_PASSWORD", "plural_local_dev_pw")
    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = os.getenv("POSTGRES_PORT", "5433")
    admin_url = f"postgresql+psycopg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/postgres"
    test_db_name = "plural_upsell_test"
    test_pg_url = f"postgresql+psycopg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{test_db_name}"

    try:
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 2})
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                {"dbname": test_db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
        admin_engine.dispose()
        return test_pg_url
    except Exception:
        db_file = tmp_path_factory.mktemp("test_db") / "isolated_test.sqlite3"
        return f"sqlite+pysqlite:///{db_file}"


@pytest.fixture(scope="session")
def test_database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    return _resolve_test_database_url(tmp_path_factory)


@pytest.fixture(scope="session")
def test_settings(test_database_url: str) -> Settings:
    return Settings(
        app_name="Plural Upsell & Cross-Sell Service (Test)",
        app_version="0.2.0-test",
        environment="testing",
        debug=False,
        log_level="DEBUG",
        database_url=test_database_url,
        cors_origins=["http://testserver"],
    )


@pytest.fixture(scope="session")
def test_engine(test_database_url: str) -> Generator[Engine, None, None]:
    connect_args = {"check_same_thread": False} if test_database_url.startswith("sqlite") else {}
    engine = create_engine(test_database_url, connect_args=connect_args, pool_pre_ping=True)

    if test_database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fks(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    """Provide a clean database state for each test function."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(test_settings: Settings, db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to the isolated test database session."""
    app = create_app(test_settings)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_get_settings() -> Settings:
        return test_settings

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = _override_get_settings

    with TestClient(app) as test_client:
        yield test_client
