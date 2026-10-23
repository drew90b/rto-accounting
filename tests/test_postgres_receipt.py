"""
Opt-in PostgreSQL check. SQLite hides type-cast problems that Postgres raises.

Skipped unless RTO_TEST_POSTGRES_URL points at a throwaway database whose name
ends in "_scratch" (the test wipes its public schema, then builds it with
alembic upgrade head). Example:

    RTO_TEST_POSTGRES_URL=postgresql+psycopg2://user@127.0.0.1:55432/rto_scratch pytest tests/test_postgres_receipt.py
"""

import io
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("RTO_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="RTO_TEST_POSTGRES_URL not set")


@pytest.fixture
def pg_client(tmp_path):
    assert URL.rsplit("/", 1)[-1].endswith("_scratch"), "refusing to wipe a database not named *_scratch"
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    root = os.path.dirname(os.path.dirname(__file__))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, check=True,
                   env={**os.environ, "DATABASE_URL": URL}, capture_output=True)

    from app.database import get_db
    from app.main import app
    from app.models.enums import UserRole
    from app.services import user_service
    import app.services.document_service as document_service

    Session = sessionmaker(bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    original_storage = document_service.STORAGE_DIR
    document_service.STORAGE_DIR = str(tmp_path / "receipts")
    s = Session()
    user_service.create_user(s, username="belinda", display_name="Belinda", role=UserRole.employee,
                             password="a long test password", must_change_password=False)
    user_service.create_user(s, username="boss", display_name="Boss", role=UserRole.admin,
                             password="a long test password", must_change_password=False)
    s.commit()
    s.close()
    try:
        with TestClient(app, headers={"Origin": "http://testserver"}) as c:
            yield c, Session
    finally:
        document_service.STORAGE_DIR = original_storage
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def login(c, username):
    r = c.post("/login", data={"username": username, "password": "a long test password"},
               follow_redirects=False)
    assert r.status_code == 303


def test_create_purchase_with_receipt_then_edit_review_reopen(pg_client):
    c, Session = pg_client
    login(c, "belinda")
    data = {"purchaser": "P", "purchase_date": "2026-11-02", "vendor": "PG Vendor", "amount": "42.50",
            "category": 'PARTS - "RTO" car parts (Repair)', "payment_source": "Cash"}
    r = c.post("/purchases/new", data=data, files={"receipt": ("../r.jpg", io.BytesIO(b"x"), "image/jpeg")},
               follow_redirects=False)
    assert r.status_code == 303, r.text
    pid = r.headers["location"].split("/")[2]
    r = c.post(f"/purchases/{pid}/edit", data={**data, "amount": "43.00"},
               files={"receipt": ("again.jpg", io.BytesIO(b"y"), "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303
    assert "History" in c.get(f"/purchases/{pid}/edit").text

    c.post("/logout")
    login(c, "boss")
    assert c.post(f"/purchases/{pid}/reviewed", follow_redirects=False).status_code == 303
    assert c.post(f"/purchases/{pid}/reopen", data={"reason": "check"}, follow_redirects=False).status_code == 303

    with Session() as s:
        actions = [r[0] for r in s.execute(text("select action from purchase_events order by id"))]
    assert actions == ["created", "receipt_added", "edited", "receipt_added", "reviewed", "reopened"]
