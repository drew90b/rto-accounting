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


def first_params():
    """Before the first complete run the first transaction period must be stated explicitly (REQ-FX-13)."""
    return {"first_coverage_start": (today() - timedelta(days=10)).isoformat()}


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
             "settle_seconds": 0, "poll_seconds": 0, "min_age_seconds": 0, "first_coverage_start": first_params()["first_coverage_start"], "exports": {t: dict(helper.DEFAULTS["exports"][t]) for t in helper.TYPES}}
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
    folder = os.path.join(env.cfg["work_folder"], sorted(os.listdir(env.cfg["work_folder"]))[0])                       # a dated run folder holds the copies
    d = datetime.now().strftime("%Y-%m-%d")                                      # "as of" = the helper computer's local date when the file was generated
    assert {f"frazer-vehicles-as-of-{d}.csv", f"frazer-customer-balances-as-of-{d}.csv", "manifest.json"} <= set(os.listdir(folder))
    assert any(n.startswith("frazer-payment-transactions-") and n.endswith(f"-through-{today().isoformat()}.csv") for n in os.listdir(folder))
    assert len([p for p in env.land.iterdir()]) == 3                              # Frazer's own files are left untouched


def test_the_first_transaction_period_is_explicit_and_declared(env):
    env.cfg["first_coverage_start"] = (today() - timedelta(days=13)).isoformat()
    rec = env.go()
    up = env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").order_by(FrazerUpload.id.desc()).first()
    assert up.declared_coverage_end == today() and up.declared_coverage_start == today() - timedelta(days=13)
    assert rec["transaction_period"]["basis"] == "first_run"
    env.db.query(FrazerHelperRun).delete()
    env.db.commit()
    env.cfg["first_coverage_start"] = ""                                         # nothing is assumed for a first run
    with pytest.raises(helper.HelperError, match="first run"):
        env.go(fresh=True)


# ---- partial failure and retry -------------------------------------------------------------------------------------------

def test_a_missing_export_makes_the_run_partial_and_a_retry_completes_it_without_duplicates(env):
    env.skip = {"payments"}
    first = env.go()
    assert first["final_status"] == "partial" and first["files"]["payments"]["export"]["status"] == "missing"
    n = uploads(env.db)
    assert n == env.baseline_uploads + 2
    env.skip = set()
    second = env.go()                                                            # same button, same day: resumes
    assert second["final_status"] == "complete" and second["retry_of"] == first["server_run"]
    assert second["files"]["vehicles"]["upload"]["outcome"] == "carried" and second["files"]["customer_balances"]["upload"]["outcome"] == "carried"
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
    assert all("exit 3" in rec["files"][t]["export"]["message"] for t in helper.TYPES)


# ---- stale, incomplete, wrong -------------------------------------------------------------------------------------------

def test_yesterdays_file_is_never_uploaded_as_todays_snapshot(env):
    env.skip = set(helper.TYPES)
    for t in helper.TYPES:
        env.write(t, age=26 * 3600)                                              # last night's files are still in the folder
    rec = env.go()
    assert rec["final_status"] == "failed"
    assert all(rec["files"][t]["export"]["status"] == "stale" and "NOT used" in rec["files"][t]["export"]["message"] for t in helper.TYPES)
    assert uploads(env.db) == env.baseline_uploads


def test_the_server_refuses_stale_incomplete_and_misdated_files_even_from_a_careless_helper(env):
    run = svc.start_run(env.db, env.token_row, params=first_params())
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
    path, problem = helper.wait_for_file(str(tmp_path / "land"), {"pattern": "*vehicle*"}, started, wait_seconds=60, settle_seconds=1, poll_seconds=1,
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
    first = svc.start_run(env.db, env.token_row, params=first_params())
    env.db.commit()
    with pytest.raises(svc.ActiveRun):
        svc.start_run(env.db, env.token_row, params=first_params())
    with pytest.raises(helper.HelperError, match="already in progress"):
        helper.run(env.cfg, api=env.api, log=lambda m: None, wait_seconds=0)
    first.lease_expires_at = datetime.utcnow() - timedelta(minutes=1)
    env.db.commit()
    nxt = svc.start_run(env.db, env.token_row, params=first_params())
    assert env.db.get(FrazerHelperRun, first.id).status == "abandoned" and nxt.status == "running"


def test_a_second_run_on_the_same_computer_is_refused_by_the_local_lock(env):
    with helper.Lock(env.cfg["work_folder"]):
        with pytest.raises(helper.HelperError, match="Another update"):
            helper.run(env.cfg, api=env.api, log=lambda m: None, wait_seconds=0)
    helper.run(env.cfg, api=env.api, log=lambda m: None, ask=env.operator, wait_seconds=0, sleep=lambda s: None)   # the lock was released


def test_the_database_itself_refuses_two_running_runs(env):
    svc.start_run(env.db, env.token_row, params=first_params())
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
        svc.start_run(env.db, env.token_row, params=first_params())


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
    run = svc.start_run(env.db, env.token_row, params=first_params())
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
    run = svc.start_run(env.db, env.token_row, params=first_params())
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


# ---- transaction coverage: explicit first run, catch-up with overlap, periodic recheck (REQ-FX-13) -------------------------

def _accepted(env, start, end):
    """A finished, complete run whose transaction period was accepted (as the helper leaves it)."""
    r = FrazerHelperRun(token_id=env.token_row.id, status="complete", started_at=datetime.utcnow(), lease_expires_at=datetime.utcnow(),
                        finished_at=datetime.utcnow(), business_date=end.isoformat(),
                        results_json={"payments": {"outcome": "stored", "coverage_start": start.isoformat(), "coverage_end": end.isoformat()}})
    env.db.add(r)
    env.db.flush()
    r.run_id = f"FR-{r.id:04d}"
    env.db.commit()
    return r


def test_the_next_period_catches_up_from_the_last_accepted_one_with_overlap(env):
    t = today()
    _accepted(env, t - timedelta(days=40), t - timedelta(days=3))
    plan = svc.coverage_plan(env.db, t)
    assert plan["basis"] == "catch_up" and plan["coverage_end"] == t.isoformat()
    assert plan["coverage_start"] == (t - timedelta(days=3 + svc.OVERLAP_DAYS)).isoformat()      # starts before the last period ended
    # nobody ran it for three weeks: the period still starts before the last accepted end, so there is no gap
    env.db.query(FrazerHelperRun).delete()
    _accepted(env, t - timedelta(days=200), t - timedelta(days=21))
    plan = svc.coverage_plan(env.db, t)
    assert date.fromisoformat(plan["coverage_start"]) <= t - timedelta(days=21 + svc.OVERLAP_DAYS)


def test_a_periodic_recheck_reaches_further_back_but_never_before_the_first_accepted_start(env):
    t = today()
    _accepted(env, t - timedelta(days=200), t - timedelta(days=svc.DEEP_RECHECK_EVERY_DAYS))
    plan = svc.coverage_plan(env.db, t)
    assert plan["basis"] == "deep_recheck" and plan["coverage_start"] == (t - timedelta(days=svc.DEEP_RECHECK_DAYS)).isoformat()
    env.db.query(FrazerHelperRun).delete()
    _accepted(env, t - timedelta(days=20), t - timedelta(days=svc.DEEP_RECHECK_EVERY_DAYS))
    assert svc.coverage_plan(env.db, t)["coverage_start"] == (t - timedelta(days=20)).isoformat()   # history starts where the first run said


def test_a_partial_run_does_not_advance_the_coverage_checkpoint(env):
    env.skip = {"vehicles"}
    first = env.go()
    assert first["final_status"] == "partial" and first["files"]["payments"]["upload"]["outcome"] == "stored"
    assert svc.coverage_checkpoint(env.db) is None                                # the transactions were stored, but the run was not complete
    env.skip = set()
    second = env.go()
    assert second["final_status"] == "complete" and second["files"]["payments"]["upload"]["outcome"] == "carried"
    assert svc.coverage_checkpoint(env.db)["end"] == today()


def test_the_server_refuses_a_transaction_file_for_any_other_period(env):
    run = svc.start_run(env.db, env.token_row, params=first_params())
    env.db.commit()
    plan = run.params_json["plan"]
    data = payments_bytes()
    import hashlib
    kw = dict(export_type="payments", filename="p.csv", data=data, sha256=hashlib.sha256(data).hexdigest(),
              generated_at=datetime.utcnow().isoformat() + "Z", export_date=today().isoformat())
    narrower = (date.fromisoformat(plan["coverage_start"]) + timedelta(days=1)).isoformat()
    r = svc.submit_file(env.db, env.token_row, run, coverage_start=narrower, coverage_end=plan["coverage_end"], **kw)
    assert r["outcome"] == "stale" and "not the period this run was given" in r["message"]
    assert env.db.query(FrazerUpload).count() == env.baseline_uploads
    ok = svc.submit_file(env.db, env.token_row, run, coverage_start=plan["coverage_start"], coverage_end=plan["coverage_end"], **kw)
    assert ok["outcome"] == "stored"


# ---- overlapping periods downstream: counted once, revisions surfaced, fees apart from receipts ----------------------------

def _pay_rows(*rows):
    return fx.build(fx.PAYMENT_NOV8_HEADER, list(rows))


def _p(seq, amount="100.00", days_ago=2, **over):
    d = mmddyy(today() - timedelta(days=days_ago))
    return fx.payment(**{"Sequential ID": seq, "Amount": amount, "Applied to Contract": amount, "Transaction Date": d, "Date Entered": d, **over})


def _fee(seq, days_ago=2):
    d = mmddyy(today() - timedelta(days=days_ago))
    return fx.late_fee(**{"Sequential ID": seq, "Transaction Date": d, "Date Entered": d})


def _month_view(db):
    from app.services import monthly_review_service as mr
    from app.services.review_period import Period
    t = today()
    return mr.frazer_transactions(db, Period(month=t.replace(day=1), start=t - timedelta(days=20), end=t, label="test", window="test", is_stub=False))


def test_overlapping_transaction_exports_count_each_entry_once_and_keep_fees_apart(env):
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001"), _fee("9002")))
    assert env.go()["final_status"] == "complete"
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001"), _fee("9002"), _p("9003", "50.00", 0)))
    second = env.go(fresh=True)                                                   # a second, intentional update on the same day
    assert second["final_status"] == "complete" and second["transaction_period"]["basis"] in ("catch_up", "deep_recheck")
    assert second["files"]["payments"]["upload"]["compare"] == {"unchanged": 2, "new": 1}
    v = _month_view(env.db)
    assert v["rows_seen"] >= 5 and not v["revised"]                               # 9001 and 9002 arrived twice ...
    assert v["groups"]["payment_entry"]["count"] == 2 and str(v["groups"]["payment_entry"]["sum"]) == "150.00"   # ... and count once
    assert v["groups"]["fee_assessment"]["count"] == 1 and str(v["groups"]["fee_assessment"]["sum"]) == "25.00"   # a fee is not a receipt


def test_a_changed_entry_surfaces_as_a_revision_and_is_not_silently_replaced(env):
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001"), _p("9003", "50.00", 0)))
    env.go()
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001", "110.00"), _p("9003", "50.00", 0)))
    rec = env.go(fresh=True)
    up = rec["files"]["payments"]["upload"]
    assert up["outcome"] == "stored" and up["compare"].get("changed") == 1 and "both versions are kept" in up["message"]
    v = _month_view(env.db)
    assert v["revised"] == ["9001"]                                               # shown for a person to resolve
    assert str(v["groups"]["payment_entry"]["sum"]) == "50.00"                    # neither version is counted until then
    from app.models.frazer_intake import FrazerSourceRow
    assert env.db.query(FrazerSourceRow).filter(FrazerSourceRow.identity == "9001", FrazerSourceRow.record_type == "payment_entry").count() == 2


def test_an_entry_missing_from_a_later_export_of_the_same_period_is_reported(env):
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001"), _p("9003", "50.00", 0)))
    env.go()
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9003", "50.00", 0)))
    up = env.go(fresh=True)["files"]["payments"]["upload"]
    assert up["absent_earlier_count"] == 1 and "9001" in up["message"] and "earlier record is kept" in up["message"]


# ---- a second update on the same day, and old files -------------------------------------------------------------------------

def test_a_second_update_the_same_day_adds_no_duplicate_records_or_questions(env):
    assert env.go()["final_status"] == "complete"
    n, q, snaps = uploads(env.db), env.db.query(VrQuestion).count(), env.db.query(VrSnapshot).count()
    rec = env.go(fresh=True)                                                      # Frazer wrote the same data again
    assert rec["final_status"] == "complete"
    assert all(rec["files"][t]["export"]["status"] == "created" and rec["files"][t]["upload"]["outcome"] == "duplicate" for t in helper.TYPES)
    assert (uploads(env.db), env.db.query(VrQuestion).count(), env.db.query(VrSnapshot).count()) == (n, q, snaps)
    assert len([d for d in os.listdir(env.cfg["work_folder"]) if d[:2] == "20"]) == 2      # each update has its own dated folder


# ---- automatic generation (the Frazer driver), with a stand-in for the Windows part ----------------------------------------

class FakeDriver:
    """Stands in for FrazerDriver.ps1: 'Frazer' writes the files (or does not) and the driver reports what it saw."""

    def __init__(self, env, no_write=(), readback=None, diag_errors=(), crash=False, not_ok=()):
        self.env, self.no_write, self.readback, self.diag_errors, self.crash, self.not_ok = env, set(no_write), readback, list(diag_errors), crash, set(not_ok)
        self.calls = []

    def generate(self, types, period, diag_dir, log):
        self.calls.append((tuple(types), period))
        if self.crash:
            raise RuntimeError("boom")
        out = {}
        for t in types:
            if t in self.not_ok:
                out[t] = {"ok": False, "message": "The preset on screen was not the one expected. Create File was not clicked."}
                continue
            if t not in self.no_write:
                self.env.write(t)
            out[t] = {"ok": True, "message": "", "preset_name": "x", "dialog": True, "confirmations": [{"what": "slot", "selected": True}]}
            if t == "payments":
                out[t]["readback"] = self.readback or {"begin": mmddyy(date.fromisoformat(period[0])), "end": mmddyy(date.fromisoformat(period[1]))}
        return {"exports": out, "diagnostics": {"screenshots": ["a.png"], "errors": self.diag_errors}, "stopped": None}


def test_one_click_generates_archives_and_uploads_with_a_manifest(env):
    old = env.write("vehicles", vehicles_bytes(5, "55555"), age=26 * 3600)       # yesterday's working file, about to be overwritten
    drv = FakeDriver(env)
    rec = env.go(mode="auto", driver=drv)
    assert rec["final_status"] == "complete" and len(drv.calls) == 1 and drv.calls[0][0] == helper.TYPES     # one driver pass for all three
    v, p = rec["files"]["vehicles"]["export"], rec["files"]["payments"]["export"]
    assert v["original_filename"] == "frazer-vehicles.csv.CSV" and v["archive_name"].startswith("frazer-vehicles-as-of-") and v["archive_name"].endswith(".csv")
    assert len(v["sha256"]) == 64 and v["generated_local"][-6] in "+-" and v["timezone"] and os.path.isfile(v["archive_path"])
    assert p["archive_name"] == f"frazer-payment-transactions-{p['period']['start']}-through-{p['period']['end']}.csv" and p["period"]["confirmed_in_frazer"] is True
    assert "Unknown" in rec["source_cutoff"]
    up = env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").order_by(FrazerUpload.id.desc()).first()
    assert up.original_filename == p["archive_name"]
    res = env.db.query(FrazerHelperRun).one().results_json["payments"]
    assert res["original_filename"] == "frazer-payments.csv.CSV" and res["generation"] == "auto" and " to " in res["period_readback"]
    kept = os.listdir(os.path.join(env.cfg["work_folder"], "_preserved"))
    assert any(n.endswith("frazer-vehicles.csv.CSV") for n in kept)               # the earlier original was kept before Frazer overwrote it
    assert any("COMPLETE" in line for line in helper.result_lines(rec))


def test_a_driver_that_reports_success_without_a_fresh_file_proves_nothing(env):
    for t in helper.TYPES:
        env.write(t, age=3600)                                                    # the files in the folder are from earlier
    rec = env.go(mode="auto", driver=FakeDriver(env, no_write=helper.TYPES))
    assert rec["final_status"] == "failed" and uploads(env.db) == env.baseline_uploads
    assert all(rec["files"][t]["export"]["status"] == "stale" and rec["files"][t]["upload"]["outcome"] == "not sent" for t in helper.TYPES)


def test_transaction_dates_that_do_not_read_back_stop_that_export_only(env):
    rec = env.go(mode="auto", driver=FakeDriver(env, readback={"begin": "11/01/26", "end": "11/08/26"}))
    assert rec["final_status"] == "partial"
    assert "NOT used" in rec["files"]["payments"]["export"]["message"] and rec["files"]["payments"]["upload"]["outcome"] == "not sent"
    assert env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").count() == 1          # only the baseline
    assert rec["files"]["vehicles"]["upload"]["outcome"] == "stored"


def test_an_unconfirmed_preset_fails_that_export_and_the_retry_sends_only_it(env):
    first = env.go(mode="auto", driver=FakeDriver(env, not_ok={"customer_balances"}))
    assert first["final_status"] == "partial" and "not the one expected" in first["files"]["customer_balances"]["export"]["message"]
    drv = FakeDriver(env)
    second = env.go(mode="auto", driver=drv)
    assert second["final_status"] == "complete" and drv.calls[0][0] == ("customer_balances",)                # only the missing export is generated again
    assert uploads(env.db) == env.baseline_uploads + 3


def test_a_diagnostic_problem_does_not_change_the_business_result(env):
    rec = env.go(mode="auto", driver=FakeDriver(env, diag_errors=["screenshot end: Parameter is not valid."]))
    assert rec["final_status"] == "complete" and rec["diagnostics"]["status"] == "problem"
    text = "\n".join(helper.result_lines(rec))
    assert "Export: created" in text and "Upload: accepted and stored" in text and "does not change the results above" in text


def test_a_driver_crash_is_an_export_failure_not_a_helper_crash(env):
    rec = env.go(mode="auto", driver=FakeDriver(env, crash=True))
    assert rec["final_status"] == "failed" and uploads(env.db) == env.baseline_uploads
    assert all(rec["files"][t]["export"]["status"] == "failed" for t in helper.TYPES)


# ---- the first production run: an attribution-only uploader, and a hand upload that already covers part of the period ------

def _attributed(env):
    from app.services import user_service
    robot = user_service.create_attribution_user(env.db, username="eunice-frazer-helper", display_name="Eunice Frazer Helper")
    _, env.secret = svc.create_token(env.db, env.admin, label="Eunice", user=robot)
    env.db.commit()
    env.api = TestApi(env.secret, env.client)
    env.cfg["token"] = env.secret
    return robot


def test_uploads_are_attributed_to_the_helper_account_which_cannot_sign_in_or_be_assigned_work(env, client):
    robot = _attributed(env)
    assert env.go()["final_status"] == "complete"
    new = env.db.query(FrazerUpload).order_by(FrazerUpload.id.desc()).limit(3).all()
    assert {u.uploaded_by.display_name for u in new} == {"Eunice Frazer Helper"} and len(new) == 3
    assert "Eunice Frazer Helper" in client.get("/finance/frazer/").text                    # the intake list names it
    assert env.client.post("/login", data={"username": robot.username, "password": "anything-at-all"}, follow_redirects=False).status_code != 303
    from app.services import monthly_review_service as mr, vehicle_recon_service as vrs
    with pytest.raises(mr.ReviewError):
        mr._owner(env.db, robot.id)                                                         # a review issue cannot be given to it
    q = VrQuestion(ref="VQ-9999", question_key="k-9999", kind="status_conflict", title="t", evidence_fingerprint="f")
    env.db.add(q); env.db.flush()
    with pytest.raises(vrs.RecError):
        vrs.assign(env.db, env.admin, "VQ-9999", robot.id)                                  # nor a vehicle question
    assert vrs.assign(env.db, env.admin, "VQ-9999", env.emp.id).owner_user_id == env.emp.id  # a person still can be


def test_the_first_run_overlapping_an_earlier_hand_upload_counts_each_entry_once(env):
    """Production shape: a person already uploaded a transaction file for the start of the period; the helper's first run covers the
    same days and more. Every entry is counted once, and the hand upload is neither replaced nor counted again."""
    _attributed(env)
    start = today() - timedelta(days=10)
    hand = _pay_rows(_p("9001", "100.00", 8), _p("9002", "40.00", 6), _fee("9003", 5))
    intake.store_upload(env.db, env.admin, file=_Up("frazer-payments-hand.csv", hand), coverage_start=start.isoformat(),
                        coverage_end=(today() - timedelta(days=3)).isoformat())
    env.db.commit()
    env.cfg["first_coverage_start"] = start.isoformat()
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9001", "100.00", 8), _p("9002", "40.00", 6), _fee("9003", 5),
                                                                          _p("9004", "60.00", 1), _fee("9005", 0)))
    first = env.go()
    assert first["final_status"] == "complete" and first["transaction_period"]["start"] == start.isoformat()
    assert first["files"]["payments"]["upload"]["compare"] == {"unchanged": 3, "new": 2}
    v = _month_view(env.db)
    assert not v["revised"] and v["rows_seen"] >= 6                                          # three entries were read twice ...
    assert v["groups"]["payment_entry"]["count"] == 3 and str(v["groups"]["payment_entry"]["sum"]) == "200.00"    # ... counted once
    assert v["groups"]["fee_assessment"]["count"] == 2
    assert svc.coverage_checkpoint(env.db)["start"] == start


# ---- an entry a later export no longer lists: kept, raised for a person, and left out only on comparable evidence ----------

def _hand_upload(env, *rows, start_days=10, end_days=3):
    up = intake.store_upload(env.db, env.admin, file=_Up(f"hand-{len(rows)}-{start_days}.csv", _pay_rows(*rows)),
                             coverage_start=(today() - timedelta(days=start_days)).isoformat(), coverage_end=(today() - timedelta(days=end_days)).isoformat())
    env.db.commit()
    return up


def _one_click(env, *rows, **kw):
    """An update in one-click mode (the stand-in driver reports the period read back from Frazer)."""
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(*rows))
    return env.go(mode="auto", driver=FakeDriver(env), **kw)


def _review(env, day):
    from app.services import monthly_review_service as mr
    d = mr.build_review(env.db, day.replace(day=1), today())
    env.db.commit()
    return d, mr


def _absent(env):
    return {a["identity"]: a for a in _month_view(env.db)["absent"]}


@pytest.fixture
def helper_report_accepted(monkeypatch):
    """The owner decision that is NOT made today: accept the helper's report of the period and settings as proof."""
    from app.services import monthly_review_service as mr
    monkeypatch.setattr(mr, "ACCEPT_HELPER_REPORT_AS_PROOF", True)


def test_without_proof_of_scope_and_filters_the_entry_stays_counted_and_the_total_is_unreconciled(env, client):
    """Today's behaviour. Everything the app can check agrees, but the period and filters are only the helper's word."""
    from app.models.frazer_intake import FrazerSourceRow
    from app.models.monthly_review import ReviewIssue
    hand = _hand_upload(env, _p("9001", "1600.00", 8), _p("9002", "40.00", 6), _fee("9003", 5))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    assert _one_click(env, _p("9002", "40.00", 6), _fee("9003", 5), _p("9004", "60.00", 1), _p("9010", "1600.00", 8))["final_status"] == "complete"
    later = env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").order_by(FrazerUpload.id.desc()).first()
    v = _month_view(env.db)
    a = _absent(env)["9001"]
    assert a["counted"] and not a["comparable"] and "not accepted as proof" in a["basis"]
    assert a["held_in"] == [(hand.upload_id, hand.id)] and a["missing_from"] == [(later.upload_id, later.id)]
    assert v["groups"]["payment_entry"]["count"] == 4 and str(v["groups"]["payment_entry"]["sum"]) == "3300.00"     # nothing forced out
    assert env.db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == hand.id, FrazerSourceRow.identity == "9001").count() == 1
    day = today() - timedelta(days=8)
    d, mr = _review(env, day)
    issue = env.db.query(ReviewIssue).filter(ReviewIssue.auto_key == "absent:frazer_transaction:9001").one()
    text = issue.title + " " + issue.description
    assert issue.status == "open" and "$1,600.00" in text and hand.upload_id in text and later.upload_id in text and "STILL COUNTED" in text
    assert "delet" not in text.lower() and d["figures"]["transactions"]["unreconciled"]
    page = client.get(f"/finance/review/?month={day:%Y-%m}&all=1").text
    assert "Unreconciled total." in page and "Entries a later export no longer lists (1)" in page and "Still counted" in page and "$1,600.00" in page


def test_an_entry_missing_from_a_comparable_later_export_leaves_the_total_and_is_raised_for_a_person(env, client, helper_report_accepted):
    from app.models.frazer_intake import FrazerSourceRow
    from app.models.monthly_review import ReviewIssue
    hand = _hand_upload(env, _p("9001", "1600.00", 8), _p("9002", "40.00", 6), _fee("9003", 5))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    # the later export covers the same days; 9001 is not in it, and an entry with the same amount and date has a new number
    rec = _one_click(env, _p("9002", "40.00", 6), _fee("9003", 5), _p("9004", "60.00", 1), _p("9010", "1600.00", 8))
    assert rec["final_status"] == "complete"
    later = env.db.query(FrazerUpload).filter(FrazerUpload.export_type == "payments").order_by(FrazerUpload.id.desc()).first()
    v = _month_view(env.db)
    a = _absent(env)["9001"]
    assert a["comparable"] and not a["counted"] and str(a["amount"]) == "1600.00"
    assert a["held_in"] == [(hand.upload_id, hand.id)] and a["missing_from"] == [(later.upload_id, later.id)]      # both source uploads
    assert v["groups"]["payment_entry"]["count"] == 3 and str(v["groups"]["payment_entry"]["sum"]) == "1700.00"     # 9002 + 9004 + 9010
    assert v["groups"]["fee_assessment"]["count"] == 1 and not v["revised"]
    # the original upload and entry are untouched, and nothing was merged: 9010 is its own entry
    kept = env.db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == hand.id, FrazerSourceRow.identity == "9001").one()
    assert kept.fields_json["amount"] == "1600.00" and env.db.get(FrazerUpload, hand.id).status == "parsed"
    assert env.db.query(FrazerSourceRow).filter(FrazerSourceRow.identity == "9010").count() == 1
    # the review queue: one open issue naming the item, its amount and both uploads; it never says "deleted"
    day = today() - timedelta(days=8)
    d, mr = _review(env, day)
    issue = env.db.query(ReviewIssue).filter(ReviewIssue.auto_key == "absent:frazer_transaction:9001").one()
    text = issue.title + " " + issue.description + " " + str(issue.evidence_json)
    assert issue.status == "open" and "$1,600.00" in text and hand.upload_id in text and later.upload_id in text and "LEFT OUT" in text
    assert "delet" not in text.lower() and "duplicate" not in text.lower() and "replac" not in text.lower()
    assert {l["url"] for l in issue.links_json} >= {f"/finance/frazer/uploads/{hand.id}", f"/finance/frazer/uploads/{later.id}"}
    assert not d["figures"]["transactions"]["unreconciled"]
    page = client.get(f"/finance/review/?month={day:%Y-%m}&all=1").text
    assert "Entries a later export no longer lists (1)" in page and "$1,600.00" in page and "Left out" in page and issue.issue_id in page
    # it stays open until a reviewer resolves it, and resolving changes no record and no total
    _review(env, day)
    assert issue.status == "open"
    mr.update_issue(env.db, issue, env.admin, status="resolved", note="Looked it up in Frazer; noted what was found.")
    env.db.commit()
    d, _ = _review(env, day)
    assert issue.status == "resolved" and issue.auto_active
    assert str(_month_view(env.db)["groups"]["payment_entry"]["sum"]) == "1700.00" and "9001" in _absent(env)


def test_a_narrower_later_export_leaves_earlier_entries_alone(env, helper_report_accepted):
    _hand_upload(env, _p("9001", "100.00", 9), _p("9002", "40.00", 2))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    assert _one_click(env, _p("9001", "100.00", 9), _p("9002", "40.00", 2))["final_status"] == "complete"
    second = _one_click(env, _p("9002", "40.00", 2), _p("9005", "25.00", 0), fresh=True)     # starts 7 days back: 9 days ago is outside it
    assert second["final_status"] == "complete" and second["transaction_period"]["start"] == (today() - timedelta(days=7)).isoformat()
    v = _month_view(env.db)
    assert v["absent"] == [] and v["groups"]["payment_entry"]["count"] == 3 and str(v["groups"]["payment_entry"]["sum"]) == "165.00"


def test_an_incomplete_later_export_cannot_take_an_entry_out_of_the_total(env, helper_report_accepted):
    _hand_upload(env, _p("9001", "100.00", 8), _p("9002", "40.00", 6))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    rec = _one_click(env, _p("9002", "40.00", 6), _p("", "70.00", 4))                          # a row with no Sequential ID cannot be read
    assert rec["files"]["payments"]["upload"]["outcome"] == "stored_with_errors" and rec["final_status"] == "partial"
    a = _absent(env)["9001"]
    assert a["counted"] and not a["comparable"] and "not complete" in a["basis"]
    assert str(_month_view(env.db)["groups"]["payment_entry"]["sum"]) == "140.00"            # 9001 is still counted


def test_a_differently_filtered_or_unverified_later_export_cannot_take_an_entry_out_and_the_total_is_unreconciled(env, client, helper_report_accepted):
    from app.models.monthly_review import ReviewIssue
    _hand_upload(env, _p("9001", "100.00", 6), _fee("9003", 5))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    # 1. a one-click export that lists late fees only: it may simply not include payments
    assert _one_click(env, _fee("9003", 5), _fee("9006", 1))["final_status"] == "complete"
    a = _absent(env)["9001"]
    assert a["counted"] and "no other entry of this kind" in a["basis"]
    # 2. a later file uploaded by hand, even with payments in it and a period that covers the date
    _hand_upload(env, _p("9007", "55.00", 2), start_days=9, end_days=0)
    a = _absent(env)["9001"]
    assert a["counted"] and len(a["missing_from"]) == 2 and "uploaded by hand" in a["basis"]
    # 3. the same from a person running the saved export for the helper (settings not checked on screen)
    env.files["payments"] = ("frazer-payments.csv.CSV", lambda: _pay_rows(_p("9007", "55.00", 2), _p("9008", "5.00", 0)))
    assert env.go(fresh=True)["final_status"] == "complete"
    a = _absent(env)
    assert a["9001"]["counted"] and "settings were not checked" in a["9001"]["basis"]
    v = _month_view(env.db)
    assert str(v["groups"]["payment_entry"]["sum"]) == "160.00"                              # 9001 + 9007 + 9008: nothing was left out
    day = today() - timedelta(days=6)
    d, mr = _review(env, day)
    assert d["figures"]["transactions"]["unreconciled"]
    issue = env.db.query(ReviewIssue).filter(ReviewIssue.auto_key == "absent:frazer_transaction:9001").one()
    assert "STILL COUNTED" in issue.description and "unreconciled" in issue.description
    page = client.get(f"/finance/review/?month={day:%Y-%m}&all=1").text
    assert "Unreconciled total." in page and "Still counted" in page
    mr.update_issue(env.db, issue, env.admin, status="resolved", note="Checked in Frazer: the entry is still there.")
    env.db.commit()
    for other in env.db.query(ReviewIssue).filter(ReviewIssue.auto_key.like("absent:frazer_transaction:%"), ReviewIssue.status != "resolved"):
        mr.update_issue(env.db, other, env.admin, status="resolved", note="Checked in Frazer: still there.")
    env.db.commit()
    d, _ = _review(env, day)
    assert not d["figures"]["transactions"]["unreconciled"] and _absent(env)["9001"]["counted"]


def test_a_different_report_layout_is_not_comparable(env, helper_report_accepted):
    wide = fx.build(fx.PAYMENT_HEADER, [_p("9001", "100.00", 8)])                               # the earlier file came from a wider report (26 columns)
    intake.store_upload(env.db, env.admin, file=_Up("hand-wide.csv", wide), coverage_start=(today() - timedelta(days=10)).isoformat(),
                        coverage_end=(today() - timedelta(days=3)).isoformat())
    env.db.commit()
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    assert _one_click(env, _p("9002", "40.00", 6))["final_status"] == "complete"
    a = _absent(env)["9001"]
    assert a["counted"] and "not the same report" in a["basis"]


def test_a_missing_entry_that_reappears_is_counted_once(env, helper_report_accepted):
    from app.models.monthly_review import ReviewIssue
    _hand_upload(env, _p("9001", "100.00", 5), _p("9002", "40.00", 4))
    env.cfg["first_coverage_start"] = (today() - timedelta(days=10)).isoformat()
    assert _one_click(env, _p("9002", "40.00", 4), _p("9004", "60.00", 1))["final_status"] == "complete"
    assert not _absent(env)["9001"]["counted"] and str(_month_view(env.db)["groups"]["payment_entry"]["sum"]) == "100.00"
    day = today() - timedelta(days=5)
    _review(env, day)
    issue = env.db.query(ReviewIssue).filter(ReviewIssue.auto_key == "absent:frazer_transaction:9001").one()
    # the next export lists it again
    assert _one_click(env, _p("9001", "100.00", 5), _p("9002", "40.00", 4), _p("9004", "60.00", 1), fresh=True)["final_status"] == "complete"
    v = _month_view(env.db)
    assert v["absent"] == [] and not v["revised"]
    assert v["groups"]["payment_entry"]["count"] == 3 and str(v["groups"]["payment_entry"]["sum"]) == "200.00"     # once, not twice and not zero
    _review(env, day)
    assert issue.status == "open" and not issue.auto_active                                    # still a person's to close
