from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import LeaseStatus, DelinquencyStatus, PaymentFrequency


class LeaseAccount(Base):
    __tablename__ = "lease_accounts"

    id = Column(Integer, primary_key=True, index=True)
    lease_id = Column(String(20), unique=True, nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    deal_date = Column(Date, nullable=False)
    original_agreed_amount = Column(Numeric(10, 2))
    down_payment = Column(Numeric(10, 2), default=0)
    financed_balance = Column(Numeric(10, 2))
    scheduled_payment_amount = Column(Numeric(10, 2))
    payment_frequency = Column(SAEnum(PaymentFrequency), default=PaymentFrequency.monthly)
    status = Column(SAEnum(LeaseStatus), default=LeaseStatus.active, nullable=False)
    # outstanding_balance is NOT stored here.
    # Use lease_service.calculate_remaining_balance(lease, db) to get the computed value.
    delinquency_status = Column(SAEnum(DelinquencyStatus), default=DelinquencyStatus.current)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="lease_accounts")
    unit = relationship("Unit", back_populates="lease_accounts")
    payments = relationship("Payment", back_populates="lease_account")
    transactions = relationship("Transaction", back_populates="lease_account")
    invoices = relationship("Invoice", back_populates="lease_account")
