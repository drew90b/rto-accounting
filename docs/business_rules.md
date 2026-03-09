# RTO Accounting System — Business Rules

Derived from `CLAUDE.md` and the operational model. These rules govern data entry,
status transitions, classification, and exception handling throughout the system.

---

## Table of Contents

1. [Revenue Stream Rules](#1-revenue-stream-rules)
2. [Business Line Rules](#2-business-line-rules)
3. [Unit Lifecycle Rules](#3-unit-lifecycle-rules)
4. [Transaction Classification Rules](#4-transaction-classification-rules)
5. [Repair Job Rules](#5-repair-job-rules)
6. [Sale Rules](#6-sale-rules)
7. [Lease / RTO Rules](#7-lease--rto-rules)
8. [Payment Rules](#8-payment-rules)
9. [Exception Rules](#9-exception-rules)
10. [Document Rules](#10-document-rules)
11. [Month-End Rules](#11-month-end-rules)

---

## 1. Revenue Stream Rules

Revenue streams must never be collapsed into a generic category. Every revenue transaction must have an explicit `revenue_stream` value.

### Mapping: business_line + transaction_type → revenue_stream

| Business Line | Transaction Type | Revenue Stream |
|---|---|---|
| `golf_cart` | `sale` | `golf_cart_sale` |
| `golf_cart` | `repair_revenue` | `golf_cart_repair` |
| `golf_cart` | `parts_revenue` | `golf_cart_parts_sale` |
| `car` | `sale` | `car_sale` |
| `car` | `collection` | `car_rto_lease` |
| `car` | `repair_revenue` | `car_repair` |

### Rules
- `revenue_stream` must be set on all revenue transactions (`sale`, `collection`, `repair_revenue`, `parts_revenue`, `charge`).
- `revenue_stream` must be null on cost transactions (`purchase`, `labor_cost`, `materials_cost`, `overhead`, `adjustment`).
- A transaction with a revenue `transaction_type` and no `revenue_stream` triggers a `missing_revenue_stream` exception.
- Revenue stream must match the `business_line` — a `golf_cart` transaction may not use a `car_*` revenue stream.

---

## 2. Business Line Rules

Every unit, repair job, sale, lease account, and transaction must be explicitly assigned to a business line.

| Business Line | Covers |
|---|---|
| `car` | Car sales, car RTO/lease, car repairs |
| `golf_cart` | Golf cart sales, golf cart repairs, parts sales |

### Rules
- `business_line` is required on every transaction, unit, repair job, and sale. It may never be null or ambiguous.
- When entering a transaction, `business_line` must match the linked unit's `business_line` if a unit is provided.
- Overhead transactions (e.g., utilities, insurance) still require a `business_line`. If truly shared, use the dominant line or split into two transactions and note accordingly.

---

## 3. Unit Lifecycle Rules

### Status flow

```
acquired
  → in_inspection
  → in_repair       (or waiting_parts)
  → frontline_ready
  → sold            (outright sale)
  → leased_rto_active  (RTO/lease deal)
  → closed          (deal fully complete)
  → returned_special_review  (repossession, dispute, or exception)
```

### Rules
- A unit status of `sold` or `leased_rto_active` requires a linked `customer_id` on the unit record.
- A unit may only have one active `sale` record and one active `lease_account` at a time.
- A unit should not have both an active `sale` and an active `lease_account` simultaneously.
- Moving a unit to `sold` or `leased_rto_active` without a linked customer triggers a `missing_linked_unit_or_customer` exception.
- A unit in `frontline_ready` should have no open `repair_jobs` of type `internal_recon`.
- A unit moved to `closed` must have no open exceptions or unresolved repair jobs.
- `returned_special_review` is used for repossessions, disputes, or situations requiring review before re-entering inventory.

---

## 4. Transaction Classification Rules

### Required fields by transaction type

| Transaction Type | revenue_stream | vendor_id | customer_id | unit_id |
|---|---|---|---|---|
| `purchase` | — | Required | — | Recommended |
| `sale` | Required | — | Required | Required |
| `charge` | Required | — | Required | — |
| `collection` | Required | — | Required | — |
| `repair_revenue` | Required | — | Required | Recommended |
| `parts_revenue` | Required | — | Required | — |
| `labor_cost` | — | Optional | — | Recommended |
| `materials_cost` | — | Required | — | Recommended |
| `overhead` | — | Optional | — | — |
| `adjustment` | — | — | — | — |

### Coding rules
- Every transaction must be assigned a `category` before `coding_complete` can be set to true.
- `coding_complete = false` with an amount > $500 triggers a `missing_coding` exception.
- `receipt_attached` must be true for all purchases and cost transactions before month-end close.
- `review_status` progresses: `pending` → `reviewed` → `approved`. Do not skip steps.
- `adjustment` transactions require a description explaining the reason.

### Cost assignment rules
Every cost transaction must be linked to one of:
- A specific unit (`unit_id`)
- A specific repair job (`repair_job_id`)
- Overhead (no unit/job link, `transaction_type = overhead`)
- A mixed case — enter with notes and flag for `review_needed`

A purchase transaction with no `unit_id`, no `repair_job_id`, and `transaction_type` not equal to `overhead` triggers a `missing_assignment` exception.

---

## 5. Repair Job Rules

### Job types
| Job Type | Description | Revenue? |
|---|---|---|
| `internal_recon` | Reconditioning an inventory unit before sale | No — costs only |
| `customer_repair` | Repair performed for a paying customer | Yes — `total_billed_amount` required |
| `customer_support_repair` | Repair performed for a BHPH customer at no cost or below cost to support the relationship | Optional — may be 0 or discounted |

### Rules
- `internal_recon` jobs must have a `unit_id`. They must not have a `total_billed_amount` (or it should be 0).
- `customer_repair` jobs should have a `customer_id`. `total_billed_amount` must be greater than 0.
- `customer_support_repair` jobs must have a `customer_id` and usually reference a `unit_id` associated with the customer's deal.
- For `customer_support_repair`: `total_billed_amount` may be 0, at cost, or below cost. These jobs represent goodwill repairs, warranty-style fixes, or retention repairs intended to keep a lease customer active.
- A job cannot be closed (`status = complete`) without a `close_date`.
- Costs charged to a repair job must be recorded as transactions with `repair_job_id` set.
- Revenue from a paying customer repair must be recorded as a separate transaction with `transaction_type = repair_revenue` and the matching `repair_job_id`.
- Revenue may or may not exist for `customer_support_repair` jobs depending on whether the repair was partially billed.
- Payments received for a repair job must be recorded in the `payments` table with `repair_job_id` set.
- If a repair job is marked `customer_support_repair` and `total_billed_amount = 0`, the system should treat the job as a cost of maintaining the lease portfolio, not as revenue.
- An `internal_recon` job on a unit that is already `sold` or `closed` triggers a `review_needed` exception.

---

## 6. Sale Rules

### Rules
- Every sale requires a `customer_id` and a `unit_id`.
- `sale_amount` must be greater than 0.
- `total_contract_amount` = `sale_amount` + `fees` (if not entered manually, calculate before close).
- When a sale is recorded, the linked unit's status should be updated to `sold` and `linked_customer_id` set.
- A sale event must have a corresponding `transaction` record with `transaction_type = sale` and the matching `sale_id`.
- Down payments must be recorded as a `payment` with `sale_id` set, not embedded in the sale record's `sale_amount`.
- A sale may not be marked `complete` if the unit still has an open `internal_recon` repair job.
- A unit that already has a `complete` sale should not have a second active sale created.

---

## 7. Lease / RTO Rules

### Rules
- Every lease account requires a `customer_id` and a `unit_id`.
- `original_agreed_amount` is the full payoff amount (not the per-payment amount).
- `financed_balance` = `original_agreed_amount` − `down_payment`.
- Remaining balance is **not stored** as a mutable column. It is computed at runtime: `financed_balance − SUM(payments.amount WHERE lease_account_id = lease.id)`. Use `lease_service.calculate_remaining_balance(lease, db)` wherever a current balance is needed.
- `scheduled_payment_amount` and `payment_frequency` define the expected payment schedule but the system does not auto-generate payment records — payments are entered manually.
- When a lease account is set up, the linked unit's status must be updated to `leased_rto_active`.
- RTO payment collections must be recorded as transactions with `transaction_type = collection`, `revenue_stream = car_rto_lease`, and `lease_account_id` set.
- Payments must also be recorded in the `payments` table with `lease_account_id` set.
- **Do not record both a `sale` record and a `lease_account` record for the same unit** unless the sale was a down payment event that converted to a lease — document this in notes.

### Delinquency status guidance
| Status | Meaning |
|---|---|
| `current` | Payments up to date |
| `late` | One payment missed or overdue by < 30 days |
| `delinquent` | 30–90 days past due |
| `default` | 90+ days past due or repo initiated |

Delinquency status is updated manually. A `delinquency_status` of `late`, `delinquent`, or `default` should appear on the dashboard warning panel.

---

## 8. Payment Rules

### Rules
- Every payment must have a `customer_id`, `payment_date`, `amount`, and `payment_method`.
- Each payment must be applied to exactly one of: a `sale`, a `lease_account`, or a `repair_job`. A payment with none of these set triggers an `unmatched_customer_payment` exception.
- Do not record a payment by modifying `sale_amount` directly — always create a new payment record in the `payments` table.
- Down payments are recorded as payments applied to the `sale_id`, not as a field on the sale record alone.
- Partial payments are allowed — they do not need to match the scheduled payment amount.
- After posting a payment to a lease account, the remaining balance is automatically recalculated at runtime — no manual field update is required.
- After posting a final payment to a lease account, set `status = paid_off` and update the linked unit to `closed`.

---

## 9. Exception Rules

### When to create an exception

Exceptions should be created when any of the following conditions exist:

| Exception Type | Trigger Condition |
|---|---|
| `missing_receipt` | A purchase or cost transaction has `receipt_attached = false` after 7 days |
| `missing_assignment` | A cost transaction has no `unit_id`, `repair_job_id`, and is not `overhead` |
| `missing_coding` | A transaction has `coding_complete = false` and amount > $500 |
| `duplicate_suspected` | Same amount, same date, same vendor/customer appears twice |
| `review_needed` | Any record flagged by a user for manual review |
| `invalid_status_transition` | A unit or record moved to an illogical status (e.g., recon job opened on a sold unit) |
| `inventory_variance` | Physical count does not match unit records |
| `close_blocker` | Any condition that prevents month-end close |
| `missing_revenue_stream` | Revenue transaction has no `revenue_stream` |
| `unmatched_customer_payment` | Payment with no linked sale, lease account, or repair job |
| `negative_balance_inconsistency` | Computed remaining balance on a lease is negative (payments exceed financed_balance) |
| `missing_linked_unit_or_customer` | Sale, lease, or transaction missing a required link |

### Exception lifecycle
```
open → in_review → resolved
open → dismissed  (use sparingly; requires a note)
```

- An exception cannot be resolved without a `resolution_action` note.
- Status changes are appended to `audit_history` automatically.
- Open exceptions that are `close_blocker` type prevent month-end close from proceeding.
- All open exceptions should be reviewed as part of the month-end checklist.

---

## 10. Document Rules

### Rules
- Receipts and supporting documents are stored on disk under `storage/receipts/{record_type}/{record_id}/`.
- The database stores metadata only — file path, original filename, upload date, uploader, and notes.
- A document may be linked to any of: `transaction`, `unit`, `repair_job`, `sale`, `lease_account`, `payment`.
- There is no limit on the number of documents per record.
- Supported file types include PDF, JPG, PNG, XLSX, and DOCX. Any file type is accepted by the system.
- When a receipt is uploaded and linked to a transaction, `receipt_attached` on that transaction should be manually updated to `true`.
- Documents should not be deleted — if a file is incorrect, upload the correct version and note the discrepancy.

---

## 11. Month-End Rules

The following must be complete before a month is considered closed:

### Checklist
1. All transactions for the period have `receipt_attached = true`.
2. All transactions for the period have `coding_complete = true`.
3. All transactions for the period have `review_status = approved`.
4. No open exceptions of type `close_blocker` exist.
5. All open repair jobs have been reviewed — costs entered and statuses current.
6. All lease accounts have been reviewed — all payments posted, delinquency status current. (Remaining balance is computed automatically.)
7. Inventory unit statuses are accurate — no unit stuck in `in_repair` or `in_inspection` without a current note.
8. All payments received have been applied to the correct sale, lease, or repair job.
9. Revenue by stream has been reviewed for completeness and accuracy.
10. Month-end Excel export pack has been generated and saved.

### Export pack contents (per month)
- Transaction register (all transactions for the period)
- Categorized spend summary
- Revenue by stream summary
- Repair revenue and cost by job
- Unit profitability (acquisition cost + repair costs vs. sale or lease amount)
- Lease / RTO collections summary
- Sales summary
- Unresolved exceptions
- Inventory status snapshot
- Month-end review checklist

---

## Summary: Classification Hierarchy

Every financially significant event should be traceable through this chain:

```
Business Event
  → transaction record          (what happened, amount, date)
      → business_line           (car or golf_cart)
      → transaction_type        (purchase, sale, collection, etc.)
      → revenue_stream          (if revenue)
      → linked entity           (unit, repair_job, sale, lease_account)
      → vendor or customer      (who was involved)
      → receipt / document      (supporting file)
      → review_status           (pending → reviewed → approved)
```

No transaction should be left unlinked, uncoded, or without a receipt beyond the end of the month.
