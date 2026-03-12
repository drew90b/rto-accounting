from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base
from app.models.enums import InvoiceType, InvoiceStatus


class Invoice(Base):
    """
    Persisted invoice record created automatically when a revenue event occurs.

    invoice_type distinguishes the source of the invoice:
      sale        — vehicle or golf cart outright sale
      repair      — customer_repair job billing
      rto_payment — a single RTO/lease payment receipt

    invoice_number (INV-00001) is generated post-flush from the integer pk,
    following the same pattern as all other human-readable IDs in the system.

    FK columns are all nullable because each invoice type only populates
    the relevant one:
      sale_id          — set for sale invoices
      repair_job_id    — set for repair invoices
      lease_account_id — set for rto_payment invoices
      payment_id       — set for rto_payment invoices (specific payment record)
    """
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(20), unique=True, nullable=True)  # INV-00001, set post-flush

    invoice_type = Column(SAEnum(InvoiceType), nullable=False)
    status = Column(SAEnum(InvoiceStatus), default=InvoiceStatus.open, nullable=False)

    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    repair_job_id = Column(Integer, ForeignKey("repair_jobs.id"), nullable=True)
    lease_account_id = Column(Integer, ForeignKey("lease_accounts.id"), nullable=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)

    invoice_date = Column(Date, nullable=False)

    subtotal = Column(Numeric(10, 2), nullable=False, default=0)
    tax_rate = Column(Numeric(5, 4), nullable=False, default=0)   # e.g. 0.0700 = 7 %
    tax_amount = Column(Numeric(10, 2), nullable=False, default=0)
    total = Column(Numeric(10, 2), nullable=False, default=0)
    amount_paid = Column(Numeric(10, 2), nullable=False, default=0)
    balance = Column(Numeric(10, 2), nullable=False, default=0)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    customer = relationship("Customer", back_populates="invoices")
    sale = relationship("Sale", back_populates="invoices")
    repair_job = relationship("RepairJob", back_populates="invoices")
    lease_account = relationship("LeaseAccount", back_populates="invoices")
    payment = relationship("Payment", back_populates="invoice", foreign_keys=[payment_id])
    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.sort_order",
    )


class InvoiceItem(Base):
    """
    A single line item on an invoice.

    For a sale invoice: one line per charge component (sale amount, fees).
    For a repair invoice: one line per cost component (labor, materials).
    For an rto_payment invoice: one line for the payment amount.
    """
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)

    description = Column(String(500), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False, default=0)
    line_total = Column(Numeric(10, 2), nullable=False, default=0)
    sort_order = Column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="items")
