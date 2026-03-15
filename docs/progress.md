# RTO Accounting System — Progress Log

Tracks what has been built, decisions made, and current state of the system.
Update this file as work is completed.

---

## Current State

**Phase:** 1.5 — Workflow Hardening (in progress)
**As of:** 2026-03-14
**Server:** Deployed on Render.
**Database:** Migrations 001–004 applied. Seed data tested.

---

## Completed Work

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
| Dashboard cash warning | Basic dashboard exists. Cash warning panel not yet built. |
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
| 2026-03-08 | No authentication in Phase 1 | Speeds up MVP; single-user or trusted-network use assumed initially |
| 2026-03-08 | `customer_support_repair` added as a third repair job type | BHPH model requires tracking goodwill/retention repairs as a portfolio cost, not revenue |
| 2026-03-08 | Documents stored on disk, metadata in DB | Simple; avoids binary storage in Postgres; easy to back up |
| 2026-03-08 | Flash messages via URL query param (`?msg=...`) | No session middleware required; keeps stack simple |
| 2026-03-08 | `create_all` runs on startup in addition to Alembic | Allows quick startup in dev without running migrations manually |

---

## How to Update This File

When work is completed:
1. Add a dated section under **Completed Work** describing what was built
2. Move the item from **Known Gaps** to the completed section
3. Add any new decisions to the **Key Decisions Log**
4. Update **Current State** at the top with the current phase and date
