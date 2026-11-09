"""
Frazer export helper: who may upload, what each helper run did, and the approved column layout of each saved export.

These tables hold no customer data. A token proves only "this installation may upload the three Frazer exports"; it is not a
sign-in session and opens no page. Uploaded files and everything read from them live in the existing frazer_* intake tables.
"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import relationship

from app.database import Base


class FrazerHelperToken(Base):
    __tablename__ = "frazer_helper_tokens"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(80), nullable=False)                           # e.g. "Frazer PC, front office"
    token_hash = Column(String(64), nullable=False, unique=True)         # SHA-256 of the secret; the secret is shown once and never stored
    token_prefix = Column(String(12), nullable=False)                    # first characters, to recognise it in a list
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)    # uploads are attributed to this person; it grants them nothing
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class FrazerHelperRun(Base):
    __tablename__ = "frazer_helper_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(20), unique=True, nullable=True)              # FR-0001
    token_id = Column(Integer, ForeignKey("frazer_helper_tokens.id"), nullable=False)
    status = Column(String(20), nullable=False)                          # running | complete | partial | failed | abandoned
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_expires_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    retry_of_run_id = Column(Integer, ForeignKey("frazer_helper_runs.id"), nullable=True)
    business_date = Column(String(10), nullable=False)                   # the Chicago date the run started, ISO
    params_json = Column(JSON, nullable=True)                            # helper version, host name, payment period asked for
    results_json = Column(JSON, nullable=False, default=dict)            # export_type -> per-file result (see the service)
    sync_json = Column(JSON, nullable=True)                              # what the reconciliation synchronisation did
    summary = Column(Text, nullable=True)

    token = relationship("FrazerHelperToken")

    __table_args__ = (
        # One active run at a time, enforced by the database and not only by a check-then-insert.
        Index("uq_one_running_helper_run", "status", unique=True,
              sqlite_where=text("status = 'running'"), postgresql_where=text("status = 'running'")),
    )


class FrazerHelperLayout(Base):
    __tablename__ = "frazer_helper_layouts"

    export_type = Column(String(30), primary_key=True)                   # vehicles | customer_balances | payments
    headers_json = Column(JSON, nullable=False)                          # the approved header row, in order
    source_upload_id = Column(Integer, ForeignKey("frazer_uploads.id"), nullable=True)
    set_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    set_at = Column(DateTime, default=datetime.utcnow, nullable=False)
