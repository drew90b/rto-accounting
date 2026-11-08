"""Vehicle reconciliation: server-side permissions, restricted evidence, and the real HTTP flow (AC-VR-15..19)."""
import re
from datetime import date

import pytest

from app.models.enums import UnitStatus
from app.models.vehicle_recon import VrDecision, VrQuestion
from app.services import vehicle_recon_service as svc
from tests import vr_fixtures as f
from tests.test_vehicle_recon import (
    D1, TODAY, answer, assign_to, load, mkpurchase, mkunit, mkuser, qs, refresh, row_for, shared_vin_setup, status_setup,
)

ADMIN_PATHS = ["/vehicles/", "/vehicles/queue", "/vehicles/v/10063", "/vehicles/q/VQ-0001"]
ADMIN_POSTS = ["/vehicles/refresh", "/vehicles/q/VQ-0001/answer", "/vehicles/q/VQ-0001/accept", "/vehicles/q/VQ-0001/reject",
               "/vehicles/q/VQ-0001/assign"]


def employee_user(db):
    return mkuser(db, "testemployee", "employee")


def test_signed_out_visitors_are_sent_to_sign_in(anon_client, db):
    shared_vin_setup(db)
    for p in ADMIN_PATHS + ["/vehicle-questions/mine", "/vehicle-questions/mine/VQ-0001"]:
        r = anon_client.get(p, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), p
    for p in ADMIN_POSTS + ["/vehicle-questions/mine/VQ-0001/answer"]:
        r = anon_client.post(p, data={}, follow_redirects=False)
        assert r.status_code == 303, p


def test_employee_is_refused_on_every_admin_page_and_write(employee_client, db):
    shared_vin_setup(db)
    for p in ADMIN_PATHS:
        assert employee_client.get(p).status_code == 403, p
    for p in ADMIN_POSTS:
        assert employee_client.post(p, data={"choice": "reentry", "expected_version": "1", "expected_fp": "x"}).status_code == 403, p


def test_employee_sees_only_questions_assigned_to_them(employee_client, db):
    shared_vin_setup(db)
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    [q] = qs(db, "shared_vin")
    # not assigned: empty list, and the question itself answers 404 (existence not revealed)
    assert "Nothing is assigned" in employee_client.get("/vehicle-questions/mine").text
    assert employee_client.get(f"/vehicle-questions/mine/{q.ref}").status_code == 404
    assert employee_client.get("/vehicle-questions/mine/VQ-9999").status_code == 404
    assign_to(db, admin, q, emp)
    page = employee_client.get("/vehicle-questions/mine").text
    assert q.ref in page
    assert employee_client.get(f"/vehicle-questions/mine/{q.ref}").status_code == 200


def test_employee_cannot_open_a_purchase_question_even_if_it_is_assigned_to_them(employee_client, db):
    load(db, [f.vehicle("10300", 3), f.vehicle("10301", 4)])
    mkpurchase(db, "10300", mkunit(db, 4), amount="3412.00")
    refresh(db)
    emp = employee_user(db)
    [q] = qs(db, "purchase_match")
    q.owner_user_id = emp.id                      # forced directly: the service itself refuses this assignment
    db.commit()
    assert employee_client.get(f"/vehicle-questions/mine/{q.ref}").status_code == 404
    assert q.ref not in employee_client.get("/vehicle-questions/mine").text
    assert employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer",
                                data={"choice": "shared_expense", "expected_version": q.version, "expected_fp": q.evidence_fingerprint}
                                ).status_code == 404
    assert db.query(VrDecision).count() == 0


def test_employee_evidence_view_has_no_money_customers_or_other_questions(employee_client, db):
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    load(db, [f.vehicle("10400", 5, **{"Total Cost": "4321.09", "Original Cost": "4321.09"}),
              f.vehicle("10063", 1), f.vehicle("10118", 1)], [f.contract("10400", 5)])
    mkpurchase(db, "10400", mkunit(db, 5, cost="3777.31"), amount="3777.31")
    refresh(db)
    for q in qs(db):
        if q.kind != "purchase_match":
            assign_to(db, admin, q, emp)
    seen = ""
    for q in qs(db):
        if q.owner_user_id == emp.id:
            seen += employee_client.get(f"/vehicle-questions/mine/{q.ref}").text
    seen += employee_client.get("/vehicle-questions/mine").text
    assert "10400" in seen and "10063" in seen
    for forbidden in ("4,321.09", "4321.09", "3,777.31", "3777.31", f.CUSTOMER_NAME, "ZZFIRST", f.CUSTOMER_BALANCE, "7,777.77",
                      "payroll", "Payroll", "Total Cost"):
        assert forbidden not in seen, forbidden
    # employee_view is an allow-list: its keys are fixed
    q = next(q for q in qs(db) if q.kind == "status_conflict")
    assert set(svc.employee_view(q)) == {"ref", "kind", "title", "why", "status", "version", "fingerprint", "episodes", "app", "choices"}


def test_employee_answer_over_http_is_a_proposal_and_changes_nothing_yet(employee_client, db):
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, emp)
    r = employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer", follow_redirects=False,
                             data={"choice": "reentry", "expected_version": q.version, "expected_fp": q.evidence_fingerprint, "note": "yes"})
    assert r.status_code == 303
    db.refresh(q)
    assert q.status == "proposed" and len(svc.reconciled(db)[0]) == 3
    page = employee_client.get(f"/vehicle-questions/mine/{q.ref}").text
    assert "waiting for an administrator" in page
    # employees cannot accept their own answer
    r = employee_client.post(f"/vehicles/q/{q.ref}/accept", data={"expected_version": q.version, "expected_fp": q.evidence_fingerprint})
    assert r.status_code == 403
    db.refresh(q)
    assert q.status == "proposed"


def test_employee_stale_answer_is_refused_with_409(employee_client, db):
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, emp)
    v, fp = q.version, q.evidence_fingerprint
    answer(db, admin, q, "duplicate")                       # an administrator got there first
    svc.assign(db, admin, q.ref, emp.id)
    db.commit()
    r = employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer",
                             data={"choice": "reentry", "expected_version": v, "expected_fp": fp})
    assert r.status_code == 409 and "changed after you opened it" in r.text
    assert [d.choice_key for d in db.query(VrDecision)] == ["duplicate"]


def test_employee_cannot_overwrite_an_accepted_decision(employee_client, db):
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, emp)
    answer(db, admin, q, "reentry")
    db.refresh(q)
    r = employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer",
                             data={"choice": "duplicate", "expected_version": q.version, "expected_fp": q.evidence_fingerprint})
    assert r.status_code == 404
    assert [d.choice_key for d in db.query(VrDecision)] == ["reentry"]


def test_employee_status_answer_over_http_is_accepted(employee_client, db):
    admin = mkuser(db, "vradmin", "admin")
    emp = employee_user(db)
    status_setup(db)
    [q] = qs(db, "status_conflict")
    assign_to(db, admin, q, emp)
    r = employee_client.post(f"/vehicle-questions/mine/{q.ref}/answer", follow_redirects=False,
                             data={"choice": "on_hand", "as_of": "2026-11-06", "expected_version": q.version,
                                   "expected_fp": q.evidence_fingerprint})
    assert r.status_code == 303
    db.refresh(q)
    assert q.status == "decided"
    assert row_for(db, "10400").confirmations[0]["status"] == "on_hand"


# --- the administrator's screens -----------------------------------------------------------------

def test_admin_list_search_filters_and_detail(admin_client, db):
    shared_vin_setup(db)
    page = admin_client.get("/vehicles/").text
    assert "10063" in page and "10118" in page and "10200" in page and "Needs review" in page
    assert "does not say where a car physically is" in page
    only = admin_client.get("/vehicles/?search=10200").text
    assert "10200" in only and "10063" not in only
    assert "10063" in admin_client.get("/vehicles/?state=needs_review").text
    assert "10200" not in admin_client.get("/vehicles/?state=needs_review").text
    assert "10063" in admin_client.get(f"/vehicles/?search={f.vin(1)}").text
    d = admin_client.get("/vehicles/v/10063").text
    assert "Confirmed by a person" in d and "Reported by sources" in d and "VQ-0001" in d
    assert admin_client.get("/vehicles/v/NOPE").status_code == 404


def test_admin_answers_over_http_and_the_list_changes(admin_client, db):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    page = admin_client.get(f"/vehicles/q/{q.ref}").text
    assert "Why this was raised" in page and "Not sure" in page and "Needs someone else" in page
    r = admin_client.post(f"/vehicles/q/{q.ref}/answer", follow_redirects=False,
                          data={"choice": "reentry", "expected_version": q.version, "expected_fp": q.evidence_fingerprint, "note": "ok"})
    assert r.status_code == 303
    after = admin_client.get("/vehicles/?search=10063").text
    assert "10063 / 10118" in after and "Related stock numbers confirmed by a person" in after
    assert "Resolved by a person" in after


def test_admin_stale_answer_over_http_gets_409_and_current_state(admin_client, db):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    v, fp = q.version, q.evidence_fingerprint
    admin = mkuser(db, "other_admin", "admin")
    answer(db, admin, q, "duplicate")
    r = admin_client.post(f"/vehicles/q/{q.ref}/answer", data={"choice": "reentry", "expected_version": v, "expected_fp": fp})
    assert r.status_code == 409 and "Nothing was saved" in r.text and "Correct the decision" in r.text


def test_refresh_button_is_idempotent(admin_client, db):
    shared_vin_setup(db)
    n = db.query(VrQuestion).count()
    for _ in range(2):
        assert admin_client.post("/vehicles/refresh", follow_redirects=False).status_code == 303
    assert db.query(VrQuestion).count() == n


def test_cross_origin_writes_are_refused(admin_client, db):
    shared_vin_setup(db)
    r = admin_client.post("/vehicles/refresh", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
