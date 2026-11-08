"""
My actions: the restricted view of assigned review issues (spec section 20, REQ-MR-23..25). Synthetic data only.

Belinda (an employee) sees only the questions assigned to HER, can answer them and supply permitted records, and never gets the
owner's Monthly Review page, restricted payroll, financial totals, internal notes or anyone else's items.
"""
import io
import os
import re
from datetime import date

import pytest

import app.services.my_actions_service as ma
from app.models.monthly_review import ReviewIssue, ReviewIssueFile, ReviewSourceRecord
from app.models.user import User
from app.services import business_calendar as bc
from tests.test_monthly_review import issue_by_key, issues, post, txt, user  # noqa: F401  (shared helpers)

BASE = "/my-actions"
NOV = date(2026, 11, 1)
SECRET_DESC = "INTERNAL reconcile the 12345.67 difference with the bank"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(bc, "chicago_today", lambda now=None: date(2026, 11, 9))
    prev = bc.set_calendar(bc.load_calendar())
    yield
    bc.set_calendar(prev)


def assign(admin, db, emp, **kw):
    """An administrator creates an issue for the employee. Returns the issue."""
    data = dict(month="2026-11", title="Where are the Nov 2-8 cash count sheets?", description=SECRET_DESC, category="records", checklist_ref="CL-1",
                owner_id=str(emp.id), due_date="2026-11-12", link="/finance/frazer/", message_to_owner="Please tell me where they are kept.")
    data.update(kw)
    r = post(admin, "/issues", **data)
    assert r.status_code == 303, r.text[:300]
    return issues(db, month=NOV, kind="manual")[-1]


def get(c, path):
    return c.get(f"{BASE}{path}", follow_redirects=False)


def test_the_employee_sees_her_questions_and_nothing_internal(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    post(admin_client, f"/issues/{i.id}/update", month="2026-11", note="INTERNAL NOTE total is $999,999")
    listing = employee_client.get(f"{BASE}/").text
    assert i.title in listing and i.issue_id in listing
    html = employee_client.get(f"{BASE}/{i.issue_id}").text
    t = txt(html)
    assert i.title in t and "Please tell me where they are kept." in t and "An administrator" in t
    for hidden in (SECRET_DESC, "12345.67", "INTERNAL NOTE", "999,999", "Automatic evidence", "Source link"):
        assert hidden not in html, hidden
    hrefs = set(re.findall(r'href="([^"]+)"', html))
    assert not any(h.startswith(("/finance", "/documents", "/transactions", "/units", "/customers")) for h in hrefs), hrefs


def test_the_employee_cannot_open_the_owners_pages_or_any_linked_record(employee_client, admin_client, db):
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    for path in ("/finance/review/?month=2026-11", "/finance/review/schedule?month=2026-11", "/finance/frazer/", "/finance/opening/", f"/finance/review/?month=2026-10"):
        assert employee_client.get(path).status_code == 403, path
    for path in ("/documents/", "/transactions/", "/exceptions/"):
        assert employee_client.get(path).status_code == 403, path
    assert employee_client.post("/finance/review/notes", data={"month": "2026-11", "body": "x"}, follow_redirects=False).status_code == 403
    assert employee_client.post(f"/finance/review/issues/{i.id}/update", data={"month": "2026-11", "status": "resolved", "note": "x"}, follow_redirects=False).status_code == 403


def test_visitors_are_sent_to_sign_in_on_every_page_and_download(anon_client):
    for path in ("/", "/RI-0001", "/files/1"):
        r = anon_client.get(f"{BASE}{path}", follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"], path
    for path in ("/RI-0001/reply", "/RI-0001/files", "/RI-0001/record"):
        assert anon_client.post(f"{BASE}{path}", data={}, follow_redirects=False).status_code == 303, path


def test_only_her_own_unrestricted_issues_are_reachable(admin_client, employee_client, db):
    emp, admin = user(db, "testemployee"), user(db, "testadmin")
    mine = assign(admin_client, db, emp)
    theirs = assign(admin_client, db, admin, title="An administrator-only question", owner_id=str(admin.id), message_to_owner="")
    unassigned = assign(admin_client, db, emp, title="Someone else's later", owner_id="")
    assert get(employee_client, f"/{mine.issue_id}").status_code == 200
    for ref in (theirs.issue_id, unassigned.issue_id, "RI-9999", "nonsense", str(theirs.id)):
        assert get(employee_client, f"/{ref}").status_code == 404, ref
    assert theirs.title not in employee_client.get(f"{BASE}/").text and unassigned.title not in employee_client.get(f"{BASE}/").text
    # writes are equally narrow
    assert employee_client.post(f"{BASE}/{theirs.issue_id}/reply", data={"body": "hi"}, follow_redirects=False).status_code == 404
    assert employee_client.post(f"{BASE}/{theirs.issue_id}/files", files={"file": ("a.txt", b"x", "text/plain")}, follow_redirects=False).status_code == 404


def test_payroll_items_are_never_assigned_to_or_shown_to_an_employee(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    r = post(admin_client, "/issues", month="2026-11", title="Payroll check", category="payroll", owner_id=str(emp.id), description="kept in drawer")
    assert r.status_code == 400 and "stay with administrators" in txt(r.text)
    admin = user(db, "testadmin")
    post(admin_client, "/issues", month="2026-11", title="Payroll check", category="payroll", owner_id=str(admin.id), description="kept in drawer")
    pay = issues(db, month=NOV, category="payroll", kind="manual")[0]
    r = post(admin_client, f"/issues/{pay.id}/update", month="2026-11", owner_id=str(emp.id))
    assert r.status_code == 400
    # even if a row were assigned behind the app's back (a bug or a manual fix), the view still refuses
    db.expire_all()
    p = db.get(ReviewIssue, pay.id)
    p.owner_user_id = emp.id
    db.commit()
    assert get(employee_client, f"/{pay.issue_id}").status_code == 404
    assert pay.title not in employee_client.get(f"{BASE}/").text
    # an automatic payroll issue is likewise unreachable
    admin_client.get("/finance/review/?month=2026-11")
    auto = issue_by_key(db, "payroll:2026-11-06")
    auto.owner_user_id = emp.id
    db.commit()
    assert get(employee_client, f"/{auto.issue_id}").status_code == 404 and auto.title not in employee_client.get(f"{BASE}/").text


def test_replying_is_recorded_in_the_shared_issue_history_and_flagged_to_the_owners(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    r = employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "They are in the green binder on the shelf.", "status": "in_progress"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    i = db.get(ReviewIssue, i.id)
    kinds = [(e.event_type, e.user_id) for e in i.events]
    assert ("owner_reply", emp.id) in kinds and ("status", emp.id) in kinds and i.status == "in_progress"      # one queue, one history
    t = txt(admin_client.get("/finance/review/?month=2026-11").text)
    assert "They are in the green binder" in t and "reply from owner" in t and "Reply" in t
    assert i.title and "replied" in t


def test_she_cannot_resolve_reopen_or_choose_other_statuses(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    for st in ("resolved", "open", "bogus"):
        assert employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "done", "status": st}, follow_redirects=False).status_code == 400, st
    assert employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "", "status": ""}, follow_redirects=False).status_code == 400
    post(admin_client, f"/issues/{i.id}/update", month="2026-11", status="resolved", note="Answered; closing")
    assert employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "more"}, follow_redirects=False).status_code == 400
    assert employee_client.post(f"{BASE}/{i.issue_id}/files", files={"file": ("a.txt", b"x", "text/plain")}, follow_redirects=False).status_code == 400
    assert "closed" in txt(get(employee_client, f"/{i.issue_id}").text) or get(employee_client, f"/{i.issue_id}").status_code == 200


def test_files_are_stored_privately_and_downloaded_only_by_the_owner_and_administrators(admin_client, employee_client, db, tmp_path):
    emp, admin = user(db, "testemployee"), user(db, "testadmin")
    i = assign(admin_client, db, emp)
    up = lambda name, data, note="": employee_client.post(f"{BASE}/{i.issue_id}/files", data={"note": note}, files={"file": (name, io.BytesIO(data), "application/octet-stream")}, follow_redirects=False)
    assert up("count-sheet.txt", b"Nov 2-8 count sheets, 5 pages", "photo of the binder").status_code == 303
    f = db.query(ReviewIssueFile).one()
    assert f.user_id == emp.id and f.original_filename == "count-sheet.txt" and f.stored_path.startswith(ma.storage_root()) and os.path.isfile(f.stored_path)
    assert os.path.basename(f.stored_path) != "count-sheet.txt"                                  # never served by its own name
    # refusals
    assert up("count-sheet.txt", b"Nov 2-8 count sheets, 5 pages").status_code == 400            # the exact file again
    assert up("virus.exe", b"MZ").status_code == 400 and up("page.html", b"<script>").status_code == 400
    assert up("empty.txt", b"").status_code == 400
    assert up("../../etc/passwd.txt", b"harmless").status_code == 303                           # path is stripped, not followed
    assert all(os.path.dirname(x.stored_path) == ma.storage_root() for x in db.query(ReviewIssueFile))
    assert db.query(ReviewIssueFile).filter(ReviewIssueFile.original_filename == "passwd.txt").count() == 1
    # downloads
    r = employee_client.get(f"{BASE}/files/{f.id}")
    assert r.status_code == 200 and r.content == b"Nov 2-8 count sheets, 5 pages"
    assert r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert admin_client.get(f"{BASE}/files/{f.id}").status_code == 200
    assert "/my-actions/files/" in admin_client.get("/finance/review/?month=2026-11").text            # administrators find it on the issue
    # not in the shared Documents list, and not reachable by anyone else
    assert "count-sheet" not in admin_client.get("/documents/").text
    assert get(employee_client, "/files/99999").status_code == 404


def test_another_employee_cannot_open_her_file_or_question(admin_client, employee_client, db):
    from app.models.enums import UserRole
    from app.services import user_service
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from tests.conftest import TEST_PASSWORD
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    employee_client.post(f"{BASE}/{i.issue_id}/files", files={"file": ("a.txt", b"mine", "text/plain")}, follow_redirects=False)
    f = db.query(ReviewIssueFile).one()
    user_service.create_user(db, username="other", display_name="Other", role=UserRole.employee, password=TEST_PASSWORD, must_change_password=False)
    db.commit()
    other = TestClient(fastapi_app, headers={"Origin": "http://testserver"})
    other.__enter__()
    try:
        assert other.post("/login", data={"username": "other", "password": TEST_PASSWORD}, follow_redirects=False).status_code == 303
        assert other.get(f"{BASE}/files/{f.id}").status_code == 404
        assert other.get(f"{BASE}/{i.issue_id}").status_code == 404
        assert i.title not in other.get(f"{BASE}/").text
    finally:
        other.__exit__(None, None, None)


def test_a_restricted_issue_blocks_the_download_even_for_its_owner(admin_client, employee_client, db, tmp_path):
    from datetime import datetime
    emp, admin = user(db, "testemployee"), user(db, "testadmin")
    post(admin_client, "/issues", month="2026-11", title="Payroll folder check", category="payroll", owner_id=str(admin.id), description="kept in drawer")
    pay = issues(db, month=NOV, category="payroll", kind="manual")[0]
    path = os.path.join(ma.storage_root(), "x.txt")
    os.makedirs(ma.storage_root(), exist_ok=True)
    open(path, "wb").write(b"restricted evidence")
    db.add(ReviewIssueFile(issue_pk=pay.id, original_filename="x.txt", stored_path=path, size_bytes=19, sha256="0" * 64, note="", user_id=admin.id, created_at=datetime.utcnow()))
    db.commit()
    f = db.query(ReviewIssueFile).one()
    pay = db.get(ReviewIssue, pay.id)
    pay.owner_user_id = emp.id                      # an assignment that slipped past the form
    db.commit()
    assert get(employee_client, f"/files/{f.id}").status_code == 404 and get(employee_client, f"/{pay.issue_id}").status_code == 404
    assert admin_client.get(f"{BASE}/files/{f.id}").status_code == 200


def test_the_employee_can_note_that_she_has_a_record_for_a_source_question_only(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    admin_client.get("/finance/review/?month=2026-11")
    bank = issue_by_key(db, "source:bank_statement")
    post(admin_client, f"/issues/{bank.id}/update", month="2026-11", owner_id=str(emp.id), message_to_owner="Do you have the October statement?")
    assert "I have this record" in employee_client.get(f"{BASE}/{bank.issue_id}").text
    assert employee_client.post(f"{BASE}/{bank.issue_id}/record", data={"coverage_start": "2026-11-02", "coverage_end": "2026-11-15"}, follow_redirects=False).status_code == 400   # nowhere kept
    r = employee_client.post(f"{BASE}/{bank.issue_id}/record", data={"coverage_start": "2026-11-02", "coverage_end": "2026-11-15", "where_kept": "Bank portal"}, follow_redirects=False)
    assert r.status_code == 303
    rec = db.query(ReviewSourceRecord).one()
    assert rec.user_id == emp.id and rec.source_key == "bank_statement" and rec.status == "received"
    assert "Not covered: Nov 16 to Nov 30" in txt(admin_client.get("/finance/review/?month=2026-11").text)      # the owners see it; nothing was verified
    # not for a payroll source, nor a question that is not about a record
    manual = assign(admin_client, db, emp, title="A general question")
    assert employee_client.post(f"{BASE}/{manual.issue_id}/record", data={"coverage_start": "2026-11-02", "coverage_end": "2026-11-15", "where_kept": "x"}, follow_redirects=False).status_code == 400
    assert "I have this record" not in employee_client.get(f"{BASE}/{manual.issue_id}").text


def test_payroll_text_in_a_reply_is_refused(admin_client, employee_client, db):
    emp = user(db, "testemployee")
    i = assign(admin_client, db, emp)
    assert employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "Weekly payroll was $5,694 total"}, follow_redirects=False).status_code == 400
    assert employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "The sheets say we took $120 in cash on Tuesday"}, follow_redirects=False).status_code == 303


def test_employee_posts_need_a_matching_origin(admin_client, employee_client, db):
    i = assign(admin_client, db, user(db, "testemployee"))
    r = employee_client.post(f"{BASE}/{i.issue_id}/reply", data={"body": "x"}, headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_my_actions_is_in_the_menu_for_everyone_signed_in_and_the_review_link_only_for_administrators(admin_client, employee_client):
    assert "/my-actions/" in employee_client.get("/purchases/").text and "/finance/review/" not in employee_client.get("/purchases/").text
    assert "/my-actions/" in admin_client.get("/").text and "/finance/review/" in admin_client.get("/").text


def test_assigning_grants_no_access_to_the_review_page(admin_client, employee_client, db):
    assign(admin_client, db, user(db, "testemployee"))
    assert employee_client.get("/finance/review/?month=2026-11").status_code == 403
    assert "no access to this page" in admin_client.get("/finance/review/?month=2026-11").text
