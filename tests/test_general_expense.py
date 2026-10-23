"""
Tests for general-expense purchases and payment source.

Covers:
  - general_expense_* categories: business line coding, never create a Unit
  - payment_source: required on a new purchase, stored on the Transaction,
    "Unknown - needs review" accepted, editable, never erased by a blank edit
  - historical (source_reference) purchases are not flagged for what was
    never recorded
  - review flags: uncertain classification, shared allocation, payment source,
    receipt
  - "shared" / "unallocated" lines are offered for expenses only, never for
    units, sales, or repair jobs
  - exports carry the Payment Source column

Every test uses its own vendor name, because tests/conftest.py's per-test
rollback isolation does not fully hold (see tests/test_review_queue.py).
"""
import io

import pytest

from app.models.enums import PurchaseCategory
from app.services.purchase_service import business_line_for_category
from tests.test_review_queue import card_containing

GENERAL_CATEGORIES = [
    (PurchaseCategory.general_expense_car, "car"),
    (PurchaseCategory.general_expense_golf_cart, "golf_cart"),
    (PurchaseCategory.general_expense_shared, "shared"),
    (PurchaseCategory.general_expense_unsure, "unallocated"),
]


def post_expense(client, vendor, category=PurchaseCategory.general_expense_car, **overrides):
    data = {
        "purchaser": "GE Tester",
        "purchase_date": "2026-11-03",
        "vendor": vendor,
        "amount": "42.50",
        "category": category.value,
        "site_location": "Eunice",
        "stock_number": "",
        "mileage": "",
        "description": "Shop towels",
        "entered_by": "",
        "payment_source": "Visa 1234",
    }
    data.update(overrides)
    return client.post("/purchases/new", data=data, follow_redirects=False)


def purchase_for(db, vendor):
    from app.models.purchase import Purchase
    from app.models.vendor import Vendor
    return (
        db.query(Purchase).join(Vendor, Purchase.vendor_id == Vendor.id)
        .filter(Vendor.name == vendor).first()
    )


# ---------------------------------------------------------------------------
# Categories and business line
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("category,line", GENERAL_CATEGORIES)
def test_general_expense_category_maps_to_business_line(category, line):
    assert business_line_for_category(category) == line


def test_existing_categories_keep_their_business_line():
    assert business_line_for_category(PurchaseCategory.parts_rto_car_repair) == "car"
    assert business_line_for_category(PurchaseCategory.auction_golf_cart_flip) == "golf_cart"


@pytest.mark.parametrize("category,line", GENERAL_CATEGORIES)
def test_general_expense_creates_no_unit_and_codes_the_line(client, db, category, line):
    from app.models.unit import Unit
    vendor = f"GE Vendor {line}"
    units_before = db.query(Unit).count()

    r = post_expense(client, vendor, category=category, stock_number="Shop")
    assert r.status_code == 303 and "/edit" in r.headers["location"]

    p = purchase_for(db, vendor)
    assert p.unit_id is None
    assert db.query(Unit).count() == units_before
    assert p.transaction.business_line.value == line
    assert p.transaction.transaction_type.value == "purchase"
    assert float(p.transaction.amount) == 42.50
    assert p.transaction.unit_id is None


# ---------------------------------------------------------------------------
# Payment source
# ---------------------------------------------------------------------------

def test_payment_source_is_stored_on_the_transaction(client, db):
    post_expense(client, "PS Vendor Stored", payment_source="  Steve (personal)  ")
    p = purchase_for(db, "PS Vendor Stored")
    assert p.transaction.payment_source == "Steve (personal)"


@pytest.mark.parametrize("blank", ["", "   "])
def test_payment_source_is_required_on_a_new_purchase(client, db, blank):
    r = post_expense(client, "PS Vendor Blank", payment_source=blank)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/purchases/new?error=")
    assert purchase_for(db, "PS Vendor Blank") is None


def test_unknown_payment_source_is_accepted(client, db):
    r = post_expense(client, "PS Vendor Unknown", payment_source="Unknown - needs review")
    assert r.status_code == 303 and "/edit" in r.headers["location"]
    assert purchase_for(db, "PS Vendor Unknown").transaction.payment_source == "Unknown - needs review"


def test_edit_updates_payment_source_and_blank_never_erases_it(client, db):
    post_expense(client, "PS Vendor Edit", payment_source="Cash")
    p = purchase_for(db, "PS Vendor Edit")

    def edit(**overrides):
        data = {
            "purchaser": "GE Tester", "purchase_date": "2026-11-03", "vendor": "PS Vendor Edit",
            "amount": "42.50", "category": PurchaseCategory.general_expense_car.value,
            "site_location": "Eunice", "stock_number": "", "mileage": "", "description": "",
            "entered_by": "", "payment_source": "",
        }
        data.update(overrides)
        return client.post(f"/purchases/{p.id}/edit", data=data, follow_redirects=False)

    assert edit(payment_source="Visa 5678").status_code == 303
    db.expire(p.transaction)
    assert p.transaction.payment_source == "Visa 5678"

    assert edit(payment_source="").status_code == 303
    db.expire(p.transaction)
    assert p.transaction.payment_source == "Visa 5678"


def test_editing_category_recodes_the_business_line(client, db):
    post_expense(client, "PS Vendor Recode", category=PurchaseCategory.general_expense_unsure)
    p = purchase_for(db, "PS Vendor Recode")
    assert p.transaction.business_line.value == "unallocated"

    r = client.post(f"/purchases/{p.id}/edit", data={
        "purchaser": "GE Tester", "purchase_date": "2026-11-03", "vendor": "PS Vendor Recode",
        "amount": "42.50", "category": PurchaseCategory.general_expense_golf_cart.value,
        "site_location": "Eunice", "stock_number": "", "mileage": "", "description": "",
        "entered_by": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire(p.transaction)
    assert p.transaction.business_line.value == "golf_cart"


def test_new_form_suggests_existing_payment_sources_and_unknown(client, db):
    post_expense(client, "PS Vendor Suggest", payment_source="Chase Ink 9876")
    html = client.get("/purchases/new").text
    assert 'name="payment_source"' in html
    assert "Chase Ink 9876" in html
    assert "Unknown - needs review" in html


def test_service_does_not_require_payment_source_so_importer_can_leave_it_null(db):
    from datetime import date
    from decimal import Decimal
    from app.services.purchase_service import record_purchase
    p = record_purchase(
        purchase_date=date(2025, 10, 6), purchaser="Steve", vendor_name="PS Vendor Import",
        amount=Decimal("10.00"), category=PurchaseCategory.parts_rto_car_repair, db=db,
    )
    assert p.transaction.payment_source is None


# ---------------------------------------------------------------------------
# Review flags
# ---------------------------------------------------------------------------

def test_unsure_expense_is_flagged_as_uncertain_classification(client, db):
    post_expense(client, "Flag Vendor Unsure", category=PurchaseCategory.general_expense_unsure)
    card = card_containing(client.get("/review/").text, "Flag Vendor Unsure")
    assert "Classification uncertain" in card
    assert "has-flags" in card


def test_shared_expense_is_not_flagged_for_being_shared(client, db):
    post_expense(client, "Flag Vendor Shared", category=PurchaseCategory.general_expense_shared)
    p = purchase_for(db, "Flag Vendor Shared")
    p.receipt_attached = True
    db.commit()
    card = card_containing(client.get("/review/").text, "Flag Vendor Shared")
    assert "Shared" in card
    assert "has-flags" not in card
    assert "allocation" not in card


def test_shared_expense_can_be_marked_reviewed_and_stays_shared(client, db):
    post_expense(client, "Flag Vendor Shared Rev", category=PurchaseCategory.general_expense_shared)
    p = purchase_for(db, "Flag Vendor Shared Rev")
    r = client.post(f"/review/purchases/{p.id}/reviewed", follow_redirects=False)
    assert r.status_code == 303
    db.refresh(p)
    assert p.review_status.value == "reviewed"
    assert p.category == PurchaseCategory.general_expense_shared
    assert p.transaction.business_line.value == "shared"
    assert "Flag Vendor Shared Rev" in client.get("/review/?show=reviewed").text


def test_marking_reviewed_does_not_resolve_warnings(client, db):
    post_expense(client, "Flag Vendor Warn Rev", category=PurchaseCategory.general_expense_unsure,
                 payment_source="Unknown - needs review")
    p = purchase_for(db, "Flag Vendor Warn Rev")
    client.post(f"/review/purchases/{p.id}/reviewed")
    card = card_containing(client.get("/review/?show=reviewed").text, "Flag Vendor Warn Rev")
    assert "Classification uncertain" in card
    assert "Payment source unknown" in card
    assert "Missing receipt" in card


def test_unknown_payment_source_is_flagged(client, db):
    post_expense(client, "Flag Vendor Unknown Src", payment_source="Unknown - needs review")
    card = card_containing(client.get("/review/").text, "Flag Vendor Unknown Src")
    assert "Payment source unknown" in card


def test_missing_receipt_is_flagged_and_clears_when_attached(client, db):
    post_expense(client, "Flag Vendor Receipt")
    assert "Missing receipt" in card_containing(client.get("/review/").text, "Flag Vendor Receipt")

    p = purchase_for(db, "Flag Vendor Receipt")
    p.receipt_attached = True
    db.commit()
    assert "Missing receipt" not in card_containing(client.get("/review/").text, "Flag Vendor Receipt")


def test_clean_general_expense_has_no_flags(client, db):
    r = client.post(
        "/purchases/new",
        data={
            "purchaser": "GE Tester", "purchase_date": "2026-11-03", "vendor": "Flag Vendor Clean",
            "amount": "42.50", "category": PurchaseCategory.general_expense_car.value,
            "site_location": "Eunice", "stock_number": "", "mileage": "", "description": "",
            "entered_by": "", "payment_source": "Visa 1234",
        },
        files={"receipt": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "has-flags" not in card_containing(client.get("/review/?show=all").text, "Flag Vendor Clean")


def test_historical_purchase_is_not_flagged_for_missing_payment_source_or_receipt(client, db):
    from app.models.purchase import Purchase
    post_expense(client, "Flag Vendor Historical")
    p = purchase_for(db, "Flag Vendor Historical")
    p.source_reference = "historical-import:99999"
    p.transaction.payment_source = None
    db.commit()

    card = card_containing(client.get("/review/").text, "Flag Vendor Historical")
    assert "Missing payment source" not in card
    assert "Missing receipt" not in card


def test_historical_purchase_can_be_edited_without_a_payment_source(client, db):
    post_expense(client, "Hist Edit Vendor")
    p = purchase_for(db, "Hist Edit Vendor")
    p.source_reference = "historical-import:99998"
    p.transaction.payment_source = None
    db.commit()

    r = client.post(f"/purchases/{p.id}/edit", data={
        "purchaser": "GE Tester", "purchase_date": "2026-11-03", "vendor": "Hist Edit Vendor",
        "amount": "50.00", "category": PurchaseCategory.general_expense_car.value,
        "site_location": "Eunice", "stock_number": "", "mileage": "", "description": "",
        "entered_by": "",
    }, follow_redirects=False)
    assert r.status_code == 303 and "Purchase+updated" in r.headers["location"]
    db.expire(p)
    assert float(p.amount) == 50.00
    assert p.transaction.payment_source is None


# ---------------------------------------------------------------------------
# Where the new business lines appear
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/units/new", "/sales/new", "/repair-jobs/new"])
def test_asset_forms_do_not_offer_shared_or_unallocated(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert 'value="golf_cart"' in resp.text
    assert 'value="shared"' not in resp.text
    assert 'value="unallocated"' not in resp.text


def test_transaction_form_offers_shared_and_unallocated(client):
    html = client.get("/transactions/new").text
    assert 'value="shared"' in html
    assert 'value="unallocated"' in html
    assert 'name="payment_source"' in html


def test_manual_transaction_saves_payment_source(client, db):
    from app.models.transaction import Transaction
    r = client.post("/transactions/new", data={
        "transaction_date": "2026-11-04", "transaction_type": "overhead",
        "business_line": "shared", "amount": "310.00", "description": "Manual Overhead PS Test",
        "payment_source": "Business checking",
    }, follow_redirects=False)
    assert r.status_code == 303
    t = db.query(Transaction).filter(Transaction.description == "Manual Overhead PS Test").first()
    assert t.business_line.value == "shared"
    assert t.payment_source == "Business checking"


# ---------------------------------------------------------------------------
# Exports and list
# ---------------------------------------------------------------------------

def _header_and_rows(response):
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(response.content)).active
    rows = list(ws.iter_rows(values_only=True))
    return list(rows[0]), rows[1:]


def test_purchases_export_includes_payment_source(client, db):
    post_expense(client, "Export Vendor PS", payment_source="Amex 4321")
    header, rows = _header_and_rows(client.get("/purchases/export"))
    assert header[-1] == "Payment Source"
    assert any(r[3] == "Export Vendor PS" and r[-1] == "Amex 4321" for r in rows)


def test_transactions_export_includes_payment_source(client, db):
    post_expense(client, "Export Vendor Txn", payment_source="Amex 8765")
    header, rows = _header_and_rows(client.get("/transactions/export"))
    assert header[-1] == "Payment Source"
    assert any(r[-1] == "Amex 8765" for r in rows)


def test_purchase_list_shows_payment_source(client, db):
    post_expense(client, "List Vendor PS", payment_source="Discover 1357")
    html = client.get("/purchases/").text
    assert "Payment Source" in html
    assert "Discover 1357" in html
