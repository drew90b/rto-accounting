"""
Shared pytest fixtures — Transactional sandbox architecture.

Schema is created once per test session. Each test runs inside an outer
rollback transaction so no state persists between tests.

Fixture hierarchy:

    engine (session-scoped)
        └── db_connection (per-test, outer rollback transaction)
                ├── db      — ORM session for unit tests (direct service calls)
                └── client  — FastAPI TestClient with get_db overridden

How isolation works:
    1. db_connection opens a real connection and begins an outer transaction.
    2. Sessions created from that connection use join_transaction_mode=
       "create_savepoint", so any session.commit() only releases a SAVEPOINT
       rather than committing the outer transaction.
    3. After each test, db_connection rolls back the outer transaction,
       reverting everything written during the test regardless of how many
       commits the code under test issued.

Data visibility within a test:
    Both db and client depend on the same db_connection instance (pytest
    resolves function-scoped fixtures once per test). SQLite does not create
    read isolation between savepoints on the same connection, so data written
    by a route handler and released from its savepoint is immediately visible
    to subsequent queries through the db session.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app as fastapi_app
import app.models  # noqa: registers all models with Base


# ---------------------------------------------------------------------------
# Session-scoped engine — schema created once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine():
    """
    Single SQLite in-memory engine shared across the entire test session.

    StaticPool guarantees all engine.connect() calls within the process
    return the same underlying physical connection, which is required for
    SQLite :memory: databases to stay populated across multiple connections.

    Schema is created once here and dropped when the session ends.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


# ---------------------------------------------------------------------------
# Per-test connection — outer rollback transaction
# ---------------------------------------------------------------------------

@pytest.fixture
def db_connection(engine):
    """
    Per-test database connection wrapped in an outer rollback transaction.

    All per-test DB fixtures depend on this. After each test the entire outer
    transaction is rolled back, reverting all changes regardless of how many
    commits occurred inside the test.
    """
    conn = engine.connect()
    txn = conn.begin()
    yield conn
    txn.rollback()
    conn.close()


# ---------------------------------------------------------------------------
# Per-test ORM session — for direct service / model calls
# ---------------------------------------------------------------------------

@pytest.fixture
def db(db_connection):
    """
    Transactional test session for unit tests that call services directly.

    join_transaction_mode="create_savepoint" means the session wraps every
    commit in a SAVEPOINT rather than committing the outer connection
    transaction. Tests that call service functions which internally call
    db.flush() or db.commit() are fully supported.
    """
    Session = sessionmaker(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
    )
    session = Session()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Per-test FastAPI HTTP client — for route-level integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_connection):
    """
    FastAPI TestClient with get_db overridden to use the per-test connection.

    Each request to a route receives a fresh session bound to db_connection
    with join_transaction_mode="create_savepoint". When the route calls
    db.commit(), only the SAVEPOINT is released — the outer test transaction
    remains open and is rolled back by db_connection at test teardown.

    Because both this fixture and db depend on the same db_connection
    instance, data written by routes is visible to verification queries
    made through db in the same test.
    """
    TestSession = sessionmaker(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=True) as c:
        yield c
    fastapi_app.dependency_overrides.pop(get_db, None)
