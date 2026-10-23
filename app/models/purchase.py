from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import PurchaseCategory, ReviewStatus


class Purchase(Base):
    """
    Employee-facing purchase submission — the front door that replaces the
    Google Purchase Order Form. Always creates a linked Transaction (the
    accounting ledger entry); Vendor and Unit are linked where they can be
    resolved, but a purchase never requires either.
    """
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(String(20), unique=True, nullable=True)
    purchase_date = Column(Date, nullable=False)
    purchaser = Column(String(100), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), nullable=True)
    other_fees = Column(Numeric(10, 2), nullable=True)
    category = Column(SAEnum(PurchaseCategory), nullable=False)
    site_location = Column(String(100))
    stock_number = Column(String(50))
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    mileage = Column(Integer)
    description = Column(Text)
    review_status = Column(SAEnum(ReviewStatus), default=ReviewStatus.pending, nullable=False)
    receipt_attached = Column(Boolean, default=False)
    # True only when this purchase itself created its Unit, and so established the
    # unit's acquisition_cost. Only such a purchase may sync a correction onto the
    # unit. False for every historical row (provenance unknown, never guessed) and
    # for purchases that matched a pre-existing unit.
    created_unit = Column(Boolean, nullable=False, default=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    # Free text. Historical rows only; native rows are attributed by entered_by_user_id.
    entered_by = Column(String(50))
    # Set by the server from the signed-in user, never from a form field.
    # purchaser (above) is who made the purchase; these are who entered/reviewed it.
    # NULL on historical rows: attribution is never backfilled or guessed.
    entered_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    # Set only by the historical importer (app/scripts/import_historical_purchases.py)
    # so a re-run can never create a duplicate. NULL for every native/UI-created purchase.
    source_reference = Column(String(100), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="purchases")
    unit = relationship("Unit", back_populates="purchases")
    transaction = relationship("Transaction", back_populates="purchase")
    entered_by_user = relationship("User", foreign_keys=[entered_by_user_id])
    reviewed_by_user = relationship("User", foreign_keys=[reviewed_by_user_id])
    events = relationship(
        "PurchaseEvent", back_populates="purchase", order_by="PurchaseEvent.id",
        cascade="all, delete-orphan",
    )
