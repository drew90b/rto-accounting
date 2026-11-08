"""
The reconciled view (spec REQ-VR-04..06, -09, -10, -19). Pure reading: this module never writes anything.

From the newest source records plus the ACCEPTED decisions it builds:
  * episodes   - one per stock number seen in a Frazer source (never merged by this module on its own)
  * rows       - what the list shows: an episode, or episodes a person has accepted are the same physical car
  * purchase matches, question candidates (what the evidence supports), and each row's state and next action

Vocabulary is kept apart on purpose: physical vehicle (a group a person accepted), stock-number episode, contract.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.vehicle_recon import (
    SOURCE_APP_PURCHASES, SOURCE_APP_UNITS, SOURCE_FRAZER_CONTRACTS, SOURCE_FRAZER_VEHICLES,
    VrDecision, VrQuestion, VrSnapshot, VrSourceRecord,
)

UNSOLD_UNIT_STATUSES = {"acquired", "in_inspection", "in_repair", "waiting_parts", "frontline_ready"}
SOLD_UNIT_STATUSES = {"sold", "leased_rto_active"}

STATE_AGREE, STATE_REVIEW, STATE_LACK, STATE_RESOLVED = "agree", "needs_review", "lack_evidence", "resolved"
STATE_LABEL = {STATE_AGREE: "Agrees", STATE_REVIEW: "Needs review", STATE_LACK: "Lacks evidence",
               STATE_RESOLVED: "Resolved by a person"}

LIVE_QUESTION_STATUSES = ("open", "proposed", "review_needed")


def norm_vin(v: Optional[str]) -> Optional[str]:
    return v.strip().upper() if v and v.strip() else None


def norm_stock(s: Optional[str]) -> Optional[str]:
    """Used ONLY to find a near-match proposal. A normalised match is never a match (REQ-VR-01)."""
    return s.strip().upper().lstrip("0") if s and s.strip() else None


def fingerprint(core) -> str:
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class PurchaseMatch:
    rec: VrSourceRecord
    status: str                 # attached | confirmed | unresolved | shared_expense | insufficient | waiting_identity
    row_stock: Optional[str]    # the episode stock it is attached to (None unless attached/confirmed)
    basis: str
    candidates: list = field(default_factory=list)       # [stock, ...]
    question_key: Optional[str] = None
    evidence_core: Optional[dict] = None


@dataclass
class Row:
    key: str
    stocks: list                       # episode stock numbers, primary first; [] for a unit-only row
    relation_note: str = ""            # e.g. "Confirmed by a person: repossession and re-entry"
    vehicle: Optional[VrSourceRecord] = None
    contract: Optional[VrSourceRecord] = None
    episodes: dict = field(default_factory=dict)       # stock -> {"vehicle": rec, "contract": rec}
    unit: Optional[VrSourceRecord] = None
    ambiguous_units: list = field(default_factory=list)
    purchases: list = field(default_factory=list)      # PurchaseMatch
    questions: list = field(default_factory=list)      # VrQuestion
    confirmations: list = field(default_factory=list)  # accepted person-confirmed facts
    differences: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    state: str = STATE_LACK
    state_note: str = ""
    source_lines: list = field(default_factory=list)
    next_action: str = ""
    owner_user_id: Optional[int] = None
    owner_name: str = ""

    @property
    def vin(self):
        for r in (self.vehicle, self.contract, self.unit):
            if r is not None and r.vin:
                return r.vin
        return None

    @property
    def ymm(self):
        for r in (self.vehicle, self.contract, self.unit):
            if r is not None and (r.year or r.make or r.model):
                return " ".join(x for x in (r.year, r.make, r.model) if x)
        return ""

    def search_text(self) -> str:
        bits = list(self.stocks) + [self.vin or "", self.ymm]
        return " ".join(bits).lower()


@dataclass
class Derived:
    """What accepted decisions say. Nothing here comes from a proposal."""
    groups: list = field(default_factory=list)         # (relation, [stocks], decision_id)
    wrong_vin: dict = field(default_factory=dict)      # stock -> decision_id
    purchase_links: dict = field(default_factory=dict) # purchase source_key -> (target, decision_id)
    confirmations: dict = field(default_factory=dict)  # stock -> [effect dict + decision_id]
    unknown_vin: set = field(default_factory=set)      # shared-VIN question keys answered "still unknown"


@dataclass
class World:
    vehicles: dict
    contracts: dict
    units: list
    purchases: list
    loaded: dict
    snap_dates: dict                 # snapshot id -> source_date
    latest: dict                     # source type -> newest snapshot date
    derived: Derived
    questions: dict                  # question_key -> VrQuestion


def load_world(db: Session) -> World:
    snaps = {s.id: s for s in db.query(VrSnapshot).all()}
    loaded = {t: False for t in (SOURCE_FRAZER_VEHICLES, SOURCE_FRAZER_CONTRACTS, SOURCE_APP_UNITS, SOURCE_APP_PURCHASES)}
    latest = {}
    for s in snaps.values():
        loaded[s.source_type] = True
        if s.source_type not in latest or s.source_date > latest[s.source_type]:
            latest[s.source_type] = s.source_date
    cur = db.query(VrSourceRecord).filter(VrSourceRecord.is_current.is_(True)).order_by(VrSourceRecord.id).all()
    vehicles = {r.source_key: r for r in cur if r.source_type == SOURCE_FRAZER_VEHICLES}
    contracts = {r.source_key: r for r in cur if r.source_type == SOURCE_FRAZER_CONTRACTS}
    units = [r for r in cur if r.source_type == SOURCE_APP_UNITS]
    purchases = [r for r in cur if r.source_type == SOURCE_APP_PURCHASES]
    questions = {q.question_key: q for q in db.query(VrQuestion).all()}
    return World(vehicles, contracts, units, purchases, loaded, {i: s.source_date for i, s in snaps.items()}, latest,
                 derive(db), questions)


def derive(db: Session) -> Derived:
    d = Derived()
    rows = (db.query(VrDecision, VrQuestion).join(VrQuestion, VrQuestion.id == VrDecision.question_id)
            .filter(VrDecision.state == "accepted").order_by(VrDecision.id).all())
    for dec, q in rows:
        e = dec.effect_json or {}
        t = e.get("type")
        if t == "group":
            d.groups.append((e["relation"], list(e["stocks"]), dec.id))
        elif t == "wrong_vin":
            d.wrong_vin[e["stock"]] = dec.id
        elif t == "purchase_link":
            d.purchase_links[e["purchase_key"]] = (e["target"], dec.id)
        elif t == "confirm_status":
            d.confirmations.setdefault(e["stock"], []).append({**e, "decision_id": dec.id, "by": dec.answered_by_user_id,
                                                              "at": dec.decided_at or dec.answered_at})
        elif t == "unknown_vin":
            d.unknown_vin.add(q.question_key)
    return d


# ---------------------------------------------------------------------------
# Episodes, groups, VIN sets
# ---------------------------------------------------------------------------

def episode_vin(w: World, stock: str) -> Optional[str]:
    v = w.vehicles.get(stock)
    c = w.contracts.get(stock)
    return (v.vin if v is not None and v.vin else None) or (c.vin if c is not None and c.vin else None)


def episode_stocks(w: World) -> list:
    return sorted(set(w.vehicles) | set(w.contracts))


def shared_vin_sets(w: World) -> dict:
    """norm VIN -> sorted stock numbers, for VINs seen under two or more distinct stock numbers (raw evidence, decision-free)."""
    by_vin = {}
    for s in episode_stocks(w):
        v = norm_vin(episode_vin(w, s))
        if v:
            by_vin.setdefault(v, []).append(s)
    return {v: sorted(ss) for v, ss in by_vin.items() if len(ss) > 1}


def _union_groups(stocks: list, derived: Derived) -> dict:
    parent = {s: s for s in stocks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _rel, ss, _id in derived.groups:
        ss = [s for s in ss if s in parent]
        for a in ss[1:]:
            parent[find(a)] = find(ss[0])
    return {s: find(s) for s in stocks}


def _purchase_date(w: World, stock: str) -> str:
    v = w.vehicles.get(stock)
    return ((v.fields_json or {}).get("purchase_date") if v is not None else None) or "9999-12-31"


def episode_sort_key(w: World, stock: str):
    return (_purchase_date(w, stock), stock)


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

def _vin_episodes(w: World, uvin: Optional[str]) -> list:
    if not uvin:
        return []
    return [s for s in episode_stocks(w) if s not in w.derived.wrong_vin and norm_vin(episode_vin(w, s)) == uvin]


def evaluate_purchases(w: World, group_of: dict) -> list:
    out = []
    stocks = set(episode_stocks(w))
    for p in sorted(w.purchases, key=lambda r: r.id):
        f = p.fields_json or {}
        stock = p.stock_number
        uvin = norm_vin(f.get("unit_vin"))
        key = p.source_key
        vin_eps = _vin_episodes(w, uvin)
        if key in w.derived.purchase_links:
            target, _ = w.derived.purchase_links[key]
            if target in ("shared_expense", "insufficient"):
                out.append(PurchaseMatch(p, target, None, "Decided by a person"))
            else:
                out.append(PurchaseMatch(p, "confirmed", target, "Confirmed by a person"))
            continue
        exact = stock if stock in stocks else None
        near = [s for s in sorted(stocks) if stock and exact is None and s != stock and norm_stock(s) == norm_stock(stock)]
        core = None
        m = None
        if exact is not None and uvin:
            exact_vin = norm_vin(episode_vin(w, exact))
            same_group_vin = any(group_of.get(e) == group_of.get(exact) for e in vin_eps)
            if exact_vin is None or exact in vin_eps or same_group_vin:
                m = PurchaseMatch(p, "attached", exact, "Stock number and unit VIN agree")
            else:
                cands = sorted({exact, *vin_eps})
                core = {"purchase": f.get("purchase_ref") or p.source_key, "stock": stock, "unit_vin": uvin,
                        "case": "stock_vs_vin", "candidates": [(c, norm_vin(episode_vin(w, c))) for c in cands]}
                m = PurchaseMatch(p, "unresolved", None, "Stock number and the unit's VIN point at different vehicles",
                                  cands, f"purchase_match:{key}", core)
        elif exact is not None:
            m = PurchaseMatch(p, "attached", exact, "Stock number only (the VIN was not checked)")
        elif near:
            cands = sorted({*near, *vin_eps})
            core = {"purchase": f.get("purchase_ref") or p.source_key, "stock": stock, "unit_vin": uvin, "case": "near_stock",
                    "candidates": [(c, norm_vin(episode_vin(w, c))) for c in cands]}
            m = PurchaseMatch(p, "unresolved", None, "Stock number only matches after ignoring zeros or capitals",
                              cands, f"purchase_match:{key}", core)
        elif stock and vin_eps:
            cands = sorted(vin_eps)
            core = {"purchase": f.get("purchase_ref") or p.source_key, "stock": stock, "unit_vin": uvin, "case": "stock_unknown",
                    "candidates": [(c, norm_vin(episode_vin(w, c))) for c in cands]}
            m = PurchaseMatch(p, "unresolved", None, "Stock number is not a known vehicle, but the unit's VIN is",
                              cands, f"purchase_match:{key}", core)
        elif not stock and vin_eps:
            rows = {group_of.get(e, e) for e in vin_eps}
            if len(rows) == 1:
                m = PurchaseMatch(p, "attached", sorted(vin_eps, key=lambda s: episode_sort_key(w, s))[0], "Unit VIN only")
            else:
                m = PurchaseMatch(p, "waiting_identity", None, "The unit's VIN is shared by several stock numbers", sorted(vin_eps))
        if m is not None:
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Status conflicts
# ---------------------------------------------------------------------------

def latest_episode(w: World, stocks: list) -> str:
    return sorted(stocks, key=lambda s: episode_sort_key(w, s))[-1]


def status_conflict(w: World, row: Row) -> Optional[dict]:
    """Evidence-supported status difference between the app's unit and Frazer's contract list, or None."""
    if row.unit is None or not row.stocks or not w.loaded.get(SOURCE_FRAZER_CONTRACTS):
        return None
    current = latest_episode(w, row.stocks)
    contract = w.contracts.get(current)
    status = row.unit.reported_status
    if status in UNSOLD_UNIT_STATUSES and contract is not None:
        case = "app_unsold_frazer_contract"
    elif status == "leased_rto_active" and contract is None:
        case = "app_leased_frazer_no_contract"
    else:
        return None
    return {"case": case, "stock": current, "unit_pk": (row.unit.fields_json or {}).get("unit_pk"), "unit_status": status,
            "unit_updated": str(row.unit.reported_status_date) if row.unit.reported_status_date else None,
            "contract_sale_date": (contract.fields_json or {}).get("sale_date") if contract is not None else None,
            "contract_present": contract is not None}


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def build_rows(w: World, db: Optional[Session] = None) -> list:
    stocks = episode_stocks(w)
    group_of = _union_groups(stocks, w.derived)
    members = {}
    for s in stocks:
        members.setdefault(group_of[s], []).append(s)

    rows = {}
    for root, ss in members.items():
        ss = sorted(ss, key=lambda s: episode_sort_key(w, s))
        row = Row(key=ss[0], stocks=ss)
        for s in ss:
            row.episodes[s] = {"vehicle": w.vehicles.get(s), "contract": w.contracts.get(s)}
        row.vehicle = w.vehicles.get(ss[0]) or next((w.vehicles[s] for s in ss if s in w.vehicles), None)
        row.contract = w.contracts.get(latest_episode(w, ss)) or next((w.contracts[s] for s in ss if s in w.contracts), None)
        if len(ss) > 1:
            rels = sorted({rel for rel, gs, _ in w.derived.groups if set(gs) & set(ss)})
            row.relation_note = "Confirmed by a person: " + " / ".join(
                {"reentry": "same car, repossessed and re-entered", "duplicate": "same car, duplicate record"}.get(r, r) for r in rels)
        rows[ss[0]] = row
    stock_to_row = {s: r for r in rows.values() for s in r.stocks}

    # Units: attach by exact VIN only when it identifies exactly one row.
    for u in sorted(w.units, key=lambda r: r.id):
        uvin = norm_vin(u.vin)
        cand_rows = {stock_to_row[s].key for s in _vin_episodes(w, uvin)} if uvin else set()
        if len(cand_rows) == 1:
            r = rows[next(iter(cand_rows))]
            if r.unit is None:
                r.unit = u
            else:
                r.flags.append(f"Two app units share this VIN (unit {(u.fields_json or {}).get('unit_ref')})")
        elif len(cand_rows) > 1:
            for k in cand_rows:
                rows[k].ambiguous_units.append(u)
        else:
            rows[f"unit:{u.source_key}"] = Row(key=f"unit:{u.source_key}", stocks=[], unit=u)

    for m in evaluate_purchases(w, group_of):
        if m.status in ("attached", "confirmed") and m.row_stock in stock_to_row:
            stock_to_row[m.row_stock].purchases.append(m)
        elif m.status == "unresolved":
            for c in m.candidates:
                if c in stock_to_row and m not in stock_to_row[c].purchases:
                    stock_to_row[c].purchases.append(m)
        elif m.status == "waiting_identity":
            for c in m.candidates:
                if c in stock_to_row:
                    stock_to_row[c].purchases.append(m)

    for s, items in w.derived.confirmations.items():
        if s in stock_to_row:
            stock_to_row[s].confirmations.extend(items)

    return list(rows.values()), stock_to_row, group_of


# ---------------------------------------------------------------------------
# Question candidates: what the evidence supports (REQ-VR-08). Deterministic; decision-free.
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    key: str
    kind: str
    title: str
    why: str
    subject: dict
    evidence: dict
    core: dict
    choices: list

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.core)


DEFER_CHOICES = [
    {"key": "not_sure", "label": "Not sure", "defer": True},
    {"key": "needs_someone_else", "label": "Needs someone else", "defer": True},
]


def _desc(w: World, stock: str) -> dict:
    v = w.vehicles.get(stock)
    c = w.contracts.get(stock)
    r = v or c
    return {
        "stock": stock, "vin": episode_vin(w, stock),
        "ymm": " ".join(x for x in (r.year, r.make, r.model) if x) if r is not None else "",
        "vehicle_source": (v.fields_json or {}).get("vehicle_source") if v is not None else None,
        "purchase_date": (v.fields_json or {}).get("purchase_date") if v is not None else None,
        "in_vehicle_list": v is not None, "has_contract": c is not None,
        "contract_sale_date": (c.fields_json or {}).get("sale_date") if c is not None else None,
        "vehicle_record_id": v.id if v is not None else None, "contract_record_id": c.id if c is not None else None,
    }


def candidates(w: World, rows: list, stock_to_row: dict, group_of: dict) -> list:
    out = []
    # --- shared VIN (identity) ---
    for vin, ss in sorted(shared_vin_sets(w).items()):
        descs = [_desc(w, s) for s in ss]
        core = {"vin": vin, "episodes": [(d["stock"], d["vin"], d["ymm"], d["vehicle_source"]) for d in descs]}
        choices = [
            {"key": "reentry", "label": "Repossession and re-entry: one car, a new stock number"},
            {"key": "duplicate", "label": "Duplicate record: the same car entered twice"},
        ] + [{"key": f"wrong_vin:{s}", "label": f"Stock {s} has the wrong VIN"} for s in ss] + [
            {"key": "unknown", "label": "Still unknown"},
        ] + DEFER_CHOICES
        out.append(Candidate(
            key=f"shared_vin:{vin}", kind="shared_vin",
            title=f"Stock numbers {', '.join(ss)} share one VIN. What is this?",
            why="Two or more stock numbers carry the same VIN in the loaded records. A matching VIN is evidence, not proof: "
                "nothing has been merged.",
            subject={"stocks": ss, "vin": vin}, evidence={"episodes": descs}, core=core, choices=choices))
    # --- purchase matches (financial) ---
    for m in evaluate_purchases(w, group_of):
        if m.status != "unresolved":
            continue
        f = m.rec.fields_json or {}
        descs = [_desc(w, c) for c in m.candidates]
        choices = [{"key": f"belongs:{c}", "label": f"Belongs to vehicle {c}"} for c in m.candidates] + [
            {"key": "other_vehicle", "label": "Belongs to a different vehicle (enter its stock number)", "needs": "other_stock"},
            {"key": "shared_expense", "label": "A shared business expense, not one vehicle"},
            {"key": "insufficient", "label": "Not enough information"},
        ] + DEFER_CHOICES
        ref = f.get("purchase_ref") or f"id {m.rec.source_key}"
        out.append(Candidate(
            key=m.question_key, kind="purchase_match",
            title=f"Which vehicle does purchase {ref} belong to?",
            why=m.basis + ". The purchase record is unchanged and its cost is not copied anywhere.",
            subject={"stocks": list(m.candidates), "purchase_key": m.rec.source_key, "purchase_ref": ref},
            evidence={"purchase_record_id": m.rec.id, "purchase_stock": m.rec.stock_number, "unit_vin": f.get("unit_vin"),
                      "purchase_date": f.get("purchase_date"), "category": f.get("category"), "episodes": descs},
            core=m.evidence_core, choices=choices))
    # --- status conflicts (operational) ---
    for r in rows:
        sc = status_conflict(w, r)
        if sc is None:
            continue
        desc = _desc(w, sc["stock"])
        if sc["case"] == "app_unsold_frazer_contract":
            why = (f"The app's unit says '{sc['unit_status'].replace('_', ' ')}' (unit last updated {sc['unit_updated']}), but the "
                   f"Frazer customer-balance list shows a contract for stock {sc['stock']}"
                   f"{' (sale date ' + sc['contract_sale_date'] + ')' if sc['contract_sale_date'] else ''}.")
        else:
            why = (f"The app's unit says 'leased RTO active' (unit last updated {sc['unit_updated']}), but the Frazer "
                   f"customer-balance list shows no contract for stock {sc['stock']}.")
        why += " A source report is not the car's confirmed status or location."
        core = {k: sc[k] for k in ("case", "stock", "unit_status", "unit_updated", "contract_present", "contract_sale_date")}
        choices = [
            {"key": "sold_financed", "label": "Sold or financed", "needs": "as_of"},
            {"key": "on_hand", "label": "On hand / available", "needs": "as_of"},
            {"key": "returned", "label": "Returned or repossessed", "needs": "as_of"},
            {"key": "something_else", "label": "Something else (describe it)", "needs": "as_of", "needs_note": True},
        ] + DEFER_CHOICES
        out.append(Candidate(
            key=f"status_conflict:{sc['stock']}:unit{sc['unit_pk']}", kind="status_conflict",
            title=f"Stock {sc['stock']}: the app and Frazer report different statuses. What can you confirm?",
            why=why, subject={"stocks": list(r.stocks), "stock": sc["stock"], "unit_pk": sc["unit_pk"]},
            evidence={"unit_status": sc["unit_status"], "unit_updated": sc["unit_updated"],
                      "contract_present": sc["contract_present"], "contract_sale_date": sc["contract_sale_date"],
                      "episodes": [desc]},
            core=core, choices=choices))
    return out


# ---------------------------------------------------------------------------
# Finalise rows: attach questions, state, source lines, next action
# ---------------------------------------------------------------------------

def _source_lines(w: World, r: Row) -> list:
    lines = []
    for s in r.stocks:
        v = w.vehicles.get(s)
        tag = f"Stock {s}: " if len(r.stocks) > 1 else ""
        if v is not None:
            d = w.snap_dates.get(v.snapshot_id)
            newest = w.latest.get(SOURCE_FRAZER_VEHICLES)
            absent = d is not None and newest is not None and d < newest
            lines.append({"source": "Frazer vehicle list", "text": tag + ("Not in the newest list" if absent else "Listed"),
                          "as_of": d, "kind": "reported", "note": "Frazer's export has no status column and does not say where the car is."})
        elif w.loaded[SOURCE_FRAZER_VEHICLES]:
            lines.append({"source": "Frazer vehicle list", "text": tag + "Not listed", "as_of": w.latest.get(SOURCE_FRAZER_VEHICLES), "kind": "reported", "note": ""})
        else:
            lines.append({"source": "Frazer vehicle list", "text": "Not loaded", "as_of": None, "kind": "missing", "note": ""})
        c = w.contracts.get(s)
        if c is not None:
            f = c.fields_json or {}
            sd = f" (sale date {f['sale_date']})" if f.get("sale_date") else ""
            lines.append({"source": "Frazer customer-balance list", "text": tag + f"Contract listed{sd}",
                          "as_of": w.snap_dates.get(c.snapshot_id), "kind": "reported", "note": f.get("account_label") or ""})
        elif w.loaded[SOURCE_FRAZER_CONTRACTS]:
            lines.append({"source": "Frazer customer-balance list", "text": tag + "No contract listed",
                          "as_of": w.latest.get(SOURCE_FRAZER_CONTRACTS), "kind": "reported",
                          "note": "That list holds financed sales only; no contract here is normal for a cash sale or an unsold car."})
        else:
            lines.append({"source": "Frazer customer-balance list", "text": "Not loaded", "as_of": None, "kind": "missing", "note": ""})
    if r.unit is not None:
        f = r.unit.fields_json or {}
        lines.append({"source": "App unit", "text": f"{f.get('unit_ref') or 'Unit'}: {(r.unit.reported_status or '?').replace('_', ' ')}",
                      "as_of": r.unit.reported_status_date, "kind": "reported", "note": "Date is when the unit was last updated in the app."})
    elif r.ambiguous_units:
        lines.append({"source": "App unit", "text": "A unit has this VIN, but the VIN is shared; not attached", "as_of": None, "kind": "reported", "note": ""})
    elif w.loaded[SOURCE_APP_UNITS]:
        lines.append({"source": "App unit", "text": "No unit with this VIN", "as_of": w.latest.get(SOURCE_APP_UNITS), "kind": "reported", "note": ""})
    else:
        lines.append({"source": "App unit", "text": "Not loaded", "as_of": None, "kind": "missing", "note": ""})
    return lines


def finalize(w: World, rows: list, stock_to_row: dict, users: dict) -> None:
    """users: id -> display name. Mutates rows."""
    by_unit = {}
    for r in rows:
        if r.unit is not None:
            by_unit[(r.unit.fields_json or {}).get("unit_pk")] = r
    for q in sorted(w.questions.values(), key=lambda q: q.id):
        subj = q.subject_json or {}
        targets = []
        for s in subj.get("stocks", []):
            r = stock_to_row.get(s)
            if r is not None and r not in targets:
                targets.append(r)
        if subj.get("unit_pk") in by_unit and by_unit[subj["unit_pk"]] not in targets:
            targets.append(by_unit[subj["unit_pk"]])
        for r in targets:
            r.questions.append(q)

    for r in rows:
        # identity differences between records for the same stock number
        for s in r.stocks:
            v, c = w.vehicles.get(s), w.contracts.get(s)
            if v is not None and c is not None:
                if v.vin and c.vin and norm_vin(v.vin) != norm_vin(c.vin):
                    r.differences.append(f"Stock {s}: the vehicle list and the contract list give different VINs")
                a = " ".join((v.year or "", (v.make or "").upper(), (v.model or "").upper())).strip()
                b = " ".join((c.year or "", (c.make or "").upper(), (c.model or "").upper())).strip()
                if a and b and a != b:
                    r.differences.append(f"Stock {s}: the two Frazer lists describe the car differently")
        if r.unit is not None and r.vin and r.unit.vin and norm_vin(r.unit.vin) != norm_vin(r.vin):
            r.differences.append("The app unit's VIN differs")
        r.source_lines = _source_lines(w, r)

        live = [q for q in r.questions if q.status in LIVE_QUESTION_STATUSES]
        # distinct sources that actually have a record for this row
        present = []
        if any(w.vehicles.get(s) for s in r.stocks):
            present.append("Frazer vehicle list")
        if any(w.contracts.get(s) for s in r.stocks):
            present.append("Frazer customer-balance list")
        if r.unit is not None:
            present.append("App unit")
        if any(p.status in ("attached", "confirmed") for p in r.purchases):
            present.append("App purchase")
        r.sources_present = present
        decided = [q for q in r.questions if q.status == "decided"]
        if live or r.differences:
            r.state = STATE_REVIEW
        elif decided:
            # The sources still show what they showed; a person has settled the question. Never shown as "agrees".
            r.state = STATE_RESOLVED
            r.state_note = "Settled by a person: " + "; ".join(q.title.split(".")[0].split(":")[0] for q in decided[:2]) + ". The source reports are unchanged."
        elif len(present) >= 2:
            r.state = STATE_AGREE
            only_frazer = all(p.startswith("Frazer") for p in present)
            r.state_note = ("Agrees (both lists are from Frazer): " if only_frazer else "Agrees across ") + ", ".join(present)
        else:
            r.state = STATE_LACK
            r.state_note = ("Only one source has this car: " + present[0]) if present else "No source record"
        if live:
            rank = {"review_needed": 0, "proposed": 1, "open": 2}
            q = sorted(live, key=lambda q: (rank[q.status], q.id))[0]
            r.next_action = {"review_needed": "Review: new evidence — ", "proposed": "Approve an answer: ", "open": ""}[q.status] + q.title
            r.owner_user_id = q.owner_user_id
            r.owner_name = users.get(q.owner_user_id, "") if q.owner_user_id else ""
            if q.status == "proposed":
                # The next step is an approval, which only an administrator can give; the answerer has already acted.
                r.owner_user_id, r.owner_name = None, "an administrator (to approve)"
            r.next_question = q
        elif r.differences:
            r.next_action = r.differences[0] + ". Check the source records."
            r.next_question = None
        elif r.state == STATE_LACK:
            r.next_action = "Nothing to answer. Load or add another source to corroborate."
            r.next_question = None
        else:
            r.next_action = "None needed."
            r.next_question = None
    # person-confirmed vs reported stays separate: confirmations are on the row, never merged into source_lines
