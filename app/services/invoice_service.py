"""
Invoice generation service.

Every revenue event produces one Invoice record.  Three creation paths:

  create_invoice_from_sale(sale, db)
      Called from finalize_new_sale() immediately after payment records
      are created.  Builds line items from sale_amount and fees.

  create_invoice_from_repair(job, db)
      Called from close_repair_job() for customer_repair jobs that have
      a billed amount.  Builds line items from labor and materials.

  create_invoice_from_rto_payment(lease, payment, db)
      Called from record_rto_payment().  Creates one receipt per payment
      event, showing the amount collected and remaining balance.

  create_simple_receipt(customer_id, description, amount, db)
      Ad-hoc utility for one-off receipts not tied to a specific business
      entity (e.g. parts-only counter sales before a full parts module
      is built).

  generate_invoice_document(invoice_id, db)
      Renders the invoice HTML template and saves it to
      storage/invoices/<invoice_number>.html.  Returns the saved file path.
      The route handler renders separately for the HTTP response.

Invoice number format: INV-00001 (set post-flush from the integer pk).
Tax rate defaults to 0 — there is no tax module yet; the field exists for
future configuration.

Caller must db.commit() after any create_* function returns.
"""
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.invoice import Invoice, InvoiceItem
from app.models.enums import InvoiceType, InvoiceStatus

# Path where rendered invoice HTML files are stored.
_INVOICE_DIR = Path(__file__).parents[2] / "storage" / "invoices"


# ---------------------------------------------------------------------------
# Public creation functions
# ---------------------------------------------------------------------------

def create_invoice_from_sale(sale, db: Session) -> Invoice:
    """
    Build an Invoice from a completed (or newly-created) Sale.

    Line items:
      • Vehicle / Unit Sale — sale_amount
      • Doc Fees / Tags    — fees  (only if fees > 0)

    amount_paid reflects any payment already recorded:
      • Golf cart — always paid in full at counter.
      • Car       — down_payment only (may be 0).

    Status is set to 'paid' when amount_paid == total, 'open' otherwise.
    """
    business_line = (
        sale.business_line.value
        if hasattr(sale.business_line, "value")
        else sale.business_line
    )
    sale_amount = Decimal(str(sale.sale_amount)) if sale.sale_amount else Decimal("0")
    fees = Decimal(str(sale.fees)) if sale.fees else Decimal("0")
    down_payment = Decimal(str(sale.down_payment)) if sale.down_payment else Decimal("0")
    total = (
        Decimal(str(sale.total_contract_amount))
        if sale.total_contract_amount
        else sale_amount + fees
    )

    if business_line == "golf_cart":
        amount_paid = total  # always paid in full at counter
    else:
        amount_paid = down_payment

    invoice = _build_invoice(
        invoice_type=InvoiceType.sale,
        customer_id=sale.customer_id,
        invoice_date=sale.sale_date,
        subtotal=sale_amount + fees,
        amount_paid=amount_paid,
        sale_id=sale.id,
        db=db,
    )

    # Line items
    unit_desc = _unit_description(sale.unit)
    _add_item(invoice, f"Vehicle Sale — {unit_desc}", sale_amount, 1, db)
    if fees > Decimal("0"):
        _add_item(invoice, "Doc Fees / Tags", fees, 2, db)

    return invoice


def create_invoice_from_repair(job, db: Session) -> Invoice:
    """
    Build an Invoice from a closed customer_repair job.

    Line items:
      • Labor            — labor_amount  (if > 0)
      • Parts / Materials — materials_amount  (if > 0)

    The invoice starts with amount_paid = 0 because payments are collected
    after the job is closed via record_repair_payment().
    """
    total_billed = Decimal(str(job.total_billed_amount)) if job.total_billed_amount else Decimal("0")

    invoice = _build_invoice(
        invoice_type=InvoiceType.repair,
        customer_id=job.customer_id,
        invoice_date=job.close_date or date_type.today(),
        subtotal=total_billed,
        amount_paid=Decimal("0"),
        repair_job_id=job.id,
        db=db,
    )

    sort = 1
    labor = Decimal(str(job.labor_amount)) if job.labor_amount else Decimal("0")
    materials = Decimal(str(job.materials_amount)) if job.materials_amount else Decimal("0")

    if labor > Decimal("0"):
        _add_item(invoice, "Labor", labor, sort, db)
        sort += 1
    if materials > Decimal("0"):
        _add_item(invoice, "Parts / Materials", materials, sort, db)
        sort += 1
    if sort == 1:
        # Neither labor nor materials broken out — use total as single line
        _add_item(invoice, "Repair Services", total_billed, 1, db)

    return invoice


def create_invoice_from_rto_payment(lease, payment, db: Session) -> Invoice:
    """
    Build a payment receipt for a single RTO collection event.

    This is a receipt that shows:
      • Payment amount collected
      • Remaining balance on the lease after this payment

    The invoice total equals the payment amount (not the lease total).
    amount_paid == total → status is 'paid' immediately.
    """
    amount = Decimal(str(payment.amount)) if payment.amount else Decimal("0")
    remaining = _compute_remaining_balance(lease, db)

    unit_desc = _unit_description(lease.unit) if lease.unit else "Unit"
    lease_id = lease.lease_id or f"Lease #{lease.id}"

    invoice = _build_invoice(
        invoice_type=InvoiceType.rto_payment,
        customer_id=lease.customer_id,
        invoice_date=payment.payment_date,
        subtotal=amount,
        amount_paid=amount,  # receipt: payment was just made
        lease_account_id=lease.id,
        payment_id=payment.id,
        db=db,
    )
    invoice.balance = remaining  # override: balance = what's left on the lease

    method = (
        payment.payment_method.value
        if hasattr(payment.payment_method, "value")
        else (payment.payment_method or "")
    )
    _add_item(
        invoice,
        f"RTO Payment — {lease_id} — {unit_desc}",
        amount,
        1,
        db,
        description_extra=f"Method: {method.title()}" if method else "",
    )

    return invoice


def create_simple_receipt(
    customer_id: int,
    description: str,
    amount: Decimal,
    db: Session,
    invoice_date=None,
) -> Invoice:
    """
    Ad-hoc receipt for one-off transactions (e.g. a counter parts sale).

    Creates a single-line invoice with the provided description and amount.
    """
    invoice = _build_invoice(
        invoice_type=InvoiceType.repair,  # closest type; or add a 'misc' type later
        customer_id=customer_id,
        invoice_date=invoice_date or date_type.today(),
        subtotal=amount,
        amount_paid=Decimal("0"),
        db=db,
    )
    _add_item(invoice, description, amount, 1, db)
    return invoice


def generate_invoice_document(invoice_id: int, db: Session) -> str:
    """
    Render the invoice HTML template and save to storage/invoices/.

    Uses Jinja2 directly (without FastAPI's request context) so this can be
    called from background tasks or CLI scripts.

    Returns the absolute path of the saved file as a string.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")

    payments, total_paid, remaining_balance, source = _load_invoice_context(invoice, db)

    template_dir = str(Path(__file__).parents[1] / "templates")
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("invoices/invoice.html")

    html = template.render(
        invoice=invoice,
        items=invoice.items,
        customer=invoice.customer,
        payments=payments,
        total_paid=total_paid,
        remaining_balance=remaining_balance,
        source=source,
        msg=None,
        request=None,
    )

    _INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{invoice.invoice_number or ('INV-' + str(invoice.id))}.html"
    out_path = _INVOICE_DIR / filename
    out_path.write_text(html, encoding="utf-8")

    return str(out_path)


# ---------------------------------------------------------------------------
# Helpers used by route handlers (so routes don't duplicate this logic)
# ---------------------------------------------------------------------------

def load_invoice_for_display(invoice_id: int, db: Session):
    """
    Load an invoice and compute all display values needed for the template.

    Returns (invoice, items, customer, payments, total_paid, remaining_balance, source)
    or raises ValueError if the invoice is not found.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")
    payments, total_paid, remaining_balance, source = _load_invoice_context(invoice, db)
    return invoice, invoice.items, invoice.customer, payments, total_paid, remaining_balance, source


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_invoice(
    invoice_type: InvoiceType,
    customer_id: int,
    invoice_date,
    subtotal: Decimal,
    amount_paid: Decimal,
    db: Session,
    sale_id: int = None,
    repair_job_id: int = None,
    lease_account_id: int = None,
    payment_id: int = None,
) -> Invoice:
    """Create, flush, and assign the invoice_number. Returns the Invoice."""
    total = subtotal  # tax_rate = 0 by default
    balance = total - amount_paid
    status = InvoiceStatus.paid if balance <= Decimal("0") else InvoiceStatus.open

    inv = Invoice(
        invoice_type=invoice_type,
        status=status,
        customer_id=customer_id,
        sale_id=sale_id,
        repair_job_id=repair_job_id,
        lease_account_id=lease_account_id,
        payment_id=payment_id,
        invoice_date=invoice_date,
        subtotal=subtotal,
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
        amount_paid=amount_paid,
        balance=max(balance, Decimal("0")),
    )
    db.add(inv)
    db.flush()
    inv.invoice_number = f"INV-{inv.id:05d}"
    return inv


def _add_item(
    invoice: Invoice,
    description: str,
    amount: Decimal,
    sort_order: int,
    db: Session,
    quantity: Decimal = Decimal("1"),
    description_extra: str = "",
) -> InvoiceItem:
    """Add one InvoiceItem to an already-flushed invoice."""
    full_desc = f"{description}  {description_extra}".strip() if description_extra else description
    item = InvoiceItem(
        invoice_id=invoice.id,
        description=full_desc,
        quantity=quantity,
        unit_price=amount,
        line_total=amount * quantity,
        sort_order=sort_order,
    )
    db.add(item)
    return item


def _unit_description(unit) -> str:
    """Return a short human-readable description of a unit."""
    if not unit:
        return "Unit"
    parts = [str(unit.year or ""), unit.make or "", unit.model or ""]
    desc = " ".join(p for p in parts if p).strip()
    return desc or (unit.unit_id or "Unit")


def _compute_remaining_balance(lease, db: Session) -> Decimal:
    """
    Compute the remaining balance on a lease without importing lease_service
    (which would create a circular import since lease_service imports this module).
    """
    from app.models.payment import Payment as PaymentModel
    if lease.financed_balance is None:
        return Decimal("0.00")
    total_paid = (
        db.query(func.sum(PaymentModel.amount))
        .filter(PaymentModel.lease_account_id == lease.id)
        .scalar()
    ) or Decimal("0.00")
    return Decimal(str(lease.financed_balance)) - Decimal(str(total_paid))


def _load_invoice_context(invoice: Invoice, db: Session):
    """
    Return (payments, total_paid, remaining_balance, source) for an invoice.

    'source' is the related Sale, RepairJob, or LeaseAccount object.
    'payments' is the list of Payment records associated with the source entity.
    """
    from app.models.payment import Payment as PaymentModel

    source = None
    payments = []

    if invoice.invoice_type == InvoiceType.sale and invoice.sale_id:
        source = invoice.sale
        payments = (
            db.query(PaymentModel)
            .filter(PaymentModel.sale_id == invoice.sale_id)
            .order_by(PaymentModel.payment_date)
            .all()
        )
    elif invoice.invoice_type == InvoiceType.repair and invoice.repair_job_id:
        source = invoice.repair_job
        payments = (
            db.query(PaymentModel)
            .filter(PaymentModel.repair_job_id == invoice.repair_job_id)
            .order_by(PaymentModel.payment_date)
            .all()
        )
    elif invoice.invoice_type == InvoiceType.rto_payment and invoice.lease_account_id:
        source = invoice.lease_account
        # For RTO receipts show only the specific payment this invoice was created for
        if invoice.payment_id:
            p = db.query(PaymentModel).filter(PaymentModel.id == invoice.payment_id).first()
            payments = [p] if p else []
        else:
            payments = []

    total_paid = sum((Decimal(str(p.amount)) for p in payments), Decimal("0"))

    # remaining_balance: for RTO receipts use the stored balance (set at creation)
    if invoice.invoice_type == InvoiceType.rto_payment:
        remaining_balance = Decimal(str(invoice.balance))
    else:
        remaining_balance = max(
            Decimal(str(invoice.total)) - total_paid, Decimal("0")
        )

    return payments, total_paid, remaining_balance, source
