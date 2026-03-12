from sqlalchemy import Column, Integer, String, Date, Text, DateTime, Enum as SAEnum
from datetime import datetime
from app.database import Base
from app.models.enums import ExceptionType, LinkedRecordType, ExceptionStatus


class ExceptionRecord(Base):
    __tablename__ = "exceptions"

    id = Column(Integer, primary_key=True, index=True)
    exception_id = Column(String(20), unique=True, nullable=True)
    exception_type = Column(SAEnum(ExceptionType), nullable=False)
    linked_record_type = Column(SAEnum(LinkedRecordType), nullable=True)
    linked_record_id = Column(Integer, nullable=True)
    opened_date = Column(Date, nullable=False)
    owner = Column(String(50))
    status = Column(SAEnum(ExceptionStatus), default=ExceptionStatus.open, nullable=False)
    notes = Column(Text)
    target_resolution_date = Column(Date, nullable=True)
    resolution_action = Column(Text)
    audit_history = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
