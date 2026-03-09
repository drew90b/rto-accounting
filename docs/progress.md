# RTO Accounting System — Progress Log

Tracks what has been built, decisions made, and current state of the system.
Update this file as work is completed.

---

## Current State

**Phase:** 1 — Core Data Entry (MVP)
**As of:** 2026-03-09
**Server:** Not yet deployed. Local development only.
**Database:** Not yet connected. Awaiting first `alembic upgrade head` run.

---

## Completed Work

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
| Vendor UI | Model and DB table exist. No list/add/edit screens yet. |
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
