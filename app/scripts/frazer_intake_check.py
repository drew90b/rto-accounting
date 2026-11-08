"""
Read a Frazer export and report what is in it. No database, no network, nothing written.

Use it to check a file before (or instead of) uploading it, and to confirm the figures you expect:

    python -m app.scripts.frazer_intake_check FILE [FILE ...] \\
        [--type vehicles|customer_balances|payments] [--export-date 2026-11-08] \\
        [--coverage-start 2026-11-01 --coverage-end 2026-11-08]

It uses the same parser as the Frazer Intake screens (app/services/frazer_parse.py), so what it prints is what
the upload page will say. Customer names are never printed. See docs/frazer_intake.md.
"""
import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from app.services import frazer_parse as fp


def _money(v) -> str:
    return f"${float(v):,.2f}" if v not in (None, "") else "-"


def describe(path: Path, pf: fp.ParsedFile) -> str:
    s, out = pf.summary, []
    w = out.append
    w(f"\n== {path.name}")
    w(f"   detected type : {pf.detected_type}   status: {pf.status}   encoding: {pf.encoding}")
    w(f"   headers       : {len(pf.headers)} ({', '.join(pf.headers[:6])}{', ...' if len(pf.headers) > 6 else ''})")
    if pf.status != "parsed":
        for e in pf.file_exceptions:
            w(f"   [{e.severity}] {e.code}: {e.message}")
        return "\n".join(out)
    w(f"   records       : {s['records']}   rows with errors: {s['rows_with_errors']}")
    if pf.export_type == fp.VEHICLES:
        w(f"   distinct stock: {s['unique_stock']}   leading-zero stock: {s['stock_with_leading_zero']}   letter-prefixed: {s['stock_letter_prefixed']}")
        w(f"   VINs repeated under several stock numbers: {len(s['duplicate_vin_groups'])}"
          + "".join(f"\n      {g['vin']} -> stock {', '.join(g['stocks'])}" for g in s["duplicate_vin_groups"]))
        w(f"   VIN blank: {s['vin_blank']}   reported zero cost: {s['zero_cost_records']}   Total Cost summed as reported: {_money(s['total_cost_sum_as_reported'])}")
        for c in s["floor_plan_columns"]:
            w(f"   floor-plan column '{c['header']}': {c['records_with_value']} with a value, {c['records_zero']} zero, {c['records_blank']} blank, sum {_money(c['sum_as_reported'])}")
        if not s["floor_plan_columns"]:
            w("   no floor-plan column found")
    elif pf.export_type == fp.CUSTOMERS:
        w(f"   with stock: {s['records_with_stock']} (distinct {s['unique_stock']})   without stock: {s['records_without_stock']}")
        w(f"   balances summed as reported: {_money(s['balance_sum_as_reported'])}   (left out, no stock: {_money(s['balance_sum_excluded_rows'])})")
        w(f"   account labels: {s['account_labels']}")
        w(f"   principal {_money(s['principal_sum_as_reported'])}, fees owed {_money(s['fees_owed_sum_as_reported'])} ({s['records_with_fees_owed']} accounts), "
          f"repairs {_money(s['repairs_sum_as_reported'])}; balance = components on {s['balance_equals_components']} of {s['records']}")
        w(f"   sale dates {s['sale_date_min']} to {s['sale_date_max']}; accounts with Days past Due filled: {s['records_past_due']}")
    else:
        w(f"   Sequential ID unique in file: {s['sequential_id_unique_in_file']} ({s['unique_sequential_ids']} distinct)")
        w(f"   transaction dates {s['transaction_date_min']} to {s['transaction_date_max']}; entry dates {s['date_entered_min']} to {s['date_entered_max']}")
        w("   by Frazer's own description + method code (never added across groups):")
        for k, v in s["by_description_and_method"].items():
            w(f"      {k:34} {v['count']:4} entries  {_money(v['sum']):>12}")
        w("   by group:")
        for k, v in s["by_class"].items():
            w(f"      {fp.CLASS_LABEL.get(k, k):34} {v['count']:4} entries  {_money(v['sum']):>12}   receipt 0/blank: {s['receipt_zero_by_class'].get(k, 0)}")
        w(f"   late-fee entries with nothing applied: {s['fee_entries_with_nothing_applied']}; payment entries fully applied: {s['payment_entries_fully_applied']}")
    codes = Counter(e.code for e in pf.all_exceptions)
    lines = {}
    for e in pf.all_exceptions:
        lines.setdefault(e.code, []).append(e.row)
    w("   exceptions:")
    for code, n in codes.most_common():
        sev = next(e.severity for e in pf.all_exceptions if e.code == code)
        where = [x for x in lines[code] if x]
        w(f"      [{sev:7}] {code:34} x{n}" + (f"  lines {where[:8]}{' ...' if len(where) > 8 else ''}" if where else "  (whole file)"))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--type", choices=fp.EXPORT_TYPES)
    ap.add_argument("--export-date", type=date.fromisoformat)
    ap.add_argument("--coverage-start", type=date.fromisoformat)
    ap.add_argument("--coverage-end", type=date.fromisoformat)
    args = ap.parse_args(argv)
    declared = fp.Declared(args.export_date, args.coverage_start, args.coverage_end)
    rc = 0
    for path in args.files:
        if not path.is_file():
            print(f"\n== {path}: not found", file=sys.stderr)
            rc = 2
            continue
        pf = fp.parse_export(path.read_bytes(), args.type, fp.declared_from_filename(path.name, declared))
        print(describe(path, pf))
        if pf.status != "parsed":
            rc = max(rc, 1)
    print("\nNo database was used and nothing was written. Customer names are not printed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
