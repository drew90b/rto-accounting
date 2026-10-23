"""Purchase history: who created, corrected, reviewed and reopened a purchase."""

from decimal import Decimal

from app.models.enums import PurchaseEventAction as A
from app.models.purchase import Purchase
from app.models.purchase_event import PurchaseEvent
from tests.test_purchase_access import edit_data, last_purchase, upload
from tests.test_purchase_workflow import post_purchase


def events(db, *actions):
    db.expire_all()
    q = db.query(PurchaseEvent).order_by(PurchaseEvent.id)
    return [e for e in q.all() if not actions or e.action in actions]


def test_create_records_a_snapshot_by_the_signed_in_user(employee_client, db):
    post_purchase(employee_client, vendor="History Vendor")
    (e,) = events(db)
    assert e.action == A.created and e.user.username == "testemployee"
    assert e.changes["vendor"] == [None, "History Vendor"]
    assert e.changes["amount"] == [None, "125.50"]
    assert e.changes["payment_source"] == [None, "Cash"]


def test_edit_records_only_the_fields_that_changed(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit",
                         data=edit_data(p, amount="99.00", description="new note"))
    edited = events(db, A.edited)
    assert len(edited) == 1
    assert edited[0].changes == {"amount": ["125.50", "99.00"],
                                 "description": ["Brake pads", "new note"]}


def test_edit_that_changes_nothing_writes_no_event(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p))
    assert events(db, A.edited) == []


def test_edit_by_another_user_is_attributed_to_that_user(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, vendor="Renamed Vendor"))
    (e,) = events(db, A.edited)
    assert e.user.username == "testadmin"
    assert e.changes["vendor"] == ["AutoZone", "Renamed Vendor"]


def test_payment_source_change_is_recorded(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, payment_source="Visa 1234"))
    (e,) = events(db, A.edited)
    assert e.changes == {"payment_source": ["Cash", "Visa 1234"]}


def test_receipt_events(employee_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p), files=upload("late.jpg"))
    (e,) = events(db, A.receipt_added)
    assert e.changes == {"receipt": [None, "late.jpg"]}
    assert events(db, A.edited) == []


def test_full_story_is_shown_on_the_purchase_page_to_both_roles(employee_client, admin_client, db):
    post_purchase(employee_client)
    p = last_purchase(db)
    admin_client.post(f"/purchases/{p.id}/reviewed")
    admin_client.post(f"/purchases/{p.id}/reopen", data={"reason": "Amount was wrong"})
    employee_client.post(f"/purchases/{p.id}/edit", data=edit_data(p, amount="99.00"))
    for c in (employee_client, admin_client):
        html = c.get(f"/purchases/{p.id}/edit").text
        assert "History" in html
        for text in ("Created", "Reviewed", "Reopened", "Edited", "Reason: Amount was wrong",
                     "amount: 125.50 → 99.00", "Testadmin", "Testemployee"):
            assert text in html, text


def test_historical_purchase_has_no_history_section(admin_client, db):
    post_purchase(admin_client)
    p = last_purchase(db)
    for e in events(db):
        db.delete(e)
    db.commit()
    assert "<div class=\"card-title\">History</div>" not in admin_client.get(f"/purchases/{p.id}/edit").text
