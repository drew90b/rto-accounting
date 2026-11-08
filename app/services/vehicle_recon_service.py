"""
Questions, answers, approvals and corrections for vehicle reconciliation (spec REQ-VR-08, -11..18).

Writes only to vr_* tables. It never creates or changes a unit, purchase, transaction, payment or any other operational record.

How a question lives
  open -> (answer) -> decided                         operational kinds, or an administrator's own answer
  open -> (employee answer) -> proposed -> (admin accepts) -> decided
                                         -> (admin rejects) -> open
  decided -> (new contradictory evidence) -> review_needed -> (correction) -> decided
  open -> (evidence gone) -> withdrawn -> (evidence returns) -> open
Every transition appends a vr_events row. Every answer carries the question version and evidence fingerprint it was based on;
a mismatch refuses the answer (compare-and-swap), so nobody silently overwrites someone else's decision.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.vehicle_recon import VrDecision, VrEvent, VrQuestion
from app.services import vr_engine as eng
from app.services import vr_sources as src

KIND_CLASS = {"shared_vin": "identity", "purchase_match": "financial", "status_conflict": "operational"}
APPROVAL_CLASSES = {"identity", "financial"}
EMPLOYEE_KINDS = {"shared_vin", "status_conflict"}       # kinds an employee may be assigned and may answer (as a proposal if identity)
ADMIN_ONLY_ASSIGNEE_KINDS = {"purchase_match"}
DEFER_KEYS = {"not_sure", "needs_someone_else"}


class RecError(ValueError):
    """A refused action with a plain-language reason."""


class StaleAnswer(RecError):
    """The question changed since the page was loaded (someone answered, or the evidence moved)."""


class NotAllowed(RecError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def event(db: Session, q: Optional[VrQuestion], kind: str, body: str = "", user: Optional[User] = None, data: Optional[dict] = None):
    e = VrEvent(question_id=q.id if q is not None else None, event_type=kind, body=body, data_json=data or {},
                user_id=user.id if user is not None else None)
    db.add(e)
    return e


# ---------------------------------------------------------------------------
# Refresh: observe the app, regenerate what the evidence supports, reconcile with existing questions
# ---------------------------------------------------------------------------

def refresh(db: Session, today: Optional[date] = None, observe_app: bool = True) -> dict:
    """Idempotent. Running it again with the same evidence creates nothing and changes nothing (REQ-VR-08)."""
    today = today or date.today()
    summary = {"raised": 0, "evidence_changed": 0, "review_needed": 0, "withdrawn": 0, "reopened": 0}
    if observe_app:
        src.observe_app_units(db, today)
        src.observe_app_purchases(db, today)
    w = eng.load_world(db)
    rows, stock_to_row, group_of = eng.build_rows(w)
    cands = eng.candidates(w, rows, stock_to_row, group_of)
    existing = dict(w.questions)
    seen = set()
    for c in cands:
        seen.add(c.key)
        q = existing.get(c.key)
        if q is None:
            q = VrQuestion(
                question_key=c.key, kind=c.kind, title=c.title, why=c.why, subject_json=c.subject, evidence_json=c.evidence,
                choices_json=c.choices, evidence_fingerprint=c.fingerprint, status="open")
            db.add(q)
            db.flush()
            q.ref = f"VQ-{q.id:04d}"
            event(db, q, "raised", c.why, None, {"fingerprint": c.fingerprint})
            summary["raised"] += 1
            continue
        if q.status == "withdrawn":
            _apply_candidate(q, c)
            q.status, q.withdrawn_reason = "open", None
            q.version += 1
            event(db, q, "reopened", "The evidence for this question is back.", None, {"fingerprint": c.fingerprint})
            summary["reopened"] += 1
        elif q.evidence_fingerprint != c.fingerprint:
            had_decision = q.status in ("decided", "proposed")
            _apply_candidate(q, c)
            q.version += 1
            if had_decision:
                q.status = "review_needed"
                event(db, q, "review_needed", "New evidence differs from what the answer was based on. The decision stays in "
                      "force until someone reviews it.", None, {"fingerprint": c.fingerprint})
                summary["review_needed"] += 1
            else:
                event(db, q, "evidence_changed", "The evidence changed; earlier answers to the old evidence are refused.", None,
                      {"fingerprint": c.fingerprint})
                summary["evidence_changed"] += 1
    for key, q in existing.items():
        if key in seen or q.status in ("withdrawn", "review_needed"):
            continue
        if q.status == "open":
            q.status, q.withdrawn_reason = "withdrawn", "The evidence behind this question is no longer present."
            q.version += 1
            event(db, q, "withdrawn", q.withdrawn_reason)
            summary["withdrawn"] += 1
        else:  # decided or proposed: never undo a decision silently
            q.status = "review_needed"
            q.version += 1
            event(db, q, "review_needed", "The condition that raised this question is no longer present in the sources; the "
                  "decision stays in force until someone reviews it.")
            summary["review_needed"] += 1
    db.flush()
    return summary


def _apply_candidate(q: VrQuestion, c: eng.Candidate) -> None:
    q.title, q.why, q.subject_json, q.evidence_json, q.choices_json = c.title, c.why, c.subject, c.evidence, c.choices
    q.evidence_fingerprint = c.fingerprint
    q.updated_at = _now()


# ---------------------------------------------------------------------------
# Who may do what (REQ-VR-16, -17)
# ---------------------------------------------------------------------------

def can_view(user: User, q: VrQuestion) -> bool:
    """Admins see every question. Anyone else sees only an employee-eligible question assigned to them."""
    if user.is_admin:
        return True
    return q.owner_user_id == user.id and q.kind in EMPLOYEE_KINDS and user.active


def get_question(db: Session, user: User, ref: str) -> Optional[VrQuestion]:
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    return q if q is not None and can_view(user, q) else None


def my_questions(db: Session, user: User) -> list:
    qs = db.query(VrQuestion).filter(VrQuestion.owner_user_id == user.id, VrQuestion.status != "withdrawn").all()
    qs = [q for q in qs if can_view(user, q)]
    order = {"review_needed": 0, "open": 1, "proposed": 2, "decided": 3}
    return sorted(qs, key=lambda q: (order.get(q.status, 9), q.id))


def assigned_items(db: Session, user: User) -> list:
    """INTEGRATION POINT (REQ-VR-18): neutral items the Monthly Review "My actions" view can list after both are merged."""
    return [{"ref": q.ref, "title": q.title, "status": q.status, "url": f"/vehicle-questions/mine/{q.ref}",
             "source": "vehicle_reconciliation"}
            for q in my_questions(db, user) if q.status in ("open", "review_needed")]


def assign(db: Session, admin: User, ref: str, user_id: Optional[int]) -> VrQuestion:
    if not admin.is_admin:
        raise NotAllowed("Only an administrator can assign a question.")
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    if q is None:
        raise RecError("No such question.")
    if q.status == "withdrawn":
        raise RecError("That question was withdrawn.")
    if user_id is None:
        q.owner_user_id = None
        event(db, q, "assigned", "Unassigned", admin)
        return q
    target = db.query(User).filter(User.id == user_id, User.active.is_(True)).first()
    if target is None:
        raise RecError("That person is not an active user.")
    if q.kind in ADMIN_ONLY_ASSIGNEE_KINDS and not target.is_admin:
        raise NotAllowed("Purchase questions show purchase amounts and can only be assigned to an administrator.")
    q.owner_user_id = target.id
    q.needs_someone_else = False
    event(db, q, "assigned", f"Assigned to {target.display_name}", admin, {"user_id": target.id})
    return q


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

def _cas(db: Session, q: VrQuestion, expected_version: int, expected_fp: str, bump: bool = True) -> None:
    """Compare-and-swap on (version, evidence fingerprint). Only one of two simultaneous answers can pass."""
    if bump:
        res = db.execute(update(VrQuestion).where(
            VrQuestion.id == q.id, VrQuestion.version == expected_version, VrQuestion.evidence_fingerprint == expected_fp,
        ).values(version=VrQuestion.version + 1, updated_at=_now()))
        ok = res.rowcount == 1
    else:
        ok = q.version == expected_version and q.evidence_fingerprint == expected_fp
    if not ok:
        db.expire(q)
        raise StaleAnswer("This question changed after you opened it (someone answered it, or the evidence moved). "
                          "Nothing was saved. Reload to see the current state.")
    db.expire(q)


def _parse_as_of(params: dict, today: date) -> date:
    raw = (params.get("as_of") or "").strip()
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise RecError("Enter the date you are confirming this as of (YYYY-MM-DD).")
    if d > today:
        raise RecError("The as-of date cannot be in the future.")
    return d


def _choice(q: VrQuestion, key: str) -> dict:
    for c in q.choices_json:
        if c["key"] == key:
            return c
    raise RecError("That is not one of the answers offered for this question.")


def build_effect(q: VrQuestion, choice: dict, params: dict, note: str, known_stocks: set, today: date) -> dict:
    """The defined effect of a choice, stated in words and as data. Describes reconciliation state only (REQ-VR-02, -11)."""
    key, subj = choice["key"], q.subject_json or {}
    if q.kind == "shared_vin":
        stocks = list(subj["stocks"])
        if key in ("reentry", "duplicate"):
            what = ("one physical car that was repossessed and re-entered under another stock number" if key == "reentry"
                    else "one physical car entered twice")
            return {"type": "group", "relation": key, "stocks": stocks,
                    "text": f"Stock numbers {', '.join(stocks)} are grouped as {what}. Each keeps its own source records and dates; "
                            "nothing is merged or deleted."}
        if key.startswith("wrong_vin:"):
            s = key.split(":", 1)[1]
            return {"type": "wrong_vin", "stock": s,
                    "text": f"A person reports that stock {s} has the wrong VIN. The stock numbers stay separate vehicles. "
                            "The VIN must be corrected at the source (Frazer); this app does not change it."}
        if key == "unknown":
            return {"type": "unknown_vin", "text": "Recorded as still unknown. The stock numbers stay separate and the question reopens on new evidence."}
    if q.kind == "purchase_match":
        pk = subj["purchase_key"]
        if key.startswith("belongs:"):
            t = key.split(":", 1)[1]
            return {"type": "purchase_link", "purchase_key": pk, "target": t,
                    "text": f"Purchase {subj.get('purchase_ref')} is linked to stock {t} for reconciliation. The purchase and its "
                            "cost are unchanged and no cost is copied."}
        if key == "other_vehicle":
            t = (params.get("other_stock") or "").strip()
            if t not in known_stocks:
                raise RecError("Enter a stock number that is in the loaded Frazer lists (exactly as written).")
            return {"type": "purchase_link", "purchase_key": pk, "target": t,
                    "text": f"Purchase {subj.get('purchase_ref')} is linked to stock {t} for reconciliation. The purchase is unchanged."}
        if key == "shared_expense":
            return {"type": "purchase_link", "purchase_key": pk, "target": "shared_expense",
                    "text": f"Purchase {subj.get('purchase_ref')} is recorded as a shared business expense, not tied to one vehicle."}
        if key == "insufficient":
            return {"type": "purchase_link", "purchase_key": pk, "target": "insufficient",
                    "text": f"Purchase {subj.get('purchase_ref')} stays unlinked: not enough information (as of {today})."}
    if q.kind == "status_conflict":
        d = _parse_as_of(params, today)
        label = {"sold_financed": "sold or financed", "on_hand": "on hand / available", "returned": "returned or repossessed",
                 "something_else": "something else"}[key]
        if key == "something_else" and not note.strip():
            raise RecError("Describe what you can confirm.")
        return {"type": "confirm_status", "stock": subj["stock"], "status": key, "status_label": label, "as_of": str(d),
                "text": f"A person confirms stock {subj['stock']} was {label} as of {d}. This is shown beside the source reports; "
                        "the app's unit status is not changed."}
    raise RecError("That answer has no defined effect.")


def answer_question(db: Session, user: User, ref: str, choice_key: str, expected_version: int, expected_fp: str,
                    params: Optional[dict] = None, note: str = "", reason: str = "", today: Optional[date] = None) -> VrQuestion:
    params = params or {}
    note, reason = (note or "").strip(), (reason or "").strip()
    today = today or date.today()
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    if q is None or not can_view(user, q):
        raise NotAllowed("No such question.")
    if q.status == "withdrawn":
        raise RecError("That question was withdrawn because its evidence is no longer present.")
    choice = _choice(q, choice_key)
    # Staleness is checked before anything else: someone who answered from an old page is told the question moved, not
    # "you need a reason" for a decision they did not know existed. The atomic compare-and-swap below is the final guard.
    if q.version != expected_version or q.evidence_fingerprint != expected_fp:
        raise StaleAnswer("This question changed after you opened it (someone answered it, or the evidence moved). "
                          "Nothing was saved. Reload to see the current state.")

    # Deferrals: "Not sure" / "Needs someone else" record who said it and decide nothing.
    if choice_key in DEFER_KEYS:
        if q.status == "decided":
            raise RecError("This question already has a decision. An administrator can correct it.")
        if choice_key == "not_sure":
            _cas(db, q, expected_version, expected_fp, bump=False)
            event(db, q, "deferred", note or "Not sure", user, {"choice": "not_sure"})
            return q
        _cas(db, q, expected_version, expected_fp)
        q.needs_someone_else = True
        if not user.is_admin:
            q.owner_user_id = None        # back to the administrators' queue
        event(db, q, "deferred", note or "Needs someone else", user, {"choice": "needs_someone_else"})
        return q

    live = db.get(VrDecision, q.current_decision_id) if q.current_decision_id else None
    superseding_accepted = live is not None and live.state == "accepted"
    if superseding_accepted:
        operational_review = KIND_CLASS[q.kind] == "operational" and q.status == "review_needed"
        if not (user.is_admin or operational_review):
            raise NotAllowed("This question already has a decision. Only an administrator can correct it.")
        if not reason:
            raise RecError("A correction needs a reason. The earlier decision stays on record.")

    # Validate before spending the version check.
    w = eng.load_world(db)
    effect = build_effect(q, choice, params, note, set(eng.episode_stocks(w)), today)
    _cas(db, q, expected_version, expected_fp)

    needs_approval = KIND_CLASS[q.kind] in APPROVAL_CLASSES
    state = "accepted" if (not needs_approval or user.is_admin) else "proposed"
    dec = VrDecision(
        question_id=q.id, choice_key=choice_key, params_json={k: v for k, v in params.items() if v}, note=note,
        evidence_json=q.evidence_json, evidence_fingerprint=q.evidence_fingerprint, effect_json=effect,
        needs_approval=needs_approval, state=state, answered_by_user_id=user.id, answered_at=_now(),
        decided_by_user_id=user.id if state == "accepted" else None, decided_at=_now() if state == "accepted" else None,
        supersedes_id=live.id if live is not None and live.state in ("accepted", "proposed") else None, reason=reason,
    )
    db.add(dec)
    db.flush()
    if live is not None and live.state in ("accepted", "proposed"):
        live.state = "superseded"
        live.superseded_by_id = dec.id
        event(db, q, "superseded", reason or "Replaced by a newer answer", user, {"decision_id": live.id, "by_decision_id": dec.id})
    q.current_decision_id = dec.id
    q.status = "decided" if state == "accepted" else "proposed"
    q.needs_someone_else = False
    same_person = state == "accepted" and needs_approval
    event(db, q, "answered", note, user, {"decision_id": dec.id, "choice": choice_key, "effect": effect["text"], "state": state,
                                         "self_accepted": same_person})
    if state == "accepted" and needs_approval:
        event(db, q, "accepted", "Answered and accepted by the same administrator.", user, {"decision_id": dec.id})
    db.flush()
    return q


def accept_decision(db: Session, admin: User, ref: str, expected_version: int, expected_fp: str) -> VrQuestion:
    if not admin.is_admin:
        raise NotAllowed("Only an administrator can approve an identity or financial decision.")
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    if q is None:
        raise RecError("No such question.")
    dec = db.get(VrDecision, q.current_decision_id) if q.current_decision_id else None
    if dec is None or dec.state != "proposed":
        raise StaleAnswer("There is no answer waiting for approval. It may already have been accepted or rejected.")
    if dec.evidence_fingerprint != q.evidence_fingerprint:
        raise StaleAnswer("The evidence changed after this answer was given. Reject it or answer again; it cannot be accepted as is.")
    _cas(db, q, expected_version, expected_fp)
    dec.state, dec.decided_by_user_id, dec.decided_at = "accepted", admin.id, _now()
    q.status = "decided"
    event(db, q, "accepted", "", admin, {"decision_id": dec.id, "effect": dec.effect_json.get("text")})
    return q


def reject_decision(db: Session, admin: User, ref: str, reason: str, expected_version: int, expected_fp: str) -> VrQuestion:
    if not admin.is_admin:
        raise NotAllowed("Only an administrator can reject an answer.")
    if not (reason or "").strip():
        raise RecError("Say why the answer is being rejected.")
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    if q is None:
        raise RecError("No such question.")
    dec = db.get(VrDecision, q.current_decision_id) if q.current_decision_id else None
    if dec is None or dec.state != "proposed":
        raise StaleAnswer("There is no answer waiting for approval.")
    _cas(db, q, expected_version, expected_fp)
    dec.state, dec.decided_by_user_id, dec.decided_at, dec.reason = "rejected", admin.id, _now(), reason.strip()
    q.status, q.current_decision_id = "open", None
    event(db, q, "rejected", reason.strip(), admin, {"decision_id": dec.id})
    return q


# ---------------------------------------------------------------------------
# Reading for the screens
# ---------------------------------------------------------------------------

def user_names(db: Session) -> dict:
    return {u.id: u.display_name for u in db.query(User).all()}


def reconciled(db: Session):
    """-> (rows, world, names). Pure read."""
    w = eng.load_world(db)
    rows, stock_to_row, group_of = eng.build_rows(w)
    names = user_names(db)
    eng.finalize(w, rows, stock_to_row, names)
    rows.sort(key=lambda r: (r.state != eng.STATE_REVIEW, r.key))
    return rows, w, names


def find_row(rows: list, key: str):
    for r in rows:
        if r.key == key or key in r.stocks:
            return r
    return None


def filter_rows(rows: list, search: str = "", state: str = "", owner: str = "", has_question: str = "", kind: str = "") -> list:
    out = []
    s = (search or "").strip().lower()
    for r in rows:
        if s and s not in r.search_text():
            continue
        if state and r.state != state:
            continue
        live = [q for q in r.questions if q.status in eng.LIVE_QUESTION_STATUSES]
        if has_question == "yes" and not live:
            continue
        if has_question == "no" and live:
            continue
        if kind and not any(q.kind == kind for q in live):
            continue
        if owner == "unassigned" and not (live and all(q.owner_user_id is None for q in live)):
            continue
        if owner.isdigit() and not any(q.owner_user_id == int(owner) for q in live):
            continue
        out.append(r)
    return out


def question_history(db: Session, q: VrQuestion, names: dict) -> list:
    evs = db.query(VrEvent).filter(VrEvent.question_id == q.id).order_by(VrEvent.id).all()
    return [{"type": e.event_type, "body": e.body, "data": e.data_json or {}, "at": e.created_at,
             "who": names.get(e.user_id, "The system") if e.user_id else "The system"} for e in evs]


def decisions_for(db: Session, q: VrQuestion) -> list:
    return db.query(VrDecision).filter(VrDecision.question_id == q.id).order_by(VrDecision.id).all()


def employee_view(q: VrQuestion) -> dict:
    """
    The restricted evidence an assignee may see (REQ-VR-16). Built from an allow-list: stock, VIN and description, what each
    source reported and when, the question and its choices. Never a cost, a purchase amount, a customer, a balance or payroll.
    """
    ev = q.evidence_json or {}
    eps = [{k: e.get(k) for k in ("stock", "vin", "ymm", "vehicle_source", "purchase_date", "in_vehicle_list", "has_contract",
                                  "contract_sale_date")} for e in ev.get("episodes", [])]
    return {
        "ref": q.ref, "kind": q.kind, "title": q.title, "why": q.why, "status": q.status, "version": q.version,
        "fingerprint": q.evidence_fingerprint, "episodes": eps,
        "app": {"unit_status": ev.get("unit_status"), "unit_updated": ev.get("unit_updated")} if q.kind == "status_conflict" else None,
        "choices": q.choices_json,
    }
