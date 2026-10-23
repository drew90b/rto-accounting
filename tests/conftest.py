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

import os

# Must be set before the app is imported: app.main refuses to start without a secret.
os.environ.setdefault("SESSION_SECRET", "test-secret-test-secret-test-secret-0123456789")
os.environ.setdefault("ALLOWED_ORIGINS", "http://testserver")

import pytest
from sqlalchemy import create_engine, event
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

    # pysqlite's default transaction handling breaks SAVEPOINTs: it never emits
    # BEGIN itself, so a "committed" savepoint leaks past the outer rollback.
    # This is SQLAlchemy's documented recipe for making SAVEPOINT work.
    @event.listens_for(eng, "connect")
    def _no_implicit_begin(dbapi_conn, _record):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _emit_begin(conn):
        conn.exec_driver_sql("BEGIN")

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

TEST_PASSWORD = "test password 12345"


@pytest.fixture
def _override_db(db_connection):
    """Point the app's get_db at the per-test connection (see module docstring)."""
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
    yield TestSession
    fastapi_app.dependency_overrides.pop(get_db, None)


def _new_client():
    # Browsers send Origin on every POST; the app's CSRF check requires it.
    return TestClient(
        fastapi_app, raise_server_exceptions=True,
        headers={"Origin": "http://testserver"},
    )


def _signed_in_client(_override_db, username, role):
    """A client signed in through the real POST /login. Auth is never bypassed."""
    from app.models.enums import UserRole
    from app.services import user_service

    session = _override_db()
    user_service.create_user(
        session, username=username, display_name=username.title(), role=UserRole(role),
        password=TEST_PASSWORD, must_change_password=False,
    )
    session.commit()
    session.close()

    c = _new_client()
    c.__enter__()
    r = c.post("/login", data={"username": username, "password": TEST_PASSWORD},
               follow_redirects=False)
    assert r.status_code == 303, "test sign-in failed"
    return c


@pytest.fixture
def anon_client(_override_db):
    """Not signed in."""
    with _new_client() as c:
        yield c


@pytest.fixture
def admin_client(_override_db):
    c = _signed_in_client(_override_db, "testadmin", "admin")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def employee_client(_override_db):
    c = _signed_in_client(_override_db, "testemployee", "employee")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def client(admin_client):
    """The default client for route tests: signed in as an administrator."""
    return admin_client


@pytest.fixture(autouse=True)
def _receipt_storage(tmp_path, monkeypatch):
    """Tests must never write into the real storage/receipts folder."""
    import app.services.document_service as document_service
    monkeypatch.setattr(document_service, "STORAGE_DIR", str(tmp_path / "receipts"))
