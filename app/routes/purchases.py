import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.purchase import Purchase
from app.models.vendor import Vendor
from app.models.document import Document
from app.models.enums import PurchaseCategory, ReviewStatus
from app.services.purchase_service import record_purchase, sync_purchase_transaction, match_unit_by_stock_number
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
        "site_locations": ["Eunice"],
        "vendor_names": [v.name for v in db.query(Vendor).order_by(Vendor.name).all()],
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


@router.get("/export")
def export_purchases(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    purchases = db.query(Purchase).order_by(Purchase.purchase_date).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchases"
    headers = [
        "Purchase ID", "Date", "Purchaser", "Vendor", "Amount", "Category",
        "Site Location", "Stock #", "Unit", "Mileage", "Notes",
        "Receipt", "Review Status",
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
            p.category.value if p.category else "",
            p.site_location or "",
            p.stock_number or "",
            p.unit.unit_id if p.unit else "",
            p.mileage or "",
            p.description or "",
            "Yes" if p.receipt_attached else "No",
            p.review_status.value if p.review_status else "",
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
    purchaser: str = Form(...),
    purchase_date: str = Form(...),
    vendor: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    site_location: str = Form(""),
    stock_number: str = Form(""),
    mileage: str = Form(""),
    description: str = Form(""),
    entered_by: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
):
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
        entered_by=entered_by or purchaser,
        db=db,
    )

    if receipt and receipt.filename:
        save_uploaded_file(
            linked_record_type="purchase",
            record_id=purchase.id,
            file=receipt,
            uploaded_by=entered_by or purchaser,
            db=db,
        )
        purchase.receipt_attached = True
        purchase.transaction.receipt_attached = True

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase.id}/edit?msg=Purchase+recorded", status_code=303)


@router.get("/{purchase_id}/edit", response_class=HTMLResponse)
def edit_purchase_form(purchase_id: int, request: Request, db: Session = Depends(get_db)):
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
        **_form_context(db),
    })


@router.post("/{purchase_id}/edit")
async def update_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    purchaser: str = Form(...),
    purchase_date: str = Form(...),
    vendor: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    site_location: str = Form(""),
    stock_number: str = Form(""),
    mileage: str = Form(""),
    description: str = Form(""),
    entered_by: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
):
    from app.services.purchase_service import find_or_create_vendor

    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/purchases/", status_code=303)

    v = find_or_create_vendor(vendor, db)
    unit = match_unit_by_stock_number(stock_number, db) if stock_number else None

    purchase.purchaser = purchaser
    purchase.purchase_date = date.fromisoformat(purchase_date)
    purchase.vendor_id = v.id
    purchase.amount = _d(amount) or Decimal("0")
    purchase.category = PurchaseCategory(category)
    purchase.site_location = site_location or None
    purchase.stock_number = stock_number or None
    purchase.unit_id = unit.id if unit else None
    purchase.mileage = _parse_int(mileage)
    purchase.description = description or None
    purchase.entered_by = entered_by or None

    sync_purchase_transaction(purchase, db)

    if receipt and receipt.filename:
        save_uploaded_file(
            linked_record_type="purchase",
            record_id=purchase.id,
            file=receipt,
            uploaded_by=entered_by or purchaser,
            db=db,
        )
        purchase.receipt_attached = True
        if purchase.transaction:
            purchase.transaction.receipt_attached = True

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}/edit?msg=Purchase+updated", status_code=303)


@router.post("/{purchase_id}/review")
def toggle_review_status(purchase_id: int, db: Session = Depends(get_db)):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/purchases/", status_code=303)
    purchase.review_status = (
        ReviewStatus.pending if purchase.review_status == ReviewStatus.reviewed else ReviewStatus.reviewed
    )
    db.commit()
    return RedirectResponse(url="/purchases/?msg=Review+status+updated", status_code=303)
