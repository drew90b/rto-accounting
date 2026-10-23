import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin, require_user
from app.database import get_db
from app.models.purchase import Purchase
from app.models.vendor import Vendor
from app.models.document import Document
from app.models.user import User
from app.models.enums import PurchaseCategory, ReviewStatus
from app.services.purchase_service import (
    record_purchase, sync_purchase_transaction, match_unit_by_stock_number, is_vehicle_purchase,
    payment_source_suggestions, is_editable, check_edit_allowed, mark_reviewed, reopen,
)
from app.services.document_service import save_uploaded_file

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


def _parse_int(val: str) -> Optional[int]:
    try:
        return int(val) if val else None
    except ValueError:
        return None


def _form_context(db: Session):
    return {
        "categories": [c.value for c in PurchaseCategory],
        "vehicle_categories": [c.value for c in PurchaseCategory if is_vehicle_purchase(c)],
        "site_locations": ["Eunice"],
        "vendor_names": [v.name for v in db.query(Vendor).order_by(Vendor.name).all()],
        "payment_sources": payment_source_suggestions(db),
    }


@router.get("/", response_class=HTMLResponse)
def list_purchases(
    request: Request,
    db: Session = Depends(get_db),
    vendor: Optional[str] = None,
    purchaser: Optional[str] = None,
    category: Optional[str] = None,
    stock_number: Optional[str] = None,
    review_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    query = db.query(Purchase)
    if vendor:
        query = query.join(Purchase.vendor).filter(Vendor.name.ilike(f"%{vendor}%"))
    if purchaser:
        query = query.filter(Purchase.purchaser.ilike(f"%{purchaser}%"))
    if category:
        query = query.filter(Purchase.category == category)
    if stock_number:
        query = query.filter(Purchase.stock_number.ilike(f"%{stock_number}%"))
    if review_status:
        query = query.filter(Purchase.review_status == review_status)
    if date_from:
        query = query.filter(Purchase.purchase_date >= date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Purchase.purchase_date <= date.fromisoformat(date_to))
    purchases = query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()).all()

    return templates.TemplateResponse("purchases/list.html", {
        "request": request,
        "purchases": purchases,
        "vendor_filter": vendor or "",
        "purchaser_filter": purchaser or "",
        "category_filter": category or "",
        "stock_number_filter": stock_number or "",
        "review_status_filter": review_status or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "categories": [c.value for c in PurchaseCategory],
        "review_statuses": [r.value for r in ReviewStatus],
    })


@router.get("/export", dependencies=[Depends(require_admin)])
def export_purchases(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    purchases = db.query(Purchase).order_by(Purchase.purchase_date).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchases"
    headers = [
        "Purchase ID", "Date", "Purchaser", "Vendor", "Amount", "Shipping", "Other Fees",
        "Category", "Site Location", "Stock #", "Unit", "Acquisition Cost", "Mileage", "Notes",
        "Receipt", "Review Status", "Payment Source",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for p in purchases:
        ws.append([
            p.purchase_id,
            str(p.purchase_date) if p.purchase_date else "",
            p.purchaser,
            p.vendor.name if p.vendor else "",
            float(p.amount) if p.amount else 0,
            float(p.shipping_cost) if p.shipping_cost else "",
            float(p.other_fees) if p.other_fees else "",
            p.category.value if p.category else "",
            p.site_location or "",
            p.stock_number or "",
            p.unit.unit_id if p.unit else "",
            float(p.unit.acquisition_cost) if p.unit and p.unit.acquisition_cost else "",
            p.mileage or "",
            p.description or "",
            "Yes" if p.receipt_attached else "No",
            p.review_status.value if p.review_status else "",
            p.transaction.payment_source if p.transaction and p.transaction.payment_source else "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=purchases.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_purchase_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("purchases/form.html", {
        "request": request,
        "purchase": None,
        "receipt": None,
        "today": date.today().isoformat(),
        **_form_context(db),
    })


@router.post("/new")
async def create_purchase(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    purchaser: str = Form(...),
    purchase_date: str = Form(...),
    vendor: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    site_location: str = Form(""),
    stock_number: str = Form(""),
    mileage: str = Form(""),
    description: str = Form(""),
    shipping_cost: str = Form(""),
    other_fees: str = Form(""),
    vin: str = Form(""),
    year: str = Form(""),
    make: str = Form(""),
    model: str = Form(""),
    payment_source: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
):
    if not payment_source.strip():
        return RedirectResponse(
            url="/purchases/new?error=Payment+source+is+required.+Type+%22Unknown+-+needs+review%22+if+you+don%27t+know.",
            status_code=303,
        )
    try:
        purchase = record_purchase(
            purchase_date=date.fromisoformat(purchase_date),
            purchaser=purchaser,
            vendor_name=vendor,
            amount=_d(amount) or Decimal("0"),
            category=PurchaseCategory(category),
            site_location=site_location,
            stock_number=stock_number,
            mileage=_parse_int(mileage),
            description=description,
            # Who entered it is the signed-in user, never a form field.
            entered_by=user.display_name,
            entered_by_user_id=user.id,
            shipping_cost=_d(shipping_cost) or Decimal("0"),
            other_fees=_d(other_fees) or Decimal("0"),
            vin=vin,
            year=_parse_int(year),
            make=make,
            model=model,
            payment_source=payment_source,
            db=db,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/purchases/new?error={str(e).replace(' ', '+')}", status_code=303)

    if receipt and receipt.filename:
        save_uploaded_file(
            linked_record_type="purchase",
            record_id=purchase.id,
            file=receipt,
            uploaded_by=user.display_name,
            db=db,
        )
        purchase.receipt_attached = True
        purchase.transaction.receipt_attached = True

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase.id}/edit?msg=Purchase+recorded", status_code=303)


@router.get("/{purchase_id}/edit", response_class=HTMLResponse)
def edit_purchase_form(
    purchase_id: int, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/purchases/")
    receipt = (
        db.query(Document)
        .filter(Document.linked_record_type == "purchase", Document.linked_record_id == purchase.id)
        .order_by(Document.upload_timestamp.desc())
        .first()
    )
    return templates.TemplateResponse("purchases/form.html", {
        "request": request,
        "purchase": purchase,
        "receipt": receipt,
        "editable": is_editable(purchase),
        **_form_context(db),
    })


@router.post("/{purchase_id}/edit")
async def update_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    purchaser: str = Form(...),
    purchase_date: str = Form(...),
    vendor: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    site_location: str = Form(""),
    stock_number: str = Form(""),
    mileage: str = Form(""),
    description: str = Form(""),
    shipping_cost: str = Form(""),
    other_fees: str = Form(""),
    payment_source: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
):
    from app.services.purchase_service import find_or_create_vendor

    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/purchases/", status_code=303)

    # A reviewed purchase is locked for everyone; an admin reopens it first.
    if not is_editable(purchase):
        raise HTTPException(status_code=403, detail="This purchase has been reviewed and can't be changed. An administrator can reopen it.")

    new_category = PurchaseCategory(category)
    matched_unit = match_unit_by_stock_number(stock_number, db) if stock_number else None
    try:
        check_edit_allowed(purchase, new_category, matched_unit)
    except ValueError as e:
        return RedirectResponse(url=f"/purchases/{purchase_id}/edit?error={str(e).replace(' ', '+')}", status_code=303)

    v = find_or_create_vendor(vendor, db)
    # A non-vehicle purchase links to a newly-matched unit; it never silently
    # unlinks one just because the free-text stock number stopped matching
    # (e.g. it was never expected to equal an auto-created unit's unit_id).
    # A vehicle purchase keeps its unit: check_edit_allowed rejected any other match.
    if matched_unit and not is_vehicle_purchase(purchase.category):
        purchase.unit_id = matched_unit.id

    purchase.purchaser = purchaser
    purchase.purchase_date = date.fromisoformat(purchase_date)
    purchase.vendor_id = v.id
    purchase.amount = _d(amount) or Decimal("0")
    purchase.shipping_cost = _d(shipping_cost)
    purchase.other_fees = _d(other_fees)
    purchase.category = PurchaseCategory(category)
    purchase.site_location = site_location or None
    purchase.stock_number = stock_number or None
    purchase.mileage = _parse_int(mileage)
    purchase.description = description or None

    sync_purchase_transaction(purchase, db)

    # A blank value never erases an existing one (historical purchases have none).
    if payment_source.strip() and purchase.transaction:
        purchase.transaction.payment_source = payment_source.strip()

    if receipt and receipt.filename:
        save_uploaded_file(
            linked_record_type="purchase",
            record_id=purchase.id,
            file=receipt,
            uploaded_by=user.display_name,
            db=db,
        )
        purchase.receipt_attached = True
        if purchase.transaction:
            purchase.transaction.receipt_attached = True

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}/edit?msg=Purchase+updated", status_code=303)


@router.post("/{purchase_id}/reviewed")
def mark_purchase_reviewed(
    purchase_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if purchase and mark_reviewed(purchase, user, db):
        db.commit()
    return RedirectResponse(url="/purchases/?msg=Marked+reviewed", status_code=303)


@router.post("/{purchase_id}/reopen")
def reopen_purchase(
    purchase_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin),
    reason: str = Form(""),
):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/purchases/", status_code=303)
    try:
        reopen(purchase, user, reason, db)
    except ValueError as e:
        return RedirectResponse(url=f"/purchases/{purchase_id}/edit?error={str(e).replace(' ', '+')}", status_code=303)
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}/edit?msg=Reopened+for+editing", status_code=303)
