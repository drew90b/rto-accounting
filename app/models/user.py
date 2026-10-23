from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum as SAEnum
from datetime import datetime
from app.database import Base
from app.models.enums import UserRole


class User(Base):
    """
    A person who signs in. Accounts are created from the command line
    (python -m app.scripts.users); there is no signup page.

    session_version is bumped when a password is reset or an account is
    disabled or re-roled, which ends that user's other sessions.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)  # stored lowercase
    display_name = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    session_version = Column(Integer, nullable=False, default=1)
    must_change_password = Column(Boolean, nullable=False, default=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin
