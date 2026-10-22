"""
One-time LOCAL data-enrichment script: populate Unit.year/make/model for
historical vehicle-acquisition Units from their associated historical
Purchase.notes, using deterministic local text parsing only. No LLM, no
external lookup, no vehicle database.

Scope: only Units linked to a Purchase whose source_reference starts with
"historical-import:" AND whose category is a vehicle-acquisition category
(is_vehicle_purchase(), reused from app.services.purchase_service, not
reimplemented). Every other Unit in the database is untouched. This does
not change normal Purchase/Unit creation behavior at all -- it is a
read-then-patch utility that runs once, outside the regular app workflow.

Usage:
    python -m app.scripts.enrich_historical_units --dry-run
    python -m app.scripts.enrich_historical_units --commit

Same CLI safety convention as the historical purchase importer: neither
flag or both flags refuses to run. Dry run makes zero database writes
(same SAVEPOINT-per-row + unconditional final rollback pattern).

Conservative by design:
  - Never overwrites a field that already has a non-null value. This pass
    only ever fills in a currently-NULL year/make/model; it never
    "corrects" an existing one.
  - Only proposes a field when it can be extracted by a simple, explicit
    rule below -- never guesses between multiple candidates.

  Year: a single 4-digit number in [1995, 2027] found anywhere in the
  text. Zero or 2+ distinct in-range 4-digit numbers -> unresolved. Chosen
  range covers every year actually observed in the real historical notes
  (2006-2020) with headroom, while rejecting obvious typos like "1015" or
  "2100". 2-digit years are deliberately NOT supported -- none of the 154
  real historical notes need it, and a bare 2-digit number (battery
  voltage, a trim code, a dollar figure) is too easily something else to
  interpret safely without more context than plain text parsing gives.

  Make: matched against a small known-make list (KNOWN_MAKES below), with
  a few common abbreviations observed in the real data (Chevy/Chev ->
  Chevrolet, VW -> Volkswagen). No fuzzy matching, no typo correction --
  "FOR FUSION" (a typo for "FORD FUSION" seen in the real data) is left
  unresolved for make rather than guessed.

  Model: the token immediately following the recognized make, skipping a
  repeated make word ("Mazda Mazda 3" -> 3, "Mazda three" -> 3) and
  skipping a known color word. A short (<=3 letter) alpha code is
  uppercased and combined with an immediately-following 2-4 digit number
  ("IS" + "250" -> "IS250", matching Lexus/Acura/Mercedes naming; "RDX"
  alone with nothing following stays "RDX"). Anything after that first
  token (trim level, series/payload number, drivetrain) is dropped -- same
  simplification as the task's own "GMC Acadia Denali -> Acadia" example,
  not an attempt to fully classify trims.
"""
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.purchase import Purchase
from app.models.unit import Unit
from app.services.purchase_service import is_vehicle_purchase

MIN_YEAR = 1995
MAX_YEAR = 2027

KNOWN_MAKES = {
    "ford": "Ford",
    "chevrolet": "Chevrolet", "chevy": "Chevrolet", "chev": "Chevrolet",
    "gmc": "GMC",
    "buick": "Buick",
    "nissan": "Nissan",
    "toyota": "Toyota",
    "honda": "Honda",
    "hyundai": "Hyundai",
    "kia": "Kia",
    "lexus": "Lexus",
    "chrysler": "Chrysler",
    "dodge": "Dodge",
    "jeep": "Jeep",
    "mazda": "Mazda",
    "acura": "Acura",
    "mercedes": "Mercedes-Benz", "mercedes-benz": "Mercedes-Benz",
    "bmw": "BMW",
    "audi": "Audi",
    "volkswagen": "Volkswagen", "vw": "Volkswagen",
    "subaru": "Subaru",
    "mitsubishi": "Mitsubishi",
    "volvo": "Volvo",
    "cadillac": "Cadillac",
    "lincoln": "Lincoln",
    "infiniti": "Infiniti",
    "pontiac": "Pontiac",
    "saturn": "Saturn",
    "mercury": "Mercury",
    "ram": "Ram",
    "tesla": "Tesla",
}

COLOR_WORDS = {
    "white", "black", "blk", "silver", "gray", "grey", "blue", "red",
    "green", "tan", "beige", "maroon", "navy", "brown", "burgundy", "gold",
    "whote", "maroob",  # observed typos in the real data
}


# ---------------------------------------------------------------------------
# Parsing — explicit rules, no vehicle database.
# ---------------------------------------------------------------------------

def find_year(text: str) -> tuple:
    """Returns (year_or_None, reason)."""
    candidates = sorted({
        int(n) for n in re.findall(r"\d+", text)
        if len(n) == 4 and MIN_YEAR <= int(n) <= MAX_YEAR
    })
    if len(candidates) == 1:
        return candidates[0], f"single 4-digit year {candidates[0]} in range"
    if len(candidates) == 0:
        return None, "no 4-digit number in the accepted year range"
    return None, f"ambiguous: {len(candidates)} distinct in-range years found {candidates}"


def _tokenize_for_make_model(text: str) -> list:
    # Split letter<->digit boundaries so glued text like "2013Ford" or
    # "UX200" tokenizes the same as if it had been written with a space.
    spaced = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Za-z])(\d)", r"\1 \2", spaced)
    raw = re.split(r"[\s,./\-–—]+", spaced)
    return [t for t in raw if t]


def find_make_and_model(text: str) -> tuple:
    """Returns (make_or_None, model_or_None, reason)."""
    tokens = _tokenize_for_make_model(text)

    make_idx = None
    make_canonical = None
    for i, tok in enumerate(tokens):
        canonical = KNOWN_MAKES.get(tok.lower())
        if canonical:
            make_idx = i
            make_canonical = canonical
            break

    if make_idx is None:
        return None, None, "no known make token found"

    j = make_idx + 1
    while j < len(tokens):
        key = tokens[j].lower()
        if KNOWN_MAKES.get(key) == make_canonical:
            j += 1  # repeated make word, e.g. "Mazda Mazda 3"
            continue
        if key in COLOR_WORDS:
            j += 1
            continue
        break

    if j >= len(tokens):
        return make_canonical, None, "make found but nothing follows it"

    candidate = tokens[j]

    if candidate.isdigit():
        return make_canonical, candidate, "make + numeric model token"

    if candidate.isalpha() and len(candidate) <= 3:
        model = candidate.upper()
        if j + 1 < len(tokens) and tokens[j + 1].isdigit() and 2 <= len(tokens[j + 1]) <= 4:
            model = f"{model}{tokens[j + 1]}"
        return make_canonical, model, "make + short alpha code model token"

    if candidate.isalpha():
        return make_canonical, candidate.capitalize(), "make + alpha model token"

    return make_canonical, None, "make found but next token is not a usable model candidate"


# ---------------------------------------------------------------------------
# Per-unit proposal
# ---------------------------------------------------------------------------

@dataclass
class Proposal:
    """
    Everything needed for reporting is captured here as plain values at
    build time, never re-read from the ORM objects afterward. Dry run ends
    with a db.rollback() (which expires ORM state) and main() closes the
    session before printing -- reporting must not depend on either still
    being alive. unit/purchase are kept only for apply_proposal(), which
    always runs while the session is still open.
    """
    unit: Unit
    purchase: Purchase
    unit_id: str
    stock_number: str
    source_reference: str
    notes: str
    existing_year: Optional[int]
    existing_make: Optional[str]
    existing_model: Optional[str]
    proposed_year: Optional[int]
    proposed_make: Optional[str]
    proposed_model: Optional[str]
    year_reason: str
    make_model_reason: str
    year_skipped_existing: bool
    make_skipped_existing: bool
    model_skipped_existing: bool

    @property
    def action(self) -> str:
        applied = [
            self.proposed_year is not None and not self.year_skipped_existing,
            self.proposed_make is not None and not self.make_skipped_existing,
            self.proposed_model is not None and not self.model_skipped_existing,
        ]
        if all(applied):
            return "update"
        if any(applied):
            return "partial"
        return "unresolved"

    @property
    def reason(self) -> str:
        return f"year: {self.year_reason}; make/model: {self.make_model_reason}"


def build_proposal(unit: Unit, purchase: Purchase) -> Proposal:
    notes = purchase.description or ""
    year, year_reason = find_year(notes)
    make, model, mm_reason = find_make_and_model(notes)

    return Proposal(
        unit=unit,
        purchase=purchase,
        unit_id=unit.unit_id,
        stock_number=purchase.stock_number or "",
        source_reference=purchase.source_reference,
        notes=notes,
        existing_year=unit.year,
        existing_make=unit.make,
        existing_model=unit.model,
        proposed_year=year,
        proposed_make=make,
        proposed_model=model,
        year_reason=year_reason,
        make_model_reason=mm_reason,
        year_skipped_existing=unit.year is not None,
        make_skipped_existing=bool(unit.make),
        model_skipped_existing=bool(unit.model),
    )


def historical_vehicle_units(db: Session) -> list:
    """(unit, purchase) pairs for every historical vehicle-acquisition Unit."""
    purchases = (
        db.query(Purchase)
        .filter(Purchase.source_reference.like("historical-import:%"))
        .filter(Purchase.unit_id.isnot(None))
        .all()
    )
    vehicle_purchases = [p for p in purchases if is_vehicle_purchase(p.category)]
    seen_units = set()
    pairs = []
    for p in vehicle_purchases:
        if p.unit_id in seen_units:
            continue
        seen_units.add(p.unit_id)
        pairs.append((p.unit, p))
    return pairs


def apply_proposal(proposal: Proposal) -> None:
    if proposal.proposed_year is not None and not proposal.year_skipped_existing:
        proposal.unit.year = proposal.proposed_year
    if proposal.proposed_make is not None and not proposal.make_skipped_existing:
        proposal.unit.make = proposal.proposed_make
    if proposal.proposed_model is not None and not proposal.model_skipped_existing:
        proposal.unit.model = proposal.proposed_model


# ---------------------------------------------------------------------------
# Summary / reporting
# ---------------------------------------------------------------------------

@dataclass
class Summary:
    units_inspected: int = 0
    already_complete: int = 0
    year_found: int = 0
    make_found: int = 0
    model_found: int = 0
    all_three_would_populate: int = 0
    partially_populated: int = 0
    unresolved: int = 0
    skipped_existing_data: int = 0
    proposals: list = field(default_factory=list)


def summarize(proposals: list) -> Summary:
    s = Summary()
    s.units_inspected = len(proposals)
    for p in proposals:
        if p.existing_year is not None and p.existing_make and p.existing_model:
            s.already_complete += 1
            continue
        if p.proposed_year is not None:
            s.year_found += 1
        if p.proposed_make is not None:
            s.make_found += 1
        if p.proposed_model is not None:
            s.model_found += 1
        if p.year_skipped_existing or p.make_skipped_existing or p.model_skipped_existing:
            s.skipped_existing_data += 1

        action = p.action
        if action == "update":
            s.all_three_would_populate += 1
        elif action == "partial":
            s.partially_populated += 1
        else:
            s.unresolved += 1
    s.proposals = proposals
    return s


def print_summary(summary: Summary) -> None:
    print()
    print("Historical acquisition Units inspected:", summary.units_inspected)
    print("Units already having complete year/make/model:", summary.already_complete)
    print("Units with year found:", summary.year_found)
    print("Units with make found:", summary.make_found)
    print("Units with model found:", summary.model_found)
    print("Units where all 3 would be populated:", summary.all_three_would_populate)
    print("Units partially populated:", summary.partially_populated)
    print("Units unresolved:", summary.unresolved)
    print("Units skipped because existing data was present:", summary.skipped_existing_data)


def write_review_csv(summary: Summary, out_path: Path) -> None:
    import csv
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "unit_id", "stock_number", "source_reference", "notes",
            "proposed_year", "proposed_make", "proposed_model", "reason", "action",
        ])
        for p in summary.proposals:
            writer.writerow([
                p.unit_id, p.stock_number, p.source_reference,
                p.notes, p.proposed_year or "", p.proposed_make or "", p.proposed_model or "",
                p.reason, p.action,
            ])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(db: Session, dry_run: bool) -> Summary:
    pairs = historical_vehicle_units(db)
    proposals = [build_proposal(unit, purchase) for unit, purchase in pairs]

    if not dry_run:
        for p in proposals:
            if p.action in ("update", "partial"):
                apply_proposal(p)
        db.commit()

    return summarize(proposals)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time enrichment of historical Unit year/make/model from Purchase notes. "
                     "Requires --dry-run or --commit."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--review-csv", type=Path, default=None,
        help="Optional path to write the full per-unit review table as CSV.",
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.commit:
        print("Error: pass only one of --dry-run or --commit, not both.", file=sys.stderr)
        return 2
    if not args.dry_run and not args.commit:
        print("Error: pass --dry-run or --commit explicitly. Refusing to guess.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        if args.dry_run:
            summary = run(db, dry_run=True)
            db.rollback()
        else:
            summary = run(db, dry_run=False)
    finally:
        db.close()

    print_summary(summary)

    if args.review_csv:
        write_review_csv(summary, args.review_csv)
        print()
        print(f"Review table written to: {args.review_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
