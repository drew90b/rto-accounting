from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.enums import PurchaseEventAction


class PurchaseEvent(Base):
    """
    One row per create, edit, receipt, review and reopen of a purchase, so a
    reviewer can see what a purchase looked like when it was reviewed and who
    changed it afterwards. Only changes made through the app are recorded;
    historical rows have no events.

    changes is {field: [old, new]} in human-readable values.
    """
    __tablename__ = "purchase_events"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(SAEnum(PurchaseEventAction), nullable=False)
    changes = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchase = relationship("Purchase", back_populates="events")
    user = relationship("User")
