from sqlalchemy import Column, Integer, String, Date, Numeric, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import BusinessLine, RepairJobType, RepairJobStatus


class RepairJob(Base):
    __tablename__ = "repair_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(20), unique=True, nullable=True)
    business_line = Column(SAEnum(BusinessLine), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    job_type = Column(SAEnum(RepairJobType), nullable=False)
    open_date = Column(Date, nullable=False)
    close_date = Column(Date, nullable=True)
    status = Column(SAEnum(RepairJobStatus), default=RepairJobStatus.open, nullable=False)
    labor_amount = Column(Numeric(10, 2), default=0)
    materials_amount = Column(Numeric(10, 2), default=0)
    total_billed_amount = Column(Numeric(10, 2), default=0)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    unit = relationship("Unit", back_populates="repair_jobs")
    customer = relationship("Customer", back_populates="repair_jobs")
    transactions = relationship("Transaction", back_populates="repair_job")
    payments = relationship("Payment", back_populates="repair_job")
    invoices = relationship("Invoice", back_populates="repair_job")
