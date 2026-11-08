"""
My actions: the restricted view of the monthly-review issues assigned to one user (spec section 20, REQ-MR-23..25).

Belinda (an employee) can see the questions assigned to HER, answer them, and supply permitted supporting records, without
access to the owner's Monthly Review page. It reuses the review_issues / review_issue_events records; there is no second queue.

What an assignee sees is deliberately small:
  * the issue title, status, due date, and the administrators' messages to her ("to_owner" events) and her own replies;
  * her own files, with permission-checked downloads.
She never sees: the internal description, automatic evidence, internal notes, source links, other people's issues, other
months' figures, payroll (restricted issues are not shown to non-administrators at all), or any financial total.
"""
import hashlib
import os
import re
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import STORAGE_DIR
from app.models.monthly_review import ReviewIssue, ReviewIssueEvent, ReviewIssueFile
from app.models.user import User
from app.services import monthly_review_service as svc

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".csv", ".xlsx", ".txt"}
OWNER_STATUSES = ("in_progress", "waiting")                      # she can never resolve or reopen; administrators do
VISIBLE_EVENTS = {"to_owner", "owner_reply", "file", "assigned", "status", "due_date"}


class ActionError(ValueError):
    pass


def storage_root() -> str:
    return os.path.join(STORAGE_DIR, "monthly_review")


def _may_see(user: User, issue: ReviewIssue) -> bool:
    if issue.owner_user_id != user.id:
        return False
    return user.is_admin or not issue.restricted


def my_issues(db: Session, user: User) -> list:
    rows = db.query(ReviewIssue).filter(ReviewIssue.owner_user_id == user.id).all()
    rows = [i for i in rows if _may_see(user, i)]
    rows.sort(key=lambda i: (i.status == "resolved", i.due_date or datetime.max.date(), i.id))
    return rows


def get_issue(db: Session, user: User, ref: str) -> Optional[ReviewIssue]:
    """None unless the issue is assigned to this user and visible to them. Callers answer 404 so existence is not revealed."""
    q = db.query(ReviewIssue)
    i = q.filter(ReviewIssue.issue_id == ref).first() if not ref.isdigit() else q.filter(ReviewIssue.id == int(ref)).first()
    return i if i is not None and _may_see(user, i) else None


def visible_events(issue: ReviewIssue) -> list:
    out = []
    for e in issue.events:
        if e.event_type not in VISIBLE_EVENTS:
            continue
        who = "An administrator" if e.event_type == "to_owner" else (e.user.display_name or e.user.username if e.user_id else "System")
        text = e.body
        if e.event_type == "assigned":
            text = f"Assigned to {e.to_value}"
        elif e.event_type == "status":
            text = f"Status: {e.from_value} to {e.to_value}"
        elif e.event_type == "due_date":
            text = f"Due date: {e.from_value} to {e.to_value}"
        elif e.event_type == "file":
            text = e.body
        out.append({"when": e.created_at, "who": who, "kind": e.event_type, "text": text, "mine": e.event_type in ("owner_reply", "file")})
    return out


def files_for(db: Session, issue: ReviewIssue) -> list:
    return db.query(ReviewIssueFile).filter(ReviewIssueFile.issue_pk == issue.id).order_by(ReviewIssueFile.id).all()


def reply(db: Session, user: User, issue: ReviewIssue, *, body: str = "", status: str = "") -> int:
    if issue.status == "resolved":
        raise ActionError("This question is closed. Ask an administrator if something has changed.")
    body, status = (body or "").strip(), (status or "").strip()
    if not body and not status:
        raise ActionError("Write a reply, or choose a status.")
    if status and status not in OWNER_STATUSES:
        raise ActionError("You can mark a question In progress or Waiting. Only an administrator can resolve it.")
    svc.check_restricted_text(body, restricted=issue.restricted)
    n, now = 0, datetime.utcnow()
    if status and status != issue.status:
        svc._log(db, issue, "status", user, frm=svc.STATUS_LABEL[issue.status], to=svc.STATUS_LABEL[status], now=now)
        issue.status = status
        n += 1
    if body:
        svc._log(db, issue, "owner_reply", user, body=body, now=now)
        n += 1
    return n


def _clean_name(raw: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename((raw or "").replace("\\", "/"))).strip()[:255] or "file"


def attach_file(db: Session, user: User, issue: ReviewIssue, *, file, note: str = "") -> ReviewIssueFile:
    if issue.status == "resolved":
        raise ActionError("This question is closed.")
    if file is None or not getattr(file, "filename", ""):
        raise ActionError("Choose a file.")
    name = _clean_name(file.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ActionError("That file type is not accepted. Use: " + ", ".join(sorted(ALLOWED_EXT)) + ".")
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ActionError("That file is larger than 10 MB.")
    if not data:
        raise ActionError("That file is empty.")
    note = (note or "").strip()
    svc.check_restricted_text(note, restricted=issue.restricted)
    sha = hashlib.sha256(data).hexdigest()
    if db.query(ReviewIssueFile).filter(ReviewIssueFile.issue_pk == issue.id, ReviewIssueFile.sha256 == sha).first():
        raise ActionError("That exact file is already attached to this question.")
    os.makedirs(storage_root(), exist_ok=True)
    dest = os.path.join(storage_root(), uuid.uuid4().hex + ext)
    with open(dest, "wb") as fh:
        fh.write(data)
    try:
        f = ReviewIssueFile(issue_pk=issue.id, original_filename=name, stored_path=dest, size_bytes=len(data), sha256=sha, note=note,
                            user_id=user.id, created_at=datetime.utcnow())
        db.add(f)
        db.flush()
        svc._log(db, issue, "file", user, body=f"Attached {name}" + (f": {note}" if note else ""))
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return f


def record_source(db: Session, user: User, issue: ReviewIssue, *, coverage_start: str, coverage_end: str, where_kept: str) -> None:
    """For an automatic 'source:<key>' issue: the owner says she HAS the record. Allowed for non-restricted sources only."""
    key = (issue.auto_key or "")
    if not key.startswith("source:") or key.split(":", 1)[1] not in svc.SOURCES or key.split(":", 1)[1] == "payroll_weekly" or issue.restricted:
        raise ActionError("This question is not about a record you can note here.")
    if issue.status == "resolved":
        raise ActionError("This question is closed.")
    src = key.split(":", 1)[1]
    svc.add_attestation(db, user, issue.month, source_key=src, status="received", coverage_start=coverage_start, coverage_end=coverage_end,
                        where_kept=where_kept)
    svc._log(db, issue, "owner_reply", user, body=f"I have the {svc.SOURCES[src].split(' (')[0].lower()} for {coverage_start} to {coverage_end}; it is kept: {where_kept}")


def can_download(user: User, f: ReviewIssueFile) -> bool:
    if user.is_admin:
        return True
    return f.issue.owner_user_id == user.id and not f.issue.restricted


def file_path(f: ReviewIssueFile) -> Optional[str]:
    root = os.path.realpath(storage_root())
    p = os.path.realpath(f.stored_path)
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None
