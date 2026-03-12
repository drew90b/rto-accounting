from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import BusinessLine, SaleStatus


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(String(20), unique=True, nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    sale_date = Column(Date, nullable=False)
    business_line = Column(SAEnum(BusinessLine), nullable=False)
    sale_amount = Column(Numeric(10, 2), nullable=False)
    down_payment = Column(Numeric(10, 2), default=0)
    fees = Column(Numeric(10, 2), default=0)
    total_contract_amount = Column(Numeric(10, 2))
    status = Column(SAEnum(SaleStatus), default=SaleStatus.pending, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="sales")
    unit = relationship("Unit", back_populates="sales")
    payments = relationship("Payment", back_populates="sale")
    transactions = relationship("Transaction", back_populates="sale")
    invoices = relationship("Invoice", back_populates="sale")
