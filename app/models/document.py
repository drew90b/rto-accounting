from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SAEnum
from datetime import datetime
from app.database import Base
from app.models.enums import LinkedRecordType


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String(20), unique=True, nullable=False)
    linked_record_type = Column(SAEnum(LinkedRecordType), nullable=False)
    linked_record_id = Column(Integer, nullable=False)
    file_path = Column(String(500), nullable=False)
    original_filename = Column(String(200), nullable=False)
    file_type = Column(String(50))
    upload_timestamp = Column(DateTime, default=datetime.utcnow)
    uploaded_by = Column(String(50))
    notes = Column(Text)
