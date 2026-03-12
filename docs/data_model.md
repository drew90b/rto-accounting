# RTO Accounting System — Data Model

Generated from `app/models/`. All tables use PostgreSQL via SQLAlchemy + Alembic.

---

## Design Note: Human-Readable IDs

All tables include a human-readable ID column (e.g., `customer_id = "C-0001"`, `unit_id = "U-0001"`). These columns are **nullable** in both the database schema and ORM models. This is intentional:

1. A row is inserted without a human-readable ID (the column is null at insert time).
2. `db.flush()` causes the database to assign the auto-increment integer primary key (`id`).
3. Application code then generates the human-readable ID from that integer: e.g., `customer.customer_id = f"C-{customer.id:04d}"`.
4. `db.commit()` persists the fully-populated record.

Migration `003_nullable_human_readable_ids` removed the `NOT NULL` constraint from all human-readable ID columns to allow this flush-then-set pattern. The ORM models mirror this with `nullable=True`. In practice, every committed record will have a human-readable ID set — the column is never null after a successful commit.

---

## Table of Contents

1. [customers](#customers)
2. [vendors](#vendors)
3. [units](#units)
4. [repair\_jobs](#repair_jobs)
5. [sales](#sales)
6. [lease\_accounts](#lease_accounts)
7. [payments](#payments)
8. [transactions](#transactions)
9. [documents](#documents)
10. [exceptions](#exceptions)
11. [Enums Reference](#enums-reference)
12. [Entity Relationships](#entity-relationships)

---

## customers

Stores buyers, lessees, and repair customers.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| customer_id | varchar(20) | NO | — | Human-readable ID, e.g. `C-0001`. Unique. |
| full_name | varchar(100) | NO | — | |
| phone | varchar(20) | YES | — | |
| email | varchar(100) | YES | — | |
| address | text | YES | — | |
| notes | text | YES | — | |
| status | varchar(20) | NO | `active` | `active`, `inactive` |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

---

## vendors

Stores suppliers, auction houses, and parts sources.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| vendor_id | varchar(20) | NO | — | Human-readable ID, e.g. `V-0001`. Unique. |
| name | varchar(100) | NO | — | |
| phone | varchar(20) | YES | — | |
| email | varchar(100) | YES | — | |
| address | text | YES | — | |
| notes | text | YES | — | |
| created_at | timestamp | YES | now() | |

---

## units

Inventory register for cars and golf carts.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| unit_id | varchar(20) | NO | — | Human-readable ID, e.g. `U-0001`. Unique. |
| unit_type | varchar(20) | NO | — | `car`, `golf_cart` |
| business_line | varchar(20) | NO | — | `car`, `golf_cart` |
| vin_serial | varchar(50) | YES | — | VIN for cars, serial for golf carts |
| year | integer | YES | — | Model year |
| make | varchar(50) | YES | — | e.g. Toyota, Club Car |
| model | varchar(100) | YES | — | e.g. Camry, Onward |
| purchase_date | date | YES | — | |
| purchase_source | varchar(100) | YES | — | Auction, dealer, private, etc. |
| acquisition_cost | numeric(10,2) | YES | — | Total purchase cost |
| status | varchar(30) | NO | `acquired` | See unit status enum below |
| repair_status | varchar(50) | YES | — | Free-form repair status note |
| sales_status | varchar(50) | YES | — | Free-form sales status note |
| linked_customer_id | integer | YES | — | FK → customers.id. Set when sold or leased. |
| notes | text | YES | — | |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

**Unit status values:** `acquired`, `in_inspection`, `in_repair`, `waiting_parts`, `frontline_ready`, `sold`, `leased_rto_active`, `closed`, `returned_special_review`

**Indexes:** `status`, `business_line`

---

## repair_jobs

Tracks both internal reconditioning and customer-paid repair work.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| job_id | varchar(20) | NO | — | Human-readable ID, e.g. `J-0001`. Unique. |
| business_line | varchar(20) | NO | — | `car`, `golf_cart` |
| unit_id | integer | YES | — | FK → units.id. Set for internal recon. |
| customer_id | integer | YES | — | FK → customers.id. Set for customer repair. |
| job_type | varchar(30) | NO | — | `internal_recon`, `customer_repair` |
| open_date | date | NO | — | |
| close_date | date | YES | — | Null while job is open |
| status | varchar(20) | NO | `open` | `open`, `in_progress`, `waiting_parts`, `complete`, `cancelled` |
| labor_amount | numeric(10,2) | YES | 0 | |
| materials_amount | numeric(10,2) | YES | 0 | |
| total_billed_amount | numeric(10,2) | YES | 0 | For customer repairs; 0 for internal recon |
| notes | text | YES | — | |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

---

## sales

Records outright sale events for cars and golf carts.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| sale_id | varchar(20) | NO | — | Human-readable ID, e.g. `S-0001`. Unique. |
| customer_id | integer | NO | — | FK → customers.id |
| unit_id | integer | NO | — | FK → units.id |
| sale_date | date | NO | — | |
| business_line | varchar(20) | NO | — | `car`, `golf_cart` |
| sale_amount | numeric(10,2) | NO | — | Agreed sale price |
| down_payment | numeric(10,2) | YES | 0 | |
| fees | numeric(10,2) | YES | 0 | Doc fees, tags, etc. |
| total_contract_amount | numeric(10,2) | YES | — | Total of sale + fees |
| status | varchar(20) | NO | `pending` | `pending`, `complete`, `cancelled` |
| notes | text | YES | — | |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

---

## lease_accounts

Tracks rent-to-own and lease deal setups. Payment history lives in `payments`.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| lease_id | varchar(20) | NO | — | Human-readable ID, e.g. `L-0001`. Unique. |
| customer_id | integer | NO | — | FK → customers.id |
| unit_id | integer | NO | — | FK → units.id |
| deal_date | date | NO | — | Date deal was set up |
| original_agreed_amount | numeric(10,2) | YES | — | Total agreed payoff amount |
| down_payment | numeric(10,2) | YES | 0 | |
| financed_balance | numeric(10,2) | YES | — | original_agreed_amount − down_payment |
| scheduled_payment_amount | numeric(10,2) | YES | — | Per-payment amount |
| payment_frequency | varchar(20) | YES | `monthly` | `weekly`, `bi_weekly`, `monthly` |
| status | varchar(20) | NO | `active` | `active`, `paid_off`, `defaulted`, `cancelled` |
| ~~outstanding_balance~~ | ~~numeric(10,2)~~ | — | — | **Deprecated.** Renamed to `outstanding_balance_deprecated` in migration 002. Do not read or write. |
| *(computed)* | — | — | — | Remaining balance = `financed_balance − SUM(payments.amount)`. Calculated at runtime via `lease_service.calculate_remaining_balance(lease, db)`. |
| delinquency_status | varchar(20) | YES | `current` | `current`, `late`, `delinquent`, `default` |
| notes | text | YES | — | |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

**Indexes:** `delinquency_status`

---

## payments

Individual payment events. Separate from sale and lease setup records.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| payment_id | varchar(20) | NO | — | Human-readable ID, e.g. `P-0001`. Unique. |
| customer_id | integer | NO | — | FK → customers.id |
| payment_date | date | NO | — | |
| amount | numeric(10,2) | NO | — | |
| payment_method | varchar(20) | NO | — | `cash`, `check`, `card`, `transfer`, `other` |
| sale_id | integer | YES | — | FK → sales.id. Set if applied to a sale. |
| lease_account_id | integer | YES | — | FK → lease_accounts.id. Set if applied to RTO. |
| repair_job_id | integer | YES | — | FK → repair_jobs.id. Set if applied to a repair. |
| notes | text | YES | — | |
| entered_by | varchar(50) | YES | — | |
| created_at | timestamp | YES | now() | |

> One of `sale_id`, `lease_account_id`, or `repair_job_id` should be set to indicate what the payment was applied to. All three may be null for unmatched payments (triggers an exception).

---

## transactions

Financial event log. Every financially significant event should have a transaction record. Links to all other business entities.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| transaction_id | varchar(20) | NO | — | Human-readable ID, e.g. `T-00001`. Unique. |
| transaction_date | date | NO | — | Date of the business event |
| entry_date | date | YES | — | Date record was entered in the system |
| transaction_type | varchar(30) | NO | — | See transaction type enum below |
| business_line | varchar(20) | NO | — | `car`, `golf_cart` |
| revenue_stream | varchar(30) | YES | — | See revenue stream enum below. Null for costs. |
| vendor_id | integer | YES | — | FK → vendors.id. Set for purchases. |
| customer_id | integer | YES | — | FK → customers.id. Set for revenue events. |
| amount | numeric(10,2) | NO | — | |
| description | text | YES | — | |
| unit_id | integer | YES | — | FK → units.id |
| repair_job_id | integer | YES | — | FK → repair_jobs.id |
| sale_id | integer | YES | — | FK → sales.id |
| lease_account_id | integer | YES | — | FK → lease_accounts.id |
| category | varchar(50) | YES | — | e.g. parts, labor, overhead, inventory_purchase |
| payment_method | varchar(20) | YES | — | `cash`, `check`, `card`, `transfer`, `other` |
| receipt_attached | boolean | YES | false | |
| coding_complete | boolean | YES | false | |
| review_status | varchar(20) | YES | `pending` | `pending`, `reviewed`, `approved` |
| exception_status | varchar(20) | YES | `none` | `none`, `flagged`, `resolved` |
| entered_by | varchar(50) | YES | — | |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

**Indexes:** `transaction_date`, `business_line`, `revenue_stream`

---

## documents

Receipt and document metadata. Files stored at `storage/receipts/{record_type}/{record_id}/{filename}`.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| document_id | varchar(20) | NO | — | Human-readable ID, e.g. `D-0001`. Unique. |
| linked_record_type | varchar(30) | NO | — | `transaction`, `unit`, `repair_job`, `sale`, `lease_account`, `payment` |
| linked_record_id | integer | NO | — | ID of the linked record |
| file_path | varchar(500) | NO | — | Absolute path on disk |
| original_filename | varchar(200) | NO | — | Original uploaded filename |
| file_type | varchar(50) | YES | — | File extension, e.g. `.pdf`, `.jpg` |
| upload_timestamp | timestamp | YES | now() | |
| uploaded_by | varchar(50) | YES | — | |
| notes | text | YES | — | |

---

## exceptions

Exception queue for incomplete, missing, or suspicious records.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | integer | NO | autoincrement | Primary key |
| exception_id | varchar(20) | NO | — | Human-readable ID, e.g. `E-0001`. Unique. |
| exception_type | varchar(50) | NO | — | See exception type enum below |
| linked_record_type | varchar(30) | YES | — | `transaction`, `unit`, `repair_job`, `sale`, `lease_account`, `payment` |
| linked_record_id | integer | YES | — | ID of the linked record |
| opened_date | date | NO | — | |
| owner | varchar(50) | YES | — | Person responsible for resolving |
| status | varchar(20) | NO | `open` | `open`, `in_review`, `resolved`, `dismissed` |
| notes | text | YES | — | |
| target_resolution_date | date | YES | — | |
| resolution_action | text | YES | — | What was done to resolve |
| audit_history | text | YES | — | Auto-appended on status changes |
| created_at | timestamp | YES | now() | |
| updated_at | timestamp | YES | now() | |

**Indexes:** `status`

---

## Enums Reference

### business_line
| Value | Description |
|---|---|
| `car` | Car business line |
| `golf_cart` | Golf cart business line |

### transaction_type
| Value | Description |
|---|---|
| `purchase` | Inventory or parts purchase |
| `sale` | Outright sale of a unit |
| `charge` | Fee or charge billed to a customer |
| `collection` | RTO / lease payment received |
| `repair_revenue` | Revenue from a customer repair job |
| `parts_revenue` | Revenue from parts sales |
| `labor_cost` | Internal labor cost |
| `materials_cost` | Parts or materials cost |
| `overhead` | General overhead spend |
| `adjustment` | Manual correction or adjustment |

### revenue_stream
| Value | Business Line | Description |
|---|---|---|
| `golf_cart_sale` | golf_cart | Golf cart outright sale |
| `golf_cart_repair` | golf_cart | Golf cart customer repair |
| `golf_cart_parts_sale` | golf_cart | Golf cart parts sold |
| `car_sale` | car | Car outright sale |
| `car_rto_lease` | car | Car rent-to-own / lease collection |
| `car_repair` | car | Car customer repair |

### exception_type
| Value | Description |
|---|---|
| `missing_receipt` | No receipt attached to a transaction |
| `missing_assignment` | Transaction not linked to a unit, job, or overhead |
| `missing_coding` | Category or coding incomplete |
| `duplicate_suspected` | Possible duplicate entry detected |
| `review_needed` | Flagged for manual review |
| `invalid_status_transition` | Record moved to an invalid status |
| `inventory_variance` | Physical count does not match records |
| `close_blocker` | Blocks month-end close |
| `missing_revenue_stream` | Revenue transaction has no revenue stream |
| `unmatched_customer_payment` | Payment received but not applied to a record |
| `negative_balance_inconsistency` | Balance calculation is negative unexpectedly |
| `missing_linked_unit_or_customer` | Transaction or job missing required link |

---

## Entity Relationships

```
customers ──< units                (linked_customer_id — sold/leased units)
customers ──< sales                (customer_id)
customers ──< lease_accounts       (customer_id)
customers ──< payments             (customer_id)
customers ──< repair_jobs          (customer_id — customer-paid repairs)
customers ──< transactions         (customer_id)

units ──< repair_jobs              (unit_id — internal recon)
units ──< sales                    (unit_id)
units ──< lease_accounts           (unit_id)
units ──< transactions             (unit_id)

repair_jobs ──< transactions       (repair_job_id)
repair_jobs ──< payments           (repair_job_id)

sales ──< payments                 (sale_id)
sales ──< transactions             (sale_id)

lease_accounts ──< payments        (lease_account_id)
lease_accounts ──< transactions    (lease_account_id)

vendors ──< transactions           (vendor_id)

{transaction, unit, repair_job, sale, lease_account, payment} ──< documents
{transaction, unit, repair_job, sale, lease_account, payment} ──< exceptions
```

### Design rules
- Do not merge transaction_type, business_line, and revenue_stream into one field.
- Payment events are always separate from sale or lease setup records.
- The `transactions` table is the financial event log. It references the more specific business entity tables — it does not replace them.
- Outstanding balance on `lease_accounts` is updated manually as payments are entered.
