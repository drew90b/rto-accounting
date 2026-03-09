"""
Repair job business logic.

close_repair_job()   — closes the job and auto-creates a revenue transaction
                        for customer_repair jobs that have a billed amount.
record_repair_payment() — records a cash payment event for a closed repair job.

Rules enforced:
  - customer_repair with total_billed_amount > 0: revenue transaction created automatically.
  - customer_support_repair: no revenue transaction (cost-of-portfolio).
  - internal_recon: no revenue transaction.
  - The revenue transaction is created once at close time.
    Subsequent payments are recorded via record_repair_payment().

Caller must db.commit() after each function returns.
"""
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.transaction import Transaction


def close_repair_job(job, db: Session):
    """
    Mark a repair job complete and create its revenue transaction if applicable.

    Updates job.status and job.close_date on the passed ORM object.
    Returns the created Transaction, or None if no revenue transaction was needed.
    """
    today = date_type.today()
    job.status = "complete"
    if not job.close_date:
        job.close_date = today

    business_line = job.business_line.value if hasattr(job.business_line, "value") else job.business_line
    job_type = job.job_type.value if hasattr(job.job_type, "value") else job.job_type
    total_billed = Decimal(str(job.total_billed_amount)) if job.total_billed_amount else Decimal("0")

    if job_type != "customer_repair" or total_billed <= Decimal("0"):
        return None

    revenue_stream = "car_repair" if business_line == "car" else "golf_cart_repair"
    t = Transaction(
        transaction_date=today,
        entry_date=today,
        transaction_type="repair_revenue",
        business_line=business_line,
        revenue_stream=revenue_stream,
        customer_id=job.customer_id,
        unit_id=job.unit_id,
        repair_job_id=job.id,
        amount=total_billed,
        description=f"Repair billing — {job.job_id}",
        category="repair_revenue",
        coding_complete=True,
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"
    return t


def record_repair_payment(
    job,
    payment_date,
    amount: Decimal,
    payment_method: str,
    entered_by: str,
    notes: str,
    db: Session,
) -> Payment:
    """
    Record a payment received for a repair job.

    The revenue transaction was already created at close time.
    This just records the cash collection event.
    """
    p = Payment(
        customer_id=job.customer_id,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        repair_job_id=job.id,
        entered_by=entered_by or None,
        notes=notes or None,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"
    return p
