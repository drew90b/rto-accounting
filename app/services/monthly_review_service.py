"""
Monthly Review workspace (spec section 20, Increment M).

Builds the one-page picture of a month from what the app really holds, keeps issues / notes / attestations, and
creates a narrow set of automatic issues. It REVIEWS a month; it never reconciles, verifies or closes one, and it
writes only the four review_* tables (REQ-MR-14).

Rules this module enforces:
  * Figures come from supported data only; a missing input is explained, never shown as $0.
  * Frazer transaction totals use the union of uploads by Sequential ID, so overlapping or repeated uploads count once.
    Entries whose content differs between uploads (or that carry errors) are excluded and listed (DEC-42).
  * A snapshot date is not transaction coverage; a declared range is "as declared, not verified".
  * Automatic evidence is stored apart from human decisions; a cleared condition is flagged, never auto-resolved.
  * Payroll is restricted: no amounts or names, in text we generate or text we accept.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.frazer_intake import FrazerSourceRow, FrazerUpload, FrazerUploadException
from app.models.monthly_review import (
    ReviewIssue, ReviewIssueEvent, ReviewIssueFile, ReviewNote, ReviewSourceRecord, ReviewStepMark,
)
from app.models.purchase import Purchase
from app.models.user import User
from app.services import business_calendar as bc
from app.services import close_progress as cp
from app.services import frazer_parse as fp
from app.services import review_period as rp
from app.services.review_period import Period


class ReviewError(ValueError):
    pass


class DuplicateIssue(ReviewError):
    def __init__(self, existing: "ReviewIssue"):
        super().__init__(f"An open issue with that title already exists this month ({existing.issue_id}).")
        self.existing = existing


STATUSES = ("open", "in_progress", "waiting", "resolved")
STATUS_LABEL = {"open": "Open", "in_progress": "In progress", "waiting": "Waiting", "resolved": "Resolved"}
CATEGORIES = {"records": "Missing record", "coverage": "Coverage", "mapping": "Unresolved meaning",
              "payroll": "Payroll record (restricted)", "reconciliation": "Reconciliation", "other": "Other"}
CHECKLIST = {"CL-1": "Bank and physical cash", "CL-2": "Frazer and Square activity", "CL-3": "Inventory, portfolio, debt",
             "CL-4": "Payroll (restricted)", "CL-5": "Management P&L inputs", "CL-6": "Sign-off"}
SOURCES = {"bank_statement": "Bank statements", "square": "Square sales and payouts",
           "cash_count": "Cash counts (paper sheets until Slice B)", "floor_plan_statement": "Floor-plan lender statement",
           "payroll_weekly": "Weekly payroll record (restricted)"}
SOURCE_REF = {"bank_statement": "CL-1", "square": "CL-2", "cash_count": "CL-1", "floor_plan_statement": "CL-3", "payroll_weekly": "CL-4"}

FRAZER_HOME = "/finance/frazer/"
OPENING_HOME = "/finance/opening/"

# --- restricted text (payroll) ----------------------------------------------------------------------------------------

_PAYROLL_WORDS = re.compile(r"payroll|wage|salary|paycheck|pay list|pay sheet|weekly pay", re.I)
_YEAR_OR_DATE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_MONEYISH = re.compile(r"\$|\d[\d,]*\.\d|\d{3,}|\d,\d{3}")


def check_restricted_text(text: str, *, restricted: bool = False) -> None:
    """Refuse text that looks like a pay amount where payroll is involved (DEC-09). A guard, not a guarantee."""
    if not text:
        return
    if restricted or _PAYROLL_WORDS.search(text):
        if _MONEYISH.search(_YEAR_OR_DATE.sub(" ", text)):
            raise ReviewError("Individual pay amounts and large numbers do not belong here: payroll detail is restricted "
                              "(DEC-09). Keep it in the secure outside-the-app record and write only that it exists.")


# --- small helpers ----------------------------------------------------------------------------------------------------

def _money(v) -> str:
    d = Decimal(str(v or 0))
    return f"{'-' if d < 0 else ''}${abs(d):,.2f}"


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _num(s) -> Optional[Decimal]:
    try:
        return Decimal(str(s)) if s not in (None, "") else None
    except Exception:
        return None


def user_name(u: Optional[User]) -> str:
    return (u.display_name or u.username) if u else "System"


# --- 1. what the app holds for the month -----------------------------------------------------------------------------

def _payments_uploads(db: Session) -> list:
    return (db.query(FrazerUpload).filter(FrazerUpload.export_type == fp.PAYMENTS, FrazerUpload.status == "parsed")
            .order_by(FrazerUpload.id).all())


def _declared_interval(u: FrazerUpload):
    """-> (start, end, basis): the declared range if there is one, else what the data shows (flagged)."""
    if u.declared_coverage_start and u.declared_coverage_end:
        from_name = "declared_from_filename" in (u.summary_json or {}) and \
            "coverage_start" in ((u.summary_json or {}).get("declared_from_filename") or [])
        return u.declared_coverage_start, u.declared_coverage_end, ("filename" if from_name else "declared")
    if u.observed_coverage_start and u.observed_coverage_end:
        return u.observed_coverage_start, u.observed_coverage_end, "observed"
    return None, None, "none"


@dataclass
class Entry:
    identity: str
    versions: dict = field(default_factory=dict)        # content_hash -> fields
    uploads: set = field(default_factory=set)
    error: bool = False


def frazer_transactions(db: Session, period: Period) -> dict:
    """Union of the transaction uploads by Sequential ID, restricted to the reporting period."""
    ups = _payments_uploads(db)
    out = {"uploads": ups, "relevant": [], "has_uploads": bool(ups), "rows_seen": 0, "distinct": 0, "groups": {}, "pairs": {},
           "revised": [], "errored": [], "undated": 0, "outside": 0, "in_period": 0, "intervals": [], "present_start": None,
           "present_end": None}
    if not ups:
        return out
    ids = [u.id for u in ups]
    relevant = []
    for u in ups:
        s, e, _ = _declared_interval(u)
        if s and e and not (e < period.start or s > period.end):
            relevant.append(u)
    out["relevant"] = relevant
    out["intervals"] = [(max(_declared_interval(u)[0], period.start), min(_declared_interval(u)[1], period.end)) for u in relevant]
    err = {i for (i,) in db.query(FrazerUploadException.identity).filter(
        FrazerUploadException.upload_id.in_(ids), FrazerUploadException.severity == "error",
        FrazerUploadException.identity.isnot(None)).distinct()}
    entries: dict = {}
    rows = db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id.in_(ids), FrazerSourceRow.record_type == "payment_entry")
    for r in rows:
        out["rows_seen"] += 1
        if not r.identity:
            out["undated"] += 1
            continue
        e = entries.setdefault(r.identity, Entry(r.identity))
        e.versions[r.content_hash] = (r.fields_json or {}, r.entry_class)
        e.uploads.add(r.upload_id)
    out["distinct"] = len(entries)
    groups = defaultdict(lambda: {"count": 0, "sum": Decimal(0)})
    pairs = defaultdict(lambda: {"count": 0, "sum": Decimal(0)})
    dates = []
    for ident, e in sorted(entries.items(), key=lambda kv: kv[0]):
        in_p = [(f, c) for f, c in e.versions.values() if (_d(f.get("transaction_date")) or date.min) >= period.start
                and (_d(f.get("transaction_date")) or date.max) <= period.end]
        unparsed = any(_d(f.get("transaction_date")) is None for f, _ in e.versions.values())
        if unparsed:
            out["undated"] += 1
            continue
        if not in_p:
            out["outside"] += 1
            continue
        if len(e.versions) > 1:
            out["revised"].append(ident)
            continue
        if ident in err:
            out["errored"].append(ident)
            continue
        f, cls = in_p[0]
        amt = _num(f.get("amount")) or Decimal(0)
        out["in_period"] += 1
        dates.append(_d(f["transaction_date"]))
        cls = cls or "unclassified"
        groups[cls]["count"] += 1
        groups[cls]["sum"] += amt
        key = f"{f.get('description', '')} | {f.get('transaction_method', '')}"
        pairs[key]["count"] += 1
        pairs[key]["sum"] += amt
    out["groups"] = dict(groups)
    out["pairs"] = dict(pairs)
    if dates:
        out["present_start"], out["present_end"] = min(dates), max(dates)
    return out


def _snapshots(db: Session, etype: str) -> list:
    return (db.query(FrazerUpload).filter(FrazerUpload.export_type == etype, FrazerUpload.status == "parsed")
            .order_by(FrazerUpload.declared_export_date, FrazerUpload.id).all())


def _purchases(db: Session, period: Period) -> dict:
    ps = db.query(Purchase).filter(Purchase.purchase_date >= period.start, Purchase.purchase_date <= period.end).all()
    total = sum((p.amount or 0) + (p.shipping_cost or 0) + (p.other_fees or 0) for p in ps) if ps else Decimal(0)
    pending = [p for p in ps if getattr(p.review_status, "value", p.review_status) == "pending"]
    return {"count": len(ps), "total": Decimal(str(total)), "pending": len(pending),
            "with_receipt": sum(1 for p in ps if p.receipt_attached)}


def _attestations(db: Session, month: date) -> list:
    return (db.query(ReviewSourceRecord).filter(ReviewSourceRecord.month == month)
            .order_by(ReviewSourceRecord.id).all())


# --- 2. the records table ---------------------------------------------------------------------------------------------

def _row(key, label, spec, status, coverage, links, outstanding, action, *, expected=True, manual=False, detail="",
         intervals=None, confirm_id=None):
    return {"key": key, "label": label, "spec": spec, "status": status, "coverage": coverage, "links": links,
            "outstanding": outstanding, "action": action, "expected": expected, "manual": manual, "detail": detail,
            "intervals": intervals or [], "confirm_id": confirm_id}


STATUS_TEXT = {"received": "Received", "partial": "Partial", "missing": "Missing", "not_required": "Not required",
               "info": "Info", "not_due": "Not due yet"}


def records_table(db: Session, period: Period, today: date, tx: dict) -> list:
    rows = []
    # Frazer transactions
    ups = tx["relevant"]
    if not tx["has_uploads"]:
        rows.append(_row("frazer_tx", "Frazer transactions", "CL-2", "missing", "No transaction export uploaded",
                         [("Frazer Intake", FRAZER_HOME)], f"A Frazer transaction export covering {period.span}",
                         "Download from Frazer and upload", detail="Nothing has been uploaded."))
    elif not ups:
        rows.append(_row("frazer_tx", "Frazer transactions", "CL-2", "missing",
                         f"{len(tx['uploads'])} upload(s), none declared for {period.label}", [("Frazer Intake", FRAZER_HOME)],
                         f"A transaction export covering {period.span}", "Upload this month's export"))
    else:
        gaps = rp.uncovered(period, tx["intervals"])
        decl = "; ".join(
            f"{rp.fmt_range(*_declared_interval(u)[:2])} ({'read from the filename' if _declared_interval(u)[2] == 'filename' else 'declared' if _declared_interval(u)[2] == 'declared' else 'dates present, none declared'})"
            for u in ups)
        present = (f"entries dated {rp.fmt_range(tx['present_start'], tx['present_end'])}" if tx["present_start"] else "no entries dated in the period")
        cov = f"{decl}. In the data: {present}."
        links = [(f"{u.upload_id}", f"{FRAZER_HOME}uploads/{u.id}") for u in ups]
        if gaps:
            rows.append(_row("frazer_tx", "Frazer transactions", "CL-2", "partial", cov, links,
                             "Not covered: " + ", ".join(rp.fmt_range(*g) for g in gaps),
                             "Upload an export for the rest of the month", detail="A declared range is what the exporter labelled, not proof of what the file holds.",
                             intervals=tx["intervals"]))
        else:
            rows.append(_row("frazer_tx", "Frazer transactions", "CL-2", "received", cov, links,
                             "Covers the period as declared (not verified)", "Review exceptions and the totals below", intervals=tx["intervals"]))
    # Snapshots
    for etype, key, label in ((fp.CUSTOMERS, "frazer_customers", "Frazer customer balances"), (fp.VEHICLES, "frazer_vehicles", "Frazer vehicle list")):
        snaps = _snapshots(db, etype)
        dated = [u for u in snaps if u.declared_export_date]
        in_month = [u for u in dated if period.month <= u.declared_export_date <= period.end]
        at_end = [u for u in in_month if u.declared_export_date == period.end]
        links = [(u.upload_id, f"{FRAZER_HOME}uploads/{u.id}") for u in (in_month or snaps[-1:])]
        undated = len(snaps) - len(dated)
        note = f" {undated} upload(s) have no export date and are not placed in any month." if undated else ""
        if at_end:
            rows.append(_row(key, label, "CL-3", "received", "Snapshot dated " + ", ".join(rp._short(u.declared_export_date) for u in at_end),
                             links, "Month-end snapshot received (still a snapshot, not transaction coverage)", "Review it" + note, detail=note))
        elif in_month:
            rows.append(_row(key, label, "CL-3", "partial",
                             "Snapshot dated " + ", ".join(rp._short(u.declared_export_date) for u in in_month),
                             links, f"Month-end snapshot ({rp._short(period.end)}) still needed; a snapshot date is not transaction coverage",
                             "Upload a snapshot on month end", detail=note))
        else:
            other = f"{len(snaps)} upload(s) from other months" if snaps else "none uploaded"
            rows.append(_row(key, label, "CL-3", "missing", other, [("Frazer Intake", FRAZER_HOME)] + links[:0],
                             f"A snapshot dated {rp._short(period.end)}", "Download from Frazer on month end and upload", detail=note))
    # Purchases / receipts
    pur = _purchases(db, period)
    if pur["count"]:
        rows.append(_row("purchases", "Expenses and receipts (Purchases)", "CL-5", "info",
                         f"{pur['count']} purchase(s) entered, {pur['with_receipt']} with a receipt, {pur['pending']} pending review",
                         [("Purchases", "/purchases/"), ("Review queue", "/review/")],
                         "Entry is not completeness: compare with statements", "Review pending purchases" if pur["pending"] else "Compare with bank and card statements",
                         expected=False))
    else:
        rows.append(_row("purchases", "Expenses and receipts (Purchases)", "CL-5", "info", "No purchases entered in the period",
                         [("Purchases", "/purchases/")], "Unknown whether none were made or none were entered", "Enter purchases or confirm none", expected=False))
    # Attested sources
    att = _attestations(db, period.month)
    for key in ("bank_statement", "square", "cash_count", "floor_plan_statement"):
        mine = [a for a in att if a.source_key == key]
        rec = [a for a in mine if a.status == "received"]
        props = [a for a in mine if a.status == "not_required"]
        confs = [a for a in mine if a.status == "not_required_confirmed" and any(p.user_id != a.user_id for p in props)]
        ints = [(a.coverage_start, a.coverage_end) for a in rec if a.coverage_start and a.coverage_end]
        if rec:
            gaps = rp.uncovered(period, ints)
            cov = "; ".join(f"{rp.fmt_range(a.coverage_start, a.coverage_end) if a.coverage_start and a.coverage_end else 'dates not given'}"
                            f" (kept: {a.where_kept or 'not stated'})" for a in rec)
            if not ints:
                rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "partial", cov, [], "Coverage dates were not recorded",
                                 "Record the dates it covers", manual=True))
            elif gaps:
                rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "partial", cov, [], "Not covered: " + ", ".join(rp.fmt_range(*g) for g in gaps),
                                 "Record the rest", manual=True, intervals=ints))
            else:
                rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "received", cov, [], "Recorded as received (the file is not stored here)",
                                 "Reconcile outside the app", manual=True, intervals=ints))
        elif confs:
            p, c = props[-1], confs[-1]
            rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "not_required",
                             f"Not required this month: {p.note} (proposed by {user_name(p.user)}, confirmed by {user_name(c.user)}; evidence kept: {p.where_kept})",
                             [], "Two administrators agreed", "None", manual=True))
        elif props:
            p = props[-1]
            rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "missing",
                             f"'Not required' proposed by {user_name(p.user)} ({p.note}). It counts as missing until a different administrator confirms it.",
                             [], f"A record covering {period.span}, or a second administrator's confirmation", "A different administrator confirms",
                             manual=True, confirm_id=p.id))
        else:
            rows.append(_row(key, SOURCES[key], SOURCE_REF[key], "missing", "Not captured in the app; nothing recorded", [],
                             f"A record covering {period.span}", "Record that you have it and where it is kept", manual=True))
    # Payroll: presence only
    pays = [f for f in rp.fridays(period.start, period.end) if f <= today]
    done = {a.period_label for a in att if a.source_key == "payroll_weekly" and a.status == "received"}
    got = [f for f in pays if f.isoformat() in done]
    if not pays:
        rows.append(_row("payroll_weekly", SOURCES["payroll_weekly"], "CL-4", "not_due", "No pay date has passed yet in this period", [],
                         "Restricted: only the existence of the record is noted here", "None yet", manual=True))
    else:
        st = "received" if len(got) == len(pays) else ("partial" if got else "missing")
        rows.append(_row("payroll_weekly", SOURCES["payroll_weekly"], "CL-4", st,
                         f"{len(got)} of {len(pays)} pay date(s) recorded: " + ", ".join(rp._short(f) for f in pays),
                         [], "Restricted: no amounts or names are held or shown here",
                         "Record that each week's payroll record is kept" if st != "received" else "None", manual=True))
    # Opening position (Nov 2026)
    try:
        from app.services import opening_snapshot_service as oss
        snap = oss.get_snapshot(db)
    except Exception:  # the Opening Snapshot feature is not installed
        snap = None
    if snap is not None and period.month <= snap.as_of_date <= period.end:
        n = len(snap.figures)
        rows.append(_row("opening", "Reported opening position", "§3.4 / Slice A", "info",
                         f"As reported for {snap.as_of_date.strftime('%b %-d, %Y')} ({snap.label}); cutoff: {snap.cutoff_note}. "
                         f"{n} figure(s), kept as reported.", [("Opening Snapshot", OPENING_HOME)],
                         "Reported, not verified. The Nov 8 Frazer exports are not opening balances", "Clarify cutoffs and add evidence there", expected=False))
    return rows


# --- 3. figures -------------------------------------------------------------------------------------------------------

def figures(db: Session, period: Period, tx: dict, records: list) -> dict:
    """Only what existing data supports. Everything else says what is missing."""
    f = {"transactions": None, "tx_missing": None, "snapshots": [], "spending": None, "unavailable": []}
    status = {r["key"]: r["status"] for r in records}
    if not tx["relevant"]:
        f["tx_missing"] = ("No Frazer transaction export covers this period, so no collection figure can be shown. "
                           "Upload an export (Frazer Intake).")
    else:
        g = tx["groups"]
        order = [("payment_entry", "Money-received entries", "Provisional classification (by Frazer's description, DEC-35)"),
                 ("fee_assessment", "Fee assessments (late fees)", "Provisional: an amount owed, not money received (DEC-36)"),
                 ("cancelled_label", "Entries labelled CANCELLED", "Source total, meaning unconfirmed"),
                 ("unclassified", "Unclassified descriptions", "Source total, not grouped")]
        f["transactions"] = {
            "rows": [{"label": l, "basis": b, "count": g.get(k, {}).get("count", 0), "sum": _money(g.get(k, {}).get("sum", 0)),
                      "present": k in g} for k, l, b in order],
            "partial": status.get("frazer_tx") != "received",
            "pairs": [{"key": k, "count": v["count"], "sum": _money(v["sum"])} for k, v in sorted(tx["pairs"].items())],
            "rows_seen": tx["rows_seen"], "distinct": tx["distinct"], "in_period": tx["in_period"], "outside": tx["outside"],
            "revised": tx["revised"], "errored": tx["errored"], "undated": tx["undated"], "uploads": len(tx["relevant"]),
            "present": rp.fmt_range(tx["present_start"], tx["present_end"]) if tx["present_start"] else None,
        }
    for etype, label in ((fp.CUSTOMERS, "Customer balances"),):
        for u in _snapshots(db, etype):
            s = u.summary_json or {}
            f["snapshots"].append({"label": label, "upload": u, "date": u.declared_export_date,
                                   "records": u.row_count, "total": _money(s.get("balance_sum_as_reported")) if s.get("balance_sum_as_reported") is not None else None,
                                   "in_month": bool(u.declared_export_date and period.month <= u.declared_export_date <= period.end),
                                   "month_end": u.declared_export_date == period.end})
    pur = _purchases(db, period)
    f["spending"] = pur
    f["unavailable"] = [
        ("Collections by tender and drawer cash", "Method code CA is on both Cash and Down Payment entries (DEC-35 open) and no cash-count or custody records exist (Slice B)."),
        ("Bank deposits and transfers", "No bank statement or deposit records are held in the app (Slice B / REC-02)."),
        ("Square sales, payouts and card fees", "No Square records are held in the app."),
        ("Revenue and profit", "Collections are not revenue; the CPA's revenue basis is undecided (DEC-06, -11, -16, -18)."),
        ("Payroll and labor totals", "Restricted (DEC-09); payroll detail is kept outside the app."),
    ]
    return f


# --- 4. reconciliation requirements, with their real status ----------------------------------------------------------

def reconciliation(records: list, issues: list) -> list:
    st = {r["key"]: r["status"] for r in records}
    open_by = defaultdict(int)
    for i in issues:
        if i.status != "resolved" and i.checklist_ref:
            open_by[i.checklist_ref] += 1

    def inputs(*keys):
        got = [k for k in keys if st.get(k) in ("received", "not_required")]
        part = [k for k in keys if st.get(k) == "partial"]
        if len(got) == len(keys):
            return "Inputs received, not reconciled"
        if got or part:
            return "Inputs partial, not reconciled"
        return "Inputs missing, not reconciled"
    L = {"frazer": ("Frazer Intake", FRAZER_HOME), "opening": ("Opening Snapshot", OPENING_HOME)}
    items = [
        ("CL-1", "Reconcile bank and physical cash", "§6.2, REQ-LOC-03, REQ-CNT-*", [],
         inputs("bank_statement", "cash_count"), "Counts, bank observations and verification (Slice B, DEC-29) are not built",
         "Bank statements and count sheets can be recorded as received; nothing is compared"),
        ("CL-2", "Reconcile Frazer and Square customer activity, refunds, deposits, fees", "§6.2, REQ-REC-01/02", [L["frazer"]],
         inputs("frazer_tx", "square"), "Matching to Square, deposits and custody (REC-02, Slice B) is not built",
         "Frazer upload and preview exist; the comparison does not"),
        ("CL-3", "Verify inventory, portfolio treatment, debt", "§6.2, REQ-AD-01", [L["frazer"], L["opening"]],
         inputs("frazer_customers", "frazer_vehicles"), "Floor-plan statement and valuation basis have no workflow; vehicle reconciliation is later",
         "Snapshots and duplicate-VIN flags exist"),
        ("CL-4", "Confirm payroll and direct/management/shared allocation", "§6.2, GATE-P, DEC-09", [],
         "Handled outside the app", "Payroll capture is not built; restricted to named viewers (DEC-09)",
         "Only the existence of each weekly record is noted"),
        ("CL-5", "Simple management P&L inputs", "§6.2", [("Purchases", "/purchases/")],
         "Not started", "The CPA's revenue basis is undecided (DEC-06, -11, -16, -18)", "Purchases can be reviewed"),
        ("CL-6", "Upcoming cash needs, differences, sign-off", "§6.2, REQ-DR-07", [],
         "Unavailable", "In-app close, sign-off and reopen are later (L); November closes by hand", "Review notes only"),
        ("REC-01", "Daily source check (Frazer cash listing vs Square cash)", "§7.10", [], "Not built", "Slice B", ""),
        ("REC-02", "Imports and matching by reference", "§7.10, §19", [L["frazer"]], "Intake only",
         "Matching and operational import are not built (REQ-REC-02)", "Source evidence only"),
        ("REC-03", "Completeness signals", "§7.10", [], "Partly shown as issues",
         "Missing source and partial coverage are flagged here; receipt-without-custody needs Slice B", ""),
    ]
    return [{"id": i, "name": n, "spec": s, "links": lk, "status": stt, "dependency": d, "available": a,
             "open_issues": open_by.get(i, 0)} for i, n, s, lk, stt, d, a in items]


# --- 5. issues ----------------------------------------------------------------------------------------------------------

def _next_issue_id(issue: ReviewIssue) -> str:
    return f"RI-{issue.id:04d}"


def _log(db: Session, issue: ReviewIssue, etype: str, user: Optional[User], *, frm=None, to=None, body="", now=None):
    ev = ReviewIssueEvent(issue_pk=issue.id, event_type=etype, from_value=None if frm is None else str(frm)[:200],
                          to_value=None if to is None else str(to)[:200], body=body or "", user_id=user.id if user else None,
                          created_at=now or datetime.utcnow())
    db.add(ev)
    issue.updated_at = ev.created_at
    return ev


def _links_ok(db: Session, month: date, urls: list) -> list:
    allowed = allowed_links(db, month)
    out = []
    for u in dict.fromkeys(urls):
        if u not in allowed:
            raise ReviewError("That source link is not one this page can point to.")
        out.append({"label": allowed[u], "url": u})
    return out


def allowed_links(db: Session, month: date) -> dict:
    d = {FRAZER_HOME: "Frazer Intake", OPENING_HOME: "Opening Snapshot", "/purchases/": "Purchases", "/review/": "Purchase review queue",
         f"/finance/review/?month={month.strftime('%Y-%m')}": "This month's review"}
    for u in db.query(FrazerUpload).order_by(FrazerUpload.id):
        d[f"{FRAZER_HOME}uploads/{u.id}"] = f"{u.upload_id} {u.original_filename}"
    return d


def _create_issue(db, *, month, kind, category, title, description, user, checklist_ref=None, links=None, auto_key=None,
                  due=None, due_proposed=False, owner=None, evidence=None, now=None) -> ReviewIssue:
    now = now or datetime.utcnow()
    issue = ReviewIssue(month=month, kind=kind, auto_key=auto_key, category=category, checklist_ref=checklist_ref,
                        restricted=(category == "payroll"), title=title[:200], description=description, links_json=links or [],
                        created_by_user_id=user.id if user else None, created_at=now, status="open",
                        owner_user_id=owner.id if owner else None, due_date=due, due_is_proposed=due_proposed,
                        evidence_json=evidence, evidence_updated_at=now if evidence else None, auto_active=True, updated_at=now)
    db.add(issue)
    db.flush()
    issue.issue_id = _next_issue_id(issue)
    _log(db, issue, "created", user, to="open", body=("Detected automatically." if kind == "auto" else ""), now=now)
    if owner:
        _log(db, issue, "assigned", user, to=user_name(owner), now=now)
    return issue


def create_manual_issue(db: Session, user: User, month: date, *, title: str, description: str = "", category: str = "other",
                        checklist_ref: str = "", owner_id: str = "", due_date: str = "", link: str = "",
                        message_to_owner: str = "") -> ReviewIssue:
    title = (title or "").strip()
    if not title:
        raise ReviewError("Give the issue a title.")
    if len(title) > 200:
        raise ReviewError("The title is limited to 200 characters.")
    if category not in CATEGORIES:
        raise ReviewError("Choose a category from the list.")
    if checklist_ref and checklist_ref not in CHECKLIST:
        raise ReviewError("Choose a checklist item from the list.")
    check_restricted_text(title + "\n" + (description or ""), restricted=(category == "payroll"))
    dup = (db.query(ReviewIssue).filter(ReviewIssue.month == month, ReviewIssue.status != "resolved",
                                        func.lower(ReviewIssue.title) == title.lower()).first())
    if dup:
        raise DuplicateIssue(dup)
    owner = _owner(db, owner_id)
    _owner_may_hold(category == "payroll", owner)
    message_to_owner = (message_to_owner or "").strip()
    check_restricted_text(message_to_owner, restricted=(category == "payroll"))
    due = _parse_due(due_date)
    links = _links_ok(db, month, [link] if link else [])
    issue = _create_issue(db, month=month, kind="manual", category=category, title=title, description=(description or "").strip(),
                          user=user, checklist_ref=checklist_ref or None, links=links, due=due, owner=owner)
    if message_to_owner:
        _log(db, issue, "to_owner", user, body=message_to_owner)
    return issue


def _owner(db: Session, owner_id) -> Optional[User]:
    if owner_id in (None, "", "none"):
        return None
    try:
        u = db.query(User).filter(User.id == int(owner_id), User.active.is_(True)).first()
    except (TypeError, ValueError):
        u = None
    if u is None:
        raise ReviewError("Choose an owner from the list of active users.")
    return u


def _owner_may_hold(restricted: bool, owner: Optional[User]) -> None:
    """Assignment grants no access, and restricted (payroll) items never go to a non-administrator (DEC-09)."""
    if restricted and owner is not None and not owner.is_admin:
        raise ReviewError("Payroll items are restricted and stay with administrators (DEC-09). Assign it to Steve or Drew.")


def _parse_due(raw) -> Optional[date]:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ReviewError("The due date must look like 2026-12-07.")


_UNSET = object()


def update_issue(db: Session, issue: ReviewIssue, user: User, *, owner_id=_UNSET, status=None, due_date=_UNSET, note: str = "",
                 message_to_owner: str = "") -> int:
    """Apply any combination of changes. Every change and note is written as an event. Returns how many events were added."""
    note = (note or "").strip()
    message_to_owner = (message_to_owner or "").strip()
    check_restricted_text(note, restricted=issue.restricted)
    check_restricted_text(message_to_owner, restricted=issue.restricted)
    changes = 0
    now = datetime.utcnow()
    if status is not None and status != issue.status:
        if status not in STATUSES:
            raise ReviewError("Choose a status from the list.")
        if "resolved" in (status, issue.status) and not note:
            raise ReviewError("Resolving or reopening needs a note saying what was decided or done. "
                              "It does not change any source record or verify anything.")
        _log(db, issue, "status", user, frm=STATUS_LABEL[issue.status], to=STATUS_LABEL[status], body=note, now=now)
        issue.status = status
        note_used = True
        changes += 1
    else:
        note_used = False
    if owner_id is not _UNSET:
        new = _owner(db, owner_id)
        _owner_may_hold(issue.restricted, new)
        if (new.id if new else None) != issue.owner_user_id:
            _log(db, issue, "assigned", user, frm=user_name(issue.owner) if issue.owner_user_id else "nobody",
                 to=user_name(new) if new else "nobody", now=now)
            issue.owner_user_id = new.id if new else None
            issue.owner = new
            changes += 1
    if due_date is not _UNSET:
        new_due = _parse_due(due_date)
        if new_due != issue.due_date:
            _log(db, issue, "due_date", user, frm=issue.due_date.isoformat() if issue.due_date else "none",
                 to=new_due.isoformat() if new_due else "none", now=now)
            issue.due_date = new_due
            issue.due_is_proposed = False
            changes += 1
    if note and not note_used:
        _log(db, issue, "note", user, body=note, now=now)
        changes += 1
    if message_to_owner:
        _log(db, issue, "to_owner", user, body=message_to_owner, now=now)
        changes += 1
    if not changes:
        raise ReviewError("Nothing to change: pick a new value or write a note.")
    return changes


# --- automatic issues ------------------------------------------------------------------------------------------------

def _candidates(db: Session, period: Period, today: date, tx: dict, records: list) -> list:
    st = {r["key"]: r for r in records}
    c = []
    bd5, bd7 = rp.business_day_after(period.end, 5), rp.business_day_after(period.end, 7)
    mp = f"/finance/review/?month={period.key}"

    def add(key, cat, title, desc, ref, due, evidence, links=()):
        c.append(dict(key=key, category=cat, title=title, description=desc, checklist_ref=ref, due=due, evidence=evidence,
                      links=[{"label": l, "url": u} for l, u in links]))

    r = st["frazer_tx"]
    if r["status"] != "received":
        add("coverage:frazer_transactions", "coverage", f"Frazer transaction coverage is incomplete for {period.label}",
            "The Frazer transaction exports uploaded so far do not cover the whole reporting period. A snapshot date does not "
            "establish transaction coverage, and a declared range is not proof of content.", "CL-2", bd5,
            {"status": r["status"], "coverage": r["coverage"], "outstanding": r["outstanding"]},
            [("Frazer Intake", FRAZER_HOME)] + [(l, u) for l, u in r["links"]])
    for key, title in (("frazer_customers", "customer-balance"), ("frazer_vehicles", "vehicle")):
        r = st[key]
        if r["status"] != "received":
            add(f"snapshot:{key}", "records", f"No month-end Frazer {title} snapshot for {period.label}",
                f"A snapshot dated {period.end.strftime('%b %-d')} is needed for the month-end position. An earlier snapshot is "
                "kept as its own date, not as month end.", "CL-3", bd7,
                {"status": r["status"], "coverage": r["coverage"], "outstanding": r["outstanding"]}, [("Frazer Intake", FRAZER_HOME)] + [(l, u) for l, u in r["links"]])
    for key, ref in (("bank_statement", "CL-1"), ("square", "CL-2"), ("cash_count", "CL-1"), ("floor_plan_statement", "CL-3")):
        r = st[key]
        if r["status"] in ("missing", "partial"):
            add(f"source:{key}", "records", f"{SOURCES[key].split(' (')[0]}: {'no record' if r['status'] == 'missing' else 'incomplete coverage'} for {period.label}",
                "This record is not held in the app. Record that you have it, the dates it covers, and where it is kept.", ref, bd5,
                {"status": r["status"], "coverage": r["coverage"], "outstanding": r["outstanding"]})
    for f in rp.fridays(period.start, period.end):
        if f <= today and not any(a.source_key == "payroll_weekly" and a.status == "received" and a.period_label == f.isoformat()
                                  for a in _attestations(db, period.month)):
            add(f"payroll:{f.isoformat()}", "payroll", f"Weekly payroll record for {f.strftime('%b %-d')} is not recorded",
                "Record that this week's payroll record exists and where it is kept. Restricted: do not enter amounts or names.",
                "CL-4", rp.business_day_after(f, 3), {"pay_date": f.isoformat(), "status": "missing"})
    # unresolved meanings, only from entries inside the period
    if tx["in_period"]:
        desc_by_method = defaultdict(set)
        for k, v in tx["pairs"].items():
            d, m = [x.strip() for x in k.rsplit("|", 1)]
            if m:
                desc_by_method[m].add(d)
        for m, ds in sorted(desc_by_method.items()):
            if len(ds) > 1:
                add(f"mapping:method_code:{m}", "mapping", f"Meaning of Frazer method code {m} is not confirmed",
                    f"Method code {m} appears on several descriptions this month ({', '.join(sorted(ds))}). Nothing is inferred from it: "
                    "it blocks tender and drawer-cash figures, not intake or preview (DEC-35).", "CL-2", None,
                    {"code": m, "descriptions": sorted(ds)}, [("Frazer Intake", FRAZER_HOME)])
        fee = tx["groups"].get("fee_assessment")
        if fee:
            add("mapping:class:fee_assessment", "mapping", "Late fee treatment is not confirmed",
                f"{fee['count']} Late Fee entr{'y' if fee['count'] == 1 else 'ies'} dated in this period are grouped as fee assessments (not money received) "
                "on Frazer's own description. Confirm with Steve/CPA before any fee or collection figure is relied on (DEC-36).",
                "CL-2", None, {"entries": fee["count"], "total": str(fee["sum"])}, [("Frazer Intake", FRAZER_HOME)])
    if tx["revised"]:
        add("conflict:frazer_transactions", "mapping", "Some Frazer entries differ between uploads",
            "The same Sequential ID appears with different content in different uploads. Both versions are kept; these entries are "
            "left out of the totals until someone decides which is right (DEC-42).", "CL-2", None,
            {"sequential_ids": tx["revised"][:50]}, [("Frazer Intake", FRAZER_HOME)])
    return c


def sync_auto_issues(db: Session, period: Period, today: date, tx: Optional[dict] = None, records: Optional[list] = None) -> dict:
    """Idempotent: one issue per (month, key). Returns counts. Caller commits."""
    out = {"created": 0, "evidence": 0, "cleared": 0, "returned": 0}
    if period.start > today:
        return out
    tx = tx or frazer_transactions(db, period)
    records = records or records_table(db, period, today, tx)
    cands = {c["key"]: c for c in _candidates(db, period, today, tx, records)}
    existing = {i.auto_key: i for i in db.query(ReviewIssue).filter(ReviewIssue.month == period.month, ReviewIssue.kind == "auto")}
    now = datetime.utcnow()
    for key, c in cands.items():
        issue = existing.get(key)
        if issue is None:
            seen, links = set(), []
            for lk in c["links"]:
                if lk["url"] not in seen:
                    seen.add(lk["url"])
                    links.append(lk)
            _create_issue(db, month=period.month, kind="auto", category=c["category"], title=c["title"], description=c["description"],
                          user=None, checklist_ref=c["checklist_ref"], links=links, auto_key=key, due=c["due"],
                          due_proposed=c["due"] is not None, evidence=c["evidence"], now=now)
            out["created"] += 1
            continue
        if issue.evidence_json != c["evidence"]:
            issue.evidence_json, issue.evidence_updated_at = c["evidence"], now
            _log(db, issue, "evidence", None, body="Automatic evidence updated.", now=now)
            out["evidence"] += 1
        if not issue.auto_active:
            issue.auto_active = True
            if issue.status == "resolved":
                _log(db, issue, "returned", None, frm="Resolved", to="Open", body="The detected condition is back; the issue was reopened.", now=now)
                issue.status = "open"
            else:
                _log(db, issue, "returned", None, body="The detected condition is back.", now=now)
            out["returned"] += 1
    for key, issue in existing.items():
        if key not in cands and issue.auto_active:
            issue.auto_active = False
            _log(db, issue, "cleared", None, body="The condition is no longer detected. The issue stays as it is until a person resolves it.", now=now)
            out["cleared"] += 1
    db.flush()
    return out


# --- notes and attestations --------------------------------------------------------------------------------------------

def add_note(db: Session, user: User, month: date, *, note_type: str, body: str, review_date: str = "",
             corrects_note_id: str = "", today: Optional[date] = None) -> ReviewNote:
    body = (body or "").strip()
    if note_type not in ("review", "decision"):
        raise ReviewError("Choose review note or decision.")
    if not body:
        raise ReviewError("Write the note.")
    check_restricted_text(body)
    rd = _parse_due(review_date) or today or date.today()
    corr = None
    if corrects_note_id:
        corr = db.query(ReviewNote).filter(ReviewNote.id == int(corrects_note_id), ReviewNote.month == month).first() \
            if str(corrects_note_id).isdigit() else None
        if corr is None:
            raise ReviewError("That note is not in this month.")
    n = ReviewNote(month=month, note_type=note_type, review_date=rd, body=body, corrects_note_id=corr.id if corr else None,
                   user_id=user.id, created_at=datetime.utcnow())
    db.add(n)
    db.flush()
    return n


def add_attestation(db: Session, user: User, month: date, *, source_key: str, status: str = "received", coverage_start: str = "",
                    coverage_end: str = "", where_kept: str = "", note: str = "", pay_date: str = "") -> ReviewSourceRecord:
    if source_key not in SOURCES:
        raise ReviewError("Choose a record type from the list.")
    if status not in ("received", "not_required"):
        raise ReviewError("Choose received or not required.")
    where_kept, note = (where_kept or "").strip(), (note or "").strip()
    check_restricted_text(where_kept + "\n" + note, restricted=(source_key == "payroll_weekly"))
    s, e = _parse_due(coverage_start), _parse_due(coverage_end)
    label = ""
    if status == "not_required":
        if source_key not in cp.RECORD_NOT_REQUIRED_OK:
            raise ReviewError(f"{SOURCES[source_key]} is a mandatory record every month and cannot be marked not required. "
                              "Record it, or add an issue explaining what is missing.")
        if len(note) < 15:
            raise ReviewError(f"Say why it is not required. The only accepted condition is: {cp.RECORD_NOT_REQUIRED_OK[source_key]}")
        if not where_kept:
            raise ReviewError("Say where the evidence is kept (outside this app).")
        s = e = None
    elif source_key == "payroll_weekly":
        pd = _parse_due(pay_date)
        if pd is None:
            raise ReviewError("Choose the pay date this record is for.")
        label = pd.isoformat()
        s = e = None
    else:
        if not (s and e):
            raise ReviewError("Give the first and last date the record covers.")
        if s > e:
            raise ReviewError("The start date is after the end date.")
    if status == "received" and not where_kept:
        raise ReviewError("Say where the record is kept (outside this app).")
    r = ReviewSourceRecord(month=month, source_key=source_key, status=status, period_label=label, coverage_start=s,
                           coverage_end=e, where_kept=where_kept[:200], note=note, user_id=user.id, created_at=datetime.utcnow())
    db.add(r)
    db.flush()
    return r


def confirm_not_required(db: Session, user: User, month: date, record_id: int) -> ReviewSourceRecord:
    """A different administrator confirms a 'not required' proposal. A proposal alone never counts (REQ-MR-26)."""
    prop = db.query(ReviewSourceRecord).filter(ReviewSourceRecord.id == record_id, ReviewSourceRecord.month == month,
                                               ReviewSourceRecord.status == "not_required").first()
    if prop is None:
        raise ReviewError("That is not a pending 'not required' proposal for this month.")
    if prop.user_id == user.id:
        raise ReviewError("A different administrator must confirm it: the person who proposed it cannot.")
    if db.query(ReviewSourceRecord).filter(ReviewSourceRecord.month == month, ReviewSourceRecord.source_key == prop.source_key,
                                           ReviewSourceRecord.status == "not_required_confirmed").first():
        raise ReviewError("It was already confirmed.")
    r = ReviewSourceRecord(month=month, source_key=prop.source_key, status="not_required_confirmed", period_label="", coverage_start=None,
                           coverage_end=None, where_kept=prop.where_kept, note="Confirmed by a second administrator", user_id=user.id,
                           created_at=datetime.utcnow())
    db.add(r)
    db.flush()
    return r


# --- the whole page -----------------------------------------------------------------------------------------------------

def _next_action(issues: list, notes: list, today: date, period_key: str) -> dict:
    live = [i for i in issues if i.status != "resolved"]
    overdue = sorted([i for i in live if i.due_date and i.due_date < today], key=lambda i: i.due_date)
    if overdue:
        i = overdue[0]
        return {"text": f"Overdue since {i.due_date.strftime('%b %-d')}: {i.title}", "anchor": f"#issue-{i.issue_id}",
                "owner": user_name(i.owner) if i.owner_user_id else "No owner yet"}
    unowned = [i for i in live if not i.owner_user_id]
    if unowned:
        i = sorted(unowned, key=lambda i: (i.due_date or date.max, i.id))[0]
        return {"text": f"Assign an owner: {i.title}", "anchor": f"#issue-{i.issue_id}", "owner": "Nobody yet"}
    dated = sorted([i for i in live if i.due_date], key=lambda i: i.due_date)
    if dated:
        i = dated[0]
        return {"text": f"Next due {i.due_date.strftime('%b %-d')}: {i.title}", "anchor": f"#issue-{i.issue_id}", "owner": user_name(i.owner)}
    if live:
        i = live[0]
        return {"text": f"Work on: {i.title}", "anchor": f"#issue-{i.issue_id}", "owner": user_name(i.owner)}
    if not notes:
        return {"text": "No issues are open. Save an owner review note for this month.", "anchor": "#owner-review", "owner": ""}
    return {"text": "Nothing outstanding here. The month is still not reconciled or closed.", "anchor": "#reconciliation", "owner": ""}


def build_review(db: Session, month: date, today: date, *, sync: bool = True) -> dict:
    period = rp.period_for(month)
    tx = frazer_transactions(db, period)
    records = records_table(db, period, today, tx)
    sync_counts = sync_auto_issues(db, period, today, tx, records) if sync else {}
    issues = (db.query(ReviewIssue).filter(ReviewIssue.month == period.month).all())
    issues.sort(key=lambda i: (i.status == "resolved", i.due_date or date.max, i.id))
    notes = db.query(ReviewNote).filter(ReviewNote.month == period.month).order_by(ReviewNote.id.desc()).all()
    atts = _attestations(db, period.month)
    expected = [r for r in records if r["expected"] and r["status"] != "not_due"]
    complete = [r for r in expected if r["status"] in ("received", "not_required")]
    live = [i for i in issues if i.status != "resolved"]
    overdue = [i for i in live if i.due_date and i.due_date < today]
    touched = bool(notes or atts or any(r["status"] in ("received", "partial") for r in records if r["key"].startswith("frazer")))
    headline = "In review" if notes else ("Collecting records" if touched else "Not started")
    stamps = [u.uploaded_at for u in db.query(FrazerUpload)] + [i.updated_at for i in issues] + [n.created_at for n in notes] + [a.created_at for a in atts]
    owners = db.query(User).filter(User.active.is_(True)).order_by(User.display_name).all()
    recon = reconciliation(records, issues)
    marks = db.query(ReviewStepMark).filter(ReviewStepMark.month == period.month).order_by(ReviewStepMark.id).all()
    wd0 = bc.wd0_for_month(period.month)
    close = cp.evaluate(dict(
        today=today, wd0=wd0, period=period, records={r["key"]: r for r in records}, purchases=_purchases(db, period),
        pay_fridays=rp.fridays(period.start, period.end),
        payroll_done={a.period_label for a in atts if a.source_key == "payroll_weekly" and a.status == "received"},
        marks=cp.marks_state(marks), issues=issues))
    sched = cp.schedule(close, period.month, today)
    ind = bc.indicator(period.month, today)
    ind["next_activity"] = cp.next_scheduled(sched)
    overdue_acts = [a for a in sched if a["overdue"]]
    if overdue:
        nxt = _next_action(issues, notes, today, period.key)
    elif overdue_acts:
        a = overdue_acts[0]
        nxt = {"text": f"Overdue since {a['date'].strftime('%b %-d')} ({a['wd_label']}): {a['title']}", "anchor": a["href"], "owner": a["role_text"]}
    else:
        n = cp.first_next_action(close)
        nxt = {"text": n["text"], "anchor": n["href"], "owner": n["owner"], "why": n["why"]}
    files = defaultdict(list)
    for f in db.query(ReviewIssueFile).filter(ReviewIssueFile.issue_pk.in_([i.id for i in issues] or [0])).order_by(ReviewIssueFile.id):
        files[f.issue_pk].append(f)
    answered = [i for i in live if i.events and i.events[-1].event_type in ("owner_reply", "file") and i.events[-1].user_id == i.owner_user_id]
    # the short list at the top: what is late, blocked or waiting on us
    alerts = []
    for i in sorted(overdue, key=lambda i: i.due_date)[:4]:
        alerts.append({"kind": "Overdue", "text": f"{i.issue_id} {i.title}", "meta": f"due {i.due_date.strftime('%b %-d')} · {user_name(i.owner) if i.owner_user_id else 'no owner'}", "href": f"#issue-{i.issue_id}"})
    for a in overdue_acts[:3]:
        alerts.append({"kind": "Overdue", "text": a["title"], "meta": f"{a['wd_label']} {a['date'].strftime('%b %-d')} · {a['role_text']}", "href": a["href"]})
    for i in answered[:3]:
        alerts.append({"kind": "Reply", "text": f"{i.issue_id} {user_name(i.owner)} replied: {i.title}", "meta": "review the reply", "href": f"#issue-{i.issue_id}"})
    for s_ in close["steps"]:
        if s_["status"] == "blocked":
            alerts.append({"kind": "Blocked", "text": f"{s_['key']} {s_['name']}", "meta": (s_["next"] or {}).get("owner", ""), "href": f"#step-{s_['key']}"})
    miss = sum(1 for r in expected if r["status"] == "missing")
    part = sum(1 for r in expected if r["status"] == "partial")
    fig_tx = figures(db, period, tx, records)
    summaries = {
        "records": f"{len(complete)} of {len(expected)} in place · {miss} missing · {part} partial",
        "attention": f"{len(live)} open · {len(overdue)} overdue · {sum(1 for i in live if not i.owner_user_id)} without an owner"
                     + (f" · {len(answered)} reply to review" if answered else ""),
        "figures": ((f"Frazer: {tx['in_period']} entries" + (" (partial coverage)" if records[0]['status'] != 'received' else "")) if tx["relevant"]
                    else "No Frazer export covers this period") + f" · {fig_tx['spending']['count']} purchase(s) entered",
        "recon": f"Nothing reconciled · {close['ready']['met']} of {close['ready']['total']} readiness milestones · "
                 f"{len(close['ready']['blocked_capabilities'])} unbuilt features block the rest",
        "review": (f"Last review {notes[0].review_date.strftime('%b %-d')} by {user_name(notes[0].user)} · {len(notes)} note(s)" if notes else "No notes yet"),
    }
    return {
        "close": close, "schedule": sched, "indicator": ind, "marks": marks, "overdue_activities": overdue_acts,
        "period": period, "today": today, "records": records, "tx": tx, "figures": fig_tx,
        "issues": issues, "live_issues": live, "overdue": overdue, "notes": notes, "recon": recon, "owners": owners,
        "attestations": atts, "sync": sync_counts, "links": allowed_links(db, period.month), "files": dict(files), "answered": answered,
        "alerts": alerts, "summaries": summaries,
        "status": {"headline": headline, "records_complete": len(complete), "records_expected": len(expected),
                   "open_issues": len(live), "overdue": len(overdue),
                   "last_review": notes[0] if notes else None,
                   "last_update": max(stamps) if stamps else None,
                   "reconciliation": "Not reconciled", "close": "Not available",
                   "close_reason": "In-app close and reopen are later work (spec §6, REQ-DR-07). November is closed by hand outside the app."},
        "next_action": nxt,
        "payroll_dates": [f for f in rp.fridays(period.start, period.end) if f <= today],
    }


# --- close-step marks ----------------------------------------------------------------------------------------------------

def set_step_mark(db: Session, user: User, month: date, today: date, *, step_key: str, kind: str, action: str, note: str = "",
                  evidence: str = "") -> ReviewStepMark:
    """Record a person's review, approval, or a not-applicable decision for a close step. Never completes a step by itself:
    completion is evaluated from all milestones (records, capabilities, decisions and these marks)."""
    if step_key not in cp.STEP_BY_KEY:
        raise ReviewError("Choose a checklist step.")
    if kind not in cp.MARK_KINDS or action not in ("set", "clear"):
        raise ReviewError("Unknown step action.")
    note, evidence = (note or "").strip(), (evidence or "").strip()
    check_restricted_text(note + "\n" + evidence, restricted=(step_key == "CL-4"))
    data = build_review(db, month, today, sync=False)
    step = next(s for s in data["close"]["steps"] if s["key"] == step_key)
    marks = cp.marks_state(db.query(ReviewStepMark).filter(ReviewStepMark.month == month).order_by(ReviewStepMark.id).all())
    extra = None
    if kind in ("not_applicable", "na_concur"):
        if step_key not in cp.NA_POLICY:
            raise ReviewError("This step cannot be excluded: " + cp.NA_NEVER.get(step_key, "it is a mandatory requirement") +
                              " A reason alone does not bypass a mandatory requirement.")
        pol = cp.NA_POLICY[step_key]
        prop, conc = marks.get((step_key, "not_applicable")), marks.get((step_key, "na_concur"))
        if kind == "not_applicable" and action == "set":
            if len(note) < 15:
                raise ReviewError(f"Say why it does not apply. The only accepted condition is: {pol['condition']}")
            if len(evidence) < 5:
                raise ReviewError(pol["evidence"])
            if step_key == "CL-4" and any(a.source_key == "payroll_weekly" and a.status == "received" for a in _attestations(db, month)):
                raise ReviewError(pol["contradiction"])
            note = f"{note} | Evidence kept: {evidence}"
            if conc is not None:                                       # a new proposal needs a fresh concurrence
                extra = ReviewStepMark(month=month, step_key=step_key, kind="na_concur", action="clear",
                                       note="Reset by a new proposal", user_id=user.id, created_at=datetime.utcnow())
        elif kind == "na_concur":
            if action == "set":
                if prop is None:
                    raise ReviewError("Nothing has been proposed for this step.")
                if prop.user_id == user.id:
                    raise ReviewError("A different administrator must concur: the person who proposed it cannot.")
        if action == "clear" and not note:
            raise ReviewError("Say why you are clearing it.")
    elif action == "set":
        prep = [c for c in step["criteria"] if c["track"] == "prep" and c["kind"] != "signoff"]
        if kind in ("confirm", "review") and any(not c["met"] for c in prep):
            raise ReviewError("The records for this step are not all in place yet, so it cannot be reviewed. "
                              "Uploading a file or recording a record is not the review itself.")
        if kind == "review" and not any(c["key"] == "confirm" and c["met"] for c in step["criteria"]):
            raise ReviewError("Record Steve's review before Drew's.")
        if kind == "approve":
            others = [c for c in step["criteria"] if c["key"] != "approve"]
            blocked = [c["label"] for c in others if not c["met"]]
            if blocked:
                raise ReviewError("Approval needs everything before it: " + "; ".join(blocked[:3]) +
                                  (" and more." if len(blocked) > 3 else ".") + " Matching and reconciliation are not built yet.")
    if action == "clear" and kind in ("confirm", "review", "approve") and not note:
        raise ReviewError("Say why you are clearing it.")
    if extra is not None:
        db.add(extra)
    m = ReviewStepMark(month=month, step_key=step_key, kind=kind, action=action, note=note, user_id=user.id, created_at=datetime.utcnow())
    db.add(m)
    db.flush()
    return m
