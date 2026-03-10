"""
Integration test: POST /customers/new creates a customer record.

Uses TestClient (HTTP-level) with a fresh SQLite in-memory database per test.
A separate engine/session is used here instead of the conftest `db` fixture
because route handlers call db.commit(), which conflicts with the
connection-level transaction rollback pattern used in conftest.py.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app as fastapi_app
import app.models  # noqa: registers all models with Base


@pytest.fixture
def http_db():
    """Fresh SQLite in-memory DB per test; no shared transaction wrapper."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(http_db):
    """TestClient with get_db overridden to use the test SQLite session."""
    def override_get_db():
        try:
            yield http_db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=True) as c:
        yield c
    fastapi_app.dependency_overrides.pop(get_db, None)


def test_create_customer_redirects_and_persists(client, http_db):
    """POST /customers/new should create a record and redirect to the edit page."""
    from app.models.customer import Customer

    response = client.post(
        "/customers/new",
        data={
            "full_name": "Jane Doe",
            "phone": "555-1234",
            "email": "jane@example.com",
            "address": "123 Main St",
            "notes": "",
            "status": "active",
        },
        follow_redirects=False,
    )

    # Route returns 303 See Other on success
    assert response.status_code == 303, (
        f"Expected 303, got {response.status_code}. Body: {response.text[:500]}"
    )

    # Redirect location points to the edit page for the new record
    location = response.headers.get("location", "")
    assert "/customers/" in location and "/edit" in location, (
        f"Unexpected redirect location: {location}"
    )

    # Record must exist in the database
    customer = http_db.query(Customer).filter(Customer.full_name == "Jane Doe").first()
    assert customer is not None, "Customer record was not created in the database"

    # Human-readable ID must be set and follow the expected format
    assert customer.customer_id is not None, "customer_id should be set after create"
    assert customer.customer_id.startswith("C-"), (
        f"Expected customer_id like 'C-0001', got: {customer.customer_id}"
    )
