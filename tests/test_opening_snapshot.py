"""
Opening Snapshot (Slice A): acceptance tests AC-OS-01..16 from
docs/specs/cash-financial-control-spec.md section 18.6.

Synthetic DEMO figures only. Real Workday 1 figures are never seeded.
"""
import hashlib
import os
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.services.opening_snapshot_service as svc
from app.main import app as fastapi_app
from app.models.document import Document
from app.models.enums import OpeningEvidenceKind, OpeningFigureStatus
from app.models.opening_snapshot import (
    ImmutableRecordError, OpeningFigure, OpeningFigureEntry, OpeningFigureEvidence, OpeningSnapshot,
)
from app.models.user import User

BASE = "/finance/opening"
FORBIDDEN_WORDS = re.compile(r"\b(Available|Projected|Equity|Net|Subtotal|Total)\b", re.I)
ORIGINAL_COLUMNS = [
    "section", "label", "reported_amount", "reported_count", "reported_statement", "reported_by_name",
    "reported_via", "reported_on", "reported_time_note", "reported_cutoff", "status", "basis_note",
    "pending_need", "due_note", "display_order", "recorded_by_user_id", "recorded_at", "snapshot_id",
]


# ---- helpers ---------------------------------------------------------------

def main_block(html: str) -> str:
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S)
    assert m, "page has no <main> block"
    return m.group(1)


def without_statements(html: str) -> str:
    return re.sub(r"<div[^>]*figure-statement[^>]*>.*?</div>", "", html, flags=re.S)


def make_snapshot(client):
    r = client.post(f"{BASE}/setup", data={
        "name": "DEMO Starting position", "as_of_date": "2026-11-02", "label": "Workday 1",
        "cutoff_note": "Unknown"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def make_figure(client, **over):
    data = {"section": "cash", "label": "DEMO Bank balance", "status": "reported", "amount": "10000",
            "reported_by": "DEMO Steve", "reported_via": "text message", "reported_on": "2026-11-02",
            "reported_cutoff": "Unknown", "statement": "", "pending_need": ""}
    data.update(over)
    r = client.post(f"{BASE}/figures/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(re.search(r"/figures/(\d+)", r.headers["location"]).group(1))


def add_clarification(client, fid, **over):
    data = {"body": "DEMO clarification"}
    data.update(over)
    r = client.post(f"{BASE}/figures/{fid}/clarify", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def upload(client, fid, content=b"DEMO evidence bytes", filename="demo.txt", confirm=True, **over):
    data = {"kind": "message_from_steve", "description": "DEMO screenshot of the text"}
    if confirm:
        data["no_pay_detail"] = "yes"
    data.update(over)
    return client.post(f"{BASE}/figures/{fid}/evidence", data=data,
                       files={"file": (filename, content, "text/plain")}, follow_redirects=False)


def stored_files(tmp_path):
    return [os.path.join(d, f) for d, _, fs in os.walk(tmp_path) for f in fs]


def original_values(db, fid):
    db.expire_all()
    f = db.get(OpeningFigure, fid)
    return {c: getattr(f, c) for c in ORIGINAL_COLUMNS}


def user_id(db, username):
    return db.scalar(select(User.id).where(User.username == username))


# ---- AC-OS-01 --------------------------------------------------------------

def test_ac_os_01_overview_shows_reported_and_pending(client):
    make_snapshot(client)
    make_figure(client, label="DEMO Bank balance", amount="10000")
    make_figure(client, label="DEMO Cash on hand", amount="500")
    make_figure(client, section="not_reported", label="DEMO Bank account list", status="pending",
                amount="", pending_need="DEMO account list from the owner")
    html = client.get(f"{BASE}/").text
    assert "$10,000" in html and "$500" in html
    assert "Reported" in html and "Pending" in html
    assert "Pending — DEMO account list from the owner" in html
    assert "$0" not in html
    assert "Reported figures — not verified" in html
    # fixed section order
    order = [html.index(t) for t in ("Cash reported", "Other reported figures", "Debts reported",
                                     "Known commitments", "Not yet reported")]
    assert order == sorted(order)


def test_pending_item_cannot_carry_an_amount_or_lack_a_need(client):
    make_snapshot(client)
    r = client.post(f"{BASE}/figures/new", data={
        "section": "not_reported", "label": "DEMO x", "status": "pending", "amount": "0",
        "pending_need": "need", "reported_by": "DEMO", "reported_via": "text"})
    assert r.status_code == 400 and "no amount" in r.text
    r = client.post(f"{BASE}/figures/new", data={
        "section": "not_reported", "label": "DEMO x", "status": "pending", "amount": "",
        "pending_need": "", "reported_by": "DEMO", "reported_via": "text"})
    assert r.status_code == 400 and "what is needed" in r.text


# ---- AC-OS-02 --------------------------------------------------------------

def test_ac_os_02_no_totals_available_equity_net(client):
    make_snapshot(client)
    a = make_figure(client, label="DEMO Bank balance", amount="10000",
                    statement="DEMO reported $10,500 total")
    make_figure(client, label="DEMO Cash on hand", amount="500")
    make_figure(client, section="debt", label="DEMO Floor plan", amount="4000", count="12")
    fid = make_figure(client, section="not_reported", label="DEMO Other obligations", status="pending",
                      amount="", pending_need="DEMO list")
    pages = [client.get(f"{BASE}/").text] + [client.get(f"{BASE}/figures/{i}").text for i in (a, fid)]
    for html in pages:
        text = without_statements(main_block(html))
        assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text).group(0)
    # the statement itself may use the word, and is shown as the owner's words
    assert "DEMO reported $10,500 total" in pages[0]
    # and nothing is summed: $10,000 + $500 never appears
    assert "$10,500" not in without_statements(pages[0])


# ---- AC-OS-03 / 05 ---------------------------------------------------------

def test_ac_os_03_clarification_leaves_original_unchanged(client, db):
    make_snapshot(client)
    fid = make_figure(client, statement="DEMO said so")
    before = original_values(db, fid)
    add_clarification(client, fid, body="DEMO from the bank app at 8 AM", cutoff_update="Nov 2, 8:00 AM, bank app",
                      basis_update="statement balance", basis_confirmed="yes", refers_to_date="2026-11-02")
    assert original_values(db, fid) == before
    html = client.get(f"{BASE}/figures/{fid}").text
    cu = html[html.index("Current understanding"):html.index("History")]
    assert "Nov 2, 8:00 AM, bank app" in cu and "statement balance" in cu and "Yes" in cu
    orig = html[html.index("Original report"):html.index("Current understanding")]
    assert "Unknown" in orig and "8:00 AM" not in orig


def test_ac_os_05_correction_links_and_current_understanding_follows_it(client):
    make_snapshot(client)
    fid = make_figure(client)
    add_clarification(client, fid, body="DEMO first clarification", cutoff_update="Nov 2, 9:00 PM")
    html = client.get(f"{BASE}/figures/{fid}").text
    first_id = int(re.search(r'id="entry-(\d+)"', html).group(1))
    add_clarification(client, fid, body="DEMO that was the wrong time", cutoff_update="Nov 2, 9:00 AM",
                      corrects_entry_id=str(first_id))
    html = client.get(f"{BASE}/figures/{fid}").text
    cu = html[html.index("Current understanding"):html.index("History")]
    assert "Nov 2, 9:00 AM" in cu and "9:00 PM" not in cu
    hist = html[html.index("History"):]
    assert "DEMO first clarification" in hist and "DEMO that was the wrong time" in hist
    assert f'href="#entry-{first_id}"' in hist and "Corrected by a later entry" in hist


def test_correction_without_new_values_falls_back_to_original_cutoff(client):
    make_snapshot(client)
    fid = make_figure(client)
    add_clarification(client, fid, body="DEMO a mistaken cutoff", cutoff_update="Nov 9, noon")
    first_id = int(re.search(r'id="entry-(\d+)"', client.get(f"{BASE}/figures/{fid}").text).group(1))
    add_clarification(client, fid, body="DEMO disregard that", corrects_entry_id=str(first_id))
    html = client.get(f"{BASE}/figures/{fid}").text
    cu = html[html.index("Current understanding"):html.index("History")]
    assert "Nov 9" not in cu and "as originally reported" in cu


# ---- AC-OS-04 --------------------------------------------------------------

def test_ac_os_04_no_update_or_delete_routes_or_service_functions(client):
    make_snapshot(client)
    fid = make_figure(client)
    upload(client, fid)
    paths = [r for r in fastapi_app.routes if getattr(r, "path", "").startswith(BASE)]
    assert paths
    for r in paths:
        assert r.methods <= {"GET", "HEAD", "POST"}, (r.path, r.methods)
        assert not re.search(r"(edit|update|delete|remove)", r.path), r.path
    for method in ("put", "patch", "delete"):
        for p in (f"{BASE}/figures/{fid}", f"{BASE}/figures/{fid}/clarify", f"{BASE}/evidence/1/download",
                  f"{BASE}/", f"{BASE}/original"):
            assert getattr(client, method)(p).status_code in (404, 405), (method, p)
    for p in (f"{BASE}/figures/{fid}/edit", f"{BASE}/figures/{fid}/delete", f"{BASE}/evidence/1/delete"):
        assert client.post(p, data={"label": "x"}).status_code == 404
    public = [n for n in dir(svc) if not n.startswith("_") and callable(getattr(svc, n))]
    assert not [n for n in public if re.match(r"(update|edit|delete|remove|set|change)_", n)], public


def test_model_refuses_to_change_or_delete_originals(client, db):
    make_snapshot(client)
    fid = make_figure(client)
    add_clarification(client, fid)
    upload(client, fid)
    db.expire_all()
    fig = db.get(OpeningFigure, fid)
    fig.reported_amount = Decimal("1.00")
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()
    fig = db.get(OpeningFigure, fid)
    fig.reported_cutoff = "Nov 9"
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()
    for model in (OpeningFigureEntry, OpeningFigureEvidence, OpeningFigure, OpeningSnapshot):
        row = db.scalars(select(model)).first()
        db.delete(row)
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    entry = db.scalars(select(OpeningFigureEntry)).first()
    entry.body = "tampered"
    with pytest.raises(ImmutableRecordError):
        db.flush()
    db.rollback()


# ---- AC-OS-06 --------------------------------------------------------------

def test_ac_os_06_evidence_storage_hash_and_download(client, tmp_path):
    make_snapshot(client)
    fid = make_figure(client)
    payload = b"DEMO bytes \x00\x01\x02 for hashing"
    r = upload(client, fid, content=payload, filename="Steve text.txt")
    assert r.status_code == 303
    files = stored_files(tmp_path)
    assert len(files) == 1
    path = files[0]
    assert os.path.join("opening_snapshot", str(fid)) in path
    assert os.path.basename(path) != "Steve text.txt" and re.fullmatch(r"[0-9a-f]{32}\.txt", os.path.basename(path))
    page = client.get(f"{BASE}/figures/{fid}").text
    assert "Steve text.txt" in page and hashlib.sha256(payload).hexdigest()[:12] in page
    dl = re.search(r'href="(/finance/opening/evidence/\d+/download)"', page).group(1)
    resp = client.get(dl)
    assert resp.status_code == 200 and resp.content == payload
    disp = resp.headers["content-disposition"]
    from urllib.parse import unquote
    assert disp.startswith("attachment") and "Steve text.txt" in unquote(disp)
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_ac_os_06_traversal_filename_is_stored_safely(client, tmp_path):
    make_snapshot(client)
    fid = make_figure(client)
    r = upload(client, fid, filename="../../x.pdf", content=b"%PDF-DEMO")
    assert r.status_code == 303
    files = stored_files(tmp_path)
    assert len(files) == 1 and files[0].startswith(str(tmp_path))
    assert os.path.join("opening_snapshot", str(fid)) in files[0]
    assert "x.pdf" in client.get(f"{BASE}/figures/{fid}").text
    assert not os.path.exists(os.path.join(tmp_path, "x.pdf"))


def test_unknown_extension_empty_and_oversize_files_are_rejected(client, tmp_path, monkeypatch):
    make_snapshot(client)
    fid = make_figure(client)
    assert upload(client, fid, filename="run.exe").status_code == 400
    assert upload(client, fid, content=b"").status_code == 400
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 10)
    assert upload(client, fid, content=b"x" * 11).status_code == 400
    assert stored_files(tmp_path) == []


# ---- AC-OS-07 --------------------------------------------------------------

def test_ac_os_07_upload_needs_no_pay_detail_confirmation(client, db, tmp_path):
    make_snapshot(client)
    fid = make_figure(client)
    r = upload(client, fid, confirm=False)
    assert r.status_code == 400 and "does not contain individual employee pay" in r.text
    assert db.scalars(select(OpeningFigureEvidence)).first() is None
    assert db.scalars(select(OpeningFigureEntry)).first() is None
    assert stored_files(tmp_path) == []


# ---- AC-OS-08 --------------------------------------------------------------

def test_ac_os_08_evidence_is_not_in_documents(client, db):
    make_snapshot(client)
    fid = make_figure(client)
    upload(client, fid, filename="secret-demo.txt")
    assert db.scalars(select(Document)).first() is None
    assert "secret-demo.txt" not in client.get("/documents/").text
    ev_id = db.scalars(select(OpeningFigureEvidence.id)).first()
    assert client.get(f"/documents/{ev_id}/download").status_code == 404


# ---- AC-OS-09 --------------------------------------------------------------

def _all_snapshot_requests(fid, ev_id):
    return [
        ("get", f"{BASE}/"), ("get", f"{BASE}/setup"), ("post", f"{BASE}/setup"),
        ("get", f"{BASE}/figures/new"), ("post", f"{BASE}/figures/new"),
        ("get", f"{BASE}/figures/{fid}"),
        ("get", f"{BASE}/figures/{fid}/clarify"), ("post", f"{BASE}/figures/{fid}/clarify"),
        ("get", f"{BASE}/figures/{fid}/evidence"), ("post", f"{BASE}/figures/{fid}/evidence"),
        ("post", f"{BASE}/figures/{fid}/withdraw"),
        ("get", f"{BASE}/evidence/{ev_id}/download"),
        ("get", f"{BASE}/original"), ("get", f"{BASE}/withdrawn"),
    ]


def test_ac_os_09_employee_is_refused_everywhere_and_anonymous_redirected(
        admin_client, employee_client, anon_client, db, tmp_path):
    make_snapshot(admin_client)
    fid = make_figure(admin_client)
    upload(admin_client, fid, content=b"DEMO private")
    ev_id = db.scalars(select(OpeningFigureEvidence.id)).first()
    figures_before = db.scalars(select(OpeningFigure)).all()
    for method, path in _all_snapshot_requests(fid, ev_id):
        r = getattr(employee_client, method)(path, follow_redirects=False)
        assert r.status_code == 403, (method, path, r.status_code)
        assert "DEMO private" not in r.text
        r = getattr(anon_client, method)(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), (method, path, r.status_code)
    # the html version uses the app's friendly page
    r = employee_client.get(f"{BASE}/", headers={"accept": "text/html"})
    assert r.status_code == 403 and "Not allowed" in r.text
    # nothing was created by the refused posts
    db.expire_all()
    assert len(db.scalars(select(OpeningFigure)).all()) == len(figures_before)


def test_employee_and_anonymous_cannot_download_evidence_by_direct_url(
        admin_client, employee_client, anon_client, db):
    make_snapshot(admin_client)
    fid = make_figure(admin_client)
    payload = b"DEMO confidential evidence"
    upload(admin_client, fid, content=payload)
    ev_id = db.scalars(select(OpeningFigureEvidence.id)).first()
    url = f"{BASE}/evidence/{ev_id}/download"
    assert admin_client.get(url).content == payload
    r = employee_client.get(url)
    assert r.status_code == 403 and payload not in r.content
    r = anon_client.get(url, follow_redirects=False)
    assert r.status_code == 303 and payload not in r.content


def test_snapshot_link_is_in_the_menu_for_admins_only(admin_client, employee_client):
    assert "Opening Snapshot" in admin_client.get("/purchases/").text
    assert "Opening Snapshot" not in employee_client.get("/purchases/").text


# ---- AC-OS-10 --------------------------------------------------------------

def test_ac_os_10_people_come_from_the_session_not_the_form(client, db):
    make_snapshot(client)
    me = user_id(db, "testadmin")
    forged = {"recorded_by_user_id": "99999", "created_by_user_id": "99999", "uploaded_by_user_id": "99999",
              "user_id": "99999", "recorded_by": "Mallory", "entered_by": "Mallory"}
    fid = make_figure(client, **forged)
    add_clarification(client, fid, **forged)
    r = upload(client, fid, **forged)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(OpeningFigure, fid).recorded_by_user_id == me
    assert db.scalar(select(OpeningSnapshot.created_by_user_id)) == me
    assert {e.created_by_user_id for e in db.scalars(select(OpeningFigureEntry))} == {me}
    assert db.scalar(select(OpeningFigureEvidence.uploaded_by_user_id)) == me
    client.post(f"{BASE}/figures/{fid}/withdraw", data={"reason": "DEMO", **forged})
    db.expire_all()
    assert db.get(OpeningFigure, fid).withdrawn_by_user_id == me


# ---- AC-OS-11 / 12 ---------------------------------------------------------

def test_ac_os_11_withdraw_keeps_history_and_original(client, db):
    make_snapshot(client)
    keep = make_figure(client, label="DEMO Keep me")
    fid = make_figure(client, label="DEMO Typed by mistake", amount="777")
    add_clarification(client, fid, body="DEMO note before withdrawal")
    r = client.post(f"{BASE}/figures/{fid}/withdraw", data={"reason": "DEMO entered twice"}, follow_redirects=False)
    assert r.status_code == 303
    overview = client.get(f"{BASE}/").text
    assert "DEMO Typed by mistake" not in overview and "DEMO Keep me" in overview
    assert "Withdrawn (1)" in overview
    listing = client.get(f"{BASE}/withdrawn").text
    assert "DEMO Typed by mistake" in listing and "DEMO entered twice" in listing
    detail = client.get(f"{BASE}/figures/{fid}").text
    assert "DEMO note before withdrawal" in detail and "Withdrawn: DEMO entered twice" in detail
    assert "$777" in detail
    original = client.get(f"{BASE}/original").text
    assert "DEMO Typed by mistake" in original and "Withdrawn" in original and "$777" in original
    again = client.post(f"{BASE}/figures/{fid}/withdraw", data={"reason": "again"})
    assert again.status_code == 409
    db.expire_all()
    assert db.get(OpeningFigure, fid).withdrawn_reason == "DEMO entered twice"
    # a withdrawn figure takes no more clarifications or evidence
    assert client.post(f"{BASE}/figures/{fid}/clarify", data={"body": "late"}).status_code == 409
    assert upload(client, fid).status_code == 409
    assert client.get(f"{BASE}/figures/{fid}/clarify").status_code == 409
    assert keep


def test_withdraw_needs_a_reason(client):
    make_snapshot(client)
    fid = make_figure(client)
    r = client.post(f"{BASE}/figures/{fid}/withdraw", data={"reason": "  "})
    assert r.status_code == 400
    assert "DEMO Bank balance" in client.get(f"{BASE}/").text


def test_ac_os_12_original_report_is_unchanged_by_everything_after(client):
    make_snapshot(client)
    fid = make_figure(client, amount="10000", reported_cutoff="Unknown", statement="DEMO original words",
                      label="DEMO Original figure")
    add_clarification(client, fid, body="DEMO c1", cutoff_update="DEMOCUTOFF-ALPHA", basis_update="statement balance")
    first = int(re.search(r'id="entry-(\d+)"', client.get(f"{BASE}/figures/{fid}").text).group(1))
    add_clarification(client, fid, body="DEMO c2", cutoff_update="DEMOCUTOFF-BRAVO", corrects_entry_id=str(first))
    upload(client, fid)
    client.post(f"{BASE}/figures/{fid}/withdraw", data={"reason": "DEMO"})
    original = main_block(client.get(f"{BASE}/original").text)
    assert "$10,000" in original and "Unknown" in original and "DEMO original words" in original
    # distinctive tokens, not "8 AM"/"7 AM": the page prints the current clock time (e.g. "10:17 AM"), which contains "7 AM"
    assert "DEMOCUTOFF" not in original and "statement balance" not in original
    assert "DEMO c1" not in original


# ---- AC-OS-13 --------------------------------------------------------------

def test_ac_os_13_post_without_matching_origin_is_refused(client, db):
    make_snapshot(client)
    r = client.post(f"{BASE}/figures/new", headers={"Origin": "http://evil.example"}, data={
        "section": "cash", "label": "DEMO forged", "status": "reported", "amount": "1",
        "reported_by": "x", "reported_via": "y"})
    assert r.status_code == 403
    assert db.scalars(select(OpeningFigure)).first() is None


# ---- AC-OS-14 (real PostgreSQL; set OPENING_SNAPSHOT_PG_URL to the throwaway database) -------

@pytest.mark.skipif(not os.environ.get("OPENING_SNAPSHOT_PG_URL"),
                    reason="needs the throwaway PostgreSQL from spec section 18.7")
def test_ac_os_14_migration_010_upgrade_downgrade_upgrade():
    url = os.environ["OPENING_SNAPSHOT_PG_URL"]
    assert "host=/" in url, "refusing to run against anything but a unix-socket throwaway database"
    env = {**os.environ, "DATABASE_URL": url}

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    alembic("upgrade", "head")
    assert "010" in alembic("current")
    alembic("downgrade", "-1")
    assert "009" in alembic("current")
    alembic("upgrade", "head")
    assert "010" in alembic("current")


# ---- AC-OS-15 --------------------------------------------------------------

def test_ac_os_15_pay_detail_warning_on_every_text_form(client):
    make_snapshot(client)
    fid = make_figure(client)
    warning = "Do not enter individual employee pay or names with pay amounts. Totals only."
    for path in (f"{BASE}/figures/new", f"{BASE}/figures/{fid}/clarify", f"{BASE}/figures/{fid}/evidence"):
        html = main_block(client.get(path).text)
        assert warning in html, path
        assert html.index(warning) < html.index('type="submit"'), "warning must come before the save button"


def test_ac_os_15_commitment_shows_only_the_aggregate(client):
    make_snapshot(client)
    make_figure(client, section="commitment", label="DEMO weekly payroll (provisional)", amount="1000",
                statement="DEMO payroll total $1,000", due_note="Fri, date to confirm")
    html = client.get(f"{BASE}/").text
    assert "Known commitments" in html and "$1,000" in html and "date to confirm" in html
    assert "DEMO payroll total $1,000" in html


# ---- AC-OS-16 --------------------------------------------------------------

def test_ac_os_16_no_verified_status_anywhere(client):
    assert "verified" not in {s.value for s in OpeningFigureStatus}
    assert not [k for k in OpeningEvidenceKind if "verif" in k.value]
    make_snapshot(client)
    fid = make_figure(client)
    upload(client, fid, kind="message_from_steve")
    add_clarification(client, fid, basis_confirmed="yes", body="DEMO basis confirmed")
    pages = [client.get(p).text for p in (
        f"{BASE}/", f"{BASE}/figures/{fid}", f"{BASE}/figures/{fid}/clarify", f"{BASE}/figures/{fid}/evidence",
        f"{BASE}/figures/new", f"{BASE}/original", f"{BASE}/withdrawn")]
    for html in pages:
        text = main_block(html).lower().replace("not verified", "")
        assert "verified" not in text and "verify" not in text
    overview = pages[0]
    assert "Reported figures — not verified" in overview
    card = overview[overview.index("DEMO Bank balance"):]
    assert "Reported" in card and "Verified" not in card
    assert "text message" in overview
    assert "does not check the balance" in pages[3]


# ---- setup and misc --------------------------------------------------------

def test_only_one_snapshot_and_setup_hidden_once_it_exists(client):
    make_snapshot(client)
    r = client.get(f"{BASE}/setup", follow_redirects=False)
    assert r.status_code == 303
    assert client.post(f"{BASE}/setup", data={"name": "again", "as_of_date": "2026-11-03",
                                              "label": "x"}).status_code == 409


def test_overview_without_a_snapshot_offers_creation(client):
    html = client.get(f"{BASE}/").text
    assert "No starting snapshot" in html and "Create snapshot" in html
    assert client.get(f"{BASE}/figures/new", follow_redirects=False).status_code == 303


def test_unknown_ids_are_404(client):
    make_snapshot(client)
    for p in (f"{BASE}/figures/9999", f"{BASE}/figures/9999/clarify", f"{BASE}/evidence/9999/download"):
        assert client.get(p).status_code == 404
    assert client.post(f"{BASE}/figures/9999/withdraw", data={"reason": "x"}).status_code == 404


def test_validation_keeps_what_was_typed(client):
    make_snapshot(client)
    r = client.post(f"{BASE}/figures/new", data={
        "section": "cash", "label": "DEMO keep my text", "status": "reported", "amount": "abc",
        "reported_by": "DEMO", "reported_via": "text"})
    assert r.status_code == 400 and "Amount must be a number" in r.text and "DEMO keep my text" in r.text
