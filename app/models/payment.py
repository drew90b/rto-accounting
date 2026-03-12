from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import PaymentMethod


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(String(20), unique=True, nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    payment_date = Column(Date, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(SAEnum(PaymentMethod), nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    lease_account_id = Column(Integer, ForeignKey("lease_accounts.id"), nullable=True)
    repair_job_id = Column(Integer, ForeignKey("repair_jobs.id"), nullable=True)
    notes = Column(Text)
    entered_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="payments")
    sale = relationship("Sale", back_populates="payments")
    lease_account = relationship("LeaseAccount", back_populates="payments")
    repair_job = relationship("RepairJob", back_populates="payments")
    invoice = relationship("Invoice", back_populates="payment", foreign_keys="Invoice.payment_id")
