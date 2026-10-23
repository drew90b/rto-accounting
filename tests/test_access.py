"""Sign-in, sessions, CSRF, and default-deny route protection."""

import os
import subprocess
import sys
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth
from app.main import app as fastapi_app
from app.models.enums import UserRole
from app.models.user import User
from app.services import user_service as svc
from tests.conftest import TEST_PASSWORD

# Everything an employee may reach. Any other route must answer an employee with 403.
EMPLOYEE_ALLOWED = {
    ("GET", "/"),                                    # redirects to /purchases/new
    ("GET", "/purchases/"),
    ("GET", "/purchases/new"),
    ("POST", "/purchases/new"),
    ("GET", "/purchases/{purchase_id}/edit"),
    ("POST", "/purchases/{purchase_id}/edit"),
    ("GET", "/documents/{doc_id}/download"),         # purchase receipts only (checked per document)
    ("GET", "/account/password"),
    ("POST", "/account/password"),
    ("POST", "/logout"),
}
PUBLIC = {("GET", "/login"), ("POST", "/login"), ("POST", "/logout")}


def all_routes():
    for route in fastapi_app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, route.path


def fill(path):
    import re
    return re.sub(r"\{[^}]+\}", "1", path)


def call(client, method, path):
    return client.request(method, fill(path), follow_redirects=False)


# --- Route inventory ---------------------------------------------------------

def test_inventory_finds_the_routes():
    # Guards the test itself: if this shrinks, the walk below proves nothing.
    assert len(list(all_routes())) > 80


def test_every_route_requires_sign_in(anon_client):
    unprotected = []
    for method, path in all_routes():
        if (method, path) in PUBLIC:
            continue
        r = call(anon_client, method, path)
        if r.status_code != 303 or not r.headers["location"].startswith("/login"):
            unprotected.append((method, path, r.status_code))
    assert unprotected == []


def test_every_route_outside_the_allow_list_is_forbidden_to_employees(employee_client):
    open_routes = []
    for method, path in all_routes():
        if (method, path) in EMPLOYEE_ALLOWED or (method, path) in PUBLIC:
            continue
        r = call(employee_client, method, path)
        if r.status_code != 403:
            open_routes.append((method, path, r.status_code))
    assert open_routes == []


def test_allow_list_has_no_stale_entries():
    routes = set(all_routes())
    assert EMPLOYEE_ALLOWED <= routes


def test_api_docs_are_not_served(anon_client, admin_client):
    for c in (anon_client, admin_client):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path, follow_redirects=False).status_code in (404, 303)
    assert admin_client.get("/docs").status_code == 404
    assert admin_client.get("/openapi.json").status_code == 404


def test_employee_home_goes_to_new_purchase(employee_client):
    r = employee_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/purchases/new"


def test_admin_sees_dashboard(admin_client):
    assert admin_client.get("/").status_code == 200


def test_forbidden_page_is_friendly_for_browsers(employee_client):
    r = employee_client.get("/units/", headers={"accept": "text/html"})
    assert r.status_code == 403 and "Not allowed" in r.text


def test_employee_nav_shows_only_purchases(employee_client):
    html = employee_client.get("/purchases/").text
    assert "New Purchase" in html and "Sign out" in html
    assert "/transactions/" not in html and "/review/" not in html
    assert "/review/count" not in html


def test_admin_nav_shows_everything(admin_client):
    html = admin_client.get("/purchases/").text
    assert "/transactions/" in html and "/review/" in html and "Sign out" in html


# --- Sign in / out -----------------------------------------------------------

def make_user(db, username="belinda", role=UserRole.employee, must_change=False):
    user = svc.create_user(db, username=username, display_name=username.title(), role=role,
                           password=TEST_PASSWORD, must_change_password=must_change)
    db.commit()
    return user


def login(client, username="belinda", password=TEST_PASSWORD, **extra):
    return client.post("/login", data={"username": username, "password": password, **extra},
                       follow_redirects=False)


def test_login_success_sets_session_and_next(anon_client, db):
    make_user(db)
    r = login(anon_client, next="/purchases/new")
    assert r.status_code == 303 and r.headers["location"] == "/purchases/new"
    assert anon_client.get("/purchases/").status_code == 200


def test_session_cookie_is_httponly_and_lax(anon_client, db):
    make_user(db)
    cookie = login(anon_client).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_login_failure_message_is_identical_for_unknown_user_and_wrong_password(anon_client, db):
    make_user(db)
    a = login(anon_client, "belinda", "wrong password 123")
    b = login(anon_client, "nobody", "wrong password 123")
    assert a.status_code == b.status_code == 401
    assert "Sign-in failed" in a.text and "Sign-in failed" in b.text
    assert a.text == b.text


def test_open_redirect_via_next_is_ignored(anon_client, db):
    make_user(db)
    for bad in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = login(anon_client, next=bad)
        assert r.headers["location"] == "/", bad
        anon_client.post("/logout")


def test_login_get_with_unsafe_next_is_neutralised(anon_client):
    assert 'value="/"' in anon_client.get("/login?next=//evil.example").text


def test_lockout_after_five_failures_then_cli_style_unlock(anon_client, db):
    user = make_user(db)
    for _ in range(5):
        login(anon_client, password="wrong password 123")
    assert login(anon_client).status_code == 401  # right password, but locked
    svc.unlock_user(user)
    db.commit()
    assert login(anon_client).status_code == 303


def test_logout_ends_the_session(anon_client, db):
    make_user(db)
    login(anon_client)
    r = anon_client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


def test_logout_requires_post(anon_client):
    assert anon_client.get("/logout", follow_redirects=False).status_code == 405


def test_anonymous_get_redirects_with_next(anon_client):
    r = anon_client.get("/purchases/?vendor=abc", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=/purchases/")


# --- Sessions are re-checked on every request --------------------------------

def signed_in(anon_client, db, **kw):
    user = make_user(db, **kw)
    assert login(anon_client, user.username).status_code == 303
    return user


def test_disabling_an_account_takes_effect_on_the_next_request(anon_client, db):
    user = signed_in(anon_client, db)
    make_user(db, "boss", UserRole.admin)
    assert anon_client.get("/purchases/").status_code == 200
    svc.disable_user(db, user)
    db.commit()
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


def test_role_change_applies_on_the_next_request(anon_client, db):
    user = signed_in(anon_client, db, role=UserRole.admin)
    make_user(db, "boss", UserRole.admin)
    assert anon_client.get("/units/").status_code == 200
    svc.set_role(db, user, UserRole.employee)
    db.commit()
    # The role change also ends the session; either way the units page is no longer reachable.
    assert anon_client.get("/units/", follow_redirects=False).status_code in (303, 403)


def test_password_reset_ends_other_sessions(anon_client, db):
    user = signed_in(anon_client, db)
    svc.set_password(user, "a brand new password", must_change=False)
    db.commit()
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


def test_session_expires_after_max_hours(anon_client, db, monkeypatch):
    signed_in(anon_client, db)
    assert anon_client.get("/purchases/").status_code == 200
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 13 * 3600)
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


def test_tampered_cookie_is_rejected(anon_client, db):
    signed_in(anon_client, db)
    anon_client.cookies.set("rto_session", "garbage.value.here")
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


# --- Forced password change --------------------------------------------------

def test_temporary_password_must_be_changed_first(anon_client, db):
    make_user(db, must_change=True)
    login(anon_client)
    r = anon_client.get("/purchases/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account/password"
    assert anon_client.get("/account/password").status_code == 200
    # Cannot be bypassed by posting somewhere else.
    r = anon_client.post("/purchases/new", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account/password"


def test_password_change_flow(anon_client, db):
    user = make_user(db, must_change=True)
    login(anon_client)

    def change(current, new, confirm):
        return anon_client.post("/account/password", follow_redirects=False, data={
            "current_password": current, "new_password": new, "confirm_password": confirm})

    assert change("wrong password!!", "new password 12345", "new password 12345").status_code == 400
    assert change(TEST_PASSWORD, "new password 12345", "different 12345678").status_code == 400
    assert change(TEST_PASSWORD, "short", "short").status_code == 400
    assert change(TEST_PASSWORD, TEST_PASSWORD, TEST_PASSWORD).status_code == 400
    assert change(TEST_PASSWORD, "new password 12345", "new password 12345").status_code == 303
    db.refresh(user)
    assert user.must_change_password is False
    # Still signed in, and the new password works.
    assert anon_client.get("/purchases/").status_code == 200
    anon_client.post("/logout")
    assert login(anon_client, password="new password 12345").status_code == 303


# --- CSRF --------------------------------------------------------------------

def test_cross_origin_post_is_refused(anon_client, db):
    make_user(db)
    r = anon_client.post("/login", data={"username": "belinda", "password": TEST_PASSWORD},
                         headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_post_without_origin_or_referer_is_refused(anon_client):
    bare = TestClient(fastapi_app)  # no default Origin header
    r = bare.post("/login", data={"username": "x", "password": "y"}, follow_redirects=False)
    assert r.status_code == 403


def test_referer_is_accepted_when_origin_is_absent(anon_client, db):
    make_user(db)
    bare = TestClient(fastapi_app)
    r = bare.post("/login", data={"username": "belinda", "password": TEST_PASSWORD},
                  headers={"Referer": "http://testserver/login"}, follow_redirects=False)
    assert r.status_code == 303


def test_cross_origin_post_to_a_signed_in_route_is_refused(employee_client):
    r = employee_client.post("/purchases/new", data={}, headers={"Origin": "http://evil.example"},
                             follow_redirects=False)
    assert r.status_code == 403


# --- Startup -----------------------------------------------------------------

def test_app_refuses_to_start_without_a_session_secret():
    env = {**os.environ, "SESSION_SECRET": ""}
    result = subprocess.run([sys.executable, "-c", "import app.main"], env=env,
                            capture_output=True, text=True,
                            cwd=os.path.dirname(os.path.dirname(__file__)))
    assert result.returncode != 0 and "SESSION_SECRET" in result.stderr


# --- Idle timeout (30 minutes) inside the 12-hour absolute limit --------------

class Clock:
    """Stands in for auth.time so a test can move time forward."""
    def __init__(self):
        self.offset = 0

    def time(self):
        return time.time() + self.offset


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(auth, "time", c)
    return c


def test_session_survives_29_idle_minutes(anon_client, db, clock):
    signed_in(anon_client, db)
    clock.offset = 29 * 60
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 200


def test_session_ends_after_31_idle_minutes(anon_client, db, clock):
    signed_in(anon_client, db)
    clock.offset = 31 * 60
    r = anon_client.get("/purchases/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_activity_restarts_the_idle_clock_but_not_the_12_hour_limit(anon_client, db, clock):
    signed_in(anon_client, db)
    for minutes in range(25, 12 * 60, 25):       # a request every 25 minutes keeps it alive
        clock.offset = minutes * 60
        assert anon_client.get("/purchases/", follow_redirects=False).status_code == 200, minutes
    clock.offset = 12 * 3600 + 60                 # ...until the absolute limit
    assert anon_client.get("/purchases/", follow_redirects=False).status_code == 303


def test_idle_session_cannot_post(anon_client, db, clock):
    signed_in(anon_client, db)
    clock.offset = 31 * 60
    r = anon_client.post("/purchases/new", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


# --- Startup configuration ---------------------------------------------------

def test_app_refuses_to_start_without_allowed_origins():
    env = {**os.environ, "ALLOWED_ORIGINS": ""}
    result = subprocess.run([sys.executable, "-c", "import app.main"], env=env,
                            capture_output=True, text=True,
                            cwd=os.path.dirname(os.path.dirname(__file__)))
    assert result.returncode != 0 and "ALLOWED_ORIGINS must be set" in result.stderr


# --- Current-password guesses are throttled ------------------------------------

def test_wrong_current_passwords_lock_the_account(anon_client, db):
    user = signed_in(anon_client, db)

    def guess(pw):
        return anon_client.post("/account/password", follow_redirects=False, data={
            "current_password": pw, "new_password": "new password 12345",
            "confirm_password": "new password 12345"})

    for _ in range(svc.MAX_FAILED_LOGINS):
        assert guess("wrong password!!").status_code == 400
    db.refresh(user)
    assert user.locked_until is not None
    # Even the right current password is refused while locked, and nothing changed.
    r = guess(TEST_PASSWORD)
    assert r.status_code == 400 and "Too many" in r.text
    db.refresh(user)
    assert svc.verify_password(user.password_hash, TEST_PASSWORD)
    # The lock also blocks signing in again until it expires or is cleared.
    anon_client.post("/logout")
    assert login(anon_client).status_code == 401


def test_a_correct_password_change_still_works_after_a_few_misses(anon_client, db):
    user = signed_in(anon_client, db)
    for _ in range(svc.MAX_FAILED_LOGINS - 1):
        anon_client.post("/account/password", data={
            "current_password": "wrong password!!", "new_password": "x" * 14, "confirm_password": "x" * 14})
    r = anon_client.post("/account/password", follow_redirects=False, data={
        "current_password": TEST_PASSWORD, "new_password": "new password 12345",
        "confirm_password": "new password 12345"})
    assert r.status_code == 303
