from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import CustomerStatus


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String(20), unique=True, nullable=True)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(20))
    email = Column(String(100))
    address = Column(Text)
    notes = Column(Text)
    status = Column(SAEnum(CustomerStatus), default=CustomerStatus.active, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    units = relationship("Unit", back_populates="customer", foreign_keys="Unit.linked_customer_id")
    sales = relationship("Sale", back_populates="customer")
    lease_accounts = relationship("LeaseAccount", back_populates="customer")
    payments = relationship("Payment", back_populates="customer")
    repair_jobs = relationship("RepairJob", back_populates="customer")
    transactions = relationship("Transaction", back_populates="customer")
    invoices = relationship("Invoice", back_populates="customer")
