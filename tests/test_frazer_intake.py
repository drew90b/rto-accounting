"""
Frazer intake and import preview: routes, storage and database behaviour.

Synthetic data only (tests/frazer_fixtures.py). Spec: docs/specs/cash-financial-control-spec.md section 19,
acceptance cases AC-FZ-01..16.
"""
import os
import re
import subprocess
import sys

import pytest
from sqlalchemy import func, select

import app.services.frazer_intake_service as svc
from app.database import Base
from app.main import app as fastapi_app
from app.models.document import Document
from app.models.frazer_intake import (
    FrazerSourceRow, FrazerUpload, FrazerUploadException, FrazerUploadNote, ImmutableSourceRecordError,
)
from app.models.user import User
from tests.frazer_fixtures import (
    BHPH, PAYMENT_NOV8_HEADER, VEHICLE_HEADER, build, customer, customers_csv, late_fee, payment, payments_csv, vehicle, vehicles_csv, vin,
)

BASE = "/finance/frazer"


# ---- helpers ---------------------------------------------------------------

def up(client, data, name="export.csv", **form):
    return client.post(f"{BASE}/upload", data=form, files={"file": (name, data, "text/csv")}, follow_redirects=False)


def upload_ok(client, data, name="export.csv", **form) -> int:
    r = up(client, data, name, **form)
    assert r.status_code == 303, r.text[:400]
    return int(re.search(r"/uploads/(\d+)", r.headers["location"]).group(1))


def text(html: str) -> str:
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S)
    body = m.group(1) if m else html
    body = re.sub(r"</?(?:em|strong|a|span|b|i|code)\b[^>]*>", "", body)        # inline tags are invisible to a reader
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def summary(client, pk) -> str:
    r = client.get(f"{BASE}/uploads/{pk}")
    assert r.status_code == 200
    return text(r.text)


def counts_of(client, pk) -> dict:
    t = summary(client, pk)
    return {k: int(re.search(rf"\b{k} (\d+)", t).group(1)) for k in ("New", "Unchanged", "Changed", "Conflicting", "Unresolved")}


def sources(db) -> int:
    return db.scalar(select(func.count()).select_from(FrazerSourceRow))


def stored_files(tmp_path):
    return [os.path.join(d, f) for d, _, fs in os.walk(tmp_path) for f in fs]


# ---- AC-FZ-01 / 02: upload, original preserved, attribution ----------------

def test_upload_keeps_the_original_bytes_and_shows_a_summary(client, db, tmp_path):
    data = payments_csv([payment(**{"Sequential ID": "0042", "Receipt Number": "0042"})])
    pk = upload_ok(client, data, "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV", export_type="",
                   export_date="2026-11-08", coverage_start="2026-11-01", coverage_end="2026-11-08")
    files = stored_files(tmp_path)
    assert len(files) == 1 and open(files[0], "rb").read() == data and "frazer_intake" in files[0]
    u = db.get(FrazerUpload, pk)
    assert u.export_type == "payments" and u.status == "parsed" and u.row_count == 1 and u.upload_id == f"FU-{pk:04d}"
    assert u.original_filename == "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV"
    t = summary(client, pk)
    assert "Transactions" in t and "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV" in t
    assert "Nothing from this file has been imported" in t
    row = db.scalars(select(FrazerSourceRow)).first()
    assert row.identity == "0042" and row.fields_json["receipt_number"] == "0042" and row.raw_json["Sequential ID"] == "0042"


def test_who_uploaded_comes_from_the_session_not_the_form(client, db):
    pk = upload_ok(client, vehicles_csv(), uploaded_by_user_id="99999", uploaded_by="Mallory")
    me = db.scalar(select(User.id).where(User.username == "testadmin"))
    assert db.get(FrazerUpload, pk).uploaded_by_user_id == me


def test_only_csv_files_non_empty_and_within_the_size_limit(client, tmp_path, monkeypatch):
    assert up(client, vehicles_csv(), "export.xlsx").status_code == 400
    assert up(client, b"   ", "export.csv").status_code == 400
    assert up(client, vehicles_csv(), "ok.CSV").status_code == 303
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 50)
    r = up(client, vehicles_csv(), "big.csv")
    assert r.status_code == 400 and "10 MB" in r.text
    assert len(stored_files(tmp_path)) == 1


def test_bad_declared_dates_are_refused_without_storing_anything(client, tmp_path):
    assert up(client, vehicles_csv(), export_date="11/08/2026").status_code == 400
    assert up(client, payments_csv(), coverage_start="2026-11-09", coverage_end="2026-11-01").status_code == 400
    assert stored_files(tmp_path) == []


# ---- AC-FZ-06: repeat uploads ----------------------------------------------

def test_the_identical_file_is_refused_with_a_link_and_creates_nothing(client, db, tmp_path):
    data = vehicles_csv([vehicle()])
    pk = upload_ok(client, data)
    before = (sources(db), len(stored_files(tmp_path)), db.scalar(select(func.count()).select_from(FrazerUpload)))
    r = up(client, data, "same-content-other-name.csv")
    assert r.status_code == 409 and f"/finance/frazer/uploads/{pk}" in r.text and "already uploaded" in r.text
    assert (sources(db), len(stored_files(tmp_path)), db.scalar(select(func.count()).select_from(FrazerUpload))) == before


# ---- AC-FZ-07: overlapping transaction exports ------------------------------

def test_overlapping_exports_are_compared_by_identity_and_both_versions_are_kept(client, db):
    first = payments_csv([payment(**{"Sequential ID": "1"}), payment(**{"Sequential ID": "2", "Receipt Number": "7002"}),
                          payment(**{"Sequential ID": "3", "Receipt Number": "7003"})])
    second = payments_csv([payment(**{"Sequential ID": "2", "Receipt Number": "7002"}),
                           payment(**{"Sequential ID": "3", "Receipt Number": "7003", "Amount": "120.00", "Applied to Contract": "120.00"}),
                           payment(**{"Sequential ID": "4", "Receipt Number": "7004"}), payment(**{"Sequential ID": "5", "Receipt Number": "7005"})])
    p1 = upload_ok(client, first, "a.csv")
    p2 = upload_ok(client, second, "b.csv")
    assert counts_of(client, p1) == {"New": 3, "Unchanged": 0, "Changed": 0, "Conflicting": 0, "Unresolved": 0}
    assert counts_of(client, p2) == {"New": 2, "Unchanged": 1, "Changed": 1, "Conflicting": 0, "Unresolved": 0}
    t = summary(client, p2)
    assert "2 Sequential ID(s) also appear in the earlier upload" in t
    # both versions of identity 3 are stored; neither overwrote the other
    amounts = [r.fields_json["amount"] for r in db.scalars(select(FrazerSourceRow).where(FrazerSourceRow.identity == "3").order_by(FrazerSourceRow.id))]
    assert amounts == ["100.00", "120.00"]
    # the changed row explains what changed
    rows = client.get(f"{BASE}/uploads/{p2}/rows?status=changed").text
    assert "Amount: 100.00 → 120.00" in text(rows)


def test_the_first_transaction_upload_says_there_is_nothing_to_compare_with(client):
    pk = upload_ok(client, payments_csv())
    t = summary(client, pk)
    assert "no earlier upload of this type to compare with" in t and "not seen before" in t


def test_same_sequential_id_with_a_different_date_is_a_conflict_not_a_revision(client):
    p1 = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1"})]))
    p2 = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1", "Transaction Date": "11/05/26", "Date Entered": "11/05/26"})]))
    assert counts_of(client, p2)["Conflicting"] == 1 and counts_of(client, p2)["Changed"] == 0
    assert "disagree with the earlier version" in text(client.get(f"{BASE}/uploads/{p2}/rows").text)


def test_a_receipt_number_is_not_an_identity(client):
    # two different entries sharing a receipt number are two records; the same ID with a different receipt is a change
    rows = [payment(**{"Sequential ID": "1", "Receipt Number": "7001"}), payment(**{"Sequential ID": "2", "Receipt Number": "7001"}),
            late_fee(**{"Sequential ID": "3"}), late_fee(**{"Sequential ID": "4"})]
    p1 = upload_ok(client, payments_csv(rows), "a.csv")
    assert counts_of(client, p1) == {"New": 4, "Unchanged": 0, "Changed": 0, "Conflicting": 0, "Unresolved": 0}
    p2 = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1", "Receipt Number": "7999"})]), "b.csv")
    assert counts_of(client, p2)["Changed"] == 1


# ---- AC-FZ-08: dated snapshots ----------------------------------------------

def test_dated_snapshots_are_compared_as_information_and_never_replace_each_other(client, db):
    v1 = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "A1", "Vehicle VIN": vin(1)}), vehicle(**{"Stock #": "B2", "Vehicle VIN": vin(2)})]), "nov1.csv", export_date="2026-11-01")
    v2 = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "A1", "Vehicle VIN": vin(1), "Total Cost": "5200"}),
                                         vehicle(**{"Stock #": "C3", "Vehicle VIN": vin(3)})]), "nov8.csv", export_date="2026-11-08")
    t1, t2 = summary(client, v1), summary(client, v2)
    assert "no earlier upload of this type to compare with" in t1
    assert f"previous snapshot FU-{v1:04d}" in t2 and "not a correction of this one" in t2
    assert counts_of(client, v2) == {"New": 1, "Unchanged": 0, "Changed": 1, "Conflicting": 0, "Unresolved": 0}
    assert "1 record(s) in the previous snapshot are not in this one (reported only; nothing was removed)" in t2
    assert "not an opening balance for any earlier date" in t2 and "Nov 8, 2026" in t2
    assert db.scalar(select(func.count()).select_from(FrazerSourceRow).where(FrazerSourceRow.identity == "B2")) == 1   # still stored


def test_an_older_snapshot_uploaded_later_is_ordered_by_its_export_date(client):
    v8 = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "A1"})]), "nov8.csv", export_date="2026-11-08")
    v1 = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "A1", "Total Cost": "4000"})]), "nov1.csv", export_date="2026-11-01")
    assert "no earlier upload" in summary(client, v1)                      # nothing is earlier than Nov 1
    assert f"previous snapshot FU-{v1:04d}" in summary(client, v8)         # the Nov 8 file now has a baseline


def test_two_snapshots_with_the_same_content_but_different_dates_are_both_kept(client):
    # the identical bytes are refused, but a re-export that differs only by one cell is a separate dated snapshot
    v1 = upload_ok(client, vehicles_csv([vehicle(**{"Vehicle Notes": "a"})]), export_date="2026-11-01")
    v2 = upload_ok(client, vehicles_csv([vehicle(**{"Vehicle Notes": "b"})]), export_date="2026-11-08")
    assert counts_of(client, v2)["Changed"] == 1 and v1 != v2


# ---- AC-FZ-09: cross-file matching and duplicate VINs ------------------------

def test_cross_file_checks_report_missing_stock_vin_differences_and_duplicate_vins(client):
    vehicles = [vehicle(**{"Stock #": "1", "Vehicle VIN": vin(1)}), vehicle(**{"Stock #": "2", "Vehicle VIN": vin(2)}),
                vehicle(**{"Stock #": "4", "Vehicle VIN": vin(2)})]          # VIN 2 is on two stock numbers
    v = upload_ok(client, vehicles_csv(vehicles), "veh.csv", export_date="2026-11-08")
    c = upload_ok(client, customers_csv([customer(**{"Stock #": "1", "Vehicle VIN": vin(1)}),
                                         customer(**{"Stock #": "2", "Vehicle VIN": vin(99)}),
                                         customer(**{"Stock #": "3", "Vehicle VIN": vin(3)})]), "cust.csv", export_date="2026-11-08")
    tc = summary(client, c)
    assert "Checked against" in tc and f"FU-{v:04d}" in tc
    assert "Stock number found in the vehicle list: 2 of 3" in tc and "not found on lines 4" in tc
    assert "VIN agrees: 1" in tc and "VIN differs: 1 (lines 3)" in tc
    tv = summary(client, v)
    assert "Duplicate VINs under different stock numbers: 1" in tv and "kept separate; not merged" in tv
    assert "Balance records whose stock number is not in this vehicle list: 1" in tv
    assert text(client.get(f"{BASE}/uploads/{v}/exceptions").text).count("duplicate_vin") >= 1
    # no vehicle was merged
    assert "3 record(s)" in tv


def test_cross_file_check_uses_the_nearest_export_date_and_says_when_dates_differ(client):
    v_old = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "1"})]), "old.csv", export_date="2026-11-01")
    v_new = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "1", "Vehicle Notes": "x"})]), "new.csv", export_date="2026-11-08")
    c = upload_ok(client, customers_csv(), "cust.csv", export_date="2026-11-07")
    t = summary(client, c)
    assert f"FU-{v_new:04d}" in t and f"FU-{v_old:04d}" not in t.split("Checked against")[1][:60]
    assert "different export dates" in t


def test_without_a_counterpart_file_the_cross_check_says_so(client):
    c = upload_ok(client, customers_csv(), export_date="2026-11-08")
    assert "No vehicle list has been uploaded yet, so cross-file checks were not run" in summary(client, c)


# ---- AC-FZ-10: fee assessments are not money received ------------------------

def test_fee_assessments_and_payments_are_never_added_together(client):
    pk = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1", "Amount": "100.00", "Applied to Contract": "100.00"}),
                                         payment(**{"Sequential ID": "2", "Amount": "33.00", "Applied to Contract": "33.00", "Description": "Cash", "Transaction Method": "CA"}),
                                         late_fee(**{"Sequential ID": "3", "Amount": "25.00"}), late_fee(**{"Sequential ID": "4", "Amount": "11.00"})]),
                   coverage_start="2026-11-01", coverage_end="2026-11-08")
    t = summary(client, pk)
    assert re.search(r"Payment entry 2 \$133\.00", t) and re.search(r"Fee assessment \(Late Fee\) 2 \$36\.00", t)
    assert "$169.00" not in t and "collected" not in t.lower() and "collections" not in t.lower()
    assert "a fee assessment is not money received" in t
    assert "Late-fee entries with nothing applied to any Applied-to column: 2 of 2" in t


def test_the_transaction_detail_keeps_codes_and_descriptions_as_written(client):
    pk = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1", "Description": "Down Payment", "Transaction Method": "CA", "Transaction Type": "A"})]))
    t = summary(client, pk)
    assert "Down Payment | CA" in t and "is not confirmed and nothing is inferred from them" in t
    assert "type A" in text(client.get(f"{BASE}/uploads/{pk}/rows").text)


# ---- AC-FZ-11: zero cost and floor plan --------------------------------------

def test_zero_costs_and_floor_plan_are_shown_as_reported_and_unconfirmed(client):
    header = VEHICLE_HEADER + ["Floor Plan Balance"]
    rows = [vehicle(**{"Stock #": "1", "Total Cost": "0", "Original Cost": "0", "Floor Plan Balance": "0", "Vehicle VIN": vin(1)}),
            vehicle(**{"Stock #": "2", "Vehicle VIN": vin(2), "Floor Plan Balance": ""}), vehicle(**{"Stock #": "3", "Vehicle VIN": vin(3), "Floor Plan Balance": "900"})]
    pk = upload_ok(client, build(header, rows), export_date="2026-11-08")
    t = summary(client, pk)
    assert "Records reporting a cost of 0: 1" in t and "does not confirm the vehicle cost nothing" in t
    assert "Floor-plan column Floor Plan Balance: 2 with a value ($900.00 as reported), 1 reported as 0, 1 blank" in t
    assert 'A blank is not "no debt"' in t
    assert "as reported" in t and "not an inventory valuation" in t


# ---- AC-FZ-12: coverage, cutoff, notes ---------------------------------------

def test_declared_and_observed_coverage_and_an_unknown_cutoff_are_shown_apart(client, db):
    pk = upload_ok(client, payments_csv([payment(**{"Sequential ID": "1", "Transaction Date": "11/02/26", "Date Entered": "11/02/26"}),
                                         payment(**{"Sequential ID": "2", "Transaction Date": "11/07/26", "Date Entered": "11/07/26"})]),
                   export_date="2026-11-08", coverage_start="2026-11-01", coverage_end="2026-11-08")
    t = summary(client, pk)
    assert "Declared coverage Nov 1, 2026 to Nov 8, 2026" in t and "Coverage seen in the data Nov 2, 2026 to Nov 7, 2026" in t
    assert "Actual cutoff Unknown" in t and "not assumed to be midnight or the file date" in t
    # a later note states the cutoff; the original is not changed
    r = client.post(f"{BASE}/uploads/{pk}/notes", data={"note": "Belinda says the report ran at 5 PM.", "actual_cutoff": "Nov 8, about 5:00 PM"}, follow_redirects=False)
    assert r.status_code == 303
    t2 = summary(client, pk)
    assert "Actual cutoff Unknown" in t2 and "Later note" in t2 and "Nov 8, about 5:00 PM" in t2 and "Belinda says the report ran at 5 PM." in t2
    db.expire_all()
    assert db.get(FrazerUpload, pk).actual_cutoff == "Unknown"
    assert db.scalar(select(func.count()).select_from(FrazerUploadNote)) == 1


def test_a_cutoff_stated_at_upload_is_kept_and_a_blank_one_is_unknown(client, db):
    a = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "1"})]), actual_cutoff="Nov 8, 4:30 PM")
    b = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "2"})]))
    assert db.get(FrazerUpload, a).actual_cutoff == "Nov 8, 4:30 PM" and db.get(FrazerUpload, b).actual_cutoff == "Unknown"


def test_an_empty_note_is_refused(client):
    pk = upload_ok(client, vehicles_csv())
    assert client.post(f"{BASE}/uploads/{pk}/notes", data={"note": "  "}).status_code == 400


# ---- exceptions with source lines; customer detail behind the summary ---------

def test_exceptions_name_the_source_lines_and_row_detail_is_behind_the_summary(client):
    rows = [payment(**{"Sequential ID": "1"}), payment(**{"Sequential ID": "2", "Amount": "oops"}), payment(**{"Sequential ID": "3", "Last Name": "REALLYUNIQUELASTNAME"})]
    pk = upload_ok(client, payments_csv(rows), coverage_start="2026-11-01", coverage_end="2026-11-08")
    s = summary(client, pk)
    assert re.search(r"unparseable_amount unreadable amount &#39;oops&#39; 1 3\b", s)          # code, what was found, count, source line
    assert counts_of(client, pk)["Unresolved"] == 1
    ex = text(client.get(f"{BASE}/uploads/{pk}/exceptions").text)
    assert "unparseable_amount" in ex and "unreadable amount" in ex and "Line" in ex
    # customer-level detail is behind the summary, never on it
    assert "REALLYUNIQUELASTNAME" not in s and "REALLYUNIQUELASTNAME" not in ex
    assert "REALLYUNIQUELASTNAME" not in client.get(f"{BASE}/uploads/{pk}/rows").text      # names are in the raw detail, not the table
    assert counts_of(client, pk)["New"] == 2


def test_the_record_list_filters_by_status_and_searches(client):
    rows = [payment(**{"Sequential ID": "1", "Stock": "0099"}), payment(**{"Sequential ID": "2", "Amount": "bad"}), late_fee(**{"Sequential ID": "3"})]
    pk = upload_ok(client, payments_csv(rows))
    assert "1 record(s)" in text(client.get(f"{BASE}/uploads/{pk}/rows?status=unresolved").text)
    assert "1 record(s)" in text(client.get(f"{BASE}/uploads/{pk}/rows?q=0099").text)
    assert "1 record(s)" in text(client.get(f"{BASE}/uploads/{pk}/rows?entry_class=fee_assessment").text)
    assert "3 record(s)" in text(client.get(f"{BASE}/uploads/{pk}/rows").text)


# ---- AC-FZ-15: rejected files ------------------------------------------------

def test_an_unrecognised_file_is_kept_and_rejected_with_reasons(client, db, tmp_path):
    data = build(["Colour", "Shape"], [{"Colour": "red", "Shape": "round"}])
    pk = upload_ok(client, data, "mystery.csv")
    u = db.get(FrazerUpload, pk)
    assert u.status == "rejected" and u.row_count == 0 and sources(db) == 0
    assert open(stored_files(tmp_path)[0], "rb").read() == data
    t = summary(client, pk)
    assert "Rejected: nothing was read from this file" in t and "Colour, Shape" in t
    assert "Rejected" in text(client.get(f"{BASE}/").text)


def test_a_type_that_disagrees_with_the_columns_is_rejected(client, db):
    pk = upload_ok(client, payments_csv(), export_type="vehicles")
    assert db.get(FrazerUpload, pk).status == "rejected"
    assert "You chose vehicles, but the columns look like payments" in summary(client, pk)
    assert sources(db) == 0


# ---- AC-FZ-13: access --------------------------------------------------------

def _routes(pk):
    return [("get", f"{BASE}/"), ("post", f"{BASE}/upload"), ("get", f"{BASE}/uploads/{pk}"), ("get", f"{BASE}/uploads/{pk}/rows"),
            ("get", f"{BASE}/uploads/{pk}/exceptions"), ("get", f"{BASE}/uploads/{pk}/download"), ("post", f"{BASE}/uploads/{pk}/notes")]


def test_employees_and_visitors_are_refused_everywhere_including_the_download(admin_client, employee_client, anon_client, db):
    pk = upload_ok(admin_client, customers_csv([customer(**{"Last Name": "PRIVATEFAKENAME"})]), export_date="2026-11-08")
    before = sources(db)
    for method, path in _routes(pk):
        r = getattr(employee_client, method)(path, follow_redirects=False)
        assert r.status_code == 403 and "PRIVATEFAKENAME" not in r.text, (method, path, r.status_code)
        r = getattr(anon_client, method)(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), (method, path, r.status_code)
    r = employee_client.post(f"{BASE}/upload", files={"file": ("x.csv", vehicles_csv(), "text/csv")})
    assert r.status_code == 403 and sources(db) == before
    assert employee_client.get(f"{BASE}/", headers={"accept": "text/html"}).status_code == 403


def test_the_download_is_a_protected_attachment_and_the_file_is_not_in_the_documents_list(client, db):
    data = vehicles_csv([vehicle(**{"Vehicle Notes": "UNIQUEFAKENOTE"})])
    pk = upload_ok(client, data, "frazer-vehicles-2026-11-08.csv.CSV")
    r = client.get(f"{BASE}/uploads/{pk}/download")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff"
    assert "UNIQUEFAKENOTE" not in client.get("/documents/").text and "frazer-vehicles" not in client.get("/documents/").text
    assert db.scalars(select(Document)).first() is None
    assert client.get(f"/documents/{pk}/download").status_code == 404
    assert client.get(f"{BASE}/uploads/9999/download").status_code == 404


def test_the_menu_link_is_for_administrators_only(admin_client, employee_client):
    assert "Frazer Intake" in admin_client.get("/purchases/").text
    assert "Frazer Intake" not in employee_client.get("/purchases/").text


def test_a_post_without_a_matching_origin_is_refused(client, db):
    r = client.post(f"{BASE}/upload", headers={"Origin": "http://evil.example"}, files={"file": ("x.csv", vehicles_csv(), "text/csv")})
    assert r.status_code == 403 and sources(db) == 0


# ---- AC-FZ-14: no operational writes -----------------------------------------

def test_intake_and_preview_write_nothing_outside_the_frazer_tables(client, db):
    def snapshot():
        out = {}
        for t in Base.metadata.sorted_tables:
            if not t.name.startswith("frazer_"):
                out[t.name] = db.scalar(select(func.count()).select_from(t))
        return out
    before = snapshot()
    v = upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "1", "Vehicle VIN": vin(1)})]), export_date="2026-11-08")
    c = upload_ok(client, customers_csv([customer()]), export_date="2026-11-08")
    p = upload_ok(client, payments_csv([payment(), late_fee()]), coverage_start="2026-11-01", coverage_end="2026-11-08")
    up(client, build(["x", "y"], [{"x": "1", "y": "2"}]), "junk.csv")
    for pk in (v, c, p):
        for sub in ("", "/rows", "/exceptions", "/download"):
            assert client.get(f"{BASE}/uploads/{pk}{sub}").status_code == 200
    client.post(f"{BASE}/uploads/{p}/notes", data={"note": "n"})
    assert snapshot() == before


# ---- no edit or delete; insert-only models -----------------------------------

def test_there_is_no_route_or_model_path_that_edits_or_deletes_uploaded_data(client, db):
    pk = upload_ok(client, payments_csv(), export_date="2026-11-08")
    for r in (x for x in fastapi_app.routes if getattr(x, "path", "").startswith(BASE)):
        assert r.methods <= {"GET", "HEAD", "POST"} and not re.search(r"(edit|update|delete|remove)", r.path), r.path
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"{BASE}/uploads/{pk}").status_code in (404, 405)
    for p in (f"{BASE}/uploads/{pk}/edit", f"{BASE}/uploads/{pk}/delete"):
        assert client.post(p).status_code == 404
    up_row = db.get(FrazerUpload, pk)
    up_row.actual_cutoff = "changed"
    with pytest.raises(ImmutableSourceRecordError):
        db.flush()
    db.rollback()
    row = db.scalars(select(FrazerSourceRow)).first()
    row.identity = "x"
    with pytest.raises(ImmutableSourceRecordError):
        db.flush()
    db.rollback()
    for model in (FrazerSourceRow, FrazerUploadException, FrazerUpload):
        obj = db.scalars(select(model)).first()
        if obj is None:
            continue
        db.delete(obj)
        with pytest.raises(ImmutableSourceRecordError):
            db.flush()
        db.rollback()


# ---- migration (needs a PostgreSQL throwaway database) ------------------------
def test_payment_entries_are_matched_to_vehicles_by_their_stock_column_not_their_sequential_id(client):
    upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "10001"}), vehicle(**{"Stock #": "10002", "Vehicle VIN": vin(2)})]), "v-2026-11-08.csv")
    pk = upload_ok(client, build(PAYMENT_NOV8_HEADER, [payment(**{"Sequential ID": "1166", "Stock": "10001"}),
                                                       payment(**{"Sequential ID": "1167", "Stock": "10002"}),
                                                       payment(**{"Sequential ID": "1168", "Stock": "99999"})]), "p.csv")
    t = summary(client, pk)
    assert "Stock number found in the vehicle list: 2 of 3" in t and "not found on lines 4" in t
    assert "VIN differs: 0" in t and "could not compare (a VIN is blank): 2" in t


def test_unconfirmed_code_meanings_never_block_intake_or_preview(client, db):
    rows = [payment(**{"Sequential ID": "1"}), late_fee(**{"Sequential ID": "2"}),
            payment(**{"Sequential ID": "3", "Description": "Down Payment", "Transaction Type": "A", "Transaction Method": "CA"}),
            payment(**{"Sequential ID": "4", "Description": "Cash", "Transaction Method": "CA"}),
            payment(**{"Sequential ID": "5", "Description": "Some New Thing", "Transaction Method": "ZZ"})]
    pk = upload_ok(client, build(PAYMENT_NOV8_HEADER, rows), "p.csv")
    t = summary(client, pk)
    assert "New 5" in t and "Unresolved 0" in t                                  # all previewable
    assert "Method code 'CA' appears on several descriptions" in client.get(f"{BASE}/uploads/{pk}/exceptions").text.replace("&#39;", "'")
    assert "unclassified" in t.lower()                                           # the unlisted description is surfaced, not refused
    assert db.query(FrazerUpload).count() == 1


def test_filename_dates_become_the_declared_period_and_are_labelled(client, db):
    pk = upload_ok(client, payments_csv([payment(**{"Transaction Date": "11/02/26"}), payment(**{"Sequential ID": "5002", "Transaction Date": "11/07/26"})]),
                   "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV")
    t = summary(client, pk)
    assert "Nov 1, 2026 to Nov 8, 2026 (read from the filename)" in t
    assert "Nov 2, 2026 to Nov 7, 2026" in t                                  # the dates actually present, separately
    up_ = db.get(FrazerUpload, pk)
    assert up_.declared_coverage_start.isoformat() == "2026-11-01" and up_.observed_coverage_end.isoformat() == "2026-11-07"
    typed = upload_ok(client, payments_csv([payment(**{"Sequential ID": "6001"})]), "frazer-payments-2026-11-01-through-2026-11-08.csv.CSV",
                      coverage_start="2026-11-03", coverage_end="2026-11-04")
    assert db.get(FrazerUpload, typed).declared_coverage_start.isoformat() == "2026-11-03"   # what was typed wins
    assert "read from the filename" not in summary(client, typed)


def test_down_payment_entries_are_corroborated_against_the_customer_snapshot(client):
    from tests.frazer_fixtures import customer_nov8, customers_nov8_csv
    upload_ok(client, vehicles_csv([vehicle(**{"Stock #": "10001"}), vehicle(**{"Stock #": "10002", "Vehicle VIN": vin(2)})]), "v-2026-11-08.csv")
    upload_ok(client, customers_nov8_csv([customer_nov8(**{"Down Payment": "1000"}),
                                          customer_nov8(**{"Stock #": "10002", "Vehicle VIN": vin(2), "Down Payment": "500"})]), "c-2026-11-08.csv")
    pk = upload_ok(client, payments_csv([
        payment(**{"Sequential ID": "7001", "Description": "Down Payment", "Transaction Type": "A", "Transaction Method": "CA", "Amount": "1000", "Applied to Contract": "1000"}),
        payment(**{"Sequential ID": "7002", "Stock": "10002", "VIN": vin(2), "Description": "Down Payment", "Transaction Type": "A", "Transaction Method": "CA", "Amount": "450", "Applied to Contract": "450"}),
    ]), "p.csv")
    t = summary(client, pk)
    assert "agreeing with the Down Payment column" in t and "1 of 2" in t and "differ on lines 3" in t



@pytest.mark.skipif(not os.environ.get("FRAZER_INTAKE_PG_URL"), reason="needs a throwaway PostgreSQL database")
def test_migration_011_upgrade_downgrade_upgrade():
    url = os.environ["FRAZER_INTAKE_PG_URL"]
    assert "host=/" in url, "refusing to run against anything but a unix-socket throwaway database"
    env = {**os.environ, "DATABASE_URL": url}

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    alembic("upgrade", "head")
    assert "(head)" in alembic("current")                     # 011 itself, or whatever was built after it
    # 011 sits on 009 alone, or on 010 (Opening Snapshot) once that branch is merged first; anything built after it is undone too
    parent = re.search(r'^down_revision = "(\d+)"', open("alembic/versions/011_frazer_source_intake.py").read(), re.M).group(1)
    alembic("downgrade", parent)
    assert parent in alembic("current")
    alembic("upgrade", "head")
    assert "(head)" in alembic("current")
