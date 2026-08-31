"""Shared pytest fixtures.

Integration tests run against a real PostgreSQL database — the schema relies
on generated columns, GIN indexes and a custom text-search configuration, none
of which SQLite could stand in for honestly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://eaa:devpassword@127.0.0.1:5432/email_assistant_test",
)


def _database_available() -> bool:
    try:
        engine = create_engine(TEST_DB_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _database_available()
requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="test PostgreSQL not reachable")


@pytest.fixture(scope="session")
def db_engine():
    if not DB_AVAILABLE:
        pytest.skip("test PostgreSQL not reachable")
    # Migrations are the only way the schema is ever created — including here,
    # so the tests exercise the same DDL that production will run.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={TEST_DB_URL}", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
    engine = create_engine(TEST_DB_URL, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Iterator[Session]:
    """A session wrapped in a transaction that is rolled back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" lets the code under test commit
    # for real — exactly as it does in production — while this outer
    # transaction still rolls the whole test back afterwards.
    session = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def storage_dir() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def local_storage(storage_dir):
    from app.services.storage import LocalAttachmentStorage

    return LocalAttachmentStorage(storage_dir)


@pytest.fixture
def account(db_session: Session):
    """A mailbox with two owned addresses."""
    from app.db.models import MailboxAccount, MailboxAddress

    acc = MailboxAccount(
        email=f"peter+{uuid.uuid4().hex[:8]}@foxgroup.sk",
        display_name="Peter",
        sync_start_date=date(2025, 1, 1),
        is_active=True,
    )
    acc.email = "peter@foxgroup.sk"
    db_session.add(acc)
    db_session.flush()
    db_session.add_all(
        [
            MailboxAddress(
                account_id=acc.id, address="peter@foxgroup.sk", is_primary=True, source="primary"
            ),
            MailboxAddress(account_id=acc.id, address="info@foxgroup.sk", source="send_as"),
        ]
    )
    db_session.flush()
    db_session.refresh(acc)
    return acc


@pytest.fixture
def settings_factory():
    """Settings built from defaults, not from whatever .env happens to say."""
    from app.core.config import Settings

    def build(**overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    return build
