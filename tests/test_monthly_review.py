"""
Monthly Review workspace: routes, services and database behaviour.

Synthetic data only. Spec: docs/specs/cash-financial-control-spec.md section 20, acceptance cases AC-MR-01..AC-MR-34.
"""
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, text as sqltext

import app.services.monthly_review_service as svc
from app.database import Base
from app.models.frazer_intake import FrazerSourceRow, FrazerUpload
from app.models.monthly_review import (
    ImmutableReviewRecordError, ReviewIssue, ReviewIssueEvent, ReviewNote, ReviewSourceRecord, ReviewStepMark,
)
from app.models.user import User
from app.services import business_calendar as bc
from app.services import close_progress as cp
from app.services import review_period as rp
from tests.frazer_fixtures import (
    PAYMENT_NOV8_HEADER, build, customer_nov8, customers_nov8_csv, late_fee, payment, vehicle, vehicles_csv, vin, VEHICLE_NOV8_HEADER,
)

BASE = "/finance/review"
NOV = date(2026, 11, 1)


# ---- helpers ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Monday Nov 9, 2026 (America/Chicago) unless a test moves it."""
    state = {"d": date(2026, 11, 9)}
    monkeypatch.setattr(bc, "chicago_today", lambda now=None: state["d"])
    prev = bc.set_calendar(bc.load_calendar())
    yield state
    bc.set_calendar(prev)


def txt(html: str) -> str:
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S)
    body = re.sub(r"<style.*?</style>", "", m.group(1) if m else html, flags=re.S)
    body = re.sub(r"</?(?:em|strong|a|span|b|i|code)\b[^>]*>", "", body)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip().replace("&#39;", "'").replace("&#34;", '"').replace("&amp;", "&")


def page(client, month="2026-11", path="/") -> str:
    r = client.get(f"{BASE}{path}?month={month}")
    assert r.status_code == 200, r.text[-800:]
    return txt(r.text)


def section(t: str, start: str, end: str) -> str:
    i = t.index(start)
    j = t.index(end, i + 1) if end else len(t)
    return t[i:j]


def upload(client, data, name, **form):
    r = client.post("/finance/frazer/upload", data=form, files={"file": (name, data, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return r


def pay_csv(rows):
    return build(PAYMENT_NOV8_HEADER, rows)


def pay(i, **o):
    return payment(**{"Sequential ID": str(i), "Receipt Number": str(7000 + i), **o})


def user(db, name) -> User:
    return db.query(User).filter(User.username == name).one()


def post(client, path, **data):
    return client.post(f"{BASE}{path}", data=data, follow_redirects=False)


def issues(db, **flt):
    db.expire_all()
    return db.query(ReviewIssue).filter_by(**flt).order_by(ReviewIssue.id).all()


def issue_by_key(db, key, month=NOV):
    db.expire_all()
    return db.query(ReviewIssue).filter(ReviewIssue.month == month, ReviewIssue.auto_key == key).one_or_none()


# ---- month and period (AC-MR-01, 02) ---------------------------------------

def test_month_selection_separates_months_and_shows_the_stub_period(client):
    upload(client, pay_csv([pay(1, **{"Transaction Date": "11/03/26"})]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    upload(client, pay_csv([pay(2, **{"Transaction Date": "10/05/26"})]), "frazer-payments-2026-10-01-through-2026-10-31.csv")
    nov, octo = page(client, "2026-11"), page(client, "2026-10")
    assert "Nov 2 to 30, 2026 (partial)" in nov and "stub period" in nov
    assert "Oct 1 to Oct 31, 2026" in octo and "(partial)" not in section(octo, "Reporting period", "Month-end")
    # each month shows only its own entries
    nov_fig, oct_fig = section(nov, "4 · Collections", "5 · Reconciliation"), section(octo, "4 · Collections", "5 · Reconciliation")
    assert re.search(r"Money-received entries 1 \$100\.00", nov_fig) and re.search(r"Money-received entries 1 \$100\.00", oct_fig)
    assert "Left out" not in nov_fig


def test_a_bad_month_is_refused(client):
    for bad in ("2026-13", "november", "2026-1", "99999-01"):
        assert client.get(f"{BASE}/?month={bad}").status_code == 400
    assert client.get(f"{BASE}/schedule?month=nope").status_code == 400


def test_the_default_month_is_the_current_chicago_month(client, today):
    today["d"] = date(2026, 12, 2)
    assert "Monthly Review · December 2026" in txt(client.get(f"{BASE}/").text)


def test_status_never_claims_reconciled_closed_or_verified(client):
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    t = page(client)
    st = section(t, "1 · Month status", "2 · Records")
    assert "Reconciliation Not reconciled" in st and "Formal close Not available" in st
    assert "Collecting records" in st
    for word in ("Reconciled", "Verified", "Closed"):
        assert not re.search(rf"(?<!Not )(?<!not ){word}\b", st.replace("Not reconciled", "")), word
    assert "Close readiness 0%" in t and "Not closed" in t and "Close readiness 100%" not in t


# ---- records and coverage (AC-MR-03, 04) -----------------------------------

def test_partial_month_export_is_partial_until_the_rest_is_declared(client):
    upload(client, pay_csv([pay(1, **{"Transaction Date": "11/02/26"}), pay(2, **{"Transaction Date": "11/07/26"})]),
           "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV")
    rec = section(page(client), "2 · Records received", "3 · Needs attention")
    row = section(rec, "Frazer transactions", "Frazer customer balances")
    assert "Partial" in row and "Not covered: Nov 9 to Nov 30" in row
    assert "Nov 1 to Nov 8" in row and "read from the filename" in row and "entries dated Nov 2 to Nov 7" in row
    upload(client, pay_csv([pay(3, **{"Transaction Date": "11/20/26"})]), "frazer-payments-2026-11-09-through-2026-11-30.csv")
    row = section(section(page(client), "2 · Records received", "3 · Needs attention"), "Frazer transactions", "Frazer customer balances")
    assert "Received" in row and "Covers the period as declared (not verified)" in row and "Partial" not in row


def test_a_snapshot_date_is_not_transaction_coverage(client):
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-08.csv.CSV")
    upload(client, customers_nov8_csv([customer_nov8()]), "frazer-customer-balances-as-od-2026-11-08.CSV")
    rec = section(page(client), "2 · Records received", "3 · Needs attention")
    assert "Snapshot dated Nov 8" in section(rec, "Frazer vehicle list", "Expenses")
    assert "Partial" in section(rec, "Frazer vehicle list", "Expenses") and "Nov 30" in section(rec, "Frazer vehicle list", "Expenses")
    tx = section(rec, "Frazer transactions", "Frazer customer balances")
    assert "Missing" in tx and "No transaction export uploaded" in tx


# ---- figures: no double counting, separation, no zero (AC-MR-05..08) --------

def figures(client, month="2026-11"):
    return section(page(client, month), "4 · Collections", "5 · Reconciliation")


def test_repeated_and_overlapping_uploads_count_each_sequential_id_once(client):
    a = [pay(1), late_fee(**{"Sequential ID": "2", "Amount": "25"}), pay(3)]
    b = [pay(3), pay(4)]                                           # id 3 overlaps, identical content
    upload(client, pay_csv(a), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    assert client.post("/finance/frazer/upload", files={"file": ("again.csv", pay_csv(a), "text/csv")}).status_code == 409
    upload(client, pay_csv(b), "frazer-payments-2026-11-05-through-2026-11-12.csv")
    f = figures(client)
    assert re.search(r"Money-received entries 3 \$300\.00", f)                       # ids 1, 3, 4 once each
    assert "5 row(s) in 2 upload(s) are 4 distinct entries" in f


def test_entries_that_differ_between_uploads_are_left_out_listed_and_flagged(client, db):
    upload(client, pay_csv([pay(1), pay(3, Amount="100.00")]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    upload(client, pay_csv([pay(3, Amount="140.00", **{"Applied to Contract": "140.00"}), pay(4)]), "frazer-payments-2026-11-05-through-2026-11-12.csv")
    f = figures(client)
    assert re.search(r"Money-received entries 2 \$200\.00", f)                       # 1 and 4; 3 is not guessed
    assert "Left out of the totals" in f and "different content in different uploads (IDs 3)" in f
    i = issue_by_key(db, "conflict:frazer_transactions")
    assert i is not None and i.status == "open" and i.evidence_json["sequential_ids"] == ["3"]


def test_fee_assessments_stay_apart_and_no_revenue_or_profit_is_shown(client):
    upload(client, pay_csv([pay(1, Amount="100.00"), late_fee(**{"Sequential ID": "2", "Amount": "50"})]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    f = figures(client)
    assert re.search(r"Money-received entries 1 \$100\.00", f) and re.search(r"Fee assessments \(late fees\) 1 \$50\.00", f)
    assert "$150.00" not in f
    assert "not revenue and not profit" in f and "Revenue and profit" in f
    assert not re.search(r"(?i)total revenue|net income|profit \$|gross margin", f.replace("Revenue and profit", "").replace("not revenue and not profit", ""))


def test_unsupported_figures_explain_the_missing_input_not_zero(client):
    f = figures(client)
    assert "Not available. No Frazer transaction export covers this period" in f
    assert "$0.00" not in f
    assert "Not available: no customer-balance snapshot" in f
    assert "Not available: no purchases are entered" in f
    for needed in ("Collections by tender and drawer cash", "Bank deposits and transfers", "Square sales, payouts and card fees", "Payroll and labor totals"):
        assert needed in f


def test_customer_snapshots_are_listed_one_per_date_and_never_added(client):
    upload(client, customers_nov8_csv([customer_nov8(**{"Balance": "1000.00", "Principal Balance": "950", "Fees Owed": "50"})]), "frazer-customer-balances-2026-11-08.csv")
    upload(client, customers_nov8_csv([customer_nov8(**{"Balance": "900.00", "Principal Balance": "900", "Fees Owed": "0"})]), "frazer-customer-balances-2026-11-15.csv")
    f = figures(client)
    assert "$1,000.00" in f and "$900.00" in f and "$1,900.00" not in f
    assert "each date stands alone" in f and "not Nov 1 opening balances" in f


# ---- automatic issues (AC-MR-06, 09, 12) -----------------------------------

def test_automatic_issues_are_not_duplicated_by_refresh_or_repeat_uploads(client, db):
    page(client)
    n = len(issues(db, month=NOV))
    assert n >= 6
    for _ in range(3):
        page(client)
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    page(client)
    page(client)
    keys = [i.auto_key for i in issues(db, month=NOV) if i.kind == "auto"]
    assert len(keys) == len(set(keys))
    assert "coverage:frazer_transactions" in keys
    one = issue_by_key(db, "coverage:frazer_transactions")
    assert len([e for e in one.events if e.event_type == "created"]) == 1


def test_the_automatic_issue_set_is_the_specified_one(client, db):
    upload(client, pay_csv([pay(1), pay(2, Description="Cash", **{"Transaction Method": "CA"}),
                            pay(3, Description="Down Payment", **{"Transaction Type": "A", "Transaction Method": "CA"}),
                            late_fee(**{"Sequential ID": "4"})]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    page(client)
    keys = {i.auto_key for i in issues(db, month=NOV)}
    assert keys >= {"coverage:frazer_transactions", "snapshot:frazer_customers", "snapshot:frazer_vehicles", "source:bank_statement",
                    "source:square", "source:cash_count", "source:floor_plan_statement", "payroll:2026-11-06",
                    "mapping:method_code:CA", "mapping:class:fee_assessment"}
    assert "mapping:method_code:EL" not in keys                                      # EL appears on one description only
    assert not any(k.startswith("payroll:2026-11-13") for k in keys)                # a future pay date is not missing yet
    ca = issue_by_key(db, "mapping:method_code:CA")
    assert ca.owner_user_id is None and ca.due_date is None and "blocks tender and drawer-cash figures, not intake or preview" in ca.description


def test_future_months_get_no_automatic_issues(client, db):
    page(client, "2027-03")
    assert issues(db, month=date(2027, 3, 1)) == []


def test_a_cleared_condition_is_flagged_not_resolved_and_a_returning_one_reopens(client, db, today):
    admin = user(db, "testadmin")
    page(client)
    period = rp.period_for(NOV)
    key = "payroll:2026-11-06"
    original = svc._candidates
    i = issue_by_key(db, key)
    svc.update_issue(db, i, admin, status="in_progress", note="")
    try:
        svc._candidates = lambda *a, **k: [c for c in original(*a, **k) if c["key"] != key]
        svc.sync_auto_issues(db, period, today["d"])
        db.expire_all()
        i = issue_by_key(db, key)
        assert i.auto_active is False and i.status == "in_progress"                  # flagged, still a person's to resolve
        assert i.events[-1].event_type == "cleared"
        svc.update_issue(db, i, admin, status="resolved", note="Confirmed outside the app")
        svc._candidates = original
        svc.sync_auto_issues(db, period, today["d"])
        db.expire_all()
        i = issue_by_key(db, key)
        assert i.auto_active is True and i.status == "open" and i.events[-1].event_type == "returned"
    finally:
        svc._candidates = original


def test_the_clearing_condition_shows_on_the_page_without_resolving(client, db):
    page(client)
    post(client, "/records", month="2026-11", source_key="payroll_weekly", status="received", pay_date="2026-11-06", where_kept="Locked drawer")
    t = page(client)
    i = issue_by_key(db, "payroll:2026-11-06")
    assert i.status == "open" and i.auto_active is False
    assert "condition no longer detected" in t


# ---- manual issues, ownership, history (AC-MR-10, 11, 13) -------------------

def test_issue_lifecycle_records_every_change_with_user_and_time(client, db):
    admin, emp = user(db, "testadmin"), None
    r = post(client, "/issues", month="2026-11", title="Ask Frazer about entry 1174", description="Gap in sequence", category="records",
             checklist_ref="CL-2", owner_id=str(admin.id), due_date="2026-11-13", link="/finance/frazer/")
    assert r.status_code == 303
    i = issues(db, month=NOV, kind="manual")[0]
    assert i.issue_id.startswith("RI-") and i.owner_user_id == admin.id and i.due_date == date(2026, 11, 13) and i.links_json[0]["url"] == "/finance/frazer/"
    post(client, f"/issues/{i.id}/update", month="2026-11", status="in_progress", note="Called Frazer support")
    post(client, f"/issues/{i.id}/update", month="2026-11", due_date="2026-11-20")
    post(client, f"/issues/{i.id}/update", month="2026-11", note="They will reply Monday")
    db.expire_all()
    i = db.get(ReviewIssue, i.id)
    kinds = [e.event_type for e in i.events]
    assert kinds == ["created", "assigned", "status", "due_date", "note"]
    assert all(e.user_id == admin.id for e in i.events) and all(e.created_at for e in i.events)
    assert i.status == "in_progress" and i.due_date == date(2026, 11, 20)
    t = page(client)
    assert "Called Frazer support" in t and "They will reply Monday" in t and "Open → In progress" in t
    assert "In progress" in t


def test_resolving_needs_a_note_and_changes_nothing_else(client, db):
    page(client)
    i = issue_by_key(db, "source:square")
    before = {t.name: db.execute(sqltext(f"select count(*) from {t.name}")).scalar() for t in Base.metadata.sorted_tables
              if not t.name.startswith("review_")}
    steps_before = [(s["key"], s["status"]) for s in svc.build_review(db, NOV, date(2026, 11, 9), sync=False)["close"]["steps"]]
    r = post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved")
    assert r.status_code == 400 and "needs a note" in txt(r.text)
    db.expire_all()
    assert db.get(ReviewIssue, i.id).status == "open"
    r = post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="Square is not used for November sales")
    assert r.status_code == 303
    db.expire_all()
    assert db.get(ReviewIssue, i.id).status == "resolved"
    after = {t.name: db.execute(sqltext(f"select count(*) from {t.name}")).scalar() for t in Base.metadata.sorted_tables
             if not t.name.startswith("review_")}
    assert before == after                                                            # no source record touched
    # the record is still missing and the close step is unchanged
    steps_after = [(s["key"], s["status"]) for s in svc.build_review(db, NOV, date(2026, 11, 9), sync=False)["close"]["steps"]]
    assert steps_before == steps_after
    row = next(r for r in svc.build_review(db, NOV, date(2026, 11, 9), sync=False)["records"] if r["key"] == "square")
    assert row["status"] == "missing"


def test_reopening_needs_a_note_too(client, db):
    page(client)
    i = issue_by_key(db, "source:square")
    post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="done")
    assert post(client, f"/issues/{i.id}/update", month="2026-11", status="open").status_code == 400
    assert post(client, f"/issues/{i.id}/update", month="2026-11", status="open", note="Still missing").status_code == 303


def test_a_duplicate_open_title_is_refused_with_a_link_but_allowed_after_resolving(client, db):
    post(client, "/issues", month="2026-11", title="Chase the bank statement")
    r = post(client, "/issues", month="2026-11", title="  chase THE bank statement ")
    assert r.status_code == 409 and "RI-" in r.text and "already exists" in txt(r.text)
    assert len(issues(db, month=NOV, kind="manual")) == 1
    i = issues(db, month=NOV, kind="manual")[0]
    post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="got it")
    assert post(client, "/issues", month="2026-11", title="Chase the bank statement").status_code == 303
    assert post(client, "/issues", month="2026-10", title="Chase the bank statement").status_code == 303     # another month is separate


def test_issue_validation(client):
    assert post(client, "/issues", month="2026-11", title="").status_code == 400
    assert post(client, "/issues", month="2026-11", title="x", category="bogus").status_code == 400
    assert post(client, "/issues", month="2026-11", title="x", due_date="not-a-date").status_code == 400
    assert post(client, "/issues", month="2026-11", title="x", owner_id="99999").status_code == 400
    assert post(client, "/issues", month="2026-11", title="x", checklist_ref="CL-9").status_code == 400


def test_source_links_must_be_pages_this_page_can_point_to(client, db):
    assert post(client, "/issues", month="2026-11", title="Bad link", link="https://evil.example/").status_code == 400
    assert post(client, "/issues", month="2026-11", title="Bad link 2", link="/admin/secret").status_code == 400
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    up = db.query(FrazerUpload).one()
    assert post(client, "/issues", month="2026-11", title="Check upload", link=f"/finance/frazer/uploads/{up.id}").status_code == 303


def test_every_source_link_on_the_page_opens(client, db):
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-08.csv")
    html = client.get(f"{BASE}/?month=2026-11").text
    internal = {h for h in re.findall(r'href="(/[^"#]*)"', html) if not h.startswith(("/static", "/logout", "/account"))}
    assert any(h.startswith("/finance/frazer/uploads/") for h in internal)
    for h in sorted(internal):
        assert client.get(h).status_code == 200, h


# ---- notes are not a close (AC-MR-14) --------------------------------------

def test_a_review_note_is_attributed_dated_and_changes_neither_reconciliation_nor_close(client, db):
    before = section(page(client), "Close readiness", "Next action")
    r = post(client, "/notes", month="2026-11", note_type="review", body="Drew and Steve looked at the Nov 1-8 Frazer file together.",
             review_date="2026-11-09")
    assert r.status_code == 303
    t = page(client)
    assert "In review" in section(t, "1 · Month status", "2 · Records") and "Reconciliation Not reconciled" in t and "Formal close Not available" in t
    assert "Drew and Steve looked at the Nov 1-8 Frazer file together." in section(t, "6 · Owner review", "")
    assert "Testadmin" in section(t, "6 · Owner review", "") and "Mon Nov 9, 2026" in section(t, "6 · Owner review", "")
    assert section(t, "Close readiness", "Next action").split("Next action")[0][:60] == before.split("Next action")[0][:60]
    assert "Close readiness 0%" in t
    n = db.query(ReviewNote).one()
    assert n.user_id == user(db, "testadmin").id and n.review_date == date(2026, 11, 9) and n.created_at


def test_a_note_is_never_edited_a_correction_is_a_new_note(client, db):
    post(client, "/notes", month="2026-11", note_type="decision", body="Treat Nov 8 files as snapshots only", review_date="2026-11-09")
    first = db.query(ReviewNote).one()
    assert post(client, "/notes", month="2026-11", note_type="decision", body="Treat the Nov 8 files as snapshots, never as openings",
                corrects_note_id=str(first.id)).status_code == 303
    db.expire_all()
    notes = db.query(ReviewNote).order_by(ReviewNote.id).all()
    assert len(notes) == 2 and notes[0].body == "Treat Nov 8 files as snapshots only" and notes[1].corrects_note_id == notes[0].id
    assert post(client, "/notes", month="2026-11", note_type="review", body="x", corrects_note_id="9999").status_code == 400
    assert post(client, "/notes", month="2026-11", note_type="review", body="   ").status_code == 400


def test_review_tables_refuse_edits_and_deletes(client, db):
    post(client, "/notes", month="2026-11", note_type="review", body="a note")
    post(client, "/issues", month="2026-11", title="An issue")
    db.expire_all()
    n, i = db.query(ReviewNote).one(), issues(db, month=NOV, kind="manual")[0]
    n.body = "changed"
    with pytest.raises(ImmutableReviewRecordError):
        db.flush()
    db.rollback()
    i = db.query(ReviewIssue).filter_by(kind="manual").one()
    i.title = "changed"
    with pytest.raises(ImmutableReviewRecordError):
        db.flush()
    db.rollback()
    e = db.query(ReviewIssueEvent).first()
    db.delete(e)
    with pytest.raises(ImmutableReviewRecordError):
        db.flush()
    db.rollback()
    i = db.query(ReviewIssue).filter_by(kind="manual").one()
    i.status = "waiting"                                                              # state columns may change
    db.flush()


# ---- permissions (AC-MR-16, 18) --------------------------------------------

def test_employees_and_visitors_are_refused_on_every_page_and_write(admin_client, employee_client, anon_client, db):
    page(admin_client)
    i = db.query(ReviewIssue).first()
    gets = ["/", "/schedule"]
    posts = [("/issues", {"month": "2026-11", "title": "x"}), (f"/issues/{i.id}/update", {"month": "2026-11", "note": "x"}),
             ("/notes", {"month": "2026-11", "body": "x"}), ("/records", {"month": "2026-11", "source_key": "square"}),
             ("/steps/CL-1", {"month": "2026-11", "kind": "not_applicable", "action": "set", "note": "no reason given"})]
    n_before = db.query(ReviewIssue).count()
    for g in gets:
        assert employee_client.get(f"{BASE}{g}?month=2026-11").status_code == 403
        r = anon_client.get(f"{BASE}{g}?month=2026-11", follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"]
    for p, data in posts:
        assert employee_client.post(f"{BASE}{p}", data=data, follow_redirects=False).status_code == 403, p
        assert anon_client.post(f"{BASE}{p}", data=data, follow_redirects=False).status_code == 303, p
    db.expire_all()
    assert db.query(ReviewIssue).count() == n_before and db.query(ReviewNote).count() == 0 and db.query(ReviewStepMark).count() == 0


def test_an_action_can_be_assigned_to_an_employee_who_still_cannot_open_the_page(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    assert post(admin_client, "/issues", month="2026-11", title="Gather the bank statement", owner_id=str(emp.id)).status_code == 303
    i = issues(db, month=NOV, kind="manual")[0]
    assert i.owner_user_id == emp.id
    assert "no access to this page" in admin_client.get(f"{BASE}/?month=2026-11").text
    assert employee_client.get(f"{BASE}/?month=2026-11").status_code == 403


def test_a_post_without_a_matching_origin_is_refused(client):
    r = client.post(f"{BASE}/notes", data={"month": "2026-11", "body": "x"}, headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


# ---- payroll restriction (AC-MR-17) ----------------------------------------

@pytest.mark.parametrize("text", ["$450", "payroll was 450", "Weekly pay 1,200", "wages 12.50", "paycheck for 5694"])
def test_text_that_looks_like_pay_is_refused_where_payroll_is_involved(text):
    with pytest.raises(svc.ReviewError):
        svc.check_restricted_text(text, restricted=True)
    if re.search(r"payroll|wages|paycheck|weekly pay", text, re.I):
        with pytest.raises(svc.ReviewError):
            svc.check_restricted_text(text)


@pytest.mark.parametrize("text", ["Payroll for Nov 6 is in the locked drawer", "weekly payroll record kept 2026-11-06", "Looked at it on 11/9/2026", "Received 3 statements"])
def test_ordinary_payroll_wording_and_dates_are_allowed(text):
    svc.check_restricted_text(text, restricted=True)


def test_payroll_records_issues_and_notes_never_carry_amounts(client, db):
    page(client)
    assert post(client, "/issues", month="2026-11", title="Payroll check", category="payroll", description="Total was $5,694").status_code == 400
    assert post(client, "/issues", month="2026-11", title="Payroll check", category="payroll", description="Kept in the drawer").status_code == 303
    assert post(client, "/notes", month="2026-11", note_type="review", body="Weekly payroll came to $5,694").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="payroll_weekly", pay_date="2026-11-06", where_kept="drawer, $450 cash").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="payroll_weekly", pay_date="2026-11-06", where_kept="Locked drawer").status_code == 303
    pay_issue = issues(db, month=NOV, category="payroll", kind="manual")[0]
    assert post(client, f"/issues/{pay_issue.id}/update", month="2026-11", note="paid 450 in cash").status_code == 400
    assert post(client, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note="wages were $300 so n/a", evidence="folder x").status_code == 400
    # nothing restricted leaks into any generated or stored text
    for i in issues(db, month=NOV):
        if i.restricted:
            blob = " ".join([i.title, i.description] + [e.body for e in i.events])
            assert "$" not in blob and not re.search(r"\d{3,}", re.sub(r"\b20\d{2}(-\d{2}-\d{2})?\b", "", blob))
    t = page(client)
    pay_row = section(section(t, "2 · Records", "3 · Needs"), "Weekly payroll record", "A snapshot date does not")
    assert "1 of 1 pay date(s) recorded" in pay_row and "$" not in pay_row and "no amounts or names" in pay_row


def test_the_payroll_row_never_links_to_a_file_or_shows_a_name(client, db):
    post(client, "/records", month="2026-11", source_key="payroll_weekly", pay_date="2026-11-06", where_kept="Locked drawer")
    html = client.get(f"{BASE}/?month=2026-11").text
    row = html[html.index("Weekly payroll record (restricted)"):]
    row = row[:row.index("</tr>")]
    assert "href" not in row and "/documents" not in row


# ---- attestations -----------------------------------------------------------

def test_attestation_requires_dates_and_where_kept_and_is_insert_only(client, db):
    assert post(client, "/records", month="2026-11", source_key="bank_statement", where_kept="Bank portal").status_code == 400      # no dates
    assert post(client, "/records", month="2026-11", source_key="bank_statement", coverage_start="2026-11-02", coverage_end="2026-11-30").status_code == 400   # nowhere kept
    assert post(client, "/records", month="2026-11", source_key="bank_statement", coverage_start="2026-11-30", coverage_end="2026-11-02", where_kept="x").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="nope").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="square", status="not_required").status_code == 400                     # no reason
    r = post(client, "/records", month="2026-11", source_key="bank_statement", coverage_start="2026-11-02", coverage_end="2026-11-15", where_kept="Bank portal")
    assert r.status_code == 303
    row = section(section(page(client), "2 · Records", "3 · Needs"), "Bank statements", "Square")
    assert "Partial" in row and "Not covered: Nov 16 to Nov 30" in row and "Bank portal" in row
    post(client, "/records", month="2026-11", source_key="bank_statement", coverage_start="2026-11-16", coverage_end="2026-11-30", where_kept="Bank portal")
    row = section(section(page(client), "2 · Records", "3 · Needs"), "Bank statements", "Square")
    assert "Received" in row and "Partial" not in row
    assert db.query(ReviewSourceRecord).count() == 2


# ---- opening snapshot integration (AC-MR-19) -------------------------------

def test_the_reported_opening_position_keeps_its_date_and_unknown_cutoff(client, db):
    assert client.post("/finance/opening/setup", data={"name": "Starting position", "as_of_date": "2026-11-02", "label": "Workday 1",
                                                       "cutoff_note": "Unknown"}, follow_redirects=False).status_code == 303
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-08.csv")
    t = page(client)
    row = section(section(t, "2 · Records", "3 · Needs"), "Reported opening position", "A snapshot date does not")
    assert "Nov 2, 2026" in row and "cutoff: Unknown" in row and "Reported, not verified" in row
    assert "not opening balances" in row and "/finance/opening/" in client.get(f"{BASE}/?month=2026-11").text
    assert "Reported opening position" not in page(client, "2026-10")                 # only the month that contains it


# ---- close progress (AC-MR-21..) -------------------------------------------

def close_of(db, month=NOV, d=date(2026, 11, 9)):
    return svc.build_review(db, month, d, sync=False)["close"]


def test_progress_starts_low_and_uploads_alone_complete_no_step_or_readiness(client, db):
    before = close_of(db, d=date(2026, 12, 1))
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-30.csv")
    upload(client, customers_nov8_csv([customer_nov8()]), "frazer-customer-balances-2026-11-30.csv")
    c = close_of(db, d=date(2026, 12, 1))
    assert c["required"] == 7 and c["complete"] == 0 and not c["closed"]
    assert c["ready"]["percent"] == 0 and c["ready"]["met"] == 0                       # nothing reconciled, nothing matched
    assert c["prep"]["met"] > before["prep"]["met"] and c["prep"]["percent"] > 0        # but the paperwork advanced
    st = {s["key"]: s for s in c["steps"]}
    cl3 = {x["key"]: x for x in st["CL-3"]["criteria"]}
    assert cl3["frazer_vehicles"]["met"] and cl3["frazer_customers"]["met"] and st["CL-3"]["status"] != "complete"
    t = page(client)
    assert "Close readiness 0%" in t and re.search(r"Preparation \d+%", t)


def test_step_statuses_are_distinct_and_blocked_steps_say_why_and_who_owns_the_next_action(client, db):
    c = close_of(db)
    st = {s["key"]: s for s in c["steps"]}
    assert st["CL-2"]["status"] == "not_started" and st["CL-5"]["status"] == "not_started"
    assert st["CL-6"]["status"] == "blocked" and "Waiting for: CL-1, CL-2, CL-3, CL-4, CL-5." in st["CL-6"]["why_blocked"]
    assert st["CLOSE"]["status"] == "blocked"
    assert "Not built yet" in st["CLOSE"]["why_blocked"] or "Waiting for" in st["CLOSE"]["why_blocked"]
    assert st["CL-6"]["next"]["owner"] and st["CL-2"]["next"]["owner"] == "Belinda (gathers)"
    labels = {s["label"] for s in c["steps"]}
    assert labels <= {"Not started", "In progress", "Blocked", "Complete", "Not applicable"}
    # Frazer partly uploaded -> in progress
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    assert {s["key"]: s for s in close_of(db)["steps"]}["CL-2"]["status"] == "in_progress"


def record_everything(client):
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-30.csv")
    upload(client, customers_nov8_csv([customer_nov8()]), "frazer-customer-balances-2026-11-30.csv")
    for key in ("bank_statement", "square", "cash_count", "floor_plan_statement"):
        assert post(client, "/records", month="2026-11", source_key=key, coverage_start="2026-11-02", coverage_end="2026-11-30", where_kept="Binder").status_code == 303
    for d in ("2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27"):
        assert post(client, "/records", month="2026-11", source_key="payroll_weekly", pay_date=d, where_kept="Locked drawer").status_code == 303


def test_signoffs_need_the_records_first_and_do_not_complete_a_step_whose_capability_is_unbuilt(client, db, today):
    today["d"] = date(2026, 12, 1)
    assert post(client, "/steps/CL-1", month="2026-11", kind="confirm", action="set").status_code == 400            # records not in place
    record_everything(client)
    assert post(client, "/steps/CL-1", month="2026-11", kind="review", action="set").status_code == 400            # confirm first
    assert post(client, "/steps/CL-1", month="2026-11", kind="confirm", action="set", note="Accounts listed").status_code == 303
    assert post(client, "/steps/CL-1", month="2026-11", kind="review", action="set").status_code == 303
    c = close_of(db, d=date(2026, 12, 1))
    cl1 = next(s for s in c["steps"] if s["key"] == "CL-1")
    assert [x["met"] for x in cl1["criteria"]] == [True, True, True, True, True, False, False, False]              # preparation done; readiness blocked
    assert cl1["status"] == "blocked" and "Not built yet" in cl1["why_blocked"]
    assert cl1["next"]["owner"] == "Drew and Steve (decision)"
    assert c["ready"]["percent"] == 0 and c["complete"] == 0 and c["prep"]["percent"] > 0
    assert cl1["prep_met"] == cl1["prep_total"] == 5 and cl1["ready_met"] == 0
    t = page(client)
    assert "Blocked." in t and "The app has compared nothing" in t


def test_clearing_a_confirmation_invalidates_the_review_after_it(client, db, today):
    today["d"] = date(2026, 12, 1)
    record_everything(client)
    post(client, "/steps/CL-3", month="2026-11", kind="confirm", action="set")
    post(client, "/steps/CL-3", month="2026-11", kind="review", action="set")
    assert post(client, "/steps/CL-3", month="2026-11", kind="confirm", action="clear").status_code == 400          # needs a reason
    post(client, "/steps/CL-3", month="2026-11", kind="confirm", action="clear", note="Wrong valuation basis")
    cl3 = next(s for s in close_of(db, d=date(2026, 12, 1))["steps"] if s["key"] == "CL-3")
    assert [x["met"] for x in cl3["criteria"] if x["kind"] == "signoff"] == [False, False, False]


def test_mandatory_steps_cannot_be_excluded_by_a_reason(client, db):
    for step in ("CL-1", "CL-2", "CL-3", "CL-5", "CL-6", "CLOSE"):
        r = post(client, f"/steps/{step}", month="2026-11", kind="not_applicable", action="set",
                 note="We decided this does not apply to us at all this month", evidence="the binder")
        assert r.status_code == 400 and "mandatory requirement" in txt(r.text), step
    assert db.query(ReviewStepMark).count() == 0
    c = close_of(db)
    assert c["required"] == 7 and c["na"] == 0


def second_admin(db):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.models.enums import UserRole
    from app.services import user_service
    from tests.conftest import TEST_PASSWORD
    user_service.create_user(db, username="steve2", display_name="Steve", role=UserRole.admin, password=TEST_PASSWORD, must_change_password=False)
    db.commit()
    c2 = TestClient(fastapi_app, headers={"Origin": "http://testserver"})
    c2.__enter__()
    assert c2.post("/login", data={"username": "steve2", "password": TEST_PASSWORD}, follow_redirects=False).status_code == 303
    return c2


def test_payroll_may_be_excluded_only_with_evidence_two_administrators_and_no_contradiction(client, db):
    c = client
    note = "No payroll was paid in this period"
    assert post(c, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note="n/a", evidence="folder").status_code == 400
    assert post(c, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note=note, evidence="").status_code == 400
    assert post(c, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note=note, evidence="Secure payroll folder shows no pay run").status_code == 303
    t = close_of(db)
    cl4 = next(s for s in t["steps"] if s["key"] == "CL-4")
    assert cl4["na"]["state"] == "proposed" and cl4["status"] != "not_applicable" and t["required"] == 7           # a proposal alone changes nothing
    assert post(c, "/steps/CL-4", month="2026-11", kind="na_concur", action="set").status_code == 400                # the proposer cannot concur
    c2 = second_admin(db)
    try:
        assert post(c2, "/steps/CL-4", month="2026-11", kind="na_concur", action="set").status_code == 303
        t = close_of(db)
        assert next(s for s in t["steps"] if s["key"] == "CL-4")["status"] == "not_applicable" and t["required"] == 6 and t["na"] == 1
        marks = db.query(ReviewStepMark).order_by(ReviewStepMark.id).all()
        assert [m.kind for m in marks] == ["not_applicable", "na_concur"] and marks[0].user_id != marks[1].user_id
        assert "Evidence kept: Secure payroll folder" in marks[0].note
        post(c2, "/records", month="2026-11", source_key="payroll_weekly", pay_date="2026-11-06", where_kept="Locked drawer")
        t = close_of(db)
        assert next(s for s in t["steps"] if s["key"] == "CL-4")["na"]["state"] == "contradicted" and t["required"] == 7
    finally:
        c2.__exit__(None, None, None)


def test_a_pending_exclusion_never_raises_progress(client, db):
    before = close_of(db)
    post(client, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note="No payroll was paid in this period", evidence="Secure payroll folder")
    after = close_of(db)
    assert (after["prep"]["met"], after["prep"]["total"], after["ready"]["total"], after["required"]) == \
           (before["prep"]["met"], before["prep"]["total"], before["ready"]["total"], before["required"])


def test_a_not_required_record_needs_policy_evidence_and_a_second_administrator(client, db):
    for key in ("bank_statement", "cash_count", "payroll_weekly"):
        r = post(client, "/records", month="2026-11", source_key=key, status="not_required", where_kept="x" * 20, note="We just do not need this one", pay_date="2026-11-06")
        assert r.status_code == 400 and "mandatory" in txt(r.text), key
    assert post(client, "/records", month="2026-11", source_key="square", status="not_required", where_kept="Square dashboard", note="no").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="square", status="not_required", where_kept="", note="Square was not used for any sale this month").status_code == 400
    assert post(client, "/records", month="2026-11", source_key="square", status="not_required", where_kept="Square dashboard shows no sales",
                note="Square was not used for any sale this month").status_code == 303
    row = next(r for r in svc.build_review(db, NOV, date(2026, 11, 9), sync=False)["records"] if r["key"] == "square")
    assert row["status"] == "missing" and row["confirm_id"]                                   # a proposal counts as missing
    assert "counts as missing until a different administrator confirms it" in page(client)
    assert post(client, f"/records/{row['confirm_id']}/confirm", month="2026-11").status_code == 400    # not by the proposer
    c2 = second_admin(db)
    try:
        assert post(c2, f"/records/{row['confirm_id']}/confirm", month="2026-11").status_code == 303
        row = next(r for r in svc.build_review(db, NOV, date(2026, 11, 9), sync=False)["records"] if r["key"] == "square")
        assert row["status"] == "not_required" and "proposed by" in row["coverage"] and "confirmed by" in row["coverage"]
    finally:
        c2.__exit__(None, None, None)


def all_satisfied_ctx(monkeypatch, *, close_built):
    for k in cp.CAPABILITIES:
        monkeypatch.setitem(cp.CAPABILITIES, k, {**cp.CAPABILITIES[k], "built": True if k != "in_app_close" else close_built})
    monkeypatch.setattr(cp, "DECISIONS_CONFIRMED", {"DEC-35", "DEC-36", "DEC-09", "DEC-06", "DEC-11", "DEC-16", "DEC-18"})
    usr = SimpleNamespace(display_name="Drew", username="drew")
    marks = {(s["key"], k): SimpleNamespace(user=usr, user_id=1, created_at=datetime(2026, 12, 1), note="", id=1)
             for s in cp.STEPS if not s.get("final") for k in ("confirm", "review", "approve")}
    iv = [(date(2026, 11, 2), date(2026, 11, 30))]
    keys = ("frazer_tx", "square", "frazer_vehicles", "frazer_customers", "bank_statement", "cash_count", "floor_plan_statement", "payroll_weekly")
    recs = {k: {"status": "received", "coverage": "ok", "outstanding": "", "intervals": iv} for k in keys}
    fridays = [date(2026, 11, d) for d in (6, 13, 20, 27)]
    return dict(today=date(2026, 12, 8), wd0=date(2026, 11, 30), period=rp.period_for(NOV), records=recs,
                purchases={"count": 2, "pending": 0, "total": 5, "with_receipt": 2}, pay_fridays=fridays,
                payroll_done={f.isoformat() for f in fridays}, marks=marks, issues=[])


def evaluate_all_satisfied(monkeypatch, *, close_built):
    return cp.evaluate(all_satisfied_ctx(monkeypatch, close_built=close_built))


def test_100_percent_and_closed_appear_only_when_every_close_condition_is_satisfied(monkeypatch):
    c = evaluate_all_satisfied(monkeypatch, close_built=False)
    assert c["complete"] == 6 and c["required"] == 7 and c["percent"] < 100 and not c["closed"]       # formal close still unbuilt
    assert c["prep"]["percent"] == 100                                                                  # preparation can be complete without closing
    assert c["steps"][-1]["status"] == "blocked" and "Not built yet" in c["steps"][-1]["why_blocked"]
    c = evaluate_all_satisfied(monkeypatch, close_built=True)
    assert c["complete"] == 7 and c["percent"] == 100 and c["closed"]


def test_one_unsatisfied_milestone_keeps_the_percentage_below_100(monkeypatch):
    ctx = all_satisfied_ctx(monkeypatch, close_built=True)
    monkeypatch.setattr(cp, "DECISIONS_CONFIRMED", set())
    c = cp.evaluate(ctx)
    assert c["percent"] < 100 and not c["closed"]
    ctx = all_satisfied_ctx(monkeypatch, close_built=True)
    ctx["payroll_done"] = set(list(ctx["payroll_done"])[:3])
    c = cp.evaluate(ctx)
    assert next(s for s in c["steps"] if s["key"] == "CL-4")["status"] != "complete" and not c["closed"]


def test_resolving_issues_and_review_notes_never_move_the_percentage(client, db):
    page(client)
    before = close_of(db)
    for i in issues(db, month=NOV):
        post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="Decided; nothing else done")
    post(client, "/notes", month="2026-11", note_type="review", body="Reviewed everything")
    after = close_of(db)
    assert [(s["key"], s["status"]) for s in before["steps"]] == [(s["key"], s["status"]) for s in after["steps"]]
    assert after["ready"]["percent"] == 0 and after["prep"]["met"] == before["prep"]["met"]


def test_preparation_advances_as_real_work_is_done_and_readiness_does_not(client, db, today):
    today["d"] = date(2026, 11, 9)
    p0 = close_of(db)["prep"]
    upload(client, pay_csv([pay(1, **{"Transaction Date": "11/03/26"})]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    p1 = close_of(db)["prep"]
    assert p1["met"] == p0["met"] + 1                                                          # Frazer received to date
    post(client, "/records", month="2026-11", source_key="square", coverage_start="2026-11-02", coverage_end="2026-11-08", where_kept="Square dashboard")
    p2 = close_of(db)["prep"]
    assert p2["met"] == p1["met"] + 1
    post(client, "/records", month="2026-11", source_key="payroll_weekly", pay_date="2026-11-06", where_kept="Locked drawer")
    p3 = close_of(db)["prep"]
    assert p3["met"] == p2["met"] + 1
    t = page(client)
    assert f"Preparation {p3['percent']}%" in t and "Close readiness 0%" in t and "does not mean anything is reconciled or closed" in t
    assert close_of(db)["ready"]["met"] == 0


def test_a_partial_upload_does_not_satisfy_the_whole_period_milestone(client, db):
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    cl2 = {c["key"]: c for c in next(s for s in close_of(db)["steps"] if s["key"] == "CL-2")["criteria"]}
    assert cl2["frazer_tx_todate"]["met"] and not cl2["frazer_tx"]["met"]


# ---- next action ------------------------------------------------------------

def test_the_next_action_points_somewhere_and_overdue_comes_first(client, db, today):
    html = client.get(f"{BASE}/?month=2026-11").text
    m = re.search(r'Next action:.*?href="([^"]+)"', html, re.S)
    assert m and m.group(1).startswith(("/", f"{BASE}/?month=2026-11#"))
    today["d"] = date(2026, 12, 9)                                                    # after the BD5 due dates
    assert "Next action: Overdue" in txt(client.get(f"{BASE}/?month=2026-11").text)
    post(client, "/issues", month="2026-11", title="Earlier deadline", due_date="2026-11-10", owner_id="")
    t = txt(client.get(f"{BASE}/?month=2026-11").text)
    assert "Next action: Overdue since Nov 10: Earlier deadline" in t


# ---- indicator on the page --------------------------------------------------

@pytest.mark.parametrize("day,label,extra", [
    (date(2026, 11, 25), "WD−2", "2 workdays to WD0"),
    (date(2026, 11, 27), "WD−1", "1 workday to WD0"),
    (date(2026, 11, 28), "Non-workday", "next close workday Mon Nov 30, 2026 (WD0)"),
    (date(2026, 11, 26), "Non-workday", "Thanksgiving Day"),
    (date(2026, 11, 30), "WD0", "today is WD0"),
    (date(2026, 12, 2), "WD+2", "WD0 was Mon Nov 30, 2026"),
])
def test_the_close_workday_indicator_on_the_page(client, today, day, label, extra):
    today["d"] = day
    t = page(client)
    top = t[: t.index("Close readiness")]
    assert label in top and extra in top and "Provisional" in top and "View closing schedule" in top


def test_the_page_uses_the_chicago_date_not_the_server_utc_date(client, monkeypatch):
    # 11:30 pm Nov 30 in Chicago is already Dec 1 in UTC: the page must still say WD0
    from datetime import timezone
    monkeypatch.undo()
    real = bc.chicago_today
    monkeypatch.setattr(bc, "chicago_today", lambda now=None: real(datetime(2026, 12, 1, 5, 30, tzinfo=timezone.utc)))
    assert "WD0" in page(client)[:700]


# ---- closing schedule (AC-MR schedule cases) -------------------------------

def test_the_schedule_lists_dated_workday_activities_with_required_and_recommended_kept_apart(client):
    t = page(client, path="/schedule")
    assert "Closing schedule · November 2026" in t and "WD0 = Mon Nov 30, 2026" in t
    for needle in ("WD0 Mon Nov 30, 2026", "WD+1 Tue Dec 1, 2026", "BD5 Mon Dec 7, 2026", "BD7 Wed Dec 9, 2026", "BD10 Mon Dec 14, 2026"):
        assert needle in t
    assert "Required Spec §6.2 deadline (a proposal, DEC-17)" in t and "Recommended Recommended (not in the spec)" in t
    assert "Business calendar in use" in t and "Provisional, not confirmed by Mudbugs" in t and "Thanksgiving Day" in t
    assert "Belinda (gathers)" in t and "Drew (reviews)" in t and "Steve or Drew (restricted)" in t


def test_overdue_activities_are_visible_and_passing_a_date_completes_nothing(client, db, today):
    today["d"] = date(2026, 12, 3)
    t = page(client, path="/schedule")
    assert "Overdue" in t
    sched = svc.build_review(db, NOV, date(2026, 12, 3), sync=False)["schedule"]
    snap = next(a for a in sched if a["key"] == "snap")
    assert snap["overdue"] and snap["status"] == "not_started"
    today["d"] = date(2027, 3, 1)                                                     # months later: still not complete
    again = next(a for a in svc.build_review(db, NOV, date(2027, 3, 1), sync=False)["schedule"] if a["key"] == "snap")
    assert again["overdue"] and again["status"] == "not_started"
    s5 = next(a for a in sched if a["key"] == "s5")
    assert s5["date"] == date(2026, 12, 14) and not s5["overdue"]


def test_schedule_completion_comes_from_the_close_steps_not_a_second_checklist(client, db, today):
    today["d"] = date(2026, 12, 1)
    sched = lambda: {a["key"]: a for a in svc.build_review(db, NOV, today["d"], sync=False)["schedule"]}
    assert sched()["tx"]["status"] == "not_started"
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    assert sched()["tx"]["status"] == "complete"                                      # same fact the review page shows
    assert page(client, path="/schedule").count("Complete") >= 1
    # the schedule page has no write routes of its own
    assert client.post(f"{BASE}/schedule", data={"month": "2026-11"}, follow_redirects=False).status_code == 405
    # a required row mirrors its step
    s1 = sched()["s1"]
    assert s1["status"] == next(s for s in close_of(db, d=today["d"])["steps"] if s["key"] == "CL-1")["status"]
    assert s1["prereqs"] and s1["prereqs"][0]["status"] == sched()["bank"]["status"]


def test_the_next_scheduled_activity_is_overdue_aware(client, db, today):
    today["d"] = date(2026, 12, 3)
    ind = svc.build_review(db, NOV, today["d"], sync=False)["indicator"]
    assert ind["next_activity"]["overdue"] and ind["next_activity"]["date"] == date(2026, 11, 30)
    t = page(client)
    assert "Next scheduled:" in t[:2500] and "Overdue" in t[:2500]


def test_schedule_dates_cross_the_year_boundary(client):
    t = page(client, "2026-12", "/schedule")
    assert "WD0 = Thu Dec 31, 2026" in t and "WD+1 Mon Jan 4, 2027" in t and "BD5 Fri Jan 8, 2027" in t and "New Year's Day" in t


def test_a_configured_calendar_changes_wd0_and_is_shown_as_active(client):
    bc.set_calendar(bc.Calendar("Mudbugs confirmed", False, "Confirmed by Steve.", False, [], [{"date": "2026-11-30", "name": "Company closed"}]))
    t = page(client, path="/schedule")
    assert "WD0 = Fri Nov 27, 2026" in t and "Mudbugs confirmed" in t and "Provisional, not confirmed" not in t and "Company closed" in t


# ---- the review writes only its own tables (AC-MR-20) ----------------------

def test_the_whole_workflow_writes_only_review_tables(client, db, today):
    today["d"] = date(2026, 12, 1)
    upload(client, pay_csv([pay(1), late_fee(**{"Sequential ID": "2"})]), "frazer-payments-2026-11-01-through-2026-11-30.csv")
    upload(client, vehicles_csv([vehicle()]), "frazer-vehicles-2026-11-30.csv")
    counts = lambda: {t.name: db.execute(sqltext(f"select count(*) from {t.name}")).scalar() for t in Base.metadata.sorted_tables
                      if not t.name.startswith("review_") and t.name != "users"}
    db.expire_all()
    before = counts()
    rows_before = db.query(FrazerSourceRow).count()
    page(client)
    page(client, path="/schedule")
    post(client, "/records", month="2026-11", source_key="square", coverage_start="2026-11-02", coverage_end="2026-11-30", where_kept="Binder")
    post(client, "/issues", month="2026-11", title="Follow up")
    i = issues(db, month=NOV, kind="manual")[0]
    post(client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="done")
    post(client, "/notes", month="2026-11", note_type="decision", body="Fine")
    post(client, "/steps/CL-4", month="2026-11", kind="not_applicable", action="set", note="No payroll was paid in this period", evidence="Secure folder")
    db.expire_all()
    assert counts() == before and db.query(FrazerSourceRow).count() == rows_before
    assert db.query(ReviewIssue).count() > 5 and db.query(ReviewStepMark).count() == 1


def test_migration_012_chain_is_single_headed_and_after_011():
    import ast
    import glob
    heads = {}
    for f in glob.glob("alembic/versions/0*.py"):
        src = open(f).read()
        rev = re.search(r'^revision\b[^=]*=\s*"(\w+)"', src, re.M).group(1)
        down = re.search(r'^down_revision\b[^=]*=\s*"(\w+)"', src, re.M)
        heads[rev] = down.group(1) if down else None
    parents = set(heads.values())
    assert [r for r in heads if r not in parents] == ["013"]                           # exactly one head
    assert heads["013"] == "012" and heads["012"] == "011" and heads["011"] == "010" and heads["010"] == "009"


@pytest.mark.skipif("FRAZER_INTAKE_PG_URL" not in __import__("os").environ, reason="needs a disposable PostgreSQL (FRAZER_INTAKE_PG_URL)")
def test_migrations_012_013_upgrade_downgrade_upgrade_on_postgres():
    import os
    import subprocess
    import sys
    url = os.environ["FRAZER_INTAKE_PG_URL"]
    assert "host=/" in url, "refuses anything but a unix-socket throwaway database"
    env = {**os.environ, "DATABASE_URL": url}

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    alembic("upgrade", "head")
    assert "013 (head)" in alembic("current")
    alembic("downgrade", "012")
    cur = alembic("current")
    assert "012" in cur and "013" not in cur
    alembic("downgrade", "011")
    assert "012" not in alembic("current")
    alembic("downgrade", "009")
    assert "009" in alembic("current")
    alembic("upgrade", "head")
    assert "013 (head)" in alembic("current")


# ---- schedule anchors -------------------------------------------------------

def test_the_schedule_labels_required_rows_from_the_calendar_month_end_and_recommended_rows_from_wd0(client, db):
    sched = {a["key"]: a for a in svc.build_review(db, date(2026, 10, 1), date(2026, 10, 20), sync=False)["schedule"]}
    assert sched["snap"]["date"] == date(2026, 10, 30) and sched["snap"]["wd_label"] == "WD0"          # recommended: WD0 (Fri)
    assert sched["s1"]["date"] == date(2026, 11, 6) and sched["s1"]["wd_label"] == "BD5"               # required: 5th business day after Sat Oct 31
    assert sched["s1"]["wd_equiv"] == "WD+5" and "after month end" in sched["s1"]["anchor"]
    t = page(client, "2026-10", "/schedule")
    assert "BD5" in t and "Sat Oct 31, 2026" in t and "Fri Oct 30, 2026" in t


# ---- compact overview -------------------------------------------------------

def sections(html):
    return re.findall(r'<details class="card mr-sec" id="([\w-]+)"[^>]*?(\sopen)?>', html)


def test_the_overview_is_compact_sections_are_collapsed_but_nothing_is_lost(client):
    upload(client, pay_csv([pay(1), late_fee(**{"Sequential ID": "2"})]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    html = client.get(f"{BASE}/?month=2026-11").text
    secs = sections(html)
    assert [s for s, _ in secs] == ["status", "records", "attention", "figures", "reconciliation", "owner-review"]
    assert not any(o for _, o in secs)                                                        # all collapsed by default
    t = txt(html)
    top = t[: t.index("1 · Month status")]
    for need in ("Preparation", "Close readiness", "Next action", "View closing schedule", "WD", "Close steps ("):
        assert need in top, need
    head = t[: t.index("Close steps (")]                                                      # what shows before any expansion
    for line in ("in place", "open ·", "Frazer:", "Nothing reconciled", "No notes yet"):      # one-line summaries per section
        assert line in t[t.index("1 · Month status"):], line
    for detail in ("Frazer vehicle list", "Money-received entries", "CL-1", "Add an issue or follow-up action", "Save note"):     # collapsed, not removed
        assert detail in t, detail
    assert len(head) < 3600                                                                   # the first screens stay short
    opened = sections(client.get(f"{BASE}/?month=2026-11&all=1").text)
    assert opened and all(o for _, o in opened)


def test_overdue_and_blocked_items_are_listed_at_the_top(client, db, today):
    today["d"] = date(2026, 12, 9)
    html = client.get(f"{BASE}/?month=2026-11").text
    t = txt(html)
    top = t[: t.index("1 · Month status")]
    assert "Overdue" in top and "Blocked" in top and 'aria-label="Needs attention now"' in html
    assert re.search(r"Next action: Overdue since", top)


def test_the_owner_reply_appears_among_the_top_alerts(client, employee_client, db):
    emp = user(db, "testemployee")
    post(client, "/issues", month="2026-11", title="Where are the count sheets?", owner_id=str(emp.id), message_to_owner="Where?")
    i = issues(db, month=NOV, kind="manual")[0]
    employee_client.post(f"/my-actions/{i.issue_id}/reply", data={"body": "In the binder"}, follow_redirects=False)
    t = txt(client.get(f"{BASE}/?month=2026-11").text)
    assert "Reply" in t[: t.index("1 · Month status")] and "replied" in t[: t.index("1 · Month status")]


def test_reviewing_the_pending_purchases_completes_that_preparation_milestone(client, db):
    from decimal import Decimal
    from app.models.enums import PurchaseCategory, ReviewStatus
    from app.services import purchase_service
    cat = next(c for c in PurchaseCategory if c.name.startswith("general_expense"))
    ps = [purchase_service.record_purchase(purchase_date=date(2026, 11, d), purchaser="Belinda", vendor_name="DEMO Supply", amount=Decimal("10.00"),
                                           category=cat, description="DEMO", entered_by="Belinda", db=db) for d in (3, 4)]
    db.commit()
    item = lambda: next(c for s in close_of(db)["steps"] if s["key"] == "CL-5" for c in s["criteria"] if c["key"] == "purchases_reviewed")
    assert item()["state"] == "partial" and not item()["met"] and "2 of 2" in item()["why"]
    before = close_of(db)["prep"]["met"]
    for p in ps:
        p.review_status = ReviewStatus.reviewed
    db.commit()
    assert item()["met"] and close_of(db)["prep"]["met"] == before + 1
    assert close_of(db)["ready"]["met"] == 0


# ---- phone layout (REQ-MR-30) ----------------------------------------------
# The browser measurement (Chromium at 375/390/430 px) lives in /home/drew/rto-reviews/.../mobile/measure.py; these checks guard
# the markup and stylesheet it depends on, so the clipped-column bug cannot come back unnoticed.

def test_wide_tables_stack_into_labelled_rows_and_scroll_regions_are_marked(client):
    upload(client, pay_csv([pay(1)]), "frazer-payments-2026-11-01-through-2026-11-08.csv")
    for path in (f"{BASE}/?month=2026-11&all=1", f"{BASE}/schedule?month=2026-11"):
        html = client.get(path).text
        assert 'class="mr-page"' in html
        stacked = re.findall(r'<table class="mr-stack">(.*?)</table>', html, re.S)
        assert stacked
        for body in stacked:
            heads = re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)
            tds = re.findall(r"<td\b[^>]*>", body)
            assert tds and all("data-label=" in t for t in tds), "every cell of a stacked table carries its column name"
            assert len(tds) % len(heads) == 0                                              # nothing dropped
        # no table region may clip silently: each container is an explicit scroll region
        assert '<div class="table-container">' not in html
        assert all('role="region"' in c and "tabindex" in c for c in re.findall(r'<div class="table-container mr-scroll"[^>]*>', html))
    css = open("app/static/css/style.css").read()
    assert ".table-container.mr-scroll" in css and "overflow-x: auto" in css and "table.mr-stack td::before" in css
    assert "@media (max-width: 700px)" in css
    assert "style.css?v=4" in client.get(f"{BASE}/?month=2026-11").text                  # phones must not keep the old cached stylesheet


def test_my_actions_pages_use_the_phone_safe_wrapper(client, employee_client, db):
    from tests.test_my_actions import assign
    i = assign(client, db, user(db, "testemployee"))
    assert 'class="mr-page"' in employee_client.get("/my-actions/").text
    assert 'class="mr-page"' in employee_client.get(f"/my-actions/{i.issue_id}").text
