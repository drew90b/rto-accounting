"""
Vehicle reconciliation (docs/specs/vehicle-reconciliation-spec.md, AC-VR-01..20). Synthetic data only.
Service-level tests call the services directly; permission tests go through the real HTTP routes and real sign-in.
"""
import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.database import Base
from app.models.enums import BusinessLine, PurchaseCategory, UnitStatus, UnitType, UserRole
from app.models.purchase import Purchase
from app.models.unit import Unit
from app.models.user import User
from app.models.vehicle_recon import (
    ImmutableRecordError, VrDecision, VrEvent, VrQuestion, VrSnapshot, VrSourceRecord,
)
from app.models.vendor import Vendor
from app.services import user_service
from app.services import vehicle_recon_service as svc
from app.services import vr_engine as eng
from app.services import vr_sources as vr
from tests import vr_fixtures as f
from tests.conftest import TEST_PASSWORD

D1 = date(2026, 11, 8)
D2 = date(2026, 11, 15)
TODAY = date(2026, 11, 16)


# --- helpers -----------------------------------------------------------------

def mkuser(db, username, role):
    u = db.query(User).filter(User.username == username).first()
    if u is None:
        user_service.create_user(db, username=username, display_name=username.title(), role=UserRole(role),
                                 password=TEST_PASSWORD, must_change_password=False)
        db.commit()
        u = db.query(User).filter(User.username == username).first()
    return u


def load(db, vehicles=None, contracts=None, d=D1, name="x.csv"):
    if vehicles is not None:
        vr.load_frazer_vehicles(db, f.vehicles_csv(vehicles), f"veh-{name}", d)
    if contracts is not None:
        vr.load_frazer_contracts(db, f.contracts_csv(contracts), f"con-{name}", d)
    db.commit()


def mkunit(db, n, status=UnitStatus.frontline_ready, cost="4000.00"):
    u = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, vin_serial=f.vin(n), year=2015, make="TESTMAKE",
             model="TESTMODEL", status=status, acquisition_cost=Decimal(cost) if cost is not None else None)
    db.add(u)
    db.flush()
    u.unit_id = f"U-{u.id:04d}"
    db.commit()
    return u


def mkpurchase(db, stock, unit=None, amount="3500.00", ref="P-1"):
    v = db.query(Vendor).first() or Vendor(name="TESTVENDOR")
    db.add(v)
    db.flush()
    p = Purchase(purchase_id=ref, purchase_date=date(2026, 10, 2), purchaser="T", vendor_id=v.id, amount=Decimal(amount),
                 category=PurchaseCategory.auction_car_rto, stock_number=stock, unit_id=unit.id if unit else None)
    db.add(p)
    db.commit()
    return p


def refresh(db, today=TODAY):
    s = svc.refresh(db, today)
    db.commit()
    return s


def qs(db, kind=None):
    q = db.query(VrQuestion)
    if kind:
        q = q.filter(VrQuestion.kind == kind)
    return q.order_by(VrQuestion.id).all()


def answer(db, user, q, choice, **kw):
    db.refresh(q)
    out = svc.answer_question(db, user, q.ref, choice, kw.pop("version", q.version), kw.pop("fp", q.evidence_fingerprint),
                              kw.pop("params", None), kw.pop("note", ""), kw.pop("reason", ""), today=TODAY)
    db.commit()
    return out


def rows(db):
    return svc.reconciled(db)[0]


def row_for(db, stock):
    return svc.find_row(rows(db), stock)


def operational_fingerprint(db):
    """Content hash of every table that is not a vr_ table (and not users, whose login stamps legitimately move)."""
    h = {}
    for t in Base.metadata.sorted_tables:
        if t.name.startswith("vr_") or t.name == "users":
            continue
        data = [tuple(str(c) for c in r) for r in db.execute(t.select().order_by(*t.primary_key.columns)).all()]
        h[t.name] = hashlib.sha256(json.dumps(data).encode()).hexdigest()
    return h


@pytest.fixture
def admin(db):
    return mkuser(db, "vradmin", "admin")


@pytest.fixture
def belinda(db):
    return mkuser(db, "belinda", "employee")


def shared_vin_setup(db):
    """Two stock numbers, one VIN; the second is a repossession-sourced re-entry. Plus an unrelated car."""
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}),
              f.vehicle("10118", 1, **{"Purchase Date": "11/02/26", "Vehicle Source": "Repossessio"}),
              f.vehicle("10200", 2)])
    refresh(db)


# --- AC-VR-01 leading zeros ----------------------------------------------------

def test_leading_zero_identifiers_are_kept_exactly(db):
    load(db, [f.vehicle("00123", 1), f.vehicle("F36140", 2)], [f.contract("00123", 1)])
    rec = db.query(VrSourceRecord).filter_by(source_type="frazer_vehicles", source_key="00123").one()
    assert rec.stock_number == "00123" and rec.source_key == "00123"
    assert db.query(VrSourceRecord).filter_by(source_key="F36140").one().stock_number == "F36140"
    r = row_for(db, "00123")
    assert r.stocks == ["00123"]
    assert row_for(db, "123") is None


def test_stock_without_its_zeros_is_a_proposal_not_a_match(db):
    load(db, [f.vehicle("00123", 1)])
    u = mkunit(db, 1)
    mkpurchase(db, "123", u)
    refresh(db)
    [q] = qs(db, "purchase_match")
    assert q.subject_json["stocks"] == ["00123"]
    p = row_for(db, "00123").purchases[0]
    assert p.status == "unresolved"            # near-match only: not linked
    assert "ignoring zeros" in p.basis


# --- AC-VR-02 / 03 repossession, no automatic merge -----------------------------

def test_same_vin_raises_one_question_and_never_merges(db):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assert sorted(q.subject_json["stocks"]) == ["10063", "10118"]
    rs = rows(db)
    assert {tuple(r.stocks) for r in rs} == {("10063",), ("10118",), ("10200",)}
    assert row_for(db, "10063").state == eng.STATE_REVIEW and row_for(db, "10118").state == eng.STATE_REVIEW
    assert row_for(db, "10200").state == eng.STATE_LACK
    assert "reentry" in [c["key"] for c in q.choices_json] and "not_sure" in [c["key"] for c in q.choices_json]
    assert "needs_someone_else" in [c["key"] for c in q.choices_json]


def test_accepted_reentry_groups_the_episodes_but_keeps_every_record(db, admin):
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 1, **{"Purchase Date": "11/02/26"})],
         [f.contract("10063", 1), f.contract("10118", 1, **{"Sale Date": "11/05/26"})])
    refresh(db)
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    rs = rows(db)
    assert len(rs) == 1
    r = rs[0]
    assert r.stocks == ["10063", "10118"] and "repossessed and re-entered" in r.relation_note
    assert set(r.episodes) == {"10063", "10118"} and all(r.episodes[s]["contract"] for s in r.episodes)   # contracts stay put
    assert db.query(VrSourceRecord).filter_by(source_type="frazer_vehicles").count() == 2               # nothing deleted
    dec = db.query(VrDecision).one()
    assert dec.state == "accepted" and "nothing is merged" in dec.effect_json["text"]


def test_wrong_vin_keeps_vehicles_separate(db, admin):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "wrong_vin:10118")
    assert {tuple(r.stocks) for r in rows(db)} == {("10063",), ("10118",), ("10200",)}
    assert db.query(VrDecision).one().effect_json["type"] == "wrong_vin"


# --- AC-VR-04..07 purchases --------------------------------------------------

def test_purchase_matching_stock_and_unit_vin_is_attached_without_a_question(db):
    load(db, [f.vehicle("10300", 3)])
    mkpurchase(db, "10300", mkunit(db, 3))
    refresh(db)
    assert qs(db, "purchase_match") == []
    r = row_for(db, "10300")
    assert [p.status for p in r.purchases] == ["attached"] and r.purchases[0].basis == "Stock number and unit VIN agree"
    assert r.unit is not None and "App unit" in r.sources_present


def test_purchase_stock_and_unit_vin_disagree_raises_a_question_and_does_not_attach(db):
    load(db, [f.vehicle("10300", 3), f.vehicle("10301", 4)])
    mkpurchase(db, "10300", mkunit(db, 4))
    refresh(db)
    [q] = qs(db, "purchase_match")
    assert sorted(q.subject_json["stocks"]) == ["10300", "10301"]
    assert {c["key"] for c in q.choices_json} >= {"belongs:10300", "belongs:10301", "shared_expense", "insufficient", "not_sure",
                                                    "needs_someone_else"}
    assert not any(p.status == "attached" for p in row_for(db, "10300").purchases)


def test_purchase_for_a_stock_nobody_lists_raises_nothing(db):
    load(db, [f.vehicle("10300", 3)])
    mkpurchase(db, "99999", None)
    refresh(db)
    assert qs(db) == []


def test_stock_only_purchase_is_labelled_unverified(db):
    load(db, [f.vehicle("10300", 3)])
    mkpurchase(db, "10300", None)
    refresh(db)
    assert row_for(db, "10300").purchases[0].basis == "Stock number only (the VIN was not checked)"


# --- AC-VR-08 repeats ----------------------------------------------------------

def test_repeating_loads_and_refreshes_creates_nothing_new(db):
    shared_vin_setup(db)
    u = mkunit(db, 2)
    mkpurchase(db, "10200", u)
    refresh(db)
    before = (db.query(VrSnapshot).count(), db.query(VrSourceRecord).count(), db.query(VrQuestion).count(), db.query(VrEvent).count())
    for _ in range(3):
        again = vr.load_frazer_vehicles(db, f.vehicles_csv([f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}),
                                                           f.vehicle("10118", 1, **{"Purchase Date": "11/02/26", "Vehicle Source": "Repossessio"}),
                                                           f.vehicle("10200", 2)]), "veh-x.csv", D1)
        assert again["created"] is False   # same bytes -> no-op
        assert refresh(db) == {"raised": 0, "evidence_changed": 0, "review_needed": 0, "withdrawn": 0, "reopened": 0}
    after = (db.query(VrSnapshot).count(), db.query(VrSourceRecord).count(), db.query(VrQuestion).count(), db.query(VrEvent).count())
    assert before == after


def test_unrelated_changes_in_a_reload_do_not_reraise_or_flag(db, admin):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    # a re-export where only an unrelated field (mileage/notes are not stored; here: cost) changed
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26", "Total Cost": "5100", "Original Cost": "5100"}),
              f.vehicle("10118", 1, **{"Purchase Date": "11/02/26", "Vehicle Source": "Repossessio"}), f.vehicle("10200", 2)],
         d=D2, name="v2")
    s = refresh(db)
    assert s["review_needed"] == 0 and len(qs(db)) == 1
    db.refresh(q)
    assert q.status == "decided"


# --- AC-VR-09 contradictory evidence ---------------------------------------------

def test_new_contradicting_evidence_flags_review_and_keeps_the_decision(db, admin):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    n_events = db.query(VrEvent).filter_by(question_id=q.id).count()
    # a third stock number appears with the same VIN
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 1, **{"Purchase Date": "11/02/26"}),
              f.vehicle("10200", 2), f.vehicle("10250", 1, **{"Purchase Date": "11/07/26"})], d=D2, name="v2")
    s = refresh(db)
    assert s["review_needed"] == 1 and s["raised"] == 0
    db.refresh(q)
    assert q.status == "review_needed"
    assert db.get(VrDecision, q.current_decision_id).state == "accepted"          # not undone
    assert len(qs(db)) == 1                                                       # not duplicated
    assert db.query(VrEvent).filter_by(question_id=q.id).count() == n_events + 1
    # decision still groups the two stocks it was about; the new stock stays separate
    assert {tuple(r.stocks) for r in rows(db)} == {("10063", "10118"), ("10200",), ("10250",)}
    # refresh again: no further event
    refresh(db)
    assert db.query(VrEvent).filter_by(question_id=q.id).count() == n_events + 1


def test_evidence_changing_under_an_unanswered_question_refuses_older_answers(db, admin):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    old_version, old_fp = q.version, q.evidence_fingerprint
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 1, **{"Purchase Date": "11/02/26"}),
              f.vehicle("10200", 2), f.vehicle("10250", 1)], d=D2, name="v2")
    refresh(db)
    db.refresh(q)
    assert q.status == "open" and q.version == old_version + 1
    with pytest.raises(svc.StaleAnswer):
        answer(db, admin, q, "reentry", version=old_version, fp=old_fp)
    assert db.query(VrDecision).count() == 0


def test_condition_disappearing_withdraws_an_unanswered_question(db):
    shared_vin_setup(db)
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 9, **{"Purchase Date": "11/02/26"}),
              f.vehicle("10200", 2)], d=D2, name="v2")
    refresh(db)
    [q] = qs(db)
    assert q.status == "withdrawn" and "no longer present" in q.withdrawn_reason


# --- AC-VR-10 / 11 corrections and audit -------------------------------------------

def test_correction_supersedes_and_preserves_history(db, admin):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    answer(db, admin, q, "reentry")
    with pytest.raises(svc.RecError):                      # a reason is required
        answer(db, admin, q, "duplicate")
    db.rollback()
    answer(db, admin, q, "wrong_vin:10118", reason="Belinda checked the title: 10118 was mistyped")
    decs = db.query(VrDecision).order_by(VrDecision.id).all()
    assert [d.state for d in decs] == ["superseded", "accepted"]
    assert decs[0].superseded_by_id == decs[1].id and decs[1].supersedes_id == decs[0].id
    assert "mistyped" in decs[1].reason
    assert {tuple(r.stocks) for r in rows(db)} == {("10063",), ("10118",), ("10200",)}      # old grouping no longer in effect
    kinds = [e.event_type for e in db.query(VrEvent).filter_by(question_id=q.id).order_by(VrEvent.id)]
    assert kinds.count("answered") == 2 and "superseded" in kinds and kinds[0] == "raised"
    assert all(e.user_id for e in db.query(VrEvent).filter(VrEvent.event_type.in_(["answered", "superseded"])))


def test_events_and_source_records_cannot_be_edited_or_deleted(db, admin):
    shared_vin_setup(db)
    ev = db.query(VrEvent).first()
    ev.body = "tampered"
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()
    rec = db.query(VrSourceRecord).first()
    rec.vin = "CHANGED"
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()
    db.delete(db.query(VrEvent).first())
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()
    db.delete(db.query(VrQuestion).first())
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()


# --- AC-VR-12 cost ----------------------------------------------------------------

def test_zero_cost_is_reported_zero_and_missing_is_not_zero(db):
    load(db, [f.vehicle("10001", 1, **{"Total Cost": "0", "Original Cost": "0"}), f.vehicle("10002", 2, **{"Total Cost": "", "Original Cost": ""})])
    a = db.query(VrSourceRecord).filter_by(source_key="10001").one()
    b = db.query(VrSourceRecord).filter_by(source_key="10002").one()
    assert a.reported_cost == Decimal("0.00") and b.reported_cost is None
    from app.routes.vehicles import cost_label
    assert "zero as reported; not confirmed free" in cost_label(a) and cost_label(b) == "Not reported"


def test_purchase_cost_is_shown_beside_but_never_added_to_the_source_cost(admin_client, db):
    load(db, [f.vehicle("10300", 3, **{"Total Cost": "5000", "Original Cost": "5000"})])
    mkpurchase(db, "10300", mkunit(db, 3, cost="3500.00"), amount="3500.00")
    refresh(db)
    html = admin_client.get("/vehicles/v/10300").text
    assert "$5,000.00" in html and "$3,500.00" in html
    assert "$8,500.00" not in html and "$7,000.00" not in html          # no sum of any combination
    assert "not</strong> added together" in html


def test_linking_a_purchase_by_decision_copies_no_cost(db, admin):
    load(db, [f.vehicle("10300", 3), f.vehicle("10301", 4)])
    p = mkpurchase(db, "10300", mkunit(db, 4), amount="3500.00")
    refresh(db)
    [q] = qs(db, "purchase_match")
    answer(db, admin, q, "belongs:10301")
    r = row_for(db, "10301")
    assert [(m.status, m.row_stock) for m in r.purchases] == [("confirmed", "10301")]
    assert db.query(Purchase).count() == 1 and db.get(Purchase, p.id).amount == Decimal("3500.00")
    assert db.query(VrSourceRecord).filter_by(source_type="app_purchases").count() == 1       # the one observation; no copy per link


# --- AC-VR-13 stale and simultaneous ------------------------------------------------

def test_two_people_answering_from_the_same_page_only_one_wins(db, admin):
    steve = mkuser(db, "steve", "admin")
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    v, fp = q.version, q.evidence_fingerprint
    answer(db, admin, q, "reentry", version=v, fp=fp)
    with pytest.raises(svc.StaleAnswer):
        answer(db, steve, q, "duplicate", version=v, fp=fp)
    db.rollback()
    assert [d.choice_key for d in db.query(VrDecision)] == ["reentry"]


def test_a_stale_not_sure_is_refused_too(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    v, fp = q.version, q.evidence_fingerprint
    answer(db, admin, q, "reentry")
    with pytest.raises(svc.RecError):
        answer(db, admin, q, "not_sure", version=v, fp=fp)


# --- AC-VR-16 / 19 approval versus operational -----------------------------------------

def assign_to(db, admin, q, user):
    svc.assign(db, admin, q.ref, user.id)
    db.commit()


def test_employee_identity_answer_is_a_proposal_until_an_admin_accepts(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, belinda)
    answer(db, belinda, q, "reentry", note="Dad says it came back in October")
    db.refresh(q)
    dec = db.get(VrDecision, q.current_decision_id)
    assert q.status == "proposed" and dec.state == "proposed" and dec.needs_approval
    assert len(rows(db)) == 3                                  # nothing grouped yet
    with pytest.raises(svc.NotAllowed):
        svc.accept_decision(db, belinda, q.ref, q.version, q.evidence_fingerprint)
    db.rollback()
    svc.accept_decision(db, admin, q.ref, q.version, q.evidence_fingerprint)
    db.commit()
    db.refresh(q)
    assert q.status == "decided" and len(rows(db)) == 2
    d = db.get(VrDecision, q.current_decision_id)
    assert d.answered_by_user_id == belinda.id and d.decided_by_user_id == admin.id


def test_rejecting_a_proposal_reopens_the_question_with_a_reason(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, belinda)
    answer(db, belinda, q, "duplicate")
    db.refresh(q)
    with pytest.raises(svc.RecError):
        svc.reject_decision(db, admin, q.ref, "  ", q.version, q.evidence_fingerprint)
    db.rollback()
    svc.reject_decision(db, admin, q.ref, "VINs differ on the title", q.version, q.evidence_fingerprint)
    db.commit()
    db.refresh(q)
    assert q.status == "open" and db.query(VrDecision).one().state == "rejected" and len(rows(db)) == 3


def test_proposal_cannot_be_accepted_after_the_evidence_moved(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, belinda)
    answer(db, belinda, q, "reentry")
    load(db, [f.vehicle("10063", 1, **{"Purchase Date": "01/05/26"}), f.vehicle("10118", 1, **{"Purchase Date": "11/02/26"}),
              f.vehicle("10200", 2), f.vehicle("10250", 1)], d=D2, name="v2")
    refresh(db)
    db.refresh(q)
    assert q.status == "review_needed"
    with pytest.raises(svc.StaleAnswer):
        svc.accept_decision(db, admin, q.ref, q.version, q.evidence_fingerprint)


def status_setup(db):
    load(db, [f.vehicle("10400", 5)], [f.contract("10400", 5)])
    mkunit(db, 5, status=UnitStatus.frontline_ready)
    refresh(db)


def test_status_conflict_is_raised_only_with_evidence_from_both_sources(db):
    status_setup(db)
    [q] = qs(db, "status_conflict")
    assert "frontline ready" in q.why and "A source report is not the car's confirmed status" in q.why


def test_no_status_question_when_the_contract_list_is_not_loaded(db):
    load(db, [f.vehicle("10400", 5)])
    mkunit(db, 5)
    refresh(db)
    assert qs(db) == []                                       # AC-VR-20: no dependence on an unloaded source
    r = row_for(db, "10400")
    assert any(l["text"] == "Not loaded" for l in r.source_lines)


def test_employee_status_answer_is_accepted_as_a_dated_person_confirmation(db, admin, belinda):
    status_setup(db)
    [q] = qs(db, "status_conflict")
    unit = db.query(Unit).one()
    unit_status_before = unit.status
    assign_to(db, admin, q, belinda)
    answer(db, belinda, q, "sold_financed", params={"as_of": "2026-11-04"})
    db.refresh(q)
    assert q.status == "decided"
    r = row_for(db, "10400")
    assert r.confirmations and r.confirmations[0]["as_of"] == "2026-11-04" and r.confirmations[0]["by"] == belinda.id
    assert db.query(Unit).one().status == unit_status_before               # the unit itself is untouched
    assert any("App unit" == l["source"] for l in r.source_lines)           # reported statuses still shown separately
    assert r.state == eng.STATE_RESOLVED and "source reports are unchanged" in r.state_note   # never shown as "agrees"


def test_status_answer_needs_a_valid_non_future_date(db, admin):
    status_setup(db)
    [q] = qs(db, "status_conflict")
    for bad in ("", "not a date", "2099-01-01"):
        with pytest.raises(svc.RecError):
            answer(db, admin, q, "on_hand", params={"as_of": bad})
        db.rollback()
    assert db.query(VrDecision).count() == 0


def test_not_sure_and_needs_someone_else_decide_nothing(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, belinda)
    answer(db, belinda, q, "not_sure", note="I can't tell")
    answer(db, belinda, q, "needs_someone_else")
    db.refresh(q)
    assert q.status == "open" and q.needs_someone_else and q.owner_user_id is None and db.query(VrDecision).count() == 0
    ev = [e.event_type for e in db.query(VrEvent).filter_by(question_id=q.id).order_by(VrEvent.id)]
    assert ev.count("deferred") == 2


# --- sources: nothing sensitive stored; real-file safety ------------------------------------

def test_no_customer_name_or_balance_is_stored(db):
    load(db, [f.vehicle("10001", 1)], [f.contract("10001", 1)])
    blob = json.dumps([[r.fields_json, r.stock_number, r.vin, r.make, r.model, r.reported_status] for r in db.query(VrSourceRecord)])
    for secret in (f.CUSTOMER_NAME, "ZZFIRST", f.CUSTOMER_BALANCE):
        assert secret not in blob
    c = db.query(VrSourceRecord).filter_by(source_type="frazer_contracts").one()
    assert c.fields_json == {"sale_date": "2026-11-04", "account_label": "Buy Here - Pay Here"} and c.reported_cost is None


def test_wrong_file_types_are_refused(db):
    with pytest.raises(vr.SourceLoadError):
        vr.load_frazer_vehicles(db, f.contracts_csv([f.contract()]), "x.csv", D1)
    with pytest.raises(vr.SourceLoadError):
        vr.load_frazer_contracts(db, f.vehicles_csv([f.vehicle()]), "x.csv", D1)
    assert db.query(VrSnapshot).count() == 0


def test_older_snapshot_never_replaces_newer_evidence(db):
    load(db, [f.vehicle("10001", 1, **{"Vehicle Make": "NEWER"})], d=D2, name="new")
    load(db, [f.vehicle("10001", 1, **{"Vehicle Make": "OLDER"})], d=D1, name="old")
    cur = db.query(VrSourceRecord).filter_by(source_key="10001", is_current=True).one()
    assert cur.make == "NEWER" and db.query(VrSourceRecord).filter_by(source_key="10001").count() == 2


def test_snapshot_dates_are_shown_and_a_vehicle_absent_from_the_newest_list_is_reported_not_removed(db):
    load(db, [f.vehicle("10001", 1), f.vehicle("10002", 2)], d=D1, name="a")
    load(db, [f.vehicle("10001", 1)], d=D2, name="b")
    r = row_for(db, "10002")
    line = r.source_lines[0]
    assert line["text"] == "Not in the newest list" and line["as_of"] == D1


# --- AC-VR-14: no writes to operational tables ----------------------------------------------

def test_whole_workflow_writes_nothing_outside_the_vr_tables(db, admin, belinda):
    load(db, [f.vehicle("10063", 1), f.vehicle("10118", 1), f.vehicle("10300", 3), f.vehicle("10301", 4), f.vehicle("10400", 5)],
         [f.contract("10400", 5)])
    mkpurchase(db, "10300", mkunit(db, 4), ref="P-9")
    mkunit(db, 5)
    before = operational_fingerprint(db)
    refresh(db)
    for q in qs(db):
        assign_to(db, admin, q, admin) if q.kind == "purchase_match" else None
    for q in qs(db):
        choice = {"shared_vin": "reentry", "purchase_match": "belongs:10301", "status_conflict": "sold_financed"}[q.kind]
        answer(db, admin, q, choice, params={"as_of": "2026-11-05"})
    q = qs(db, "shared_vin")[0]
    answer(db, admin, q, "duplicate", reason="second look")
    refresh(db)
    rows(db)
    assert operational_fingerprint(db) == before
    assert db.query(VrDecision).count() >= 4


def test_reconciliation_does_not_create_ledger_entries(db, admin):
    from app.models.transaction import Transaction
    load(db, [f.vehicle("10300", 3), f.vehicle("10301", 4)])
    mkpurchase(db, "10300", mkunit(db, 4))
    n = db.query(Transaction).count()
    refresh(db)
    [q] = qs(db, "purchase_match")
    answer(db, admin, q, "shared_expense")
    assert db.query(Transaction).count() == n


# --- integration point ----------------------------------------------------------------------------

def test_assigned_items_lists_only_open_work_for_that_user(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assert svc.assigned_items(db, belinda) == []
    assign_to(db, admin, q, belinda)
    [item] = svc.assigned_items(db, belinda)
    assert item["ref"] == q.ref and item["url"].endswith(q.ref) and item["source"] == "vehicle_reconciliation"


def test_purchase_questions_cannot_be_assigned_to_an_employee(db, admin, belinda):
    load(db, [f.vehicle("10300", 3), f.vehicle("10301", 4)])
    mkpurchase(db, "10300", mkunit(db, 4))
    refresh(db)
    [q] = qs(db, "purchase_match")
    with pytest.raises(svc.NotAllowed):
        svc.assign(db, admin, q.ref, belinda.id)


# --- command-line check: read-only, no names ------------------------------------------------------------

def test_cli_check_reads_files_without_a_database_and_prints_no_customer_data(tmp_path, capsys):
    from app.scripts import vehicle_recon_load
    v = tmp_path / "v.csv"
    c = tmp_path / "c.csv"
    v.write_bytes(f.vehicles_csv([f.vehicle("00123", 1), f.vehicle("10118", 2), f.vehicle("10063", 2)]))
    c.write_bytes(f.contracts_csv([f.contract("00123", 1)]))
    assert vehicle_recon_load.main(["--check", "--vehicles", str(v), "--contracts", str(c), "--source-date", "2026-11-08"]) == 0
    out = capsys.readouterr().out
    assert "Episodes (distinct stock numbers): 3" in out and "['10063', '10118']" in out and "VIN agrees on 1" in out
    for secret in (f.CUSTOMER_NAME, "ZZFIRST", f.CUSTOMER_BALANCE):
        assert secret not in out
    assert v.read_bytes() == f.vehicles_csv([f.vehicle("00123", 1), f.vehicle("10118", 2), f.vehicle("10063", 2)])   # original untouched


def test_cli_load_refuses_without_the_database_name(capsys, tmp_path):
    from app.scripts import vehicle_recon_load
    v = tmp_path / "v.csv"
    v.write_bytes(f.vehicles_csv([f.vehicle()]))
    assert vehicle_recon_load.main(["--vehicles", str(v), "--confirm-database", "not_the_name"]) == 2
    assert "Refusing" in capsys.readouterr().out


def test_a_newer_export_with_identical_content_is_reported_unchanged_but_moves_the_as_of_date(db):
    r1 = vr.load_frazer_vehicles(db, f.vehicles_csv([f.vehicle("10001", 1), f.vehicle("10002", 2)]), "a.csv", D1)
    r2 = vr.load_frazer_vehicles(db, f.vehicles_csv([f.vehicle("10001", 1), f.vehicle("10002", 2), f.vehicle("10003", 3)]), "b.csv", D2)
    r3 = vr.load_frazer_vehicles(db, f.vehicles_csv([f.vehicle("10001", 1, **{"Vehicle Make": "CHANGED"}), f.vehicle("10002", 2)]), "c.csv",
                                 date(2026, 11, 22))
    assert (r1["new"], r1["changed"], r1["unchanged"]) == (2, 0, 0)
    assert (r2["new"], r2["changed"], r2["unchanged"]) == (1, 0, 2)
    assert (r3["new"], r3["changed"], r3["unchanged"]) == (0, 1, 1)
    db.commit()
    line = row_for(db, "10002").source_lines[0]
    assert line["text"] == "Listed" and line["as_of"] == date(2026, 11, 22)     # latest observation
    assert row_for(db, "10003").source_lines[0]["text"] == "Not in the newest list"


def test_a_waiting_approval_names_an_administrator_not_the_answerer_as_responsible(db, admin, belinda):
    shared_vin_setup(db)
    [q] = qs(db, "shared_vin")
    assign_to(db, admin, q, belinda)
    assert row_for(db, "10063").owner_name == "Belinda"
    answer(db, belinda, q, "reentry")
    r = row_for(db, "10063")
    assert r.next_action.startswith("Approve an answer") and r.owner_name == "an administrator (to approve)"
