from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import UnitType, UnitStatus, BusinessLine


class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(String(20), unique=True, nullable=True)
    unit_type = Column(SAEnum(UnitType), nullable=False)
    business_line = Column(SAEnum(BusinessLine), nullable=False)
    vin_serial = Column(String(50))
    year = Column(Integer)
    make = Column(String(50))
    model = Column(String(100))
    purchase_date = Column(Date)
    purchase_source = Column(String(100))
    acquisition_cost = Column(Numeric(10, 2))
    status = Column(SAEnum(UnitStatus), default=UnitStatus.acquired, nullable=False)
    repair_status = Column(String(50))
    sales_status = Column(String(50))
    linked_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="units", foreign_keys=[linked_customer_id])
    repair_jobs = relationship("RepairJob", back_populates="unit")
    sales = relationship("Sale", back_populates="unit")
    lease_accounts = relationship("LeaseAccount", back_populates="unit")
    transactions = relationship("Transaction", back_populates="unit")
