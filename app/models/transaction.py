from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import (
    TransactionType, BusinessLine, RevenueStream,
    PaymentMethod, ReviewStatus, ExceptionTxnStatus
)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String(20), unique=True, nullable=True)
    transaction_date = Column(Date, nullable=False)
    entry_date = Column(Date, default=datetime.utcnow)
    transaction_type = Column(SAEnum(TransactionType), nullable=False)
    business_line = Column(SAEnum(BusinessLine), nullable=False)
    revenue_stream = Column(SAEnum(RevenueStream), nullable=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    description = Column(Text)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    repair_job_id = Column(Integer, ForeignKey("repair_jobs.id"), nullable=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    lease_account_id = Column(Integer, ForeignKey("lease_accounts.id"), nullable=True)
    category = Column(String(50))
    payment_method = Column(SAEnum(PaymentMethod), nullable=True)
    receipt_attached = Column(Boolean, default=False)
    coding_complete = Column(Boolean, default=False)
    review_status = Column(SAEnum(ReviewStatus), default=ReviewStatus.pending)
    exception_status = Column(SAEnum(ExceptionTxnStatus), default=ExceptionTxnStatus.none)
    entered_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="transactions")
    customer = relationship("Customer", back_populates="transactions")
    unit = relationship("Unit", back_populates="transactions")
    repair_job = relationship("RepairJob", back_populates="transactions")
    sale = relationship("Sale", back_populates="transactions")
    lease_account = relationship("LeaseAccount", back_populates="transactions")
