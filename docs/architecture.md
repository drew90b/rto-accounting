# RTO Accounting System — Architecture

## Overview

Server-rendered Python web application used as the internal finance system of record.

- **Framework:** FastAPI with Jinja2 templates (no client-side JS framework)
- **ORM:** SQLAlchemy 2.0 (legacy `db.query(...)` style)
- **Migrations:** Alembic
- **Database:** PostgreSQL (production), SQLite in-memory (tests)
- **Exports:** openpyxl for Excel workbooks
- **File storage:** Local filesystem under `storage/receipts/` (receipts) and `storage/invoices/` (saved invoice HTML)

For an explanation of how Payments, Transactions, Invoices, and Lease Balances relate to each other, see [`docs/financial_record_architecture.md`](financial_record_architecture.md).

---

## Application Structure

```
app/
  config.py          — DATABASE_URL and STORAGE_DIR from environment
  database.py        — Engine, SessionLocal, Base, get_db dependency
  main.py            — FastAPI app, router registration, create_all, dashboard
  models/            — One file per entity; all imported in __init__.py
  routes/            — One router per module; CRUD + Excel export
  services/          — Business logic; routes call services, services call db
  templates/         — Jinja2; one list.html + form.html per module
  static/css/        — Custom minimal CSS; no external framework
alembic/versions/    — Numbered migration files
scripts/seed.py      — Sample data for local development
storage/receipts/    — Uploaded receipts and supporting documents
tests/               — pytest suite (see Testing section below)
docs/                — Project documentation
```

---

## Service Layer Pattern

Business logic lives in services. Routes are thin wrappers that call a service,
then commit and redirect.

```
Route handler
  → call service(args, db)
      → db.add(record)
      → db.flush()           # get auto-increment id
      → record.human_id = f"X-{record.id:04d}"
      → return record
  → db.commit()
  → redirect
```

Key service files:
- `app/services/lease_service.py` — `record_rto_payment()`, `calculate_remaining_balance()`, `build_balance_map()`
- `app/services/sale_service.py` — `finalize_new_sale()`
- `app/services/repair_service.py` — `close_repair_job()`, `record_repair_payment()`
- `app/services/invoice_service.py` — `create_invoice_from_sale()`, `create_invoice_from_repair()`, `create_invoice_from_rto_payment()`, `load_invoice_for_display()`

---

## Human-Readable ID Lifecycle

Every entity has a human-readable display ID (`C-0001`, `U-0042`, etc.).
These IDs are generated after flush so the auto-increment integer pk is available.

```
INSERT row (human-readable ID column is null — nullable=True in model and schema)
  ↓
db.flush()  →  database assigns integer pk
  ↓
application:  record.customer_id = f"C-{record.id:04d}"
  ↓
db.commit()  →  record persisted with human-readable ID set
```

Rules:
- Human-readable ID columns are `nullable=True` in both ORM models and DB schema.
- Never pass a human-readable ID into a model constructor — always generate post-flush.
- Integer `id` is the FK reference used in all relationships.
- Migration `003_nullable_human_readable_ids` removed NOT NULL from all such columns.

---

## Testing Architecture

### Design: Transactional Sandbox

The test suite uses a **transactional rollback** architecture to guarantee full
isolation between tests without recreating the database schema for each test.

```
pytest session starts
  │
  ├── engine (session-scoped)
  │     SQLite :memory: + StaticPool
  │     Base.metadata.create_all() — schema created ONCE
  │
  └── for each test:
        db_connection — engine.connect() + conn.begin()  (outer transaction)
          │
          ├── db     — sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
          │            for unit tests that call services directly
          │
          └── client — FastAPI TestClient
                       get_db overridden with sessionmaker(bind=conn, join_transaction_mode=...)
                       for HTTP-level integration tests

        [test runs]

        db_connection teardown — txn.rollback() + conn.close()
        All changes from the test are reverted.
```

### Why join_transaction_mode="create_savepoint"

Route handlers and services call `db.commit()`. Without savepoints, that commit
would commit the outer connection transaction, making rollback impossible.

With `join_transaction_mode="create_savepoint"`:
- Every `session.commit()` issues `RELEASE SAVEPOINT` (not `COMMIT`)
- The outer transaction on `db_connection` is never touched by application code
- After the test, `txn.rollback()` reverts everything unconditionally

### Data Visibility in HTTP Tests

`client` and `db` both depend on `db_connection`. pytest resolves
function-scoped fixtures once per test, so both fixtures share the same
physical connection. Data written by a route handler (released from its
SAVEPOINT) is immediately visible to `db.query(...)` in the same test because
SQLite does not create read isolation between savepoints on the same connection.

### pytest-randomly

`pytest-randomly` is included in `requirements-dev.txt`. It randomizes test
execution order on every run, which surfaces hidden state dependencies early.
A fixed seed can be specified with `--randomly-seed=<n>` to reproduce a run.

### Fixture Summary

| Fixture | Scope | Purpose |
|---|---|---|
| `engine` | session | One SQLite engine; schema created once |
| `db_connection` | function | Per-test connection + outer rollback transaction |
| `db` | function | ORM session for unit/service tests |
| `client` | function | FastAPI TestClient with get_db overridden |

---

## Enum Storage

All enum columns are stored as `String` in the database (not native PG enums).
New enum values can be added by editing `app/models/enums.py` with no migration
required.

---

## Outstanding Balance Calculation

`lease_accounts.outstanding_balance` is NOT stored as a mutable column.
Remaining balance is computed at runtime:

```
remaining = financed_balance - SUM(payments.amount WHERE lease_account_id = lease.id)
```

Use `lease_service.calculate_remaining_balance(lease, db)` wherever a current
balance is needed. The old column was renamed to `outstanding_balance_deprecated`
in migration `002`.
