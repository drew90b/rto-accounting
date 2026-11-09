# Frazer Intake (Increment F) — upload, validate, preview

**Status:** built on an unmerged feature branch; not deployed. Spec v0.4.1. **Governing spec:** [`specs/cash-financial-control-spec.md`](specs/cash-financial-control-spec.md) v0.4, section 19 (REQ-FZ-01..16, AC-FZ-01..16), decisions DEC-33..DEC-38. The specification governs business behavior; this page only says what was built and how to use it.

## What it is for

Monthly review needs the Frazer side of the story (what Frazer says is owed, paid and in stock) next to the Square and cash side. Before any of that can feed the ledger, someone has to be able to load a Frazer export, see what is in it, see what is wrong with it, and see what a later import *would* do. This increment is only that: **intake and preview**. It never creates or changes purchases, units, payments, cash movements or transactions (REQ-FZ-06, AC-FZ-14).

## What it does

Administrator-only screen at **Finance → Frazer Intake** (`/finance/frazer/`).

- Upload a vehicle list, a customer-balance report or a payments/transactions export (CSV, up to 10 MB). The type is detected from the column headers, not the filename. The original bytes are stored untouched and can be downloaded back.
- Per upload: export type, filename, upload time, declared export date and coverage, record counts, source totals, and five buckets: **new, unchanged, changed, conflicting, unresolved**. Where no earlier file exists to compare with, the page says so instead of calling everything new.
- A plain statement of what is ready and what is not, grouped exceptions with the affected source line numbers, row detail behind the summary (filter by status, entry class or text), cross-file checks, and append-only notes (including a later-recorded actual cutoff, which never overwrites the original "Unknown").
- Fee assessments (Late Fee) and money-received entries are reported as separate groups and are never added together. Reported zero costs and floor-plan balances are kept and flagged as unconfirmed.

## Rules it follows (task data rules → spec)

| Rule | Where |
|---|---|
| Snapshots are Nov 8, actual cutoff recorded separately, never shown as Nov 1 openings | REQ-FZ-03, DEC-37 |
| Source data stays apart from operational records | REQ-FZ-06 |
| Stock numbers, VINs, receipt and sequential IDs are text; no merging on VIN alone | REQ-FZ-04, -11 |
| Transaction date and entry date/time kept separately | REQ-FZ-04, AC-FZ-03 |
| Source codes preserved; meaning is provisional | REQ-FZ-10, DEC-35/36 |
| Identity = Sequential ID (candidate); receipt number is evidence only; changed content under one identity is a revision/conflict | REQ-FZ-07, DEC-33/34 |
| Extra trailing column only when empty | REQ-FZ-05 |
| Dated snapshots stay distinct | REQ-FZ-09 |

## Try it without the app

    python -m app.scripts.frazer_intake_check FILE [FILE ...] [--export-date 2026-11-08]

Same parser as the screens. No database, nothing written, customer names never printed.

## Tests

`tests/test_frazer_parse.py` and `tests/test_frazer_intake.py` use synthetic fixtures (`tests/frazer_fixtures.py`): real column headers, invented values. One migration test runs only when `FRAZER_INTAKE_PG_URL` points at a disposable PostgreSQL database.

## Status: implemented, verified, pending

| | |
|---|---|
| **Implemented** | Upload, type detection, parsing of the three Nov 8 layouts, identity and content hashing, baselines and the five buckets, grouped exceptions with source lines, cross-file checks (including Down Payment corroboration), filename-derived declared dates, notes, protected download, admin-only access, CLI checker, migration 011. |
| **Verified** | The three real Nov 8 files (private report `/home/drew/rto-reviews/2026-11-08-frazer-intake/REAL_FILE_FINDINGS.md`): every preliminary count and amount confirmed. Full test suite, a 22-check HTTP end-to-end run on a disposable PostgreSQL, the combined 009 > 010 > 011 migration sequence, and zero writes to operational tables. |
| **Pending** | Decisions DEC-33..DEC-38; matching to Square and the bank; any import into operational records (a separate, approved increment). |

## Merge order and integration (decided)

*Update:* the `feature/monthly-review` worktree now holds the combined code (main + Opening Snapshot + this + Monthly Review) with 011 already re-pointed to 010 and the combined sequence tested; see `docs/monthly_review.md`.

**Opening Snapshot (migration 010) merges first; this branch (011) merges second.** Both branches start from 009, and neither is committed yet, so git cannot enforce the order.

When this branch is merged after Opening Snapshot:

1. In `alembic/versions/011_frazer_source_intake.py` change `down_revision = "009"` to `down_revision = "010"` (nothing else).
2. `app/main.py`, `app/models/__init__.py`, `app/templates/base.html`, `tests/conftest.py`, `README.md`: keep **both** sides' added lines; they are independent insertions.
3. Run `alembic heads` (expect a single head, 011) and the full suite.

This exact sequence was built in a scratch copy and tested on a disposable PostgreSQL: upgrade 009 > 010 > 011, downgrade to 010 and 009, upgrade again, no `alembic check` drift for either set of tables; full suite 408 passed, 2 skipped, 1 failure in Opening Snapshot's own `test_ac_os_12`, whose assertion `"7 AM" not in page` fails whenever the clock shows a time like 10:17 AM. That test is time-of-day fragile and unrelated to this work; it should compare something other than raw time text.

The migration test here follows whichever parent 011 declares, so it passes before and after the one-line change.

## Demo

Disposable database `rto_frazer_demo` and separate storage `/home/drew/rto-demo-frazer/storage`, synthetic data only. Environment overrides added for this: `SESSION_COOKIE_NAME` (so the demo does not log you out of other apps on the same host) and `STORAGE_DIR`; defaults are unchanged.

## Before any operational import

DEC-33..DEC-38 (Sequential ID stability, custody identity, meaning of Frazer codes, late-fee treatment, balance meaning and cutoff, retention of customer-level uploads), matching to Square/bank, and an approved increment that writes operational records.
