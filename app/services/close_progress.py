"""
Month-end close progress and the recommended closing schedule (spec section 20, REQ-MR-15..30).

Pure functions: they take what the page already computed and return a plain dict. There is ONE list of steps and
milestones (STEPS, derived from section 6.2 of the spec). The closing schedule does not keep a second checklist: each
scheduled activity points at a step or a milestone and shows THAT status.

Two tracks, never mixed:
  PREPARATION  milestones that people can finish now: records received (to date, then for the whole period), reviewed
               (Steve), owner-reviewed (Drew). Advancing it says "the paperwork is ready", NOT "reconciled" or "closed".
  READINESS    what formal close needs on top: matched, reconciled, decisions confirmed, approved, then formal close.
               Every one needs a capability that is not built or a decision that is not made, so it stays BLOCKED and visible.

A manually resolved issue, a review note, an upload alone, or a passing date satisfies no milestone.

THE TWO PLACES TO UPDATE LATER: CAPABILITIES (when a gated feature is built) and DECISIONS_CONFIRMED (when a decision is made).
"""
from datetime import date
from typing import Optional

from app.services import business_calendar as bc
from app.services import review_period as rp

# --- configuration: what is built, what is decided ------------------------------------------------------------------------

CAPABILITIES = {
    "bank_comparison": {"built": False, "label": "Compare the month-end bank observation with the statement", "spec": "Slice B: REQ-LOC-03"},
    "cash_verification": {"built": False, "label": "Count review and verification of cash and bank balances", "spec": "Slice B: REQ-CNT-06, DEC-29"},
    "matching": {"built": False, "label": "Match Frazer, Square and deposits", "spec": "REQ-REC-02 (L)"},
    "custody_reconciliation": {"built": False, "label": "Show every source receipt has custody and payouts are explained", "spec": "Slice B, REQ-REC-03"},
    "vehicle_matching": {"built": False, "label": "Match vehicles across Frazer and lender records", "spec": "REQ-AD-01 (L)"},
    "vehicle_reconciliation": {"built": False, "label": "Vehicle reconciliation and debt check", "spec": "REQ-AD-01 (L)"},
    "payroll_capture": {"built": False, "label": "Restricted payroll capture and viewer list", "spec": "Slice P: GATE-P, REQ-PAY-*, DEC-09"},
    "management_pl": {"built": False, "label": "Simple management P&L on the CPA's basis", "spec": "§6.2 CL-5 (L)"},
    "cash_needs": {"built": False, "label": "Upcoming cash needs and commitments", "spec": "REQ-OV-03 (L)"},
    "in_app_close": {"built": False, "label": "In-app close, sign-off and reopen", "spec": "§6 (L), REQ-DR-07"},
}
DECISIONS_CONFIRMED: set = set()          # e.g. {"DEC-35", "DEC-36"} once Steve/Drew confirm them

ROLE = {"gather": "Belinda (gathers)", "confirm": "Steve (confirms)", "review": "Drew (reviews)",
        "decide": "Drew and Steve (decision)", "payroll": "Steve or Drew (restricted)"}

STATUS_LABEL = {"complete": "Complete", "in_progress": "In progress", "blocked": "Blocked", "not_started": "Not started",
                "not_applicable": "Not applicable"}

# --- when a step may be not applicable (REQ-MR-26) ------------------------------------------------------------------------
# A reason alone never excludes a required step. Only these may be excluded, only for the stated condition, only with the
# evidence named, and only when a second, different administrator concurs. Anything else is a mandatory requirement.
NA_POLICY = {
    "CL-4": {"condition": "No payroll was paid in the period.",
             "evidence": "Say where the evidence is kept (for example the secure payroll folder shows no pay run).",
             "deciders": "One administrator proposes; a different administrator concurs (Steve and Drew). Payroll viewers are DEC-09.",
             "contradiction": "Refused while any weekly payroll record is noted as kept for the month."},
}
NA_NEVER = {
    "CL-1": "Every month has a bank account and cash to reconcile (§6.2 CL-1).",
    "CL-2": "Frazer and Square activity is reconciled every month (§6.2 CL-2).",
    "CL-3": "Inventory and the customer portfolio exist every month (§6.2 CL-3).",
    "CL-5": "The management P&L inputs are needed every month (§6.2 CL-5).",
    "CL-6": "Sign-off is the end of the checklist (§6.2 CL-6).",
    "CLOSE": "Formal close is the final step and cannot be excluded.",
}
# By-hand records that may be "not required this month", with the same two-person rule. Everything else is mandatory.
RECORD_NOT_REQUIRED_OK = {
    "square": "Square was not used for any sale or payout in the period.",
    "floor_plan_statement": "There is no floor-plan loan in the period.",
}


def na_allowed(step_key: str) -> bool:
    return step_key in NA_POLICY


# --- definitions ------------------------------------------------------------------------------------------------------------

def _rec(key, label, *, from_wd0=False, role="gather", href=None):
    return {"key": key, "kind": "record", "track": "prep", "stage": "Received", "label": label, "from_wd0": from_wd0, "role": role, "href": href}


def _todate(key, label, *, role="gather", href=None):
    return {"key": key + "_todate", "kind": "record_todate", "base": key, "track": "prep", "stage": "Received to date", "label": label,
            "role": role, "href": href}


def _sign(mark, label, role, *, stage, track="prep"):
    return {"key": mark, "kind": "signoff", "mark": mark, "track": track, "stage": stage, "label": label, "role": role}


def _cap(cap_key, stage):
    return {"key": f"cap:{cap_key}", "kind": "capability", "cap": cap_key, "track": "ready", "stage": stage,
            "label": CAPABILITIES[cap_key]["label"], "role": "decide"}


def _dec(*ids, label):
    return {"key": "dec:" + "+".join(ids), "kind": "decision", "decisions": list(ids), "track": "ready", "stage": "Decisions", "label": label, "role": "decide"}


def _prior(kind, steps, label):
    return {"key": f"prior_{kind}", "kind": f"steps_{kind}", "steps": steps, "track": "prep" if kind == "prepared" else "ready",
            "stage": "Prepared" if kind == "prepared" else "Ready", "label": label, "role": "decide"}


FRAZER = "/finance/frazer/"
_APPROVE = "Matching and reconciliation signed off"
STEPS = [
    {"key": "CL-1", "name": "Bank and physical cash", "spec": "§6.2 CL-1", "due": 5, "criteria": [
        _todate("cash_count", "Cash count sheets recorded to date"),
        _rec("bank_statement", "Bank statements or month-end screenshots recorded", from_wd0=True),
        _rec("cash_count", "Last counts and deposit slips recorded", from_wd0=True),
        _sign("confirm", "Steve reviews the account list and his cash", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews the bank and cash records", "review", stage="Owner-reviewed"),
        _cap("bank_comparison", "Matched"),
        _cap("cash_verification", "Reconciled"),
        _sign("approve", _APPROVE, "review", stage="Approved", track="ready")]},
    {"key": "CL-2", "name": "Frazer and Square activity", "spec": "§6.2 CL-2", "due": 5, "criteria": [
        _todate("frazer_tx", "Frazer transactions received to date", href=FRAZER),
        _todate("square", "Square sales and payouts recorded to date"),
        _rec("frazer_tx", "Frazer transactions cover the period", href=FRAZER),
        _rec("square", "Square sales and payouts recorded for the period"),
        _sign("confirm", "Steve reviews refunds, voids and write-offs", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews the Frazer and Square records", "review", stage="Owner-reviewed"),
        _cap("matching", "Matched"),
        _dec("DEC-35", "DEC-36", label="Frazer code meanings and late-fee treatment confirmed"),
        _cap("custody_reconciliation", "Reconciled"),
        _sign("approve", _APPROVE, "review", stage="Approved", track="ready")]},
    {"key": "CL-3", "name": "Inventory, portfolio, debt", "spec": "§6.2 CL-3", "due": 7, "criteria": [
        _rec("frazer_vehicles", "Month-end vehicle snapshot received", from_wd0=True, href=FRAZER),
        _rec("frazer_customers", "Month-end customer-balance snapshot received", from_wd0=True, href=FRAZER),
        _rec("floor_plan_statement", "Floor-plan statement recorded", from_wd0=True),
        _sign("confirm", "Steve reviews valuation basis and overlaps", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews the inventory and portfolio records", "review", stage="Owner-reviewed"),
        _cap("vehicle_matching", "Matched"),
        _cap("vehicle_reconciliation", "Reconciled"),
        _sign("approve", _APPROVE, "review", stage="Approved", track="ready")]},
    {"key": "CL-4", "name": "Payroll (restricted)", "spec": "§6.2 CL-4, DEC-09", "due": 5, "criteria": [
        _todate("payroll_weekly", "Weekly payroll records noted as kept to date (no amounts here)", role="payroll"),
        _rec("payroll_weekly", "Every weekly payroll record noted as kept (no amounts here)", role="payroll"),
        _sign("confirm", "Steve reviews payroll and allocation", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews payroll", "review", stage="Owner-reviewed"),
        _dec("DEC-09", label="Who may see payroll detail decided"),
        _cap("payroll_capture", "Reconciled"),
        _sign("approve", _APPROVE, "review", stage="Approved", track="ready")]},
    {"key": "CL-5", "name": "Management P&L inputs", "spec": "§6.2 CL-5", "due": 10, "criteria": [
        _rec("purchases_reviewed", "Purchases for the period entered and none left pending review", href="/review/"),
        _sign("confirm", "Steve reviews one-off and owner items", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews the purchases", "review", stage="Owner-reviewed"),
        _dec("DEC-06", "DEC-11", "DEC-16", "DEC-18", label="CPA revenue basis confirmed"),
        _cap("management_pl", "Reconciled"),
        _sign("approve", _APPROVE, "review", stage="Approved", track="ready")]},
    {"key": "CL-6", "name": "Cash needs, differences, sign-off", "spec": "§6.2 CL-6", "due": 10, "criteria": [
        _prior("prepared", ["CL-1", "CL-2", "CL-3", "CL-4", "CL-5"], "CL-1 to CL-5 prepared"),
        _sign("confirm", "Steve reviews commitments", "confirm", stage="Reviewed"),
        _sign("review", "Drew reviews cash needs and differences", "review", stage="Owner-reviewed"),
        _cap("cash_needs", "Cash needs listed"),
        _prior("ready", ["CL-1", "CL-2", "CL-3", "CL-4", "CL-5"], "CL-1 to CL-5 ready"),
        _sign("approve", "Drew and Steve sign", "review", stage="Approved", track="ready")]},
    {"key": "CLOSE", "name": "Formal close", "spec": "§6 (L), REQ-DR-07", "due": 10, "final": True, "criteria": [
        _prior("ready", ["CL-1", "CL-2", "CL-3", "CL-4", "CL-5", "CL-6"], "CL-1 to CL-6 ready"),
        _cap("in_app_close", "Closed")]},
]
STEP_BY_KEY = {s["key"]: s for s in STEPS}
MARK_KINDS = ("confirm", "review", "approve", "not_applicable", "na_concur")


def marks_state(marks) -> dict:
    """(step_key, kind) -> the latest ReviewStepMark if its action is 'set', else None."""
    latest = {}
    for m in sorted(marks, key=lambda m: m.id):
        latest[(m.step_key, m.kind)] = m
    return {k: (m if m.action == "set" else None) for k, m in latest.items()}


def na_state(step_key: str, marks: dict, payroll_done) -> dict:
    """Is this step effectively not applicable? Needs the policy, a proposal, a different concurring administrator and no contradiction."""
    prop, conc = marks.get((step_key, "not_applicable")), marks.get((step_key, "na_concur"))
    pol = NA_POLICY.get(step_key)
    out = {"allowed": pol is not None, "policy": pol, "never": NA_NEVER.get(step_key), "proposal": prop, "concurrence": conc,
           "state": "none", "effective": False, "why": ""}
    if prop is None:
        return out
    if pol is None:
        out.update(state="refused", why=NA_NEVER.get(step_key, "This step cannot be excluded."))
    elif step_key == "CL-4" and payroll_done:
        out.update(state="contradicted", why="A weekly payroll record is noted as kept this month, so payroll did occur.")
    elif conc is None or conc.user_id == prop.user_id:
        out.update(state="proposed", why="Proposed; a different administrator must concur before it counts.")
    else:
        out.update(state="effective", effective=True)
    return out


# --- evaluation ------------------------------------------------------------------------------------------------------------

def _gaps(start: date, end: date, intervals) -> list:
    return rp.uncovered(rp.Period(start, start, end, "", "", False), intervals or [])


def _record_item(c, ctx):
    key, today, wd0 = c["key"], ctx["today"], ctx["wd0"]
    through = ctx["through"]
    if c["kind"] == "record_todate":
        base = c["base"]
        row = ctx["records"].get(base) or {}
        if row.get("status") == "not_required":
            return "met", "Recorded as not required this month.", True
        if through < ctx["period"].start:
            return "unmet", "No workday of the period has finished yet.", False
        if base == "payroll_weekly":
            due = [f for f in ctx["pay_fridays"] if f <= through]
            miss = [f for f in due if f.isoformat() not in ctx["payroll_done"]]
            if not due:
                return "unmet", "No pay date has passed yet.", False
            return ("met", f"All {len(due)} pay date(s) through {through.strftime('%b %-d')} recorded.", True) if not miss else \
                   ("partial" if len(miss) < len(due) else "unmet", f"Not yet recorded: {', '.join(f.strftime('%b %-d') for f in miss)}.", True)
        gaps = _gaps(ctx["period"].start, through, row.get("intervals"))
        if not gaps:
            return "met", f"Covered through {through.strftime('%b %-d')}.", True
        return ("partial" if row.get("intervals") else "unmet"), "Not covered: " + ", ".join(rp.fmt_range(*g) for g in gaps) + ".", True
    if key == "purchases_reviewed":
        p = ctx["purchases"]
        if p["count"] == 0:
            return "unmet", "No purchases are entered for the period. Enter them, or record that there were none to enter.", True
        if p["pending"]:
            return "partial", f"{p['pending']} of {p['count']} purchase(s) are still pending review.", True
        return "met", f"{p['count']} purchase(s) entered, none pending review.", True
    if key == "payroll_weekly":
        fr = ctx["pay_fridays"]
        got = [f for f in fr if f.isoformat() in ctx["payroll_done"]]
        due = [f for f in fr if f <= today and f not in got]
        if len(got) == len(fr) and fr:
            return "met", f"All {len(fr)} pay date(s) recorded.", True
        why = f"{len(got)} of {len(fr)} pay date(s) recorded."
        if due:
            return ("partial" if got else "unmet"), why, True
        return ("partial" if got else "unmet"), why + " The remaining pay dates have not arrived.", False
    row = ctx["records"].get(key)
    if row is None:
        return "unmet", "Not tracked.", True
    st = row["status"]
    avail = (not c.get("from_wd0")) or today >= wd0
    if st in ("received", "not_required"):
        return "met", row["coverage"], True
    why = row["outstanding"] if st != "not_due" else row["coverage"]
    if not avail:
        return ("partial" if st == "partial" else "unmet"), f"{why} Available from WD0 ({wd0.strftime('%b %-d')}).", False
    return ("partial" if st == "partial" else "unmet"), why, True


def _item(step, c, ctx, results_so_far):
    kind, role = c["kind"], c.get("role", "gather")
    r = {"key": c["key"], "kind": kind, "track": c["track"], "stage": c["stage"], "label": c["label"], "role": role, "owner": ROLE[role],
         "state": "unmet", "met": False, "actionable": False, "blocker": False, "waiting": None, "why": "",
         "href": c.get("href") or f"#step-{step['key']}"}
    if kind in ("record", "record_todate"):
        st, why, act = _record_item(c, ctx)
        r.update(state=st, met=st == "met", why=why, actionable=act and st != "met", waiting="date" if (not act and st != "met") else None)
        base = c.get("base", c["key"])
        if not c.get("href") and base != "purchases_reviewed":
            r["href"] = "#attest"
        if base == "purchases_reviewed":
            r["href"] = "/review/"
    elif kind == "capability":
        cap = CAPABILITIES[c["cap"]]
        r.update(met=cap["built"], state="met" if cap["built"] else "blocker", blocker=not cap["built"],
                 why=("" if cap["built"] else f"Not built yet ({cap['spec']}). Nothing in this app can satisfy this; it needs a decision to build it."))
    elif kind == "decision":
        open_ids = [d for d in c["decisions"] if d not in DECISIONS_CONFIRMED]
        r.update(met=not open_ids, state="met" if not open_ids else "blocker", blocker=bool(open_ids),
                 why=("" if not open_ids else f"Decision still open: {', '.join(open_ids)} (spec §16)."))
    elif kind in ("steps_prepared", "steps_ready"):
        table = ctx["step_prepared"] if kind == "steps_prepared" else ctx["step_ready"]
        undone = [k for k in c["steps"] if not table.get(k)]
        r.update(met=not undone, state="met" if not undone else "blocker", blocker=bool(undone),
                 why=("" if not undone else "Waiting for: " + ", ".join(undone) + "."))
    elif kind == "signoff":
        mark = ctx["marks"].get((step["key"], c["mark"]))
        prep_ok = all(x["met"] for x in results_so_far if x["track"] == "prep" and x["kind"] != "signoff")
        if c["mark"] == "confirm":
            r["met"] = mark is not None
            r["actionable"] = mark is None and prep_ok
            if mark is None and not prep_ok:
                r["why"] = "After the records are in place."
        elif c["mark"] == "review":
            confirmed = ctx["marks"].get((step["key"], "confirm")) is not None
            r["met"] = mark is not None and confirmed
            r["actionable"] = (mark is None or not confirmed) and prep_ok and confirmed
            r["why"] = "Recorded, but the review before it was cleared." if (mark is not None and not confirmed) else \
                       ("After the first review is recorded." if not confirmed else ("After the records are in place." if not prep_ok else ""))
        else:                                                    # approve: needs everything else first
            others = [x for x in results_so_far if x["key"] != "approve"]
            ready = all(x["met"] for x in others)
            r["met"] = mark is not None and ready
            r["actionable"] = mark is None and ready
            if not ready:
                r["why"] = "After the preparation is done, matching and reconciliation are done, and decisions are confirmed."
        r["state"] = "met" if r["met"] else "unmet"
        if mark is not None and r["met"]:
            r["why"] = f"Recorded by {mark.user.display_name or mark.user.username} on {mark.created_at.strftime('%b %-d')}" \
                       + (f": {mark.note}" if mark.note and step["key"] != "CL-4" else "")
    return r


def _pct(met, total):
    return int(100 * met / total) if total else 0


# which decisions a decision item stands for (for the summary line)
STEP_DECISIONS = {("CL-2", "dec:DEC-35+DEC-36"): ["DEC-35", "DEC-36"], ("CL-4", "dec:DEC-09"): ["DEC-09"],
                  ("CL-5", "dec:DEC-06+DEC-11+DEC-16+DEC-18"): ["DEC-06", "DEC-11", "DEC-16", "DEC-18"]}


def evaluate(ctx: dict) -> dict:
    """ctx: today, wd0, period, records {key: row}, purchases, pay_fridays, payroll_done {iso}, marks {(step,kind): mark}, issues."""
    ctx = dict(ctx)
    ctx.setdefault("through", min(bc.prev_workday(ctx["today"]), ctx["period"].end))
    ctx["step_prepared"], ctx["step_ready"], ctx["step_status"] = {}, {}, {}
    steps = []
    for s in STEPS:
        na = na_state(s["key"], ctx["marks"], ctx["payroll_done"])
        results = []
        for c in s["criteria"]:
            results.append(_item(s, c, ctx, results))
        prep = [r for r in results if r["track"] == "prep"]
        ready = [r for r in results if r["track"] == "ready"]
        unmet = [r for r in results if not r["met"]]
        if na["effective"]:
            status = "not_applicable"
        elif not unmet:
            status = "complete"
        else:
            actionable = [r for r in unmet if r["actionable"]]
            has_progress = any(r["met"] or r["state"] == "partial" for r in results)
            date_waiting = [r for r in unmet if r.get("waiting") == "date"]
            status = ("in_progress" if has_progress else "not_started") if (actionable or date_waiting) else "blocked"
        ctx["step_status"][s["key"]] = status
        ctx["step_prepared"][s["key"]] = status == "not_applicable" or all(r["met"] for r in prep)
        ctx["step_ready"][s["key"]] = status in ("complete", "not_applicable")
        issue = next((i for i in ctx["issues"] if i.checklist_ref == s["key"] and i.status != "resolved" and i.owner_user_id), None)
        steps.append({"key": s["key"], "name": s["name"], "spec": s["spec"], "status": status, "label": STATUS_LABEL[status],
                      "criteria": results, "final": bool(s.get("final")), "due": s["due"], "na": na,
                      "prep_met": sum(1 for r in prep if r["met"]), "prep_total": len(prep),
                      "ready_met": sum(1 for r in ready if r["met"]), "ready_total": len(ready),
                      "met": sum(1 for r in results if r["met"]), "total": len(results),
                      "assigned": (issue.owner.display_name or issue.owner.username) if issue else None,
                      "open_issues": sum(1 for i in ctx["issues"] if i.checklist_ref == s["key"] and i.status != "resolved"),
                      "restricted": s["key"] == "CL-4"})
    required = [s for s in steps if s["status"] != "not_applicable"]
    complete = [s for s in required if s["status"] == "complete"]
    closed = steps[-1]["status"] == "complete" and len(complete) == len(required)
    p_met = sum(s["prep_met"] for s in required)
    p_tot = sum(s["prep_total"] for s in required)
    r_met = sum(s["ready_met"] for s in required)
    r_tot = sum(s["ready_total"] for s in required)
    blocked_caps = sorted({c["label"] for s in required for c in s["criteria"] if c["kind"] == "capability" and not c["met"]})
    open_decs = sorted({d for s in required for c in s["criteria"] if c["kind"] == "decision" and not c["met"]
                        for d in STEP_DECISIONS.get((s["key"], c["key"]), [])})
    for s in steps:
        s["why_blocked"] = _why(s) if s["status"] == "blocked" else ""
        s["next"] = _step_next(s)
    ready_pct = 100 if closed else min(_pct(r_met, r_tot), 99)
    prep = {"met": p_met, "total": p_tot, "percent": _pct(p_met, p_tot),
            "steps_prepared": sum(1 for s in required if s["prep_total"] and ctx["step_prepared"][s["key"]]),
            "steps": sum(1 for s in required if s["prep_total"])}
    return {"steps": steps, "required": len(required), "complete": len(complete), "percent": ready_pct, "closed": closed,
            "na": len(steps) - len(required), "prep": prep,
            "ready": {"met": r_met, "total": r_tot, "percent": ready_pct, "steps_ready": len(complete), "steps": len(required),
                      "blocked_capabilities": blocked_caps, "open_decisions": open_decs},
            "explain_prep": ("Preparation = milestones people can finish now (records received to date and for the whole period, "
                             "reviewed by Steve, reviewed by Drew) ÷ all preparation milestones, steps marked not applicable "
                             "(with a second administrator's concurrence) left out. It means the paperwork is ready. It does not mean "
                             "anything is matched, reconciled or closed."),
            "explain_ready": ("Close readiness = readiness milestones met ÷ all of them: matched, reconciled, decisions confirmed, approved, then "
                              "formal close. Each needs a feature that is not built or a decision not yet made, so it stays Blocked. "
                              "Resolved issues, review notes, uploads and passing dates never count. It cannot reach 100% or say Closed "
                              "until Formal close itself is complete."),
            "explain": "Percent = readiness milestones met ÷ all readiness milestones. Preparation is shown separately.",
            "status_counts": {k: sum(1 for s in steps if s["status"] == k) for k in STATUS_LABEL}}


def _why(s) -> str:
    return "; ".join(r["why"] or r["label"] for r in s["criteria"] if r["blocker"])


def _step_next(s) -> Optional[dict]:
    if s["status"] in ("complete", "not_applicable"):
        return None
    for r in s["criteria"]:
        if not r["met"] and r["actionable"]:
            return {"text": r["label"], "owner": s["assigned"] or r["owner"], "href": r["href"], "why": r["why"], "step": s["key"]}
    b = next((r for r in s["criteria"] if r["blocker"]), None)
    if b:
        return {"text": "Blocked: " + b["label"], "owner": b["owner"], "href": f"#step-{s['key']}", "why": b["why"], "step": s["key"]}
    w = next((r for r in s["criteria"] if not r["met"]), None)
    return {"text": w["label"], "owner": w["owner"], "href": w["href"], "why": w["why"], "step": s["key"]} if w else None


def first_next_action(close: dict) -> dict:
    for s in close["steps"]:
        for r in s["criteria"]:
            if not r["met"] and r["actionable"] and s["status"] not in ("complete", "not_applicable"):
                return {"text": f"{s['key']}: {r['label']}", "owner": s["assigned"] or r["owner"], "href": r["href"], "why": r["why"]}
    for s in close["steps"]:
        if s["status"] == "blocked":
            return {"text": f"{s['key']} is blocked", "owner": s["next"]["owner"] if s["next"] else "", "href": f"#step-{s['key']}", "why": s["why_blocked"]}
    return {"text": "Nothing outstanding.", "owner": "", "href": "#top", "why": ""}


# --- recommended closing schedule ----------------------------------------------------------------------------------------

REQUIRED_NOTE = "Spec §6.2 deadline (a proposal, DEC-17)"
REC_NOTE = "Recommended (not in the spec)"
ACTIVITIES = [
    {"key": "snap", "wd": 0, "kind": "recommended", "title": "Download the month-end Frazer vehicle and customer-balance snapshots and upload them",
     "role": "gather", "refs": ["CL-3:frazer_vehicles", "CL-3:frazer_customers"], "href": FRAZER},
    {"key": "bank", "wd": 0, "kind": "recommended", "title": "Take the month-end bank screenshot and collect the final cash count sheets; record them",
     "role": "gather", "refs": ["CL-1:bank_statement", "CL-1:cash_count"], "href": "#attest"},
    {"key": "tx", "wd": 1, "kind": "recommended", "title": "Download Frazer transaction detail through month end and upload it",
     "role": "gather", "refs": ["CL-2:frazer_tx"], "href": FRAZER},
    {"key": "sq", "wd": 1, "kind": "recommended", "title": "Record Square sales and payouts for the month", "role": "gather",
     "refs": ["CL-2:square"], "href": "#attest"},
    {"key": "fp", "wd": 2, "kind": "recommended", "title": "Record the floor-plan lender statement", "role": "gather",
     "refs": ["CL-3:floor_plan_statement"], "href": "#attest"},
    {"key": "pur", "wd": 3, "kind": "recommended", "title": "Review pending purchases and receipts for the month", "role": "gather",
     "refs": ["CL-5:purchases_reviewed"], "href": "/review/"},
    {"key": "pay", "wd": 3, "kind": "recommended", "title": "Note that each week's payroll record is kept (no amounts here)", "role": "payroll",
     "refs": ["CL-4:payroll_weekly"], "href": "#attest"},
    {"key": "s1", "bd": 5, "kind": "required", "title": "CL-1 bank and physical cash: prepared, reconciled and signed off", "role": "review", "step": "CL-1", "needs": ["bank"]},
    {"key": "s2", "bd": 5, "kind": "required", "title": "CL-2 Frazer and Square activity: prepared, reconciled and signed off", "role": "review", "step": "CL-2", "needs": ["tx", "sq"]},
    {"key": "s4", "bd": 5, "kind": "required", "title": "CL-4 payroll: reviewed and signed off (restricted)", "role": "payroll", "step": "CL-4", "needs": ["pay"]},
    {"key": "s3", "bd": 7, "kind": "required", "title": "CL-3 inventory, portfolio, debt: prepared, reconciled and signed off", "role": "review", "step": "CL-3", "needs": ["snap", "fp"]},
    {"key": "s5", "bd": 10, "kind": "required", "title": "CL-5 management P&L inputs: prepared and signed off", "role": "review", "step": "CL-5", "needs": ["pur"]},
    {"key": "s6", "bd": 10, "kind": "required", "title": "CL-6 cash needs, differences and sign-off", "role": "review", "step": "CL-6", "needs": ["s1", "s2", "s3", "s4", "s5"]},
    {"key": "close", "bd": 10, "kind": "required", "title": "Formal close (after CL-6)", "role": "decide", "step": "CLOSE", "needs": ["s6"]},
]


def _ref_status(close: dict, ref: str):
    sk, ck = ref.split(":")
    step = next(s for s in close["steps"] if s["key"] == sk)
    return next(r for r in step["criteria"] if r["key"] == ck)


def schedule(close: dict, month: date, today: date) -> list:
    """Dated activities. Recommended rows hang off WD0 (the last business day); Required rows are the spec's 'BD n', the n-th
    business day after the CALENDAR month end. The two anchors are separate; BD n and WD+n coincide (see business_calendar)."""
    out, by_key = [], {}
    wd0 = bc.wd0_for_month(month)
    steps = {s["key"]: s for s in close["steps"]}
    for a in ACTIVITIES:
        if "bd" in a:
            d, label, anchor = bc.bd_after_month_end(month, a["bd"]), f"BD{a['bd']}", f"BD{a['bd']} = the {a['bd']}th business day after month end"
        else:
            d, label, anchor = bc.workday_on(wd0, a["wd"]), bc.wd_label(a["wd"]), ""
        if "step" in a:
            s = steps[a["step"]]
            status, why = s["status"], s["why_blocked"] or ""
        else:
            cs = [_ref_status(close, r) for r in a["refs"]]
            if all(c["met"] for c in cs):
                status = "complete"
            elif any(c["met"] or c["state"] == "partial" for c in cs):
                status = "in_progress"
            else:
                status = "not_started"
            why = "; ".join(c["why"] for c in cs if c["why"] and not c["met"])
        off = bc.wd_offset(d, wd0)
        item = {**a, "date": d, "wd_label": label, "wd_equiv": (bc.wd_label(off) if off is not None else "") if "bd" in a else "", "anchor": anchor,
                "status": status, "status_label": STATUS_LABEL[status], "why": why,
                "role_text": ROLE[a["role"]], "source": REQUIRED_NOTE if a["kind"] == "required" else REC_NOTE,
                "overdue": d < today and status not in ("complete", "not_applicable"),
                "href": a.get("href") or f"#step-{a.get('step', '')}"}
        out.append(item)
        by_key[a["key"]] = item
    for it in out:
        it["prereqs"] = [{"title": by_key[k]["title"], "status": by_key[k]["status"], "label": by_key[k]["status_label"]} for k in it.get("needs", [])]
    out.sort(key=lambda i: (i["date"], 0 if i["kind"] == "required" else 1, i["key"]))
    return out


def next_scheduled(sched: list) -> Optional[dict]:
    live = [i for i in sched if i["status"] not in ("complete", "not_applicable")]
    return live[0] if live else None
