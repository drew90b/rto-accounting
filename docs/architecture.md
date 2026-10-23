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
  config.py          — DATABASE_URL, STORAGE_DIR, SESSION_SECRET, ALLOWED_ORIGINS from environment
  auth.py            — require_user / require_admin dependencies; signed-session checks
  csrf.py            — Origin/Referer check on every POST
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

## Sign-In and Access Control

- `SessionMiddleware` keeps a signed (not encrypted) cookie holding only `uid`, `ver` and `login_at`. `require_user` re-reads the user from the database on every request, so disabling an account or changing a role applies on the next click; a session ends after `SESSION_MAX_HOURS` (default 12) from sign-in, or after `SESSION_IDLE_MINUTES` (default 30) without a request; activity restarts only the idle clock.
- `app/main.py` includes every router with a dependency, so a new router is protected only if someone remembers to add one: Purchases and Documents use `require_user`, everything else `require_admin`, with admin-only routes inside those two. The route-inventory test catches mistakes.
- `OriginCheckMiddleware` rejects any POST whose Origin (or Referer) is not in `ALLOWED_ORIGINS`.
- `/docs`, `/redoc` and `/openapi.json` are disabled. The app refuses to start without `SESSION_SECRET` or `ALLOWED_ORIGINS` (list every exact address people type, scheme and port included, or sign-in returns 403).
- Enum columns that are varchar in the schema must be declared `SAEnum(..., native_enum=False)`; a native enum makes Postgres cast batched inserts to a type that does not exist. `tests/test_postgres_receipt.py` (opt-in, needs `RTO_TEST_POSTGRES_URL` pointing at a scratch database named `*_scratch`) exercises receipt creation on real Postgres.
- Set `SESSION_COOKIE_SECURE=true` only when served over HTTPS (for example behind Tailscale Serve); over plain HTTP the cookie cannot be marked Secure.

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
- `app/services/dashboard_service.py` — `cash_collected_this_month()`, `delinquent_balance_total()`, `inventory_investment()`

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

### Sign-in in tests

Tests never bypass authentication. `admin_client` and `employee_client` create a
user and sign in through the real `POST /login`, sending the `Origin` header a
browser would. `client` is the admin client, so most route tests are unchanged;
`anon_client` is signed out. `tests/test_access.py` walks every route the app has
and fails if one is reachable without sign-in, or by an employee outside an
explicit allow-list.

### Fixture Summary

| Fixture | Scope | Purpose |
|---|---|---|
| `engine` | session | One SQLite engine; schema created once |
| `db_connection` | function | Per-test connection + outer rollback transaction |
| `db` | function | ORM session for unit/service tests |
| `client` | function | TestClient signed in as an admin (alias of `admin_client`) |
| `anon_client`, `admin_client`, `employee_client` | function | TestClients signed out / in as admin / in as employee |

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
