"""Who can do what with a purchase, and who is recorded as having done it."""

import io
import os
from decimal import Decimal

import pytest

from app.models.document import Document
from app.models.enums import PurchaseEventAction, ReviewStatus
from app.models.purchase import Purchase
from app.models.purchase_event import PurchaseEvent
from app.models.unit import Unit
from app.models.user import User
from tests.test_purchase_workflow import (
    make_unit, post_purchase, post_vehicle_purchase,
)


def last_purchase(db):
    db.expire_all()
    return db.query(Purchase).order_by(Purchase.id.desc()).first()


def edit_data(purchase, **overrides):
    data = {
        "purchaser": purchase.purchaser,
        "purchase_date": purchase.purchase_date.isoformat(),
        "vendor": purchase.vendor.name,
        "amount": str(purchase.amount),
        "category": purchase.category.value,
        "site_location": purchase.site_location or "",
        "stock_number": purchase.stock_number or "",
        "mileage": "", "description": purchase.description or "",
        "payment_source": "Cash",
    }
    data.update(overrides)
    return data


def upload(name, content=b"receipt"):
    return {"receipt": (name, io.BytesIO(content), "image/jpeg")}


# --- Employee abilities -------------------------------------------------------

def test_employee_can_create_list_search_and_edit_a_pending_purchase(employee_client, db):
    assert post_purchase(employee_client, vendor="Emp Vendor").status_code == 303
    p = last_purchase(db)
    assert "Emp Vendor" in employee_client.get("/purchases/?vendor=emp").text
    assert employee_client.get(f"/purchases/{p.id}/edit").status_code == 200
    r = employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, amount="99.00"),
                             follow_redirects=False)
    assert r.status_code == 303
    assert last_purchase(db).amount == Decimal("99.00")


def test_employee_can_edit_a_pending_purchase_someone_else_entered(employee_client, admin_client, db):
    post_purchase(admin_client, vendor="Admin Entered")
    p = last_purchase(db)
    r = employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, description="fixed"),
                             follow_redirects=False)
    assert r.status_code == 303
    assert last_purchase(db).description == "fixed"


def test_employee_can_attach_a_receipt_and_download_it(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), files=upload("r.jpg"),
                         follow_redirects=False)
    doc = db.query(Document).one()
    r = employee_client.get(f"/documents/{doc.id}/download")
    assert r.status_code == 200 and r.content == b"receipt"


def test_employee_cannot_download_a_non_purchase_document(employee_client, admin_client, db, tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(b"secret")
    from app.models.enums import LinkedRecordType
    doc = Document(linked_record_type=LinkedRecordType.transaction, linked_record_id=1,
                   file_path=str(path), original_filename="x.pdf", file_type=".pdf")
    db.add(doc)
    db.commit()
    assert employee_client.get(f"/documents/{doc.id}/download").status_code == 403
    assert admin_client.get(f"/documents/{doc.id}/download").status_code == 200


def test_missing_document_is_404_not_a_redirect(employee_client):
    assert employee_client.get("/documents/99999/download", follow_redirects=False).status_code == 404


# --- Employee limits -----------------------------------------------------------

def test_employee_cannot_review_reopen_or_export(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    assert employee_client.post(f"/purchases/{p.id}/reviewed", follow_redirects=False).status_code == 403
    assert employee_client.post(f"/review/purchases/{p.id}/reviewed", follow_redirects=False).status_code == 403
    admin_client.post(f"/purchases/{p.id}/reviewed")
    assert employee_client.post(f"/purchases/{p.id}/reopen", data={"reason": "x"},
                                follow_redirects=False).status_code == 403
    assert employee_client.get("/purchases/export", follow_redirects=False).status_code == 403
    assert last_purchase(db).review_status == ReviewStatus.reviewed


def test_reviewed_purchase_cannot_be_edited_by_anyone(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")
    for c in (employee_client, admin_client):
        r = c.post(f"/purchases/{p.id}/edit", data=edit_data(p, amount="1.00"),
                   files=upload("late.jpg"), follow_redirects=False)
        assert r.status_code == 403
    p = last_purchase(db)
    assert p.amount == Decimal("125.50")
    assert db.query(Document).count() == 0  # a late receipt needs a reopen too


def test_reviewed_purchase_page_is_read_only_with_explanation(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")
    html = employee_client.get(f"/purchases/{p.id}/edit").text
    assert "<fieldset disabled" in html and "Submit Purchase" not in html
    assert "Ask an administrator to reopen it" in html
    assert "Reopen for editing" not in html
    assert "Reopen for editing" in admin_client.get(f"/purchases/{p.id}/edit").text


def test_employee_list_has_no_export_or_review_buttons(employee_client, db):
    post_purchase(employee_client)
    html = employee_client.get("/purchases/").text
    assert "/purchases/export" not in html and "Mark Reviewed" not in html


# --- Attribution ---------------------------------------------------------------

def test_entered_by_comes_from_the_session_not_the_form(employee_client, db):
    r = post_purchase(employee_client, purchaser="Someone Else", entered_by="Forged Name")
    assert r.status_code == 303
    p = last_purchase(db)
    employee = db.query(User).filter(User.username == "testemployee").one()
    assert p.entered_by_user_id == employee.id
    assert p.entered_by == "Testemployee" and p.transaction.entered_by == "Testemployee"
    assert p.purchaser == "Someone Else"            # who made the purchase stays separate
    assert "Forged Name" not in employee_client.get(f"/purchases/{p.id}/edit").text


def test_editing_keeps_the_original_entered_by_even_from_another_user(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    original = p.entered_by_user_id
    admin_client.post(f"/purchases/{p.id}/edit", data={**edit_data(p), "entered_by": "Forged"},
                      follow_redirects=False)
    p = last_purchase(db)
    assert p.entered_by_user_id == original
    assert p.entered_by == "Testemployee" and p.transaction.entered_by == "Testemployee"


def test_blank_edit_no_longer_erases_entered_by(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), follow_redirects=False)
    assert last_purchase(db).entered_by == "Testemployee"


def test_receipt_uploaded_by_is_the_signed_in_user(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), files=upload("r.jpg"))
    assert db.query(Document).one().uploaded_by == "Testemployee"


def test_review_records_reviewer_and_time_and_an_event(employee_client, admin_client, db):
    post_purchase(employee_client, purchaser="Belinda Buyer")
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")
    p = last_purchase(db)
    admin = db.query(User).filter(User.username == "testadmin").one()
    assert p.review_status == ReviewStatus.reviewed
    assert p.reviewed_by_user_id == admin.id and p.reviewed_at is not None
    assert p.entered_by_user_id != p.reviewed_by_user_id
    event = db.query(PurchaseEvent).filter_by(action=PurchaseEventAction.reviewed).one()
    assert event.user_id == admin.id
    html = admin_client.get(f"/purchases/{p.id}/edit").text
    assert "Testadmin" in html and "Testemployee" in html


def test_review_queue_review_also_records_the_reviewer(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/review/purchases/{p.id}/reviewed")
    p = last_purchase(db)
    assert p.reviewed_by_user.username == "testadmin"
    assert db.query(PurchaseEvent).filter_by(action=PurchaseEventAction.reviewed).count() == 1


def test_reviewing_twice_records_one_review(admin_client, db):
    post_purchase(admin_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")
    admin_client.post(f"/purchases/{p.id}/reviewed")
    assert db.query(PurchaseEvent).filter_by(action=PurchaseEventAction.reviewed).count() == 1


def test_reopen_needs_a_reason_clears_the_reviewer_and_keeps_it_in_the_event(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")

    r = admin_client.post(f"/purchases/{p.id}/reopen", data={"reason": "  "}, follow_redirects=False)
    assert "reason" in r.headers["location"].lower()
    assert last_purchase(db).review_status == ReviewStatus.reviewed

    admin_client.post(f"/purchases/{p.id}/reopen", data={"reason": "Amount was wrong"})
    p = last_purchase(db)
    assert p.review_status == ReviewStatus.pending
    assert p.reviewed_by_user_id is None and p.reviewed_at is None
    event = db.query(PurchaseEvent).filter_by(action=PurchaseEventAction.reopened).one()
    assert event.note == "Amount was wrong"
    assert event.changes["reviewed_by"][0] == "Testadmin"
    # ... and now the employee can correct it.
    r = employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, amount="5.00"),
                             follow_redirects=False)
    assert r.status_code == 303 and last_purchase(db).amount == Decimal("5.00")


def test_historical_purchase_shows_no_made_up_attribution(admin_client, db):
    post_purchase(admin_client)
    p = last_purchase(db)
    p.entered_by_user_id = None
    p.entered_by = "Typed Name"
    p.review_status = ReviewStatus.reviewed
    p.source_reference = "historical-import:5"
    db.commit()
    html = admin_client.get(f"/purchases/{p.id}/edit").text
    assert "Typed Name" in html and "typed, not verified" in html
    assert "reviewer not recorded" in html


# --- Linked-record guards -------------------------------------------------------

def test_vehicle_stock_number_pointing_at_another_unit_is_rejected(employee_client, db):
    make_unit(db, unit_id="U-OTHER", vin="OTHERVIN000000001")
    other = db.query(Unit).filter_by(unit_id="U-OTHER").one()
    other.acquisition_cost = Decimal("1000.00")
    db.commit()
    post_vehicle_purchase(employee_client)
    p = last_purchase(db)
    own_unit = p.unit
    own_cost_before = own_unit.acquisition_cost

    r = employee_client.post(f"/purchases/{p.id}/edit",
                             data=edit_data(p, stock_number="U-OTHER", amount="1.00"),
                             follow_redirects=False)
    assert "error=" in r.headers["location"]
    db.expire_all()
    assert db.query(Unit).filter_by(unit_id="U-OTHER").one().acquisition_cost == Decimal("1000.00")
    assert db.get(Unit, own_unit.id).acquisition_cost == own_cost_before
    assert last_purchase(db).unit_id == own_unit.id


def test_vehicle_edit_keeps_its_unit_and_updates_its_cost(employee_client, db):
    post_vehicle_purchase(employee_client)
    p = last_purchase(db)
    r = employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, amount="7000.00", shipping_cost="200.00", other_fees="75.00"),
                             follow_redirects=False)
    assert "error=" not in r.headers["location"]
    p = last_purchase(db)
    assert p.unit.acquisition_cost == Decimal("7000.00") + Decimal("200.00") + Decimal("75.00")


def test_category_cannot_cross_the_vehicle_line(employee_client, db):
    post_vehicle_purchase(employee_client)
    veh = last_purchase(db)
    r = employee_client.post(f"/purchases/{veh.id}/edit",
                             data=edit_data(veh, category='PARTS - "RTO" car parts (Repair)'),
                             follow_redirects=False)
    assert "error=" in r.headers["location"]

    unit = make_unit(db, unit_id="U-PARTS", vin="PARTSVIN00000001")
    unit.acquisition_cost = Decimal("500.00")
    db.commit()
    post_purchase(employee_client, stock_number="U-PARTS")
    parts = last_purchase(db)
    assert parts.unit_id == unit.id
    r = employee_client.post(f"/purchases/{parts.id}/edit",
                             data=edit_data(parts, category="AUCTION - Purchased Cars (RTO)"),
                             follow_redirects=False)
    assert "error=" in r.headers["location"]
    db.expire_all()
    assert db.get(Unit, unit.id).acquisition_cost == Decimal("500.00")


def test_vehicle_cannot_move_between_car_and_golf_cart(employee_client, db):
    post_vehicle_purchase(employee_client)
    p = last_purchase(db)
    r = employee_client.post(
        f"/purchases/{p.id}/edit",
        data=edit_data(p, category='AUCTION - Purchased Golf Carts - ("Flip")'),
        follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_parts_purchase_still_links_to_a_matching_unit(employee_client, db):
    unit = make_unit(db, unit_id="U-LINK", vin="LINKVIN000000001")
    db.commit()
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, stock_number="U-LINK"))
    assert last_purchase(db).unit_id == unit.id


# --- Receipt storage ------------------------------------------------------------

def test_upload_with_path_traversal_name_stays_inside_the_record_folder(employee_client, db, tmp_path):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p),
                         files=upload("../../x.pdf"))
    doc = db.query(Document).one()
    root = str(tmp_path / "receipts" / "purchase" / str(p.id))
    assert os.path.dirname(os.path.realpath(doc.file_path)) == os.path.realpath(root)
    assert doc.original_filename == "x.pdf"
    assert not (tmp_path / "x.pdf").exists() and not (tmp_path.parent / "x.pdf").exists()


def test_two_uploads_with_the_same_name_do_not_overwrite_each_other(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), files=upload("receipt.jpg", b"one"))
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), files=upload("receipt.jpg", b"two"))
    docs = db.query(Document).order_by(Document.id).all()
    assert len(docs) == 2 and docs[0].file_path != docs[1].file_path
    assert open(docs[0].file_path, "rb").read() == b"one"
    assert open(docs[1].file_path, "rb").read() == b"two"


# --- Unit acquisition cost provenance --------------------------------------------

def unit_with_cost(db, unit_id, vin, cost):
    unit = make_unit(db, unit_id=unit_id, vin=vin)
    unit.acquisition_cost = cost
    db.commit()
    return unit


def vehicle_edit(client, p, **overrides):
    return client.post(
        f"/purchases/{p.id}/edit",
        data=edit_data(p, shipping_cost="", other_fees="", **overrides),
        follow_redirects=False,
    )


def test_unrelated_edit_of_a_purchase_linked_to_an_existing_unit_keeps_its_cost(employee_client, db):
    """The reviewer's scenario: unit cost $5,001, a $300 purchase is its only linked vehicle purchase."""
    unit = unit_with_cost(db, "U-5001", "COSTVIN000000001", Decimal("5001.00"))
    r = post_vehicle_purchase(employee_client, stock_number="U-5001", vin="", amount="300.00",
                              shipping_cost="", other_fees="")
    assert r.status_code == 303
    p = last_purchase(db)
    assert p.unit_id == unit.id and p.created_unit is False
    assert db.query(Purchase).filter_by(unit_id=unit.id).count() == 1   # the only linked purchase

    vehicle_edit(employee_client, p, description="unrelated note")
    db.expire_all()
    assert db.get(Unit, unit.id).acquisition_cost == Decimal("5001.00")

    vehicle_edit(employee_client, p, amount="350.00")                  # even a changed amount
    db.expire_all()
    assert db.get(Unit, unit.id).acquisition_cost == Decimal("5001.00")
    assert last_purchase(db).amount == Decimal("350.00")               # the purchase itself is corrected


def test_matching_purchase_with_a_different_cost_is_flagged_for_review(employee_client, admin_client, db):
    unit_with_cost(db, "U-FLAG", "COSTVIN000000002", Decimal("5001.00"))
    post_vehicle_purchase(employee_client, stock_number="U-FLAG", vin="", amount="300.00",
                          shipping_cost="", other_fees="")
    html = admin_client.get("/review/").text
    assert "Unit cost not changed" in html and "5,001.00" in html and "300.00" in html


def test_flag_clears_when_costs_agree_and_is_absent_for_the_creating_purchase(employee_client, admin_client, db):
    post_vehicle_purchase(employee_client)                              # creates its own unit
    assert "Unit cost not changed" not in admin_client.get("/review/").text
    unit_with_cost(db, "U-SAME", "COSTVIN000000003", Decimal("300.00"))
    post_vehicle_purchase(employee_client, stock_number="U-SAME", vin="", amount="300.00",
                          shipping_cost="", other_fees="", vendor="Same Cost Vendor")
    assert "Unit cost not changed" not in admin_client.get("/review/").text


def test_purchase_that_created_the_unit_can_still_correct_its_cost(employee_client, db):
    post_vehicle_purchase(employee_client)
    p = last_purchase(db)
    assert p.created_unit is True
    employee_client.post(
        f"/purchases/{p.id}/edit",
        data=edit_data(p, amount="6000.00", shipping_cost="100.00", other_fees="0"),
        follow_redirects=False)
    db.expire_all()
    assert last_purchase(db).unit.acquisition_cost == Decimal("6100.00")


def test_second_purchase_on_a_unit_cannot_change_cost_but_the_first_still_can(employee_client, db):
    post_vehicle_purchase(employee_client)                              # creates the unit, $6,775
    first = last_purchase(db)
    unit_id = first.unit_id
    post_vehicle_purchase(employee_client, stock_number=first.unit.unit_id, vin="", amount="50.00",
                          shipping_cost="", other_fees="", vendor="Second Vendor")
    second = last_purchase(db)
    assert second.unit_id == unit_id and second.created_unit is False

    vehicle_edit(employee_client, second, amount="60.00")
    db.expire_all()
    assert db.get(Unit, unit_id).acquisition_cost == Decimal("6775.00")

    employee_client.post(
        f"/purchases/{first.id}/edit",
        data=edit_data(first, amount="7000.00", shipping_cost="200.00", other_fees="75.00"),
        follow_redirects=False)
    db.expire_all()
    assert db.get(Unit, unit_id).acquisition_cost == Decimal("7275.00")


def test_historical_purchase_with_unknown_provenance_never_changes_unit_cost(admin_client, db):
    post_vehicle_purchase(admin_client)
    p = last_purchase(db)
    p.created_unit = False                      # what every pre-existing row has
    p.source_reference = "historical-import:77"
    db.commit()
    cost = p.unit.acquisition_cost
    admin_client.post(
        f"/purchases/{p.id}/edit",
        data=edit_data(p, amount="1.00", shipping_cost="", other_fees=""),
        follow_redirects=False)
    db.expire_all()
    assert last_purchase(db).unit.acquisition_cost == cost
    assert "Unit cost not changed" not in admin_client.get("/review/?show=reviewed").text  # history isn't flagged
