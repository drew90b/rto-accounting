"""
Parse Frazer CSV exports into source records and exceptions.

Pure functions: no database, no files, no clock. The web intake, the command-line checker
(app/scripts/frazer_intake_check.py) and the tests all use this one parser.

What it does NOT do: it never decides what a Frazer code means, never merges records, never turns an
entry into a purchase, payment or cash movement, and never guesses a missing or unreadable value
(docs/specs/cash-financial-control-spec.md, section 19).

Conventions
  * "row" numbers are source LINE numbers; the header is line 1.
  * Identifiers (stock, VIN, Sequential ID, receipt and check numbers) stay text, leading zeros kept.
  * Dates, the entry date and the entry time are separate values. Two-digit years read as 20YY.
  * Every original cell is kept beside the parsed value (Record.raw).
  * One extra trailing cell per row is accepted only when it is empty.
"""
import csv
import hashlib
import io
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

PARSER_VERSION = "frazer-parse-1"

VEHICLES, CUSTOMERS, PAYMENTS, UNRECOGNIZED = "vehicles", "customer_balances", "payments", "unrecognized"
EXPORT_TYPES = (VEHICLES, CUSTOMERS, PAYMENTS)
RECORD_TYPE = {VEHICLES: "vehicle", CUSTOMERS: "customer_balance", PAYMENTS: "payment_entry"}

# Entry classes for transactions: what Frazer LABELLED the entry, from its description. Provisional
# (spec DEC-35/36); never a tender or revenue conclusion.
FEE, PAYMENT, CANCELLED, UNCLASSIFIED = "fee_assessment", "payment_entry", "cancelled_label", "unclassified"
DESCRIPTION_CLASS = {
    "late fee": FEE,
    "paymycar (cc)": PAYMENT, "cash": PAYMENT, "down payment": PAYMENT,
    "electronic cc": PAYMENT, "electronic dc": PAYMENT,
    "cancelled": CANCELLED,
}
CLASS_LABEL = {FEE: "Fee assessment (Late Fee)", PAYMENT: "Payment entry", CANCELLED: "Labelled CANCELLED",
               UNCLASSIFIED: "Unclassified"}

# Codes that make a row "conflicting" rather than "unresolved".
CONFLICT_CODES = {"duplicate_conflict", "baseline_key_conflict"}

ERROR, WARNING, INFO = "error", "warning", "info"


# ---------------------------------------------------------------------------
# Column layouts: canonical field -> accepted header spellings (lower case, single spaces)
# ---------------------------------------------------------------------------

STOCK_ALIASES = ["stock #", "stock", "stock number", "stock no", "stock no.", "stock#"]
VIN_ALIASES = ["vehicle vin", "vin"]

VEHICLE_FIELDS = {
    "stock": STOCK_ALIASES, "year": ["vehicle year", "year"], "make": ["vehicle make", "make"],
    "model": ["vehicle model", "model"], "vin": VIN_ALIASES, "mileage": ["mileage"],
    "source": ["vehicle source", "source"], "buy_fee": ["buy fee"], "location_code": ["location code"],
    "original_cost": ["original cost"], "total_cost": ["total cost"], "purchase_date": ["purchase date"],
    "vehicle_notes": ["vehicle notes"], "vendor": ["vendor"], "lot_fee": ["lot fee"],
    "added_costs": ["added costs"], "labor_costs": ["labor costs"], "purchase_notes": ["purchase notes"],
    "ready_to_sell_date": ["ready to sell date"], "last6_vin": ["last 6 vin"],
}
VEHICLE_MONEY = {"buy_fee", "original_cost", "total_cost", "lot_fee", "added_costs", "labor_costs"}
VEHICLE_DATES = {"purchase_date", "ready_to_sell_date"}

PAYMENT_FIELDS = {
    "sequential_id": ["sequential id"], "transaction_date": ["transaction date"], "due_date": ["due date"],
    "stock": STOCK_ALIASES, "last_name": ["last name"], "first_name": ["first name"], "amount": ["amount"],
    "transaction_type": ["transaction type"], "transaction_method": ["transaction method"],
    "description": ["description"], "how_paid": ["how paid"], "check_number": ["check number"],
    "receipt_number": ["receipt number"], "applied_contract": ["applied to contract"],
    "applied_interest": ["applied to interest"], "applied_fees": ["applied to fees"],
    "applied_pickup_note": ["applied to pick-up note"], "applied_other_receivables": ["applied to other receivables"],
    "applied_repairs": ["applied to repairs"], "applied_recurring_fees": ["applied to recurring fees"],
    "remaining_balance": ["remaining balance"], "date_entered": ["date entered"], "time_entered": ["time entered"],
    "initials": ["initials"], "sale_date": ["sale date"], "vin": VIN_ALIASES,
}
PAYMENT_MONEY = {"amount", "applied_contract", "applied_interest", "applied_fees", "applied_pickup_note",
                 "applied_other_receivables", "applied_repairs", "applied_recurring_fees", "remaining_balance"}
PAYMENT_DATES = {"transaction_date", "due_date", "date_entered", "sale_date"}

CUSTOMER_FIELDS = {
    "stock": STOCK_ALIASES, "vin": VIN_ALIASES,
    "first_name": ["first name"], "last_name": ["last name"], "customer_name": ["customer", "customer name", "name"],
    "account_label": ["type of sale", "type", "account type", "sale type", "contract type", "deal type", "category"],
    "balance": ["balance", "remaining balance", "current balance", "balance due", "total balance",
                "outstanding balance", "amount owed"],
    "sale_date": ["sale date"], "payment_amount": ["payment amt"], "pay_schedule": ["pay schedule"],
    "total_due_at_sale": ["total due at time of sale"], "amount_financed": ["amt financed"],
    "down_payment": ["down payment"], "number_of_payments": ["number of payments"],
    "current_date_due": ["current date due"], "year": ["vehicle year"], "make": ["vehicle make"],
    "model": ["vehicle model"], "mileage": ["mileage"], "days_past_due": ["days past due"],
    "total_sales_tax": ["total sales tax"], "current_due": ["current due"],
    "pickup_note_payment": ["pick-up note payment amount"], "pickup_note_due_date": ["pick-up note payment due date"],
    "fees_owed": ["fees owed"], "last_pay_date": ["last pay date"], "promise_date": ["promise date"],
    "principal_balance": ["principal balance"], "repairs_balance": ["repairs balance"],
    "first_payment_date": ["1st payment date"], "contract_balance_received": ["contract balance received"],
    "payments_remaining": ["payments remaining"], "sales_price": ["sales price"],
    "purchased_account_balance": ["purchased account balance"], "last_payment_amount": ["last payment amount"],
    "total_payments_received": ["total of payments received"], "days_since_last_payment": ["days since last payment"],
    "last6_vin": ["last 6 vin"], "contract_maturity_date": ["contract maturity date"],
}
CUSTOMER_MONEY = {"balance", "payment_amount", "total_due_at_sale", "amount_financed", "down_payment", "total_sales_tax",
                  "current_due", "pickup_note_payment", "fees_owed", "principal_balance", "repairs_balance",
                  "contract_balance_received", "sales_price", "purchased_account_balance", "last_payment_amount",
                  "total_payments_received"}
CUSTOMER_DATES = {"sale_date", "current_date_due", "pickup_note_due_date", "last_pay_date", "promise_date",
                  "first_payment_date", "contract_maturity_date"}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip()).lower()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Exc:
    code: str
    severity: str
    message: str
    row: Optional[int] = None
    identity: Optional[str] = None
    field: Optional[str] = None


@dataclass
class Record:
    row_number: int
    record_type: str
    identity: Optional[str]
    raw: dict
    fields: dict
    content_hash: str
    entry_class: Optional[str] = None
    exceptions: list = field(default_factory=list)


@dataclass
class Declared:
    export_date: Optional[date] = None
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    from_filename: tuple = ()        # which of the above were read from the filename, not typed by the user


_FN_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})-through-(\d{4}-\d{2}-\d{2})", re.I)
_FN_DAY = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?![\d])")


def declared_from_filename(name: str, declared: "Declared") -> "Declared":
    """Fill blank declared values from a Frazer-style filename. The filename is a label the exporter chose: it is
    recorded as the declared period (and flagged as read from the filename), never as proof of what the rows contain."""
    base = os.path.basename((name or "").replace("\\", "/"))
    used = []
    exp, cs, ce = declared.export_date, declared.coverage_start, declared.coverage_end
    m = _FN_RANGE.search(base)
    try:
        if m and not cs and not ce:
            cs, ce = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
            if cs <= ce:
                used += ["coverage_start", "coverage_end"]
            else:
                cs = ce = None
        elif not m and not exp:
            d = _FN_DAY.search(base)
            if d:
                exp = date.fromisoformat(d.group(1))
                used.append("export_date")
    except ValueError:
        pass
    return Declared(exp, cs, ce, tuple(used))


@dataclass
class ParsedFile:
    export_type: str
    layout: Optional[str]
    status: str                      # parsed | rejected
    encoding: str
    headers: list
    records: list = field(default_factory=list)
    file_exceptions: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    observed_start: Optional[date] = None
    observed_end: Optional[date] = None
    detected_type: str = UNRECOGNIZED

    @property
    def all_exceptions(self) -> list:
        out = list(self.file_exceptions)
        for r in self.records:
            out.extend(r.exceptions)
        return out


# ---------------------------------------------------------------------------
# Value parsers
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")
_TIME_FORMATS = ("%I:%M%p", "%I:%M %p", "%H:%M", "%H:%M:%S", "%I:%M:%S%p", "%I:%M:%S %p")


def parse_date(raw: str):
    """-> (iso string | None, problem | None). Blank and '/  /' placeholders are empty, not errors."""
    t = (raw or "").strip()
    if not t or re.fullmatch(r"[\s/\-]*", t):
        return None, None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(t, fmt).date()
        except ValueError:
            continue
        if d.year < 1990 or d.year > 2100:
            return None, f"date {t!r} reads as year {d.year}"
        return d.isoformat(), None
    return None, f"unreadable date {t!r}"


def parse_time(raw: str):
    t = re.sub(r"\s+", " ", (raw or "").strip()).upper()
    if not t:
        return None, None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(t, fmt).strftime("%H:%M"), None
        except ValueError:
            continue
    return None, f"unreadable time {raw.strip()!r}"


def _money_str(d: Decimal) -> str:
    q = d.quantize(Decimal("0.01"))
    return str(q) if q == d else format(d, "f")


def parse_money(raw: str):
    """-> (canonical string | None, problem | None, was_parenthesised). Blank is empty, not zero."""
    t = (raw or "").strip()
    if not t:
        return None, None, False
    neg = t.startswith("(") and t.endswith(")")
    core = t[1:-1] if neg else t
    core = core.replace("$", "").replace(",", "").replace(" ", "")
    try:
        d = Decimal(core)
    except InvalidOperation:
        return None, f"unreadable amount {t!r}", False
    if not d.is_finite():
        return None, f"unreadable amount {t!r}", False
    return _money_str(-d if neg else d), None, neg


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_iso(v) -> bool:
    return bool(v) and bool(_ISO.fullmatch(str(v)))


def _is_num(v) -> bool:
    return v is not None and bool(re.fullmatch(r"-?\d+(\.\d+)?", str(v)))


def _hash(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Detection and reading
# ---------------------------------------------------------------------------

def detect_type(norm_headers: list) -> str:
    hs = set(norm_headers)
    if "sequential id" in hs and "transaction date" in hs and "amount" in hs:
        return PAYMENTS
    has_stock = any(a in hs for a in STOCK_ALIASES)
    has_vin = any(a in hs for a in VIN_ALIASES)
    if has_stock and has_vin and ("total cost" in hs or "original cost" in hs):
        return VEHICLES
    looks_like_transactions = bool(hs & {"transaction date", "transaction type", "receipt number", "applied to contract"})
    if has_stock and any(a in hs for a in CUSTOMER_FIELDS["balance"]) and not looks_like_transactions:
        return CUSTOMERS
    return UNRECOGNIZED


def decode(data: bytes):
    """-> (text, encoding, problem | None). The original bytes are never altered."""
    try:
        return data.decode("utf-8-sig"), "utf-8", None
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252", \
            "The file is not UTF-8; it was read as Windows-1252. Check any accented text."


def _resolve(norm_headers: list, fields: dict):
    """-> ({canonical: index}, [duplicate header names], {used indexes})"""
    cols, dups, used = {}, [], set()
    for canon, aliases in fields.items():
        hits = [i for i, h in enumerate(norm_headers) if h in aliases]
        if hits:
            cols[canon] = hits[0]
            used.update(hits)
            if len(hits) > 1:
                dups.append(norm_headers[hits[0]])
    return cols, dups, used


def _read_rows(text: str):
    """-> (header_cells, [(line_number, cells)], blank_line_count, csv_problem | None)"""
    reader = csv.reader(io.StringIO(text, newline=""))
    rows, header, blanks, problem = [], None, 0, None
    try:
        while True:
            start = reader.line_num + 1
            try:
                cells = next(reader)
            except StopIteration:
                break
            if header is None:
                if not any(c.strip() for c in cells):
                    continue
                header = cells
                continue
            if not any(c.strip() for c in cells):
                blanks += 1
                continue
            rows.append((start, cells))
    except csv.Error as e:
        problem = f"The file is not readable as CSV near line {reader.line_num}: {e}"
    return header, rows, blanks, problem


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_export(data: bytes, requested_type: Optional[str] = None, declared: Optional[Declared] = None) -> ParsedFile:
    declared = declared or Declared()
    exc: list = []
    if b"\x00" in data:
        return ParsedFile(UNRECOGNIZED, None, "rejected", "binary", [],
                          file_exceptions=[Exc("binary_file", ERROR, "The file contains binary data; it is not a CSV export.")])
    text, encoding, enc_problem = decode(data)
    if enc_problem:
        exc.append(Exc("encoding_fallback", WARNING, enc_problem))
    header, rows, blanks, csv_problem = _read_rows(text)
    if csv_problem:
        exc.append(Exc("csv_unreadable", ERROR, csv_problem))
    if not header:
        exc.append(Exc("empty_file", ERROR, "The file has no header line."))
        return ParsedFile(UNRECOGNIZED, None, "rejected", encoding, [], file_exceptions=exc)

    # Header: drop trailing empty names, keep the rest in order.
    names = list(header)
    while names and not names[-1].strip():
        names.pop()
    hlen = len(names)
    headers = [n.strip() for n in names]
    norm = [_norm(n) for n in names]
    for i, n in enumerate(norm):
        if not n:
            headers[i] = f"(column {i + 1})"
            norm[i] = f"(column {i + 1})"
            exc.append(Exc("empty_header_name", WARNING, f"Column {i + 1} has no header name; it is kept as {headers[i]!r}.", row=1))

    detected = detect_type(norm)
    etype = detected
    if requested_type:
        if requested_type not in EXPORT_TYPES:
            exc.append(Exc("type_not_supported", ERROR, f"Unknown export type {requested_type!r}."))
            return ParsedFile(UNRECOGNIZED, None, "rejected", encoding, headers, file_exceptions=exc, detected_type=detected)
        if detected != requested_type:
            exc.append(Exc("type_mismatch", ERROR,
                           f"You chose {requested_type.replace('_', ' ')}, but the columns look like "
                           f"{detected.replace('_', ' ') if detected != UNRECOGNIZED else 'no export type this app recognises'}."))
            return ParsedFile(UNRECOGNIZED, None, "rejected", encoding, headers, file_exceptions=exc, detected_type=detected)
    if etype == UNRECOGNIZED:
        exc.append(Exc("unrecognized_layout", ERROR,
                       "The columns do not match a vehicle list, a customer-balance list or a transaction list. "
                       "Nothing was read from the rows. Headers found: " + ", ".join(headers)))
        return ParsedFile(UNRECOGNIZED, None, "rejected", encoding, headers, file_exceptions=exc, detected_type=detected)

    fields = {VEHICLES: VEHICLE_FIELDS, PAYMENTS: PAYMENT_FIELDS, CUSTOMERS: CUSTOMER_FIELDS}[etype]
    cols, dups, used = _resolve(norm, fields)
    for d in dups:
        exc.append(Exc("duplicate_header", WARNING, f"The header {d!r} appears more than once; the first column is used.", row=1))

    # Customer balances: if there is no recognised account-label column, look for the one whose values say "buy here".
    label_heuristic = False
    if etype == CUSTOMERS and "account_label" not in cols:
        best = None
        for i in range(hlen):
            if i in used:
                continue
            vals = [(c[i].strip() if i < len(c) else "") for _, c in rows]
            vals = [v for v in vals if v]
            if vals and sum(1 for v in vals if "buy here" in _dash(v)) / len(vals) >= 0.5:
                best = i
                break
        if best is not None:
            cols["account_label"] = best
            used.add(best)
            label_heuristic = True
            exc.append(Exc("label_column_inferred", INFO,
                           f"No account-type column name was recognised; the column {headers[best]!r} was used because its values "
                           f"say \"buy here\"."))

    # Required identity and value columns
    required = {VEHICLES: ["stock", "vin"], PAYMENTS: ["sequential_id", "transaction_date", "amount"],
                CUSTOMERS: ["stock", "balance"]}[etype]
    missing = [r for r in required if r not in cols]
    if missing:
        exc.append(Exc("missing_required_columns", ERROR,
                       f"Required column(s) not found for a {etype.replace('_', ' ')} file: {', '.join(missing)}. "
                       "Nothing was read from the rows. Headers found: " + ", ".join(headers)))
        return ParsedFile(etype, None, "rejected", encoding, headers, file_exceptions=exc, detected_type=detected)

    # Vehicle floor-plan columns: keep any column whose header mentions "floor".
    floor_cols = {}
    if etype == VEHICLES:
        for i, n in enumerate(norm):
            if i not in used and "floor" in n:
                floor_cols[i] = headers[i]
                used.add(i)
    extra_cols = [i for i in range(hlen) if i not in used]
    if extra_cols:
        exc.append(Exc("unexpected_columns", WARNING,
                       "Columns this app does not read (kept as source text only): " + ", ".join(headers[i] for i in extra_cols)))

    pf = ParsedFile(etype, f"{etype}/v1", "parsed", encoding, headers, file_exceptions=exc, detected_type=detected)
    trailing_rows = 0
    rtype = RECORD_TYPE[etype]
    money = {VEHICLES: VEHICLE_MONEY, PAYMENTS: PAYMENT_MONEY, CUSTOMERS: CUSTOMER_MONEY}[etype]
    dates = {VEHICLES: VEHICLE_DATES, PAYMENTS: PAYMENT_DATES, CUSTOMERS: CUSTOMER_DATES}[etype]

    for line, cells in rows:
        rec_exc: list = []
        width = len(cells)
        if width < hlen:
            rec_exc.append(Exc("malformed_row", ERROR, f"The row has {width} cells; the header has {hlen}.", row=line))
            cells = list(cells) + [""] * (hlen - width)
        elif width > hlen:
            extras = cells[hlen:]
            if any(c.strip() for c in extras):
                rec_exc.append(Exc("unexpected_nonempty_extra_column", ERROR,
                                   f"The row has {width - hlen} cell(s) beyond the header and at least one has text.", row=line))
            else:
                trailing_rows += 1
        raw = {headers[i]: cells[i] for i in range(hlen)}
        if width > hlen and any(c.strip() for c in cells[hlen:]):
            raw["(beyond header)"] = cells[hlen:]

        fv = {}
        for canon, idx in cols.items():
            v = cells[idx]
            if canon in money:
                s, prob, paren = parse_money(v)
                if prob:
                    rec_exc.append(Exc("unparseable_amount", ERROR, prob, row=line, field=headers[idx]))
                elif paren:
                    rec_exc.append(Exc("negative_amount", WARNING, f"Amount {v.strip()!r} is in parentheses and was read as negative.", row=line, field=headers[idx]))
                elif s is not None and Decimal(s) < 0:
                    if canon == "current_due":
                        rec_exc.append(Exc("negative_current_due", INFO, f"Current Due is {v.strip()!r}. Kept as reported; what a negative means "
                                           "(for example paid ahead) is not confirmed.", row=line, field=headers[idx]))
                    else:
                        rec_exc.append(Exc("negative_amount", WARNING, f"Negative amount {v.strip()!r}.", row=line, field=headers[idx]))
                fv[canon] = s if not prob else v.strip()
            elif canon in dates:
                s, prob = parse_date(v)
                if prob:
                    rec_exc.append(Exc("unparseable_date", ERROR, prob, row=line, field=headers[idx]))
                fv[canon] = s if not prob else v.strip()
            elif canon == "time_entered":
                s, prob = parse_time(v)
                if prob:
                    rec_exc.append(Exc("unparseable_time", ERROR, prob, row=line, field=headers[idx]))
                fv[canon] = s if not prob else v.strip()
            else:
                fv[canon] = v.strip()
        for i, name in floor_cols.items():
            s, prob, _ = parse_money(cells[i])
            if prob:
                rec_exc.append(Exc("unparseable_amount", ERROR, prob, row=line, field=name))
            fv[f"floor_plan:{name}"] = s if not prob else cells[i].strip()
        for i in extra_cols:
            fv[f"other:{headers[i]}"] = cells[i].strip()

        identity_field = "sequential_id" if etype == PAYMENTS else "stock"
        identity = fv.get(identity_field) or None
        if not identity:
            rec_exc.append(Exc("missing_identity", ERROR, f"No {identity_field.replace('_', ' ')} on this row, so it cannot be tracked.", row=line))

        rec = Record(line, rtype, identity, raw, fv, _hash(fv), None, rec_exc)
        for e in rec_exc:
            e.identity = identity
        _type_checks(etype, rec, declared, cols, floor_cols, label_heuristic)
        pf.records.append(rec)

    if trailing_rows:
        exc.append(Exc("trailing_empty_column", INFO, f"{trailing_rows} row(s) carry one empty cell beyond the header. Accepted (it is empty)."))
    if blanks:
        exc.append(Exc("blank_lines", INFO, f"{blanks} blank line(s) were skipped."))

    _duplicate_checks(etype, pf)
    _finish(etype, pf, declared)
    return pf


def _dash(s: str) -> str:
    return re.sub(r"[‐-―]", "-", s.lower())


# ---------------------------------------------------------------------------
# Per-type row checks
# ---------------------------------------------------------------------------

def _add(rec: Record, code: str, severity: str, message: str, fld: Optional[str] = None):
    rec.exceptions.append(Exc(code, severity, message, row=rec.row_number, identity=rec.identity, field=fld))


def _type_checks(etype, rec, declared, cols, floor_cols, label_heuristic):
    f = rec.fields
    if etype == VEHICLES:
        vin = f.get("vin", "")
        if not vin:
            _add(rec, "vin_missing", WARNING, "No VIN on this vehicle.", "vin")
        elif len(vin) != 17:
            _add(rec, "vin_unusual_length", WARNING, f"VIN {vin!r} has {len(vin)} characters, not 17. Kept as written.", "vin")
        for k in ("total_cost", "original_cost"):
            if k in f and f[k] is not None and re.fullmatch(r"-?\d+(\.\d+)?", str(f[k])) and Decimal(f[k]) == 0:
                _add(rec, "zero_cost_reported", INFO,
                     f"{k.replace('_', ' ').title()} is reported as 0. Kept as 0; whether that means no cost is not confirmed.", k)
                break
        for name in [n for n in f if n.startswith("floor_plan:")]:
            if f[name] is not None and re.fullmatch(r"-?\d+(\.\d+)?", str(f[name])) and Decimal(f[name]) == 0:
                _add(rec, "floor_plan_zero_reported", INFO, f"{name[11:]} is reported as 0. Kept as 0; this does not confirm there is no debt.", name)
    elif etype == CUSTOMERS:
        if "vin" in cols and not f.get("vin"):
            _add(rec, "vin_missing", INFO, "No VIN on this balance record, so it cannot be cross-checked by VIN.", "vin")
        comp = [f.get(k) for k in ("principal_balance", "fees_owed", "repairs_balance")]
        if _is_num(f.get("balance")) and all(_is_num(c) for c in comp):
            if Decimal(f["balance"]) != sum(Decimal(c) for c in comp):
                _add(rec, "balance_components_differ", WARNING,
                     f"Balance {f['balance']} is not Principal Balance + Fees Owed + Repairs Balance ({sum(Decimal(c) for c in comp)}). "
                     "Both are kept as reported.", "balance")
        if "account_label" in f:
            lab = _dash(f.get("account_label", ""))
            if not ("buy here" in lab and "pay here" in lab):
                _add(rec, "label_not_bhph", WARNING, f"Account label {f.get('account_label')!r} is not Buy Here-Pay Here.", "account_label")
    elif etype == PAYMENTS:
        desc = re.sub(r"\s+", " ", f.get("description", "").casefold()).strip()
        cls = DESCRIPTION_CLASS.get(desc, UNCLASSIFIED)
        rec.entry_class = cls
        if cls == UNCLASSIFIED:
            _add(rec, "unclassified_description", WARNING,
                 f"Description {f.get('description')!r} is not one this app groups; kept as written, not counted as a payment or a fee.", "description")
        receipt = f.get("receipt_number", "")
        receipt_zero = receipt.strip("0") == "" if receipt else True
        if cls == PAYMENT and receipt_zero:
            _add(rec, "payment_without_receipt_number", WARNING, "A payment entry has receipt number 0 or blank.", "receipt_number")
        applied = _applied_total(f)
        if cls == FEE:
            # In the October export every Late Fee entry has nothing applied to the contract, fees or anything
            # else: consistent with an assessment, not money received. Anything applied is surfaced, not assumed.
            if applied is not None and applied != 0:
                _add(rec, "fee_with_applied_amount", WARNING,
                     f"A Late Fee entry has {applied} applied across the Applied-to columns; it is not treated as an assessment-only entry without confirmation.", "applied_contract")
            if not receipt_zero:
                _add(rec, "fee_with_receipt_number", INFO, f"A Late Fee entry carries receipt number {receipt}.", "receipt_number")
        if cls == PAYMENT and applied is not None and f.get("amount") is not None and re.fullmatch(r"-?\d+(\.\d+)?", f["amount"]) \
                and applied != Decimal(f["amount"]):
            _add(rec, "payment_applied_total_differs", WARNING,
                 f"The Applied-to columns total {applied} but the Amount is {f['amount']}.", "amount")
        if cls == CANCELLED:
            _add(rec, "cancelled_entry", WARNING,
                 "Frazer labels this entry CANCELLED. It is kept, not counted as a payment or a fee, and its treatment is not confirmed.", "description")
        if not f.get("stock") and cls != CANCELLED:
            _add(rec, "missing_stock", WARNING, "No stock number on this entry.", "stock")
        td, de = f.get("transaction_date"), f.get("date_entered")
        for nm, val in (("transaction_date", td), ("date_entered", de)):
            if not val:
                _add(rec, "date_missing", WARNING, f"{nm.replace('_', ' ').title()} is blank.", nm)
        td = td if _is_iso(td) else None          # an unreadable date is already an error; do no arithmetic on it
        de = de if _is_iso(de) else None
        if td and de and de < td:
            _add(rec, "entered_before_transaction", WARNING, f"Date Entered {de} is before Transaction Date {td}.", "date_entered")
        if declared.export_date and td and td > declared.export_date.isoformat():
            _add(rec, "dated_after_export", WARNING, f"Transaction Date {td} is after the declared export date.", "transaction_date")
        if td and declared.coverage_start and td < declared.coverage_start.isoformat():
            _add(rec, "outside_declared_coverage", WARNING, f"Transaction Date {td} is before the declared coverage start.", "transaction_date")
        if td and declared.coverage_end and td > declared.coverage_end.isoformat():
            _add(rec, "outside_declared_coverage", WARNING, f"Transaction Date {td} is after the declared coverage end.", "transaction_date")


def _applied_total(f: dict):
    """Sum of the Applied-to columns, or None when any of them is unreadable or absent."""
    keys = ("applied_contract", "applied_interest", "applied_fees", "applied_pickup_note",
            "applied_other_receivables", "applied_repairs", "applied_recurring_fees")
    vals = [f.get(k) for k in keys if k in f]
    if not vals or any(v is None or not re.fullmatch(r"-?\d+(\.\d+)?", str(v)) for v in vals):
        return None
    return sum((Decimal(v) for v in vals), Decimal(0))


def _duplicate_checks(etype, pf: ParsedFile):
    groups = defaultdict(list)
    for r in pf.records:
        if r.identity:
            groups[r.identity].append(r)
    for ident, recs in groups.items():
        if len(recs) < 2:
            continue
        first = recs[0]
        lines = ", ".join(str(r.row_number) for r in recs)
        for r in recs[1:]:
            if r.content_hash == first.content_hash:
                _add(r, "duplicate_identical", WARNING, f"Identity {ident!r} repeats with identical content (lines {lines}).")
            else:
                _add(r, "duplicate_conflict", ERROR, f"Identity {ident!r} repeats with different content (lines {lines}).")
                if not any(e.code == "duplicate_conflict" for e in first.exceptions):
                    _add(first, "duplicate_conflict", ERROR, f"Identity {ident!r} repeats with different content (lines {lines}).")
    if etype == VEHICLES:
        by_vin = defaultdict(list)
        for r in pf.records:
            v = r.fields.get("vin", "").upper()
            if v:
                by_vin[v].append(r)
        for v, recs in by_vin.items():
            stocks = sorted({r.identity for r in recs if r.identity})
            if len(stocks) > 1:
                for r in recs:
                    _add(r, "duplicate_vin", WARNING,
                         f"VIN {r.fields.get('vin')} appears under stock numbers {', '.join(stocks)}. The vehicles are not merged.", "vin")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _sum(vals):
    return sum((Decimal(v) for v in vals if v is not None and re.fullmatch(r"-?\d+(\.\d+)?", str(v))), Decimal(0))


def _finish(etype, pf: ParsedFile, declared: Declared):
    recs = pf.records
    err_rows = {r.row_number for r in recs if any(e.severity == ERROR for e in r.exceptions)}
    s = {"records": len(recs), "rows_with_errors": len(err_rows), "headers": pf.headers}
    if etype == VEHICLES:
        stocks = [r.identity for r in recs if r.identity]
        vins = [r.fields.get("vin", "").upper() for r in recs if r.fields.get("vin")]
        vin_groups = defaultdict(set)
        for r in recs:
            if r.fields.get("vin") and r.identity:
                vin_groups[r.fields["vin"].upper()].add(r.identity)
        zero = [r for r in recs if any(e.code == "zero_cost_reported" for e in r.exceptions)]
        floor_names = sorted({k[11:] for r in recs for k in r.fields if k.startswith("floor_plan:")})
        floor_vals = [(n, [r.fields.get(f"floor_plan:{n}") for r in recs]) for n in floor_names]
        s.update(
            unique_stock=len(set(stocks)),
            duplicate_vin_groups=[{"vin": v, "stocks": sorted(st)} for v, st in sorted(vin_groups.items()) if len(st) > 1],
            vin_blank=sum(1 for r in recs if not r.fields.get("vin")),
            zero_cost_records=len(zero),
            total_cost_sum_as_reported=str(_sum(r.fields.get("total_cost") for r in recs)),
            original_cost_sum_as_reported=str(_sum(r.fields.get("original_cost") for r in recs)),
            stock_with_leading_zero=sum(1 for x in stocks if re.match(r"^0\d", x)),
            stock_letter_prefixed=sum(1 for x in stocks if re.match(r"^[A-Za-z]", x)),
            purchase_date_min=min((r.fields["purchase_date"] for r in recs if r.fields.get("purchase_date")), default=None),
            purchase_date_max=max((r.fields["purchase_date"] for r in recs if r.fields.get("purchase_date")), default=None),
            floor_plan_columns=[{"header": n, "records_with_value": sum(1 for v in vals if v not in (None, "")),
                                 "records_blank": sum(1 for v in vals if v in (None, "")),
                                 "records_zero": sum(1 for v in vals if v not in (None, "") and re.fullmatch(r"-?\d+(\.\d+)?", str(v)) and Decimal(v) == 0),
                                 "sum_as_reported": str(_sum(vals))} for n, vals in floor_vals],
        )
    elif etype == CUSTOMERS:
        labels = Counter(r.fields.get("account_label", "(no label column)") or "(blank)" for r in recs)
        ident = [r for r in recs if r.identity]
        s.update(
            records_with_stock=len(ident),
            records_without_stock=len(recs) - len(ident),
            balance_sum_as_reported=str(_sum(r.fields.get("balance") for r in ident)),
            balance_sum_excluded_rows=str(_sum(r.fields.get("balance") for r in recs if not r.identity)),
            account_labels=dict(labels),
            unique_stock=len({r.identity for r in ident}),
            principal_sum_as_reported=str(_sum(r.fields.get("principal_balance") for r in ident)),
            fees_owed_sum_as_reported=str(_sum(r.fields.get("fees_owed") for r in ident)),
            repairs_sum_as_reported=str(_sum(r.fields.get("repairs_balance") for r in ident)),
            records_with_fees_owed=sum(1 for r in ident if _is_num(r.fields.get("fees_owed")) and Decimal(r.fields["fees_owed"]) != 0),
            records_past_due=sum(1 for r in ident if _is_num(r.fields.get("days_past_due"))),
            balance_equals_components=sum(1 for r in ident if not any(e.code == "balance_components_differ" for e in r.exceptions)
                                          and "principal_balance" in r.fields),
            sale_date_min=min((r.fields["sale_date"] for r in ident if _is_iso(r.fields.get("sale_date"))), default=None),
            sale_date_max=max((r.fields["sale_date"] for r in ident if _is_iso(r.fields.get("sale_date"))), default=None),
        )
    elif etype == PAYMENTS:
        by_class = defaultdict(lambda: {"count": 0, "sum": Decimal(0)})
        by_desc, by_method, by_pair = (defaultdict(lambda: {"count": 0, "sum": Decimal(0)}) for _ in range(3))
        for r in recs:
            amt = Decimal(r.fields["amount"]) if r.fields.get("amount") and re.fullmatch(r"-?\d+(\.\d+)?", r.fields["amount"]) else Decimal(0)
            for d, k in ((by_class, r.entry_class), (by_desc, r.fields.get("description", "")),
                         (by_method, r.fields.get("transaction_method", "")),
                         (by_pair, f"{r.fields.get('description', '')} | {r.fields.get('transaction_method', '')}")):
                d[k]["count"] += 1
                d[k]["sum"] += amt
        tdates = [r.fields["transaction_date"] for r in recs if _is_iso(r.fields.get("transaction_date"))]
        edates = [r.fields["date_entered"] for r in recs if _is_iso(r.fields.get("date_entered"))]
        pf.observed_start = date.fromisoformat(min(tdates)) if tdates else None
        pf.observed_end = date.fromisoformat(max(tdates)) if tdates else None
        fin = lambda d: {k: {"count": v["count"], "sum": str(v["sum"])} for k, v in sorted(d.items(), key=lambda kv: kv[0])}
        zero_receipts = Counter(r.entry_class for r in recs if (r.fields.get("receipt_number", "").strip("0") == ""))
        s.update(
            by_class=fin(by_class), by_description=fin(by_desc), by_method_code=fin(by_method), by_description_and_method=fin(by_pair),
            transaction_date_min=min(tdates, default=None), transaction_date_max=max(tdates, default=None),
            date_entered_min=min(edates, default=None), date_entered_max=max(edates, default=None),
            unique_sequential_ids=len({r.identity for r in recs if r.identity}),
            sequential_id_unique_in_file=len({r.identity for r in recs if r.identity}) == len([r for r in recs if r.identity]),
            receipt_zero_by_class=dict(zero_receipts),
            fee_entries_with_nothing_applied=sum(1 for r in recs if r.entry_class == FEE and _applied_total(r.fields) == 0),
            payment_entries_fully_applied=sum(1 for r in recs if r.entry_class == PAYMENT and _applied_total(r.fields) is not None
                                              and _is_num(r.fields.get("amount")) and _applied_total(r.fields) == Decimal(r.fields["amount"])),
            receipt_numbers_distinct=len({r.fields.get("receipt_number") for r in recs if r.fields.get("receipt_number", "").strip("0")}),
        )
        nums = sorted({int(r.identity) for r in recs if r.identity and r.identity.isdigit()})
        if len(nums) >= 2:
            gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
            s.update(sequential_id_min=str(nums[0]), sequential_id_max=str(nums[-1]), sequential_id_gaps=gaps[:50])
            if gaps:
                pf.file_exceptions.append(Exc("sequential_id_gap", INFO,
                    f"Sequential IDs run {nums[0]} to {nums[-1]}; {len(gaps)} number(s) in between are not in this file "
                    f"({', '.join(map(str, gaps[:10]))}{' ...' if len(gaps) > 10 else ''}). A gap does not show that anything is missing: "
                    "the number may belong to an entry outside this export or one that was removed. Not confirmed (DEC-33)."))
        methods_multi = defaultdict(set)
        for r in recs:
            m = r.fields.get("transaction_method", "")
            if m:
                methods_multi[m].add(r.fields.get("description", ""))
        for m, ds in sorted(methods_multi.items()):
            if len(ds) > 1:
                pf.file_exceptions.append(Exc("method_code_on_several_descriptions", INFO,
                    f"Method code {m!r} appears on several descriptions ({', '.join(sorted(ds))}). Its meaning is not confirmed; nothing is inferred from it."))
        # coverage
        if not (declared.coverage_start and declared.coverage_end):
            pf.file_exceptions.append(Exc("coverage_not_declared", WARNING, "No coverage dates were declared for this transaction export."))
        elif pf.observed_start and pf.observed_end and (pf.observed_start > declared.coverage_start or pf.observed_end < declared.coverage_end):
            pf.file_exceptions.append(Exc("coverage_observed_narrower", INFO,
                f"Declared coverage {declared.coverage_start} to {declared.coverage_end}; the transactions run {pf.observed_start} to {pf.observed_end}."))
    if declared.from_filename:
        what = {"export_date": "export date", "coverage_start": "coverage start", "coverage_end": "coverage end"}
        pf.file_exceptions.append(Exc("declared_from_filename", INFO,
            "The " + " and ".join(what[k] for k in declared.from_filename if k != "coverage_end" or "coverage_start" not in declared.from_filename)
            + " were read from the filename. A filename is a label chosen at export time, not proof of what the rows contain."))
    if etype in (VEHICLES, CUSTOMERS) and not declared.export_date:
        pf.file_exceptions.append(Exc("export_date_not_declared", WARNING, "No export date was declared for this snapshot."))
    if etype == CUSTOMERS and not any("account_label" in r.fields for r in recs):
        pf.file_exceptions.append(Exc("no_account_label_column", INFO,
            "No account-type column was found, so the Buy Here-Pay Here label could not be checked."))
    s["exceptions"] = dict(Counter(e.severity for e in pf.all_exceptions))
    pf.summary = s
