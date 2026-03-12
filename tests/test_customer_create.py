"""
Integration test: POST /customers/new creates a customer record.

Uses the shared conftest `client` fixture, which overrides get_db with a
session bound to the per-test transactional connection. The `db` fixture
is used to verify DB state after the HTTP request.

Both `client` and `db` depend on the same db_connection instance, so data
committed by the route (released from its SAVEPOINT) is immediately visible
to verification queries through `db`.
"""


def test_create_customer_redirects_and_persists(client, db):
    """POST /customers/new should create a record and redirect to the edit page."""
    from app.models.customer import Customer

    response = client.post(
        "/customers/new",
        data={
            "full_name": "Jane Doe",
            "phone": "555-1234",
            "email": "jane@example.com",
            "address": "123 Main St",
            "notes": "",
            "status": "active",
        },
        follow_redirects=False,
    )

    # Route returns 303 See Other on success
    assert response.status_code == 303, (
        f"Expected 303, got {response.status_code}. Body: {response.text[:500]}"
    )

    # Redirect location points to the edit page for the new record
    location = response.headers.get("location", "")
    assert "/customers/" in location and "/edit" in location, (
        f"Unexpected redirect location: {location}"
    )

    # Record must exist in the database
    customer = db.query(Customer).filter(Customer.full_name == "Jane Doe").first()
    assert customer is not None, "Customer record was not created in the database"

    # Human-readable ID must be set and follow the expected format
    assert customer.customer_id is not None, "customer_id should be set after create"
    assert customer.customer_id.startswith("C-"), (
        f"Expected customer_id like 'C-0001', got: {customer.customer_id}"
    )
