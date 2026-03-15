"""
Integration test: POST /vendors/new creates a vendor record.

Mirrors the pattern of test_customer_create.py. Uses the shared conftest
fixtures so each test runs inside a rolled-back outer transaction.
"""


def test_create_vendor_redirects_and_persists(client, db):
    """POST /vendors/new should create a record and redirect to the edit page."""
    from app.models.vendor import Vendor

    response = client.post(
        "/vendors/new",
        data={
            "name": "Lakeland Auto Auction",
            "phone": "863-555-0199",
            "email": "auction@lakelandauto.com",
            "address": "1400 Combee Rd, Lakeland, FL 33801",
            "notes": "Primary auction source",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303, (
        f"Expected 303, got {response.status_code}. Body: {response.text[:500]}"
    )

    location = response.headers.get("location", "")
    assert "/vendors/" in location and "/edit" in location, (
        f"Unexpected redirect location: {location}"
    )

    vendor = db.query(Vendor).filter(Vendor.name == "Lakeland Auto Auction").first()
    assert vendor is not None, "Vendor record was not created in the database"

    assert vendor.vendor_id is not None, "vendor_id should be set after create"
    assert vendor.vendor_id.startswith("V-"), (
        f"Expected vendor_id like 'V-0001', got: {vendor.vendor_id}"
    )


def test_vendor_list_returns_200(client):
    """GET /vendors/ should return 200."""
    response = client.get("/vendors/")
    assert response.status_code == 200


def test_vendor_new_form_returns_200(client):
    """GET /vendors/new should return the add form."""
    response = client.get("/vendors/new")
    assert response.status_code == 200


def test_vendor_edit_redirects_on_missing(client):
    """GET /vendors/99999/edit for a non-existent vendor should redirect."""
    response = client.get("/vendors/99999/edit", follow_redirects=False)
    # FastAPI RedirectResponse without explicit status_code defaults to 307
    assert response.status_code in (302, 303, 307)
