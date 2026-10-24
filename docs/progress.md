# RTO Accounting System — Progress Log

Tracks what has been built, decisions made, and current state of the system.
Update this file as work is completed.

---

## Current State

**Phase:** 1.5 — Workflow Hardening (in progress)
**As of:** 2026-10-24
**Server:** The running instance is the systemd service `rto-web` (enabled, since 2026-10-24) on ubuntu-dev: uvicorn as `drew`, bound to its Tailscale address on port 8000, against `rto_accounting_dev`. Manage it with `sudo systemctl restart|status rto-web` and `journalctl -u rto-web`; see README "Running as a service". Crash-restart and reboot tests are still to be run.
**Database:** Migrations 001–009 exist in the repo and `rto_accounting_dev` is at 009 (applied 2026-10-24). Employee sign-in is live; see the 2026-10-23 "Employee Sign-In" entry.
**Open work:** Historical inventory reconciliation is paused (see 2026-10-23 entry). Priority is November purchase entry and December close.

---

## Completed Work

### 2026-10-24 — systemd Service for the Live App

- `deploy/rto-web.service` runs uvicorn as `drew` from the existing checkout and `.venv`, bound to `100.123.116.111:8000`, `Restart=always` (5 s), no start limit. It starts after `tailscaled` and `postgresql`, and an `ExecStartPre` waits up to 2 minutes for the Tailscale address before binding.
- Install, verification (including a kill -9 restart test and a reboot) and rollback to tmux are in README "Running as a service". The tmux app must be stopped before the service starts.
- Installed and enabled 2026-10-24; the tmux `web` window was stopped first. Verified afterwards: one listener on 100.123.116.111:8000 (the service's uvicorn, parent pid 1, in `rto-web.service`'s cgroup), login page 200, anonymous requests redirected to `/login`, export and docs closed, record counts unchanged. Not yet run: forced-crash restart and reboot tests. Belinda's office-computer setup waits until she is available.
- PO-3788 (test entry) was corrected to $0 as "VOID - TEST" with its receipt and history kept.

### 2026-10-24 — Purchases List Paging and Mobile Menu Startup (branch `feature/employee-access`, not deployed)

Prompted by an iPhone Safari demo where pages kept loading and the menu sometimes needed two taps. Not reproduced without the phone; the code-level cause found was structural.

- **Cause found:** `/purchases/` sent all 962 rows (about 1 MB, 0.5 s on the server) and the Menu button's script sat at the very end of the page, so the button was visible but did nothing until the whole page had loaded.
- **Mobile menu:** the menu script now sits right after the sidebar in `base.html`, so it is wired before any page content. The `/review/count` fetch stays at the end (it is optional).
- **Paging:** `/purchases/` shows 50 per page, newest first, with Previous/Next and "Page N of M · X purchases". The vendor, purchaser, stock #, category, review status and date filters carry across pages. Out-of-range pages are clamped. Export is unchanged and still includes every purchase.
- **Not changed:** the menu height (`100vh`); the suspected Safari toolbar overlap has not been reproduced.
- **Verified:** `tests/test_purchase_pagination.py` (7 tests); suite 288 passed, 1 skipped. On the isolated test app with the restored dev data, both test accounts get 50 rows and about 55 KB for page 1, and 12 rows on page 20.

### 2026-10-23 — Employee Sign-In and Purchase Permissions (branch `feature/employee-access`, not deployed)

**Goal:** Let Belinda (employee) sign in individually, enter purchases, attach receipts and correct pending purchases, while review and everything else stays administrator-only. Record who entered and who reviewed each purchase, separate from who made it. Enforce on the server.

#### What was built
- [x] Stage 0: test isolation fixed with SQLAlchemy's pysqlite SAVEPOINT recipe (`tests/conftest.py`). The suite is green in random order; the `pytest-randomly` workaround below is no longer needed.
- [x] Stage 1: migration 009 (additive, no backfill): `users`, `purchases.entered_by_user_id` / `reviewed_by_user_id` / `reviewed_at`, `purchase_events`. User CLI `python -m app.scripts.users` (create, reset-password, disable, enable, set-role, unlock, list). Passwords are argon2-hashed, at least 12 characters, prompted or generated, never passed as arguments.
- [x] Stage 2: `app/auth.py` (`require_user`, `require_admin`), `app/csrf.py` (Origin/Referer check on every POST), `app/routes/auth.py` (login, logout, change password). Signed session cookie, absolute 12-hour limit, re-checked against the database on every request; `/docs` and `/openapi.json` are off. Five wrong passwords lock an account for 15 minutes.
- [x] Stage 3: employees can create, list, search and edit **pending** purchases and attach receipts; they can download receipts linked to purchases and nothing else. Mark reviewed and reopen are admin-only (`POST /purchases/{id}/reviewed`, `/reopen` with a required reason; the old toggle is gone). A reviewed purchase is locked for everyone until reopened. Vehicle purchases keep their unit on edit, and category changes that cross the vehicle line (or car/golf cart) are rejected, so an edit cannot overwrite another unit's acquisition cost. The `entered_by` form field is gone and an edit no longer erases it. Receipts are stored under random names.
- [x] Stage 4: `purchase_events` history (created, edited with old → new, receipt added, reviewed, reopened) shown on the purchase page.

#### Fixes after independent review
- Purchase creation with a receipt crashed on PostgreSQL: the `users.role` and `purchase_events.action` models declared native enums over varchar columns. Now `native_enum=False`. Reproduced on an isolated PostgreSQL cluster (`type "purchaseeventaction" does not exist`), fixed, and covered by `tests/test_model_columns.py` plus the opt-in `tests/test_postgres_receipt.py`.
- 30-minute idle timeout added (`SESSION_IDLE_MINUTES`, session `seen_at`), inside the 12-hour absolute limit; tested at 29 and 31 minutes and for sliding activity.
- The app refuses to start with an empty `ALLOWED_ORIGINS`.
- Wrong "current password" guesses on the change-password page count toward the same lockout as sign-in.
- Receipt history records the stored, sanitized filename.
- Rehearsal for deployment must include creating a purchase with a receipt on PostgreSQL.

#### Unit cost provenance (added after review)
- Review found that editing any purchase linked to an existing unit overwrote the unit's acquisition cost (a $5,001 unit became $300 when the only linked purchase, for $300, was saved). Fix: `purchases.created_unit` (in migration 009, default false, no backfill) is true only for the purchase that created the unit; only that purchase syncs a cost correction. Others leave the cost alone and are flagged "Unit cost not changed" in the review queue (new entries only; historical rows are not flagged). Tested with the $5,001 → $300 scenario, including when it is the only linked purchase, and with a second purchase on a unit the first created.
- Historical purchases have `created_unit = false`, so editing them never changes a unit's cost; the owner corrects those on the unit if needed.

#### Decisions
- Reviewed means reviewed as it stands: no edits (including a late receipt) without an admin reopening with a reason. Reopen clears the reviewer; the event keeps who and when.
- Historical purchases keep NULL attribution and show "(typed, not verified)" / "Reviewed — reviewer not recorded". Nothing is backfilled or guessed.
- Argon2 and the lockout were kept as planned although two users would be fine with less.
- `tests/conftest.py`: `client` is now signed in as an administrator through the real `POST /login`; `anon_client` and `employee_client` are added. Auth is never bypassed in tests. Tests also write receipts to a temporary folder (earlier runs left test folders under `storage/receipts/purchase/`; they were not touched).

#### Validation
- 281 tests pass (plus 1 opt-in PostgreSQL test), in random order, repeatedly. Deliberate breakages (an admin router opened to employees, the pending-only edit guard, the other-unit guard) were each caught.
- Migration 009 rehearsed on a throwaway PostgreSQL cluster restored from the pre-rollout backup: 007 → 008 → 009, downgrade to 008, upgrade again. The 962 purchases were byte-identical (checksum) and every attribution column was NULL. The app was then run against that copy and exercised end to end (sign-in, forced password change, Belinda's 403s, create/edit/review/reopen, history). `rto_accounting_dev` was not touched.

#### Deployment still to do (needs the owner)
Back up the database and `storage/receipts`; add `SESSION_SECRET`, `ALLOWED_ORIGINS` (and `SESSION_COOKIE_SECURE` if HTTPS) to `.env`; `pip install -r requirements.txt`; `alembic upgrade head`; create the admin and Belinda with `users create ... --temporary`; restart `rto:web`. Rollback: check out the previous commit (the old code runs against the new schema); a downgrade destroys attribution, so restore from backup instead. HTTPS through Tailscale Serve and running under systemd are separate, pending decisions.

### 2026-10-23 — General Expenses and Payment Source

**Goal:** Let staff record general operating spending (not a vehicle, not parts for a unit), say who or what paid, tag it car / golf cart / shared, and mark uncertain ones for review. Ready for November entry. Historical records untouched.

#### Findings that shaped the design
- No existing workflow covered it. Transactions → Log Transaction accepts `overhead` but has no payment source, no `shared` line, no receipt upload, and its entries never reach the Review Queue (the queue lists only unit-linked transactions). The database held zero overhead transactions.
- Enums are stored as plain strings with no check constraint, so new enum values need no migration.

#### New files
- [x] `alembic/versions/008_transaction_payment_source.py` — adds nullable `transactions.payment_source` varchar(100). No backfill.
- [x] `tests/test_general_expense.py` — 32 tests.

#### Modified files
- [x] `app/models/enums.py` — 4 `general_expense_*` purchase categories; `BusinessLine.shared` and `.unallocated`; `ASSET_BUSINESS_LINES` (car, golf_cart).
- [x] `app/models/transaction.py` — `payment_source`.
- [x] `app/services/purchase_service.py` — category→line mapping; `payment_source` parameter on `record_purchase`; `payment_source_suggestions()`; `is_payment_source_unknown()`.
- [x] `app/routes/purchases.py`, `templates/purchases/form.html`, `list.html` — required Payment Source field with suggestions; list column; export column; edit never erases an existing value.
- [x] `app/routes/review.py` — new flags (uncertain classification, payment source missing or unknown, missing receipt). Shared is not flagged. Imported purchases are not flagged for payment source or receipt.
- [x] `app/routes/transactions.py`, `templates/transactions/form.html` — payment source field and export column.
- [x] `app/routes/units.py`, `sales.py`, `repair_jobs.py` — dropdowns use `ASSET_BUSINESS_LINES`, so units, sales, and repair jobs cannot be `shared` or `unallocated`.
- [x] `tests/test_purchase_workflow.py`, `tests/test_review_queue.py` — existing purchase posts now send `payment_source`; one "clean entry" test marks a receipt attached.

#### Key decisions
- **"Not sure" is `unallocated`, separate from `shared`.** It stays out of both lines until someone recodes it, and the review queue flags it.
- **Payment source is free text with suggestions** taken from earlier entries, plus a permanent "Unknown - needs review" option. Required by the form, not by the schema or `record_purchase`, so the historical importer can leave it blank rather than guess.
- **No unit is ever created for a general expense.** Spending stays visible in the Purchases ledger, transactions, exports, and review queue. It is not counted in dashboard inventory investment (which sums `Unit.acquisition_cost`).
- Shared expenses are not split automatically and are not a review warning (revised 2026-10-23): `shared` is a valid classification, kept separate in reporting, and can be marked reviewed while staying shared. Marking reviewed is separate from resolving warnings. Applied to the temporary test app only; dev on port 8000 and `rto_accounting_dev` are unchanged.

#### Validation
- 184 tests pass in fixed order (152 existing + 32 new). Four deliberate breakages (historical guard, required check, asset dropdowns, unsure mapping) were each caught by the new tests.
- Migration 008 tested on a throwaway PostgreSQL cluster: upgrade from 007 with legacy rows (payment source stays NULL), ORM round-trip of all four categories and both new business lines, downgrade, re-upgrade. `rto_accounting_dev` was not touched.

#### Known issue (pre-existing)
- (Fixed on `feature/employee-access`, Stage 0.) `pytest-randomly` shuffled test order, and per-test rollback did not hold on SQLite because pysqlite's SAVEPOINTs need `isolation_level=None` plus an explicit `BEGIN`; random order failed 1–6 tests per run. Until that branch is merged, run `pytest -p no:randomly`. `tests/test_review_queue.py` points to `memory/test_isolation_bug.md`, which is not in the repository.


### 2026-10-23 — Historical Inventory Reconciliation: Findings (investigation only, paused)

No code or data was changed. All queries ran read-only against `rto_accounting_dev`.

#### Confirmed findings
- The historical importer (`app/scripts/import_historical_purchases.py`) takes the form's category as is. Vehicle categories (`auction_car_rto`, `auction_car_flip`, `auction_golf_cart_flip`) always create a unit when stock number and VIN match no `unit_id` or `vin_serial`. Historical tags never match, so every vehicle-category row created a unit (154 rows, 154 units).
- `Unit` has no stock-number column. The tag exists only on `purchases.stock_number`.
- The importer links parts rows to units through an in-memory map (`stock_to_unit`). A repeated stock number overwrites the earlier entry, so parts link to the vehicle row with the highest `source_row`. Only a warning is logged.
- `source_reference` is `historical-import:<source_row>`. The updated source file `purchase-orders-updated-2026-10-23.csv` (raw Google Form export, no `source_row` column) lines up with all 959 imported purchases at position = `source_row` (header = row 1) on date, amount, stock number and notes. Vendors differ only by spelling or abbreviation.
- Form category for refs 865, 902 and 934 was an `AUCTION` category in the source, so the importer was faithful.
- The review queue flags "Vehicle purchase has no linked unit" but nothing flags a unit created from an unverified stock tag.
- Dashboard inventory investment sums `Unit.acquisition_cost`, so suspect units inflate it.

#### Affected records (dev database)
- U-0617 (golf cart, $69): purchase 2979 (ref 902, "Car ramps", stock "Shop") plus 15 car-line "Shop" parts purchases ($972.92).
- U-0615: purchase 2977 (ref 865, $647.57, stock 169025, "Ac compressor. Hoses supplies"). U-0620: purchase 2982 (ref 934, $554.31, stock 169025, "Abs bearing sensor brake fluid") plus parts purchases 3597, 3618, 3786.
- Neither unit has VIN, year, make or model, repair jobs, sales, leases or non-purchase transactions.
- Same pattern elsewhere: "Misc" (62 rows on 2 units, U-0580 and U-0582), "F36140", "Misc. Kevin", and 20 numeric stock numbers with two or three vehicle rows each. Not yet reviewed.
- Frazer vehicle master export (`FRZDATA1.CSV`): no Stock # = 169025. Last 6 VIN and full VIN ending 169025 both match one 2015 Chevrolet Trax (Frazer stock 10064, purchased 04/03/26, original cost $5,990). The forms also use the tag 69025 for a 2015 Chevy Trax (U-0592, IAA, $1,754, 4/17/2026).

#### Unresolved questions
- Are U-0592 and the Frazer 10064 Trax the same vehicle? The dates and costs differ.
- Are purchases 2977 and 2982 parts for that Trax filed under the vehicle category? Notes suggest so, but the source cannot confirm it.
- Is "Car ramps" (2979) a shop expense? Which category should it have?
- Are tags like 169025 and 69025 VIN suffixes in general? Not checked beyond this tag.
- What are the other duplicate-stock groups?

#### Proposed (not built)
- Importer prevention: hold rows with placeholder tags ("Shop", "Misc*") or conflicting stock matches for review. Import them as unit-less pending purchases so spending stays visible. Rerun-safe via `source_reference`.
- Correction for units 615, 617 and 620 only after the owner confirms the vehicle questions above.


### 2026-03-15 — Balance Due and Remaining Months on List Views

**Goal:** Show outstanding balance and remaining payment months on the Sales and Lease list views so the owner can assess portfolio health without opening individual records.

#### New files
- [x] `tests/test_balance_and_months.py` — 15 tests: sale balance (no payments, partial, full, total_contract override, map), remaining months (monthly, bi-weekly, weekly, zero, None), build_months_map, route smoke tests, cancelled dash display

#### Modified files
- [x] `app/services/sale_service.py` — added `calculate_sale_balance()` and `build_sale_balance_map()`; added `from sqlalchemy import func` import
- [x] `app/services/lease_service.py` — added `calculate_remaining_months()` and `build_months_map()`; added `import math`, `from typing import Optional`
- [x] `app/routes/sales.py` — imports `build_sale_balance_map`; passes `balances` map to list template
- [x] `app/routes/lease_accounts.py` — imports `build_months_map`; pre-computes `balance_map` then passes both `balances` and `months` to list template
- [x] `app/templates/sales/list.html` — replaced "Down Payment" column with "Balance Due"; cancelled sales show dash
- [x] `app/templates/lease_accounts/list.html` — renamed "Balance" → "Balance Due" + status check; added "Remaining" column; colspan fixed to 10

#### Key decisions
| Decision | Reason |
|---|---|
| `calculate_remaining_months` uses `remaining / scheduled_payment_amount` | LeaseAccount has no `term_months` field. This is the only truthful derivation available. It is an approximation (assumes on-schedule payments). |
| `build_months_map` accepts a pre-computed `balance_map` | Avoids a second round of DB queries; reuses the same balances already computed for the "Balance Due" column |
| Cancelled records show "—" not "$0.00" | A cancelled record's balance is meaningless — showing $0.00 or the full financed amount would mislead the owner |
| Sales list: replaced "Down Payment" with "Balance Due" | Down payment is already visible on the detail/edit form. Balance Due is more operationally useful on the list view. Column count stays at 9. |
| `calculate_sale_balance()` mirrors inline logic in `edit_sale_form` | Consistent formula: `total_contract_amount ?? (sale_amount + fees)` minus `SUM(payments)` |
| No changes to `calculate_remaining_balance()` or `invoice_service` | Not touching existing functions; no circular import risk |

**Assumption on remaining months:** The `LeaseAccount` model has no `term_months` or `end_date` field. Remaining months is derived from `remaining_balance / scheduled_payment_amount`, adjusted for frequency. This will drift slightly for irregular payers. If the business wants calendar-accurate remaining months, adding a `term_months` column would enable it exactly.

**Result:** 61 tests pass. Both list views now show live Balance Due. Lease list shows Remaining Months. Cancelled records display dashes. Dashboard delinquent balance metric is unaffected (uses `build_balance_map` directly).

---

### 2026-03-15 — Duplicate Sale Guard

**Goal:** Prevent a unit from being linked to more than one active sale. Exact scenario: U-0004 was already linked to both S-0001 and S-0002 in seed data — this guard prevents it from recurring.

#### New files
- [x] `tests/test_sale_duplicate_guard.py` — 7 tests: blocked duplicate (pending), blocked duplicate (complete), allowed after cancel, allowed clean unit, edit blocked on unit switch, edit allowed keeping same unit, edit allowed switching to unit with only cancelled sale

#### Modified files
- [x] `app/routes/sales.py` — added `_TERMINAL_SALE_STATUSES` set and `_active_sale_for_unit()` helper; guard added to `create_sale` (before record created) and `update_sale` (before fields updated, only when unit is changing)
- [x] `app/templates/sales/form.html` — added `?error=` banner (same pattern as lease form)

#### Key decisions
| Decision | Reason |
|---|---|
| Terminal = `cancelled` only | `SaleStatus` has three values: `pending`, `complete`, `cancelled`. "returned" does not exist in this model — only `cancelled` is terminal |
| Edit guard fires only on unit change | If the unit isn't changing, no new conflict can be introduced by the edit |
| Error message includes conflicting sale ID | Owner needs the sale ID to investigate and resolve before proceeding |
| No schema change | Existing `status` column on `sales` is sufficient for the guard |
| Existing seed data not altered | U-0004 / S-0001 / S-0002 left as-is; guard only prevents future occurrences |

**Result:** 46 tests pass. Units with only cancelled sales are freely available. Active (pending or complete) units are protected.

---

### 2026-03-14 — Lease Auto-Calculated Financed Balance

**Goal:** Remove manual `financed_balance` entry from the lease create form. Derive it server-side as `original_agreed_amount − down_payment`. Add live JS display and input validation.

#### New files
- [x] `tests/test_lease_financed_balance.py` — 6 tests: derived value stored correctly, submitted value ignored, zero down payment, down > agreed error, missing agreed error, zero agreed error

#### Modified files
- [x] `app/routes/lease_accounts.py` — `POST /new`: removed `financed_balance` Form param; added validation (agreed > 0, down ≤ agreed); derives `financed = agreed - dp` server-side
- [x] `app/templates/lease_accounts/form.html` — new form: replaced editable input with read-only calculated display; added live JS (inline, no dependencies); added `?error=` display at top; edit form: hidden input passes stored value through

#### Key decisions
| Decision | Reason |
|---|---|
| Server derives financed_balance on create only | Edit route left unchanged — allows manual correction of existing records if needed |
| JS updates display on input, shows red text if down > agreed | Gives immediate visual feedback before form submission |
| Hidden input on edit form | `update_lease` route still accepts `financed_balance` Form param; hidden input passes stored value through without exposing an editable field |

**Result:** New leases no longer require manual financed balance entry. Value is always `agreed − down`, validated before save.

---

### 2026-03-14 — Dashboard Cash and Control Metrics

**Goal:** Replace count-based stat cards with money and control metrics visible at a glance for the owner.

#### New files
- [x] `app/services/dashboard_service.py` — three aggregation functions: `cash_collected_this_month()`, `delinquent_balance_total()`, `inventory_investment()`
- [x] `tests/test_dashboard_metrics.py` — 13 tests covering all three service functions plus a dashboard route smoke test

#### Modified files
- [x] `app/main.py` — dashboard route updated: imports dashboard_service, computes `metrics` dict, updates exception count to include `in_review`, updates exceptions panel query to match
- [x] `app/templates/index.html` — stats-grid replaced with four money/control metric cards; exceptions panel gains Status column; old count widgets removed
- [x] `app/static/css/style.css` — added `.stat-value.money`, `.stat-value.warning`, `.stat-meta` classes

#### Key decisions
| Decision | Reason |
|---|---|
| New `dashboard_service.py` rather than inline queries | Keeps route thin; service functions are independently testable |
| `delinquent_balance_total` filters `status = active` | Prevents paid_off/cancelled leases with stale delinquency_status from inflating the figure |
| Reuse `build_balance_map()` for delinquent total | Keeps balance calculation consistent with the rest of the app; avoids a second code path |
| Exception count changed from `open` only to `open + in_review` | In_review exceptions still require attention; count should reflect total workload |
| Dashboard exceptions panel updated to match (open + in_review) | Prevents mismatch between metric card count and visible panel rows |
| `acquisition_cost = NULL` units excluded from inventory investment | SQL SUM ignores NULLs; noted in service docstring as a known understatement risk |

**Result:** All 33 tests pass. Dashboard now shows Cash Collected, Delinquent Balance, Inventory Investment, and Active Exceptions.

---

### 2026-03-14 — Vendor UI

**Goal:** Expose the existing vendors table and ORM model through a complete CRUD interface, consistent with the customers module pattern.

#### New files
- [x] `app/routes/vendors.py` — `GET /vendors/` (list + search), `GET/POST /vendors/new` (add), `GET/POST /vendors/{id}/edit` (edit), `GET /vendors/export` (Excel)
- [x] `app/templates/vendors/list.html` — list view with name search, table, and export/add actions
- [x] `app/templates/vendors/form.html` — add/edit form with name, phone, email, address, notes
- [x] `tests/test_vendor_create.py` — 4 tests: create + persist, list 200, new form 200, missing edit redirects

#### Modified files
- [x] `app/main.py` — vendors router imported and registered at `/vendors`
- [x] `app/templates/base.html` — Vendors link added under People section of sidebar

#### Key decisions
| Decision | Reason |
|---|---|
| No status field | Vendor model has no status column; no enum or filter added |
| Human-readable ID: `V-{id:04d}` | Consistent with all other entity ID patterns |
| Placed under People in sidebar | Vendors and Customers are both contact/relationship records |
| No service layer needed | No business logic beyond CRUD; routes call db directly, same as customers |

**Result:** All 20 tests pass. Vendors can be listed, added, edited, and exported to Excel.

---

### 2026-03-12 — Invoice / Receipt Generator

**Goal:** Auto-generate a persistent Invoice record for every revenue event, with a printable HTML document and a browsable invoice list.

#### New files
- [x] `app/models/enums.py` — `InvoiceType` (sale, repair, rto_payment) and `InvoiceStatus` (open, paid) added
- [x] `app/models/invoice.py` — `Invoice` and `InvoiceItem` ORM models; `invoice_number` set post-flush (`INV-00001`); relationships to customer, sale, repair_job, lease_account, payment, items
- [x] `app/models/__init__.py` — Invoice, InvoiceItem imports added
- [x] `alembic/versions/004_add_invoices.py` — Migration 004: creates `invoices` and `invoice_items` tables with all FKs, indexes, and server_defaults
- [x] `app/services/invoice_service.py` — Five public functions: `create_invoice_from_sale()`, `create_invoice_from_repair()`, `create_invoice_from_rto_payment()`, `create_simple_receipt()`, `generate_invoice_document()`; private helpers: `_build_invoice()`, `_add_item()`, `_unit_description()`, `_compute_remaining_balance()`, `_load_invoice_context()`; `load_invoice_for_display()` utility for route handlers
- [x] `app/routes/invoices.py` — `GET /invoices/` (list with type/status filter); `GET /invoices/{id}/print` (display invoice)
- [x] `app/templates/invoices/list.html` — filterable list view extending base.html
- [x] `app/templates/invoices/invoice.html` — standalone printable invoice; shows line items, payment history, and balance; adapts heading/label text to invoice type

#### Modified files
- [x] `app/models/customer.py`, `sale.py`, `repair_job.py`, `lease_account.py`, `payment.py` — `invoices`/`invoice` relationship added (one line each)
- [x] `app/services/sale_service.py` — `finalize_new_sale()` calls `create_invoice_from_sale()` after payment and unit update
- [x] `app/services/repair_service.py` — `close_repair_job()` calls `create_invoice_from_repair()` after transaction flush
- [x] `app/services/lease_service.py` — `record_rto_payment()` calls `create_invoice_from_rto_payment()` after transaction flush
- [x] `app/main.py` — invoices router imported and registered at `/invoices`
- [x] `app/templates/base.html` — "Invoices" link added to Finance section of sidebar
- [x] `app/static/css/style.css` — `.badge-paid` and `.badge-open` aliases added

#### Key decisions
| Decision | Reason |
|---|---|
| `_compute_remaining_balance()` inlined in invoice_service | Avoids circular import: `lease_service` imports `invoice_service`, so invoice_service cannot import lease_service |
| Invoice total = payment amount for RTO receipts (not lease total) | Receipt model: each payment event gets its own receipt showing amount collected and remaining lease balance |
| Existing `sales/receipt.html` and `repair_jobs/invoice.html` kept as-is | New invoice system is additive; standalone receipts still accessible from their respective views |
| `storage/invoices/` for saved HTML files | Consistent with `storage/receipts/` pattern; `generate_invoice_document()` available for CLI or background use |

**Result:** All 16 tests pass. Invoices auto-created on every sale, repair close, and RTO payment.

---

### 2026-03-12 — Transactional pytest Architecture

**Goal:** Eliminate cross-test state contamination and hidden ordering dependencies in the pytest suite.

#### What changed

- [x] `tests/conftest.py` — Complete rewrite. New fixture hierarchy:
  - `engine` (session-scoped) — single SQLite in-memory engine with `StaticPool`; schema created once via `Base.metadata.create_all()`; dropped at session end
  - `db_connection` (per-test) — opens a connection, begins an outer `BEGIN` transaction; rolls back and closes after each test regardless of commits inside the test
  - `db` (per-test) — `sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")`; sessions use SAVEPOINTs so that `db.commit()` inside services only releases the savepoint, not the outer transaction
  - `client` (per-test) — `FastAPI TestClient` with `get_db` overridden to yield sessions from the same `db_connection`; both `client` and `db` share one connection so route-committed data is visible to test verification queries
- [x] `tests/test_customer_create.py` — Removed local `http_db` and `client` fixtures; test now uses `client` and `db` from conftest; `StaticPool` isolation is no longer needed since the shared connection handles it
- [x] `requirements-dev.txt` — Added `pytest-randomly`; forces random test order on every run to surface hidden state dependencies early

#### Result

- All 16 tests pass with randomized execution order
- No SAWarning about deassociated transactions
- No per-test schema creation overhead
- `pytest-randomly` active (`Using --randomly-seed=...` shown in output)

#### Key decisions

| Decision | Reason |
|---|---|
| `join_transaction_mode="create_savepoint"` | Prevents route `db.commit()` from committing the outer test transaction; supported natively in SQLAlchemy 2.0 |
| `StaticPool` on session-scoped engine | SQLite `:memory:` databases are per-connection; StaticPool ensures all `engine.connect()` calls return the same physical connection and therefore the same database |
| `db_connection` as a separate fixture | Provides a clean rollback point that both `db` and `client` fixtures can depend on; pytest resolves function-scoped fixtures once per test, guaranteeing both share the same connection |
| `pytest-randomly` | Randomizes ordering to catch tests that silently rely on preceding test state |

---

### 2026-03-12 — Nullable Human-Readable ID Alignment

**Goal:** Bring ORM model definitions into alignment with migration 003 so that tests using SQLite (`create_all`) behave consistently with the PostgreSQL schema.

- [x] `alembic/versions/003_nullable_human_readable_ids.py` — migration added; removes `NOT NULL` from all human-readable ID columns across 10 tables
- [x] `app/models/unit.py` — `unit_id` changed to `nullable=True`
- [x] `app/models/repair_job.py` — `job_id` changed to `nullable=True`
- [x] `app/models/sale.py` — `sale_id` changed to `nullable=True`
- [x] `app/models/lease_account.py` — `lease_id` changed to `nullable=True`
- [x] `app/models/payment.py` — `payment_id` changed to `nullable=True`
- [x] `app/models/transaction.py` — `transaction_id` changed to `nullable=True`
- [x] `app/models/document.py` — `document_id` changed to `nullable=True`
- [x] `app/models/exception_record.py` — `exception_id` changed to `nullable=True`
- [x] `app/models/vendor.py` — `vendor_id` changed to `nullable=True`
- [x] `app/services/lease_service.py` — `record_rto_payment()` updated to use enum members (`TransactionType.collection`, `BusinessLine.car`, `RevenueStream.car_rto_lease`) instead of plain strings; fixes test assertion on `.value`
- [x] `tests/test_lease_balance.py` — `make_payment` helper updated to use flush-then-set-id pattern (consistent with service layer); eliminates duplicate `payment_id` collision when same amount is used multiple times on the same lease
- [x] `docs/data_model.md` — added Design Note explaining nullable human-readable IDs and the flush-then-set lifecycle
- [x] `docs/business_rules.md` — added Section 0 documenting the human-readable ID lifecycle rule
- [x] `docs/progress.md` — this entry

Root Cause:
SQLite test schema was generated from ORM models using create_all,
while production schema was managed by Alembic migrations.
Human-readable ID columns were nullable in migration 003 but still
non-nullable in ORM models, causing schema drift between environments.


**Result:** All 16 pytest tests pass. ORM models and DB migrations are now consistent.

---

### 2026-03-09 — Revenue Capture: Auto-Records, Receipts, and Close-Job Workflow

**Goal:** Make the system the source of truth for all completed revenue events without becoming a POS system.

#### Services
- [x] `app/services/sale_service.py` — `finalize_new_sale()`: atomically creates sale transaction + payment on new sale save; auto-sets golf cart sales to complete with full-amount payment; updates unit status.
- [x] `app/services/repair_service.py` — `close_repair_job()`: closes job and auto-creates repair_revenue transaction for customer_repair jobs. `record_repair_payment()`: records payment event for a closed repair job.

#### Routes
- [x] `app/routes/sales.py` — calls `finalize_new_sale()` on create; adds `GET /sales/{id}/receipt`; adds `POST /sales/{id}/complete` with payment-or-lease enforcement; passes payment summary to edit view.
- [x] `app/routes/repair_jobs.py` — adds `GET/POST /repair-jobs/{id}/close`; adds `GET/POST /repair-jobs/{id}/record-payment`; adds `GET /repair-jobs/{id}/invoice`; passes payment summary to edit view.

#### Templates (modified)
- [x] `app/templates/sales/form.html` — adds "Payment Method" field on new sale; shows payment history and "Mark Complete" button on edit; shows "Print Receipt" link.
- [x] `app/templates/repair_jobs/form.html` — shows "Close Job", "Record Payment", and "Print Invoice" action buttons based on job status.

#### Templates (new)
- [x] `app/templates/sales/receipt.html` — printable sale receipt (no sidebar); customer, unit, amounts, payments, balance.
- [x] `app/templates/repair_jobs/invoice.html` — printable repair invoice; charges, payments, balance due; "Record Payment" button.
- [x] `app/templates/repair_jobs/close_job.html` — focused close-job form; confirms billing amounts before closing.
- [x] `app/templates/repair_jobs/record_payment.html` — 4-field payment form for repair jobs (mirrors lease record_payment).

#### Enums
- [x] `app/models/enums.py` — added `customer_support_repair` to `RepairJobType` (no migration needed; column is String in DB).

#### Docs
- [x] `docs/business_rules.md` — updated Sale Rules (auto-creation, golf cart full-payment, completion enforcement); updated Repair Job Rules (auto-revenue-transaction on close).
- [x] `docs/system_workflows.md` — updated workflows 3, 4, 7, 9 to reflect auto-creation and new shortcut paths.
- [x] `docs/progress.md` — this entry.

#### Key decisions
| Decision | Reason |
|---|---|
| Golf cart sales always complete + full payment at save time | Business rule: always paid in full at counter |
| Revenue transaction created at close time, not at billing entry | Prevents phantom revenue; close is the commit point |
| Repair payment recorded separately from close (no auto-payment on close) | Cash may be collected later; don't assume it |
| Receipts are printable HTML, no PDF library | Browser print-to-PDF covers the use case; no new dependencies |
| Parts sales handled via customer_repair job type | No SKU catalog needed; job notes serve as description |

---

### 2026-03-08 — Lease Balance Refactor

**Removed `outstanding_balance` as mutable stored state.** Remaining balance is now computed at runtime.

- [x] `app/services/lease_service.py` — `calculate_remaining_balance(lease, db)` and `build_balance_map(leases, db)`
- [x] `app/services/__init__.py` — package marker
- [x] `app/models/lease_account.py` — removed `outstanding_balance` Column; replaced with comment pointing to service
- [x] `app/routes/lease_accounts.py` — removed `outstanding_balance` form params from create and update; import and use service in list, edit, and export handlers
- [x] `app/templates/lease_accounts/list.html` — balance column reads from `balances` dict passed by route
- [x] `app/templates/lease_accounts/form.html` — replaced editable balance input with read-only computed display (edit view only)
- [x] `app/templates/index.html` — dashboard delinquent panel reads from `delinquent_balances` dict
- [x] `app/main.py` — computes and passes `delinquent_balances` to dashboard template
- [x] `scripts/seed.py` — removed `outstanding_balance` kwarg from LeaseAccount constructor
- [x] `alembic/versions/002_deprecate_outstanding_balance.py` — renames column to `outstanding_balance_deprecated`
- [x] `tests/__init__.py`, `tests/conftest.py` — SQLite in-memory test fixture
- [x] `tests/test_lease_balance.py` — 8 test scenarios (no payments, single, multiple, partial, adjustment, null financed_balance, multi-lease map, isolation)
- [x] `requirements-dev.txt` — pytest
- [x] `docs/data_model.md`, `docs/business_rules.md`, `docs/system_workflows.md`, `docs/roadmap.md` — updated to reflect computed balance

---

### 2026-03-08 — MVP Scaffold

**Initial scaffold built.** All core modules created from scratch.

#### Infrastructure
- [x] `requirements.txt` — FastAPI, SQLAlchemy, Alembic, psycopg2, Jinja2, openpyxl, python-multipart
- [x] `.env.example` — DATABASE_URL template
- [x] `alembic.ini` — Alembic configuration
- [x] `app/config.py` — DATABASE_URL and STORAGE_DIR from environment
- [x] `app/database.py` — SQLAlchemy engine, session, DeclarativeBase, `get_db` dependency
- [x] `app/main.py` — FastAPI app, router registration, `create_all`, dashboard route
- [x] `storage/receipts/` — local document storage directory

#### Data Models (`app/models/`)
- [x] `enums.py` — All enums: BusinessLine, UnitType, UnitStatus, TransactionType, RevenueStream, ExceptionType, and all others
- [x] `customer.py` — Customer table with all fields and relationships
- [x] `vendor.py` — Vendor table
- [x] `unit.py` — Unit table with status enum, business line, all inventory fields
- [x] `repair_job.py` — Repair job table (internal recon, customer repair, customer support repair)
- [x] `sale.py` — Sale table with amounts, status, business line
- [x] `lease_account.py` — Lease/RTO table with balance, delinquency, payment schedule
- [x] `payment.py` — Payment events table, linked to sale/lease/repair job
- [x] `transaction.py` — Financial event log with all classification fields
- [x] `document.py` — Receipt/document metadata table
- [x] `exception_record.py` — Exception queue table with audit history

#### Routes (`app/routes/`)
- [x] `units.py` — List (with filter), add, edit, Excel export
- [x] `customers.py` — List (with filter), add, edit, Excel export
- [x] `repair_jobs.py` — List (with filter), add, edit, Excel export
- [x] `sales.py` — List (with filter), add, edit, Excel export
- [x] `lease_accounts.py` — List (with filter), add, edit, Excel export
- [x] `payments.py` — List (with filter), add, edit, Excel export
- [x] `transactions.py` — List (with filter + date range), add, edit, Excel export
- [x] `documents.py` — List, upload, download
- [x] `exceptions.py` — List (with filter), add, edit, audit history on status change

#### Templates (`app/templates/`)
- [x] `base.html` — Sidebar layout, nav links, flash message support
- [x] `index.html` — Dashboard: counts, open exceptions, delinquent leases, quick actions
- [x] `units/list.html` + `form.html`
- [x] `customers/list.html` + `form.html`
- [x] `repair_jobs/list.html` + `form.html`
- [x] `sales/list.html` + `form.html`
- [x] `lease_accounts/list.html` + `form.html`
- [x] `payments/list.html` + `form.html`
- [x] `transactions/list.html` + `form.html`
- [x] `exceptions/list.html` + `form.html`
- [x] `documents/list.html`

#### Static
- [x] `app/static/css/style.css` — Custom sidebar layout, table, form, badge, and button styles. No external CSS framework.

#### Database Migration
- [x] `alembic/env.py` — Configured to read DATABASE_URL from config
- [x] `alembic/script.py.mako` — Migration template
- [x] `alembic/versions/001_initial_schema.py` — Full initial schema: all 10 tables with columns, FKs, and indexes

#### Seed Data (`scripts/seed.py`)
- [x] 5 customers (FL-based, realistic names)
- [x] 4 vendors (auction, parts, tire, golf cart wholesale)
- [x] 7 units (4 cars, 3 golf carts — mix of statuses)
- [x] 3 repair jobs (internal recon ×2, customer repair ×1)
- [x] 2 sales (1 car, 1 golf cart)
- [x] 1 lease/RTO account (active, current)
- [x] 5 payments (down payments, RTO collections, repair payment)
- [x] 8 transactions (purchases, sales, collections, repair revenue, cost)
- [x] 2 open exceptions (missing receipt, missing coding)

#### Documentation
- [x] `CLAUDE.md` — Governing spec (pre-existing)
- [x] `data_model.md` — Full table schema with columns, types, nullability, enums, and ER diagram
- [x] `business_rules.md` — Business rules across all modules including repair job types, revenue stream mapping, exception triggers
- [x] `system_workflows.md` — Step-by-step workflows for 13 common business events
- [x] `docs/roadmap.md` — 6-phase roadmap with status indicators and next steps
- [x] `docs/progress.md` — This file

---

## Known Gaps and Pending Work

### Phase 1 — Still needed
| Item | Notes |
|---|---|
| ~~Vendor UI~~ | ✅ Complete — 2026-03-14 |
| ~~Dashboard cash warning~~ | ✅ Complete — 2026-03-14 |
| Form validation feedback | Forms silently fail on missing required fields in some cases. No inline error messages yet. |
| Pagination | List views load all records. No pagination for large datasets. |
| Duplicate transaction guard on sale edit | If a user edits sale_amount after save, the auto-created transaction will be stale. No warning is shown. |

### Phase 2 — Not started
| Item | Notes |
|---|---|
| Month-end export pack | Single workbook, multiple tabs. See roadmap Phase 2. |
| Revenue by stream report | Summary view with period filter. |
| Unit profitability view | Acquisition + recon cost vs. sale/lease revenue. |
| Lease portfolio summary | Active accounts, balances, payment schedule. |

### Phase 3–6 — Not started
See `docs/roadmap.md` for full list.

---

## Key Decisions Log

| Date | Decision | Reason |
|---|---|---|
| 2026-03-08 | Use server-rendered Jinja2 templates, no JS framework | Low-skill users; simpler to maintain; no build step |
| 2026-03-08 | Human-readable IDs generated post-flush (`U-0001`, `C-0001`, etc.) | Easier for staff to reference records verbally and in notes |
| 2026-03-08 | All enum values stored as strings in the DB (not native PG enums) | Easier to add values later without schema migrations |
| 2026-03-08 | `outstanding_balance` on lease accounts updated manually | Avoids complex auto-calculation in MVP; acceptable for small portfolio |
| 2026-03-08 | `outstanding_balance` refactored to computed-at-runtime | Eliminated mutable stored state; balance = `financed_balance − SUM(payments)`; column renamed to `outstanding_balance_deprecated` in migration 002; `app/services/lease_service.py` is the single authoritative calculation |
| 2026-03-08 | No authentication in Phase 1 | Speeds up MVP; single-user or trusted-network use assumed initially. **Superseded 2026-10-23** by individual sign-in (branch `feature/employee-access`). |
| 2026-03-08 | `customer_support_repair` added as a third repair job type | BHPH model requires tracking goodwill/retention repairs as a portfolio cost, not revenue |
| 2026-03-08 | Documents stored on disk, metadata in DB | Simple; avoids binary storage in Postgres; easy to back up |
| 2026-03-08 | Flash messages via URL query param (`?msg=...`) | No session middleware required; keeps stack simple |
| 2026-03-08 | `create_all` runs on startup in addition to Alembic | Allows quick startup in dev without running migrations manually. **Superseded:** no `create_all` call exists in `app/` as of 2026-10-23; apply migrations with Alembic. |

---

## How to Update This File

When work is completed:
1. Add a dated section under **Completed Work** describing what was built
2. Move the item from **Known Gaps** to the completed section
3. Add any new decisions to the **Key Decisions Log**
4. Update **Current State** at the top with the current phase and date
