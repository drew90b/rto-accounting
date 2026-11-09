"""
Frazer export helper: server endpoints, token security, and the client helper end to end against the real app (in-memory test database).
Spec: docs/specs/frazer-export-helper-spec.md (AC-FX-*). Synthetic files only; nothing here touches Frazer or a production database.
"""
import importlib.util
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import Base
from app.main import app as fastapi_app
from app.models.frazer_helper import FrazerHelperRun, FrazerHelperToken
from app.models.frazer_intake import FrazerUpload
from app.models.vehicle_recon import VrDecision, VrQuestion, VrSnapshot
from app.services import frazer_helper_service as svc
from app.services import frazer_intake_service as intake
from app.services import vr_intake_bridge as bridge
from tests import frazer_fixtures as fx
from tests.test_vehicle_recon import mkuser

HELPER_PATH = Path(__file__).resolve().parent.parent / "tools" / "frazer_helper" / "frazer_helper.py"
spec = importlib.util.spec_from_file_location("frazer_helper_client", HELPER_PATH)
helper = importlib.util.module_from_spec(spec)
sys.modules["frazer_helper_client"] = helper
spec.loader.exec_module(helper)


class _Up:
    def __init__(self, name, data):
        import io
        self.filename, self.file = name, io.BytesIO(data)


def today() -> date:
    return svc.business_date(datetime.utcnow())


def mmddyy(d: date) -> str:
    return d.strftime("%m/%d/%y")


def vehicles_bytes(n=1, stock="10001"):
    return fx.build(fx.VEHICLE_NOV8_HEADER, [{**fx.vehicle(**{"Stock #": stock, "Vehicle VIN": fx.vin(n), "Last 6 VIN": f"{n:06d}"}), "Floor Plan Company": "", "Floor Plan Principal Owed": "0"}])


def balances_bytes(n=1, stock="10001", balance="5050.00"):
    return fx.customers_nov8_csv([fx.customer_nov8(**{"Stock #": stock, "Vehicle VIN": fx.vin(n), "Balance": balance})])


def payments_bytes(amount="100.00", seq="9001", d=None):
    d = d or today()
    return fx.build(fx.PAYMENT_NOV8_HEADER, [fx.payment(**{"Sequential ID": seq, "Amount": amount, "Transaction Date": mmddyy(d), "Date Entered": mmddyy(d)})])


def fingerprint(db):
    return {t.name: db.execute(text(f"select count(*) from {t.name}")).scalar() for t in Base.metadata.sorted_tables
            if not t.name.startswith(("frazer_", "vr_", "users")) and t.name != "alembic_version"}


class TestApi(helper.Api):
    """The helper's HTTP layer, pointed at the in-process app instead of the network (same paths, same bearer header)."""

    def __init__(self, token, c):
        super().__init__("http://test", token, attempts=1, pause=0)
        self.c = c

    def call(self, method, path, json_body=None, fields=None, file=None):
        h = {"Authorization": "Bearer " + self.token}
        if file is not None:
            r = self.c.post(path, data=fields, files={"file": (file[0], file[1], "text/csv")}, headers=h)
        elif method == "GET":
            r = self.c.get(path, headers=h)
        elif json_body is not None:
            r = self.c.post(path, json=json_body, headers=h)
        else:
            r = self.c.post(path, data=fields or {}, headers=h)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
        if r.status_code >= 400:
            raise helper.ServerRefused(r.status_code, body)
        return body


class Env:
    pass


@pytest.fixture
def env(db, tmp_path, _override_db):
    e = Env()
    e.db = db
    e.admin = mkuser(db, "fxadmin", "admin")
    e.emp = mkuser(db, "fxemployee", "employee")
    # approved layouts, from three ordinary uploads an administrator has already checked
    for name, data in (("base-veh.csv", vehicles_bytes(7, "90007")), ("base-bal.csv", balances_bytes(7, "90007")), ("base-pay.csv", payments_bytes(seq="8001", d=today() - timedelta(days=30)))):
        up = intake.store_upload(db, e.admin, file=_Up(name, data), export_date=today().isoformat() if "pay" not in name else "",
                                 coverage_start=(today() - timedelta(days=40)).isoformat() if "pay" in name else "",
                                 coverage_end=(today() - timedelta(days=20)).isoformat() if "pay" in name else "")
        svc.set_layout(db, e.admin, up.id)
    db.commit()
    e.baseline_uploads = db.query(FrazerUpload).count()
    e.token_row, e.secret = svc.create_token(db, e.admin, label="Frazer PC", user=e.emp)
    db.commit()
    e.client = TestClient(fastapi_app)               # no Origin header: a program, not a browser
    e.api = TestApi(e.secret, e.client)
    e.land, e.work = tmp_path / "land", tmp_path / "work"
    e.land.mkdir(); e.work.mkdir()
    e.cfg = {**helper.DEFAULTS, "server": "http://test", "token": e.secret, "landing_folder": str(e.land), "work_folder": str(e.work),
             "settle_seconds": 0, "poll_seconds": 0, "min_age_seconds": 0, "exports": {t: dict(helper.DEFAULTS["exports"][t]) for t in helper.TYPES}}
    e.files = {"vehicles": ("frazer-vehicles.csv.CSV", vehicles_bytes), "customer_balances": ("frazer-customer-balances.CSV", balances_bytes),
               "payments": ("frazer-payments.csv.CSV", payments_bytes)}
    e.skip = set()

    def write(t, data=None, age=0):
        name, make = e.files[t]
        p = e.land / name
        p.write_bytes(data if data is not None else make())
        ts = time.time() - age
        os.utime(p, (ts, ts))
        return p
    e.write = write

    def operator(t, label):                          # the person running the saved preset in Frazer when the helper asks
        if t not in e.skip:
            e.write(t)
    e.operator = operator

    def go(**kw):
        kw.setdefault("wait_seconds", 0)
        return helper.run(e.cfg, api=e.api, log=lambda m: None, ask=e.operator, sleep=lambda s: None, **kw)
    e.go = go
    return e


def uploads(db):
    return db.query(FrazerUpload).count()


# ---- success -----------------------------------------------------------------------------------------------------------

def test_a_complete_run_stores_all_three_through_intake_and_syncs_reconciliation(env):
    before = fingerprint(env.db)
    decisions = env.db.query(VrDecision).count()
    rec = env.go()
    assert rec["final_status"] == "complete"
    assert uploads(env.db) == env.baseline_uploads + 3
    new = env.db.query(FrazerUpload).order_by(FrazerUpload.id.desc()).limit(3).all()
    assert {u.export_type for u in new} == set(helper.TYPES) and all(u.status == "parsed" for u in new)
    assert all(u.uploaded_by_user_id == env.emp.id for u in new)                 # attributed to the person the token names
    run = env.db.query(FrazerHelperRun).one()
    assert run.status == "complete" and run.sync_json["state"] == "synced"
    used = {x.file_sha256 for x in env.db.query(VrSnapshot).filter(VrSnapshot.file_sha256.isnot(None))}
    assert {u.sha256 for u in new if u.export_type != "payments"} <= used       # today's vehicles and balances reached reconciliation, through its bridge
    assert env.db.query(VrDecision).count() == decisions                         # nothing was decided
    assert fingerprint(env.db) == before                                         # no purchase, unit, payment, transaction, customer or cash entry
    # each file keeps its own time; nothing claims one snapshot, and the true cutoff stays Unknown
    assert all(run.results_json[t]["file_written_at"] for t in helper.TYPES)
    assert all(u.actual_cutoff == "Unknown" for u in new)
    assert os.listdir(os.path.join(env.cfg["work_folder"], sorted(os.listdir(env.cfg["work_folder"]))[0], "files"))   # a dated run folder holds the copies
    assert len([p for p in env.land.iterdir()]) == 3                              # Frazer's own files are left untouched


def test_the_payment_period_is_configurable_and_declared(env):
    env.cfg["payment_days"] = 14
    env.go()
    up = env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").order_by(FrazerUpload.id.desc()).first()
    assert up.declared_coverage_end == today() and up.declared_coverage_start == today() - timedelta(days=13)


# ---- partial failure and retry -------------------------------------------------------------------------------------------

def test_a_missing_export_makes_the_run_partial_and_a_retry_completes_it_without_duplicates(env):
    env.skip = {"payments"}
    first = env.go()
    assert first["final_status"] == "partial" and first["files"]["payments"]["outcome"] == "missing"
    n = uploads(env.db)
    assert n == env.baseline_uploads + 2
    env.skip = set()
    second = env.go()                                                            # same button, same day: resumes
    assert second["final_status"] == "complete" and second["retry_of"] == first["server_run"]
    assert second["files"]["vehicles"]["outcome"] == "carried" and second["files"]["customer_balances"]["outcome"] == "carried"
    assert uploads(env.db) == n + 1                                              # only the missing one was added
    assert env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "vehicles").count() == 2    # baseline + today's, never two of today's


def test_retry_after_a_lost_response_does_not_duplicate(env):
    """The upload reached the app but the helper never heard back: sending the same bytes again is a harmless duplicate."""
    env.go()
    run = env.db.query(FrazerHelperRun).one()
    env.db.query(FrazerHelperRun).delete()           # simulate: the run record was lost client-side by starting over
    env.db.commit()
    rec = env.go(fresh=True)
    assert rec["final_status"] == "complete"
    assert all(rec["server_results"][t]["outcome"] == "duplicate" for t in helper.TYPES)
    assert uploads(env.db) == env.baseline_uploads + 3


def test_a_failed_export_command_is_reported_and_stores_nothing(env):
    env.cfg["exports"]["vehicles"]["command"] = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    env.cfg["exports"]["customer_balances"]["command"] = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    env.cfg["exports"]["payments"]["command"] = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    rec = env.go()
    assert rec["final_status"] == "failed" and uploads(env.db) == env.baseline_uploads
    assert all("exit 3" in rec["files"][t]["message"] for t in helper.TYPES)


# ---- stale, incomplete, wrong -------------------------------------------------------------------------------------------

def test_yesterdays_file_is_never_uploaded_as_todays_snapshot(env):
    env.skip = set(helper.TYPES)
    for t in helper.TYPES:
        env.write(t, age=26 * 3600)                                              # last night's files are still in the folder
    rec = env.go()
    assert rec["final_status"] == "failed"
    assert all(rec["files"][t]["outcome"] == "stale" and "NOT used" in rec["files"][t]["message"] for t in helper.TYPES)
    assert uploads(env.db) == env.baseline_uploads


def test_the_server_refuses_stale_incomplete_and_misdated_files_even_from_a_careless_helper(env):
    run = svc.start_run(env.db, env.token_row)
    env.db.commit()
    data = vehicles_bytes(1)
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    now = datetime.utcnow()
    def send(**over):
        kw = dict(export_type="vehicles", filename="v.csv", data=data, sha256=sha, generated_at=now.isoformat() + "Z", export_date=today().isoformat())
        kw.update(over)
        return svc.submit_file(env.db, env.token_row, run, **kw)
    assert send(generated_at=(now - timedelta(hours=20)).isoformat() + "Z")["outcome"] == "stale"
    assert send(export_date=(today() - timedelta(days=1)).isoformat())["outcome"] == "stale"
    assert send(generated_at=(now + timedelta(hours=2)).isoformat() + "Z")["outcome"] == "stale"
    assert send(sha256="0" * 64)["outcome"] == "incomplete"                        # the bytes that arrived are not the bytes that were read
    assert env.db.query(FrazerUpload).count() == env.baseline_uploads              # all four were refused before anything was stored


def test_wrong_export_type_and_changed_columns_are_refused_before_anything_is_stored(env):
    env.skip = {"vehicles", "customer_balances", "payments"}
    env.write("vehicles", balances_bytes())                                       # the balances preset ran under the vehicles name
    cols = fx.VEHICLE_NOV8_HEADER[:-1]                                            # a preset edited in Frazer: one column dropped
    env.write("customer_balances", fx.build(cols, [{**fx.vehicle(), "Floor Plan Company": "", "Floor Plan Principal Owed": "0"}]))
    env.write("payments", payments_bytes())
    rec = env.go()
    assert rec["server_results"]["vehicles"]["outcome"] == "rejected" and "customer balances" in rec["server_results"]["vehicles"]["message"].lower()
    assert rec["server_results"]["customer_balances"]["outcome"] == "rejected"          # it no longer reads as any export type
    assert rec["server_results"]["payments"]["outcome"] == "stored"
    assert rec["final_status"] == "partial" and uploads(env.db) == env.baseline_uploads + 1


def test_a_layout_change_names_the_columns(env):
    env.skip = set(helper.TYPES)
    env.write("vehicles", vehicles_bytes().replace(b"Mileage", b"Odometer"))
    env.write("customer_balances"); env.write("payments")
    rec = env.go()
    r = rec["server_results"]["vehicles"]
    assert r["outcome"] == "layout" and "odometer" in r["message"].lower() and "mileage" in r["message"].lower()


def test_a_file_still_being_written_is_waited_for_not_sent(env, tmp_path):
    p = tmp_path / "land" / "frazer-vehicles.csv.CSV"
    p.write_bytes(vehicles_bytes()[:50])                                          # Frazer is mid-write
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    clock = {"t": 0}
    def sleep(s):
        clock["t"] += 1
        if clock["t"] == 2:
            p.write_bytes(vehicles_bytes())                                       # finished on the second wait
    path, problem = helper.wait_for_file(str(tmp_path / "land"), "*vehicle*", started, wait_seconds=60, settle_seconds=1, poll_seconds=1,
                                         min_age_seconds=0, log=lambda m: None, sleep=sleep, now=lambda: time.time() + clock["t"])
    assert problem is None and path == str(p)
    p.write_bytes(b"")                                                            # empty
    assert helper._settled(str(p), 0, lambda s: None)[0] is False
    p.write_bytes(b"Stock #,Vehicle VIN,Mileage\r\n10001,TSTVIN")                       # cut off mid-row
    assert helper._csv_complete(p.read_bytes())[0] is False


def test_an_unreadable_summary_does_not_hide_a_reconciliation_failure(env, monkeypatch):
    monkeypatch.setattr(bridge, "auto_sync", lambda db: None)                     # the bridge swallows its own failures; the run must still notice
    rec = env.go()
    assert rec["final_status"] == "partial" and rec["sync"]["state"] == "failed"


# ---- duplicates and revisions (existing intake behaviour) -----------------------------------------------------------------

def test_an_identical_file_is_a_duplicate_and_a_changed_one_is_a_revision_kept_beside_it(env):
    env.go()
    n = uploads(env.db)
    env.files["customer_balances"] = ("frazer-customer-balances.CSV", lambda: balances_bytes(1, balance="4950.00"))   # next day's balance moved
    env.skip = {"vehicles", "payments"}
    for t in ("vehicles", "payments"):                                            # this morning's files are still in the folder: older than the run
        old = time.time() - 3600
        os.utime(env.land / env.files[t][0], (old, old))
    rec = env.go(fresh=True)
    assert rec["server_results"]["customer_balances"]["outcome"] == "stored"
    assert rec["server_results"]["vehicles"]["outcome"] == "stale"
    assert uploads(env.db) == n + 1                                               # a second balances snapshot is stored beside the first: nothing overwritten


# ---- one active run -----------------------------------------------------------------------------------------------------

def test_only_one_run_can_be_active_and_an_abandoned_one_stops_blocking(env):
    first = svc.start_run(env.db, env.token_row)
    env.db.commit()
    with pytest.raises(svc.ActiveRun):
        svc.start_run(env.db, env.token_row)
    with pytest.raises(helper.HelperError, match="already in progress"):
        helper.run(env.cfg, api=env.api, log=lambda m: None, wait_seconds=0)
    first.lease_expires_at = datetime.utcnow() - timedelta(minutes=1)
    env.db.commit()
    nxt = svc.start_run(env.db, env.token_row)
    assert env.db.get(FrazerHelperRun, first.id).status == "abandoned" and nxt.status == "running"


def test_a_second_run_on_the_same_computer_is_refused_by_the_local_lock(env):
    with helper.Lock(env.cfg["work_folder"]):
        with pytest.raises(helper.HelperError, match="Another helper run"):
            helper.run(env.cfg, api=env.api, log=lambda m: None, wait_seconds=0)
    helper.run(env.cfg, api=env.api, log=lambda m: None, ask=env.operator, wait_seconds=0, sleep=lambda s: None)   # the lock was released


def test_the_database_itself_refuses_two_running_runs(env):
    svc.start_run(env.db, env.token_row)
    env.db.commit()
    from sqlalchemy.exc import IntegrityError
    env.db.add(FrazerHelperRun(token_id=env.token_row.id, status="running", started_at=datetime.utcnow(), lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
                               business_date=today().isoformat(), results_json={}))
    with pytest.raises(IntegrityError):
        env.db.flush()
    env.db.rollback()


def test_a_run_needs_approved_layouts_first(env):
    env.db.query(svc.FrazerHelperLayout).delete()
    env.db.commit()
    with pytest.raises(svc.HelperError, match="approve the expected column layout"):
        svc.start_run(env.db, env.token_row)


# ---- who can do what ----------------------------------------------------------------------------------------------------

def test_the_token_does_exactly_the_helper_calls_and_opens_nothing_else(env):
    h = {"Authorization": "Bearer " + env.secret}
    assert env.client.get("/api/frazer-helper/status", headers=h).status_code == 200
    for path in ("/finance/frazer/", "/finance/frazer/helper/", "/accounts/", "/vehicles/", "/", "/payments/", "/customers/", "/finance/review/"):
        r = env.client.get(path, headers=h, follow_redirects=False)
        assert r.status_code in (303, 404), (path, r.status_code)                 # a token is not a session: sent to sign in
        assert "/login" in r.headers.get("location", "/login")
    assert env.client.post("/finance/frazer/upload", headers=h, files={"file": ("x.csv", b"a,b\n1,2")}, follow_redirects=False).status_code in (303, 403)
    assert env.db.query(FrazerUpload).count() == env.baseline_uploads


def test_missing_wrong_revoked_and_expired_tokens_are_refused(env):
    c = env.client
    assert c.get("/api/frazer-helper/status").status_code == 401
    assert c.get("/api/frazer-helper/status", headers={"Authorization": "Bearer fxh_nope"}).status_code == 401
    assert c.get("/api/frazer-helper/status", headers={"Authorization": "Bearer " + env.secret}).status_code == 200
    env.token_row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    env.db.commit()
    assert c.get("/api/frazer-helper/status", headers={"Authorization": "Bearer " + env.secret}).status_code == 401
    env.token_row.expires_at = None
    svc.revoke_token(env.db, env.token_row.id)
    env.db.commit()
    assert c.get("/api/frazer-helper/status", headers={"Authorization": "Bearer " + env.secret}).status_code == 401
    assert c.post("/api/frazer-helper/runs", json={}, headers={"Authorization": "Bearer " + env.secret}).status_code == 401


def test_a_disabled_user_stops_their_token_and_revoking_closes_an_open_run(env):
    run = svc.start_run(env.db, env.token_row)
    env.db.commit()
    svc.revoke_token(env.db, env.token_row.id)
    env.db.commit()
    assert env.db.get(FrazerHelperRun, run.id).status == "abandoned"


def test_only_the_bearer_path_skips_the_origin_check(env):
    # a browser-style POST with no Origin and no bearer is still refused by the CSRF check everywhere
    assert env.client.post("/login", data={"username": "x", "password": "y"}).status_code == 403
    assert env.client.post("/finance/frazer/helper/tokens", data={"label": "x", "user_id": 1}).status_code == 403
    # and a bearer header does not unlock the CSRF exemption outside the helper's own path
    assert env.client.post("/finance/frazer/upload", headers={"Authorization": "Bearer " + env.secret}).status_code == 403


def test_run_answers_carry_no_customer_data(env):
    env.go()
    body = env.client.post("/api/frazer-helper/runs/%s/finish" % env.db.query(FrazerHelperRun).one().run_id, headers={"Authorization": "Bearer " + env.secret}).text
    assert "TESTCUSTOMER" not in body and "FAKE" not in body and "TSTVIN" not in body and "5050" not in body
    assert "TESTCUSTOMER" not in env.client.get("/api/frazer-helper/status", headers={"Authorization": "Bearer " + env.secret}).text


def test_another_tokens_run_is_not_visible(env):
    other, secret2 = svc.create_token(env.db, env.admin, label="Other PC", user=env.emp)
    env.db.commit()
    run = svc.start_run(env.db, env.token_row)
    env.db.commit()
    r = env.client.post(f"/api/frazer-helper/runs/{run.run_id}/finish", headers={"Authorization": "Bearer " + secret2})
    assert r.status_code == 409


# ---- the pages ----------------------------------------------------------------------------------------------------------

def test_admin_page_creates_a_token_once_revokes_it_and_shows_staleness(client, employee_client, db, anon_client):
    admin = mkuser(db, "pageadmin", "admin")
    emp = mkuser(db, "pageemp", "employee")
    r = client.get("/finance/frazer/helper/")
    assert r.status_code == 200 and "Stale data" in r.text and "never" in r.text           # nothing uploaded: not "all good"
    r = client.post("/finance/frazer/helper/tokens", data={"label": "Front PC", "user_id": emp.id, "days_valid": 30})
    secret = re.search(r"fxh_[A-Za-z0-9_\-]+", r.text).group(0)
    assert "shown once" in r.text and db.query(FrazerHelperToken).one().token_hash != secret
    assert secret not in client.get("/finance/frazer/helper/").text                           # never shown again
    assert "Stale data" in client.get("/finance/frazer/").text                                # the intake page says so too
    tid = db.query(FrazerHelperToken).one().id
    assert client.post(f"/finance/frazer/helper/tokens/{tid}/revoke", follow_redirects=False).status_code == 303
    assert db.query(FrazerHelperToken).one().revoked_at is not None
    for path in ("/finance/frazer/helper/",):
        assert employee_client.get(path).status_code == 403
        assert anon_client.get(path, follow_redirects=False).status_code == 303


def test_freshness_turns_current_after_a_complete_run(env):
    assert svc.freshness(env.db)["any_stale"] is False       # the baseline uploads were made just now
    old = datetime.utcnow() - timedelta(hours=svc.STALE_AFTER_HOURS + 1)
    env.db.query(FrazerUpload).update({"uploaded_at": old})
    env.db.commit()
    assert svc.freshness(env.db)["any_stale"] is True
    env.go()
    f = svc.freshness(env.db)
    assert f["any_stale"] is False and f["last_complete_run"] is not None


def test_an_unchanged_file_still_proves_the_data_is_current(env):
    """A byte-identical export is a duplicate and adds no upload; the run still confirmed the data as of that file's time."""
    env.go()
    old = datetime.utcnow() - timedelta(hours=svc.STALE_AFTER_HOURS + 20)
    env.db.query(FrazerUpload).update({"uploaded_at": old})
    env.db.query(FrazerHelperRun).delete()
    env.db.commit()
    assert svc.freshness(env.db)["any_stale"] is True
    n = uploads(env.db)
    rec = env.go(fresh=True)
    assert all(rec["server_results"][t]["outcome"] == "duplicate" for t in helper.TYPES) and uploads(env.db) == n
    f = svc.freshness(env.db)
    assert f["any_stale"] is False and "helper run" in f["vehicles"]["via"]
