"""
The combined build: Frazer intake -> vehicle reconciliation -> My actions, with the Monthly Review stack present.
Synthetic data only. Spec: docs/specs/vehicle-reconciliation-spec.md (REQ-VR-*) and the cited intake / monthly review requirements.
"""
import re
from datetime import date

import pytest

from app.database import Base
from app.models.frazer_intake import FrazerUpload
from app.models.vehicle_recon import VrDecision, VrQuestion, VrSnapshot, VrSourceRecord
from app.services import vehicle_recon_service as svc
from app.services import vr_intake_bridge as bridge
from app.services import vr_sources as vr
from tests import vr_fixtures as f
from tests.test_vehicle_recon import answer, assign_to, mkuser, operational_fingerprint, qs, row_for
from tests.test_vehicle_recon_access import employee_user

FRAZER = "/finance/frazer"


def upload(client, data, name):
    r = client.post(f"{FRAZER}/upload", data={}, files={"file": (name, data, "text/csv")}, follow_redirects=False)
    return r


def vehicles_v1():
    return f.vehicles_csv([f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}),
                           f.vehicle("10118", 1, **{"Purchase Date": "11/02/26", "Vehicle Source": "Repossessio"}),
                           f.vehicle("00457", 7), f.vehicle("10200", 2)])


def contracts_v1():
    return f.contracts_csv([f.contract("10063", 1, **{"Sale Date": "02/20/26"}), f.contract("10118", 1, **{"Sale Date": "11/04/26"})])


# --- Frazer upload feeds reconciliation, no command line -----------------------------------------------------

def test_an_uploaded_frazer_file_appears_in_the_vehicle_list_with_its_questions(admin_client, db):
    assert upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv").status_code == 303
    assert upload(admin_client, contracts_v1(), "frazer-customer-balances-2026-11-08.csv").status_code == 303
    assert db.query(VrSnapshot).count() == 0                       # intake itself wrote nothing to reconciliation
    page = admin_client.get("/vehicles/").text
    assert "00457" in page and "10063" in page and "10200" in page      # leading zero kept, all cars listed
    assert db.query(VrSnapshot).filter(VrSnapshot.file_sha256.isnot(None)).count() == 2     # the two uploads (the app observations are separate)
    [q] = qs(db, "shared_vin")
    assert sorted(q.subject_json["stocks"]) == ["10063", "10118"]
    snap = db.query(VrSnapshot).filter_by(source_type="frazer_vehicles").one()
    assert snap.source_date == date(2026, 11, 8) and snap.label.startswith("FU-") and snap.file_name == "frazer-vehicles-2026-11-08.csv"
    assert "FU-0001" in page and "Used" in page


def test_intake_is_untouched_by_reconciliation(admin_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    before = db.query(FrazerUpload).count(), [(u.sha256, u.status, u.row_count) for u in db.query(FrazerUpload)]
    admin_client.get("/vehicles/")
    admin_client.post("/vehicles/refresh")
    after = db.query(FrazerUpload).count(), [(u.sha256, u.status, u.row_count) for u in db.query(FrazerUpload)]
    assert before == after


def test_cli_load_and_upload_of_the_same_file_resolve_to_one_snapshot(admin_client, db):
    data = vehicles_v1()
    vr.load_frazer_vehicles(db, data, "frazer-vehicles-2026-11-08.csv", date(2026, 11, 8))
    db.commit()
    upload(admin_client, data, "frazer-vehicles-2026-11-08.csv")
    admin_client.get("/vehicles/")
    assert db.query(VrSnapshot).filter_by(source_type="frazer_vehicles").count() == 1


def test_repeated_uploads_and_refreshes_do_not_duplicate_questions_or_records(admin_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    for _ in range(3):
        admin_client.get("/vehicles/")
        admin_client.post("/vehicles/refresh")
    n_q, n_r = db.query(VrQuestion).count(), db.query(VrSourceRecord).count()
    assert upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv").status_code == 409   # intake refuses identical bytes
    admin_client.get("/vehicles/")
    admin_client.post("/vehicles/refresh")
    assert (db.query(VrQuestion).count(), db.query(VrSourceRecord).count()) == (n_q, n_r)


def test_a_file_with_unreadable_rows_is_not_used_and_says_why(admin_client, db):
    bad = f.vehicles_csv([f.vehicle("10001", 1)]) + b'"10002","2015"\r\n'          # a short row is an error on its own line
    r = upload(admin_client, bad, "frazer-vehicles-2026-11-08.csv")
    assert r.status_code == 303
    page = admin_client.get("/vehicles/").text
    assert db.query(VrSnapshot).count() == 0
    assert "Not used" in page and "unreadable" in page


def test_contradictory_evidence_in_a_later_upload_flags_review_and_keeps_the_decision(admin_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    admin_client.get("/vehicles/")
    admin = mkuser(db, "vradmin", "admin")
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    later = f.vehicles_csv([f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 1, **{"Purchase Date": "11/02/26"}),
                            f.vehicle("00457", 7), f.vehicle("10200", 2), f.vehicle("10777", 1, **{"Purchase Date": "11/12/26"})])
    assert upload(admin_client, later, "frazer-vehicles-2026-11-15.csv").status_code == 303
    admin_client.get("/vehicles/")
    db.refresh(q)
    assert q.status == "review_needed" and db.get(VrDecision, q.current_decision_id).state == "accepted"
    assert len(qs(db)) == 1
    page = admin_client.get("/vehicles/?search=10063").text
    assert "Review: new evidence" in page
    admin_client.get("/vehicles/"); admin_client.post("/vehicles/refresh")
    assert len(qs(db)) == 1


# --- physical vehicle vs stock-number episodes vs contracts -----------------------------------------------------

def test_grouping_keeps_episodes_and_their_contracts_distinct(admin_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    upload(admin_client, contracts_v1(), "frazer-customer-balances-2026-11-08.csv")
    admin_client.get("/vehicles/")
    admin = mkuser(db, "vradmin", "admin")
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    rows = svc.reconciled(db)[0]
    grouped = [r for r in rows if len(r.stocks) > 1]
    assert len(grouped) == 1 and grouped[0].stocks == ["10063", "10118"]
    assert set(grouped[0].episodes) == {"10063", "10118"}
    assert grouped[0].episodes["10063"]["contract"].fields_json["sale_date"] != grouped[0].episodes["10118"]["contract"].fields_json["sale_date"]
    # stored records: one vehicle record and one contract record PER stock number, nothing merged
    assert db.query(VrSourceRecord).filter(VrSourceRecord.source_type == "frazer_vehicles", VrSourceRecord.stock_number.in_(["10063", "10118"])).count() == 2
    assert db.query(VrSourceRecord).filter(VrSourceRecord.source_type == "frazer_contracts", VrSourceRecord.stock_number.in_(["10063", "10118"])).count() == 2
    html = admin_client.get("/vehicles/v/10063").text
    for needle in ("Physical vehicle", "Stock-number episode 10063", "Stock-number episode 10118", "Contract</strong> on stock 10063", "Contract</strong> on stock 10118"):
        assert needle in html
    # correcting the decision splits the row again; every record is still there
    answer(db, admin, q, "wrong_vin:10118", reason="title check")
    assert not [r for r in svc.reconciled(db)[0] if len(r.stocks) > 1]
    assert db.query(VrSourceRecord).filter(VrSourceRecord.stock_number.in_(["10063", "10118"])).count() == 4


# --- My actions ------------------------------------------------------------------------------------------------------

def test_belinda_finds_and_answers_her_vehicle_question_through_my_actions(admin_client, employee_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    admin_client.get("/vehicles/")
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    [q] = qs(db, "shared_vin")
    assert "Vehicle questions" not in employee_client.get("/my-actions/").text
    assign_to(db, admin, q, emp)
    page = employee_client.get("/my-actions/").text
    assert "Vehicle questions" in page and q.ref in page and f"/vehicle-questions/mine/{q.ref}" in page
    r = employee_client.get(f"/vehicle-questions/mine/{q.ref}")
    assert r.status_code == 200 and "Your answer" in r.text
    r = employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer", follow_redirects=False,
                             data={"choice": "reentry", "expected_version": q.version, "expected_fp": q.evidence_fingerprint, "note": "yes"})
    assert r.status_code == 303
    db.refresh(q)
    assert q.status == "proposed"
    assert employee_client.get("/my-actions/").status_code == 200
    # admin approves; the history shows both people
    svc.accept_decision(db, admin, q.ref, q.version, q.evidence_fingerprint); db.commit()
    hist = [h["who"] + ":" + h["type"] for h in svc.question_history(db, q, svc.user_names(db))]
    assert "Testemployee:answered" in hist and "Vradmin:accepted" in hist


def test_my_actions_shows_other_peoples_vehicle_questions_to_nobody(admin_client, employee_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    admin_client.get("/vehicles/")
    emp = employee_user(db)
    [q] = qs(db, "shared_vin")
    page = employee_client.get("/my-actions/").text
    assert q.ref not in page and "10063" not in page                        # not assigned: invisible, not even a title
    assert employee_client.get(f"/vehicle-questions/mine/{q.ref}").status_code == 404


# --- permissions and financial data ----------------------------------------------------------------------------------

def test_employee_is_refused_on_intake_and_reconciliation_but_not_on_my_actions(employee_client, admin_client, db):
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    for p in ("/vehicles/", "/vehicles/queue", "/vehicles/v/10063", f"{FRAZER}/", f"{FRAZER}/uploads/1", f"{FRAZER}/uploads/1/download"):
        assert employee_client.get(p).status_code == 403, p
    assert upload(employee_client, vehicles_v1(), "x.csv").status_code == 403
    assert employee_client.get("/my-actions/").status_code == 200


def test_employee_never_sees_costs_even_for_questions_made_from_uploads(admin_client, employee_client, db):
    data = f.vehicles_csv([f.vehicle("10063", 1, **{"Total Cost": "4321.09", "Original Cost": "4321.09"}), f.vehicle("10118", 1)])
    upload(admin_client, data, "frazer-vehicles-2026-11-08.csv")
    upload(admin_client, f.contracts_csv([f.contract("10063", 1)]), "frazer-customer-balances-2026-11-08.csv")
    admin_client.get("/vehicles/")
    admin = mkuser(db, "vradmin", "admin"); emp = employee_user(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, emp)
    seen = employee_client.get("/my-actions/").text + employee_client.get(f"/vehicle-questions/mine/{q.ref}").text
    for secret in ("4321.09", "4,321.09", f.CUSTOMER_NAME, "ZZFIRST", f.CUSTOMER_BALANCE, "7,777.77"):
        assert secret not in seen
    assert f.CUSTOMER_NAME not in str([r.fields_json for r in db.query(VrSourceRecord)])    # never stored here either


def test_the_whole_upload_to_decision_flow_writes_nothing_to_operational_ledgers(admin_client, employee_client, db):
    admin = mkuser(db, "vradmin", "admin"); emp = employee_user(db)
    skip = {"frazer_uploads", "frazer_source_rows", "frazer_upload_exceptions", "frazer_upload_notes"}
    def snap():
        h = operational_fingerprint(db)
        return {k: v for k, v in h.items() if k not in skip and not k.startswith("review_")}
    before = snap()
    upload(admin_client, vehicles_v1(), "frazer-vehicles-2026-11-08.csv")
    upload(admin_client, contracts_v1(), "frazer-customer-balances-2026-11-08.csv")
    admin_client.get("/vehicles/")
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, emp)
    employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer", data={"choice": "duplicate", "expected_version": q.version, "expected_fp": q.evidence_fingerprint})
    db.refresh(q)
    admin_client.post(f"/vehicles/q/{q.ref}/accept", data={"expected_version": q.version, "expected_fp": q.evidence_fingerprint})
    assert snap() == before


# --- ordinary purchase entry still works on the combined build -----------------------------------------------------------

def test_ordinary_purchase_entry_still_works_alongside_reconciliation(admin_client, employee_client, db):
    from app.models.purchase import Purchase
    from app.models.transaction import Transaction
    from tests.test_purchase_workflow import post_purchase, post_vehicle_purchase
    r1 = post_purchase(employee_client)
    r2 = post_vehicle_purchase(admin_client)
    assert r1.status_code == 303 and r2.status_code == 303
    assert db.query(Purchase).count() == 2 and db.query(Transaction).count() == 2
    upload(admin_client, f.vehicles_csv([f.vehicle("LOT-42", 5)]), "frazer-vehicles-2026-11-08.csv")
    assert admin_client.get("/vehicles/").status_code == 200
    assert db.query(Purchase).count() == 2 and db.query(Transaction).count() == 2          # reconciliation added nothing
