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
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    entered_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="purchases")
    unit = relationship("Unit", back_populates="purchases")
    transaction = relationship("Transaction", back_populates="purchase")
