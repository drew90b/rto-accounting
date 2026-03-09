# RTO Accounting System — System Workflows

Step-by-step operational workflows for common business events. Each workflow describes
what to enter, in what order, and what records get created.

---

## Table of Contents

1. [Add a Unit to Inventory](#1-add-a-unit-to-inventory)
2. [Open and Manage an Internal Recon Job](#2-open-and-manage-an-internal-recon-job)
3. [Record a Golf Cart Sale](#3-record-a-golf-cart-sale)
4. [Record a Car Outright Sale](#4-record-a-car-outright-sale)
5. [Set Up a Lease / RTO Account](#5-set-up-a-lease--rto-account)
6. [Record an RTO Payment](#6-record-an-rto-payment)
7. [Open and Close a Customer Repair Job](#7-open-and-close-a-customer-repair-job)
8. [Open a Customer Support Repair Job](#8-open-a-customer-support-repair-job)
9. [Record a Parts Sale](#9-record-a-parts-sale)
10. [Upload a Receipt or Document](#10-upload-a-receipt-or-document)
11. [Log a Transaction Manually](#11-log-a-transaction-manually)
12. [Resolve an Exception](#12-resolve-an-exception)
13. [Month-End Close Process](#13-month-end-close-process)

---

## 1. Add a Unit to Inventory

**Trigger:** A car or golf cart is purchased and enters inventory.

### Steps

1. **Create the unit record** → Units → Add Unit
   - Set `unit_type` and `business_line`
   - Enter year, make, model, VIN/serial
   - Set `purchase_date` and `purchase_source`
   - Enter `acquisition_cost`
   - Set `status = acquired`

2. **Log the purchase transaction** → Transactions → Log Transaction
   - `transaction_date` = purchase date
   - `transaction_type = purchase`
   - `business_line` = match the unit
   - `amount` = acquisition cost
   - `vendor_id` = auction house or seller
   - `unit_id` = the new unit
   - `category = inventory_purchase`
   - Upload bill of sale → set `receipt_attached = true`

3. **Advance status as work begins**
   - Move unit to `in_inspection` when inspection starts
   - Move to `in_repair` when recon work begins (also open a repair job — see Workflow 2)
   - Move to `waiting_parts` if parts are on order

**Records created:** 1 unit, 1 transaction

---

## 2. Open and Manage an Internal Recon Job

**Trigger:** A unit in inventory needs reconditioning before it can be sold.

### Steps

1. **Update unit status** → Units → Edit
   - Set `status = in_repair`

2. **Open the repair job** → Repair Jobs → New Job
   - `business_line` = match the unit
   - `job_type = internal_recon`
   - `unit_id` = the unit being reconditioned
   - `open_date` = today
   - `status = open`
   - Leave `customer_id` blank
   - Leave `total_billed_amount = 0`

3. **Log costs as work is done** → Transactions → Log Transaction (one per vendor/event)
   - `transaction_type = materials_cost` or `labor_cost`
   - `business_line` = match the unit
   - `unit_id` = the unit
   - `repair_job_id` = this job
   - `vendor_id` = parts supplier or shop
   - `category` = parts / labor / tires / etc.
   - Upload receipts → `receipt_attached = true`
   - Update `labor_amount` and `materials_amount` on the repair job as costs are entered

4. **Close the job when recon is complete** → Repair Jobs → Edit
   - Set `close_date` = today
   - Set `status = complete`

5. **Update unit status** → Units → Edit
   - Set `status = frontline_ready`

**Records created:** 1 repair job, N cost transactions

---

## 3. Record a Golf Cart Sale

**Trigger:** A golf cart is sold outright to a customer.

### Steps

1. **Ensure the customer exists** → Customers
   - Add customer if not already in the system

2. **Ensure the unit is frontline ready** → Units
   - Confirm `status = frontline_ready` and no open recon jobs

3. **Create the sale record** → Sales → Record Sale
   - `business_line = golf_cart`
   - `customer_id` = buyer
   - `unit_id` = golf cart being sold
   - `sale_date` = date of sale
   - `sale_amount` = agreed sale price
   - `down_payment` = amount paid today (if partial)
   - `fees` = any doc or other fees
   - `total_contract_amount` = sale_amount + fees
   - `status = complete` (if paid in full) or `pending` (if balance remains)

4. **Record the payment** → Payments → Record Payment
   - `customer_id` = buyer
   - `payment_date` = today
   - `amount` = amount received
   - `payment_method` = cash / check / card
   - `sale_id` = this sale

5. **Log the sale transaction** → Transactions → Log Transaction
   - `transaction_type = sale`
   - `business_line = golf_cart`
   - `revenue_stream = golf_cart_sale`
   - `customer_id` = buyer
   - `unit_id` = golf cart
   - `sale_id` = this sale
   - `amount` = sale_amount
   - `receipt_attached = true` (attach bill of sale)
   - `coding_complete = true`

6. **Update the unit** → Units → Edit
   - `status = sold`
   - `linked_customer_id` = buyer

**Records created:** 1 sale, 1 payment, 1 transaction. Unit status updated.

---

## 4. Record a Car Outright Sale

**Trigger:** A car is sold outright (not on RTO). Same flow as golf cart with car-specific fields.

### Steps

Follow the same steps as Workflow 3 with these differences:

- `business_line = car`
- `revenue_stream = car_sale`
- Confirm there is no open `lease_account` for this unit before creating a sale

**Records created:** 1 sale, 1 payment, 1 transaction. Unit status updated.

---

## 5. Set Up a Lease / RTO Account

**Trigger:** A car is placed on a rent-to-own or lease deal with a customer.

### Steps

1. **Ensure the customer exists** → Customers
   - Add customer if not already in the system

2. **Ensure the unit is available** → Units
   - Confirm unit has no active sale or lease

3. **Create the lease account** → Lease / RTO → Setup Account
   - `customer_id` = lessee
   - `unit_id` = car
   - `deal_date` = date of deal
   - `original_agreed_amount` = total payoff amount
   - `down_payment` = amount collected at signing
   - `financed_balance` = original_agreed_amount − down_payment
   - `scheduled_payment_amount` = per-payment amount
   - `payment_frequency` = weekly / bi_weekly / monthly
   - `status = active`
   - `delinquency_status = current`

4. **Record the down payment** → Payments → Record Payment
   - `customer_id` = lessee
   - `payment_date` = deal date
   - `amount` = down payment amount
   - `payment_method` = cash / check / etc.
   - `lease_account_id` = this lease

5. **Log the down payment transaction** → Transactions → Log Transaction
   - `transaction_type = collection`
   - `business_line = car`
   - `revenue_stream = car_rto_lease`
   - `customer_id` = lessee
   - `unit_id` = car
   - `lease_account_id` = this lease
   - `amount` = down payment
   - `description` = "Down payment — [customer name]"
   - `receipt_attached = true`

6. **Update the unit** → Units → Edit
   - `status = leased_rto_active`
   - `linked_customer_id` = lessee

**Records created:** 1 lease account, 1 payment, 1 transaction. Unit status updated.

---

## 6. Record an RTO Payment

**Trigger:** A customer makes a scheduled (or catch-up) payment on their lease/RTO account.

### Steps

1. **Open the lease account** → Lease / RTO → find the account → click **Record Payment**
   - The form shows: customer name, unit, and current remaining balance

2. **Fill in the payment form** (4 fields):
   - `payment_date` = date received (defaults to today)
   - `amount` = amount received (pre-filled with scheduled payment amount)
   - `payment_method` = cash / check / card / etc.
   - Notes = optional (e.g., "catch-up for Feb")

3. **Click Record Payment**
   - The system automatically creates both:
     - A payment record (`lease_account_id` set)
     - A collection transaction (`type=collection`, `business_line=car`, `revenue_stream=car_rto_lease`)
   - Remaining balance recalculates immediately from the new payment

4. **Review the lease account** → check the balance summary
   - Update `delinquency_status` if applicable
   - If this was the final payment: set `status = paid_off` and update the unit to `status = closed`

> **Shortcut path:** Lease / RTO list → click Lease ID → Record Payment button (top right)

**Records created:** 1 payment, 1 transaction. Remaining balance recalculates automatically.

---

## 7. Open and Close a Customer Repair Job

**Trigger:** A customer brings in a car or golf cart for paid repair work.

### Steps

1. **Ensure the customer exists** → Customers

2. **Open the repair job** → Repair Jobs → New Job
   - `business_line` = car or golf_cart
   - `job_type = customer_repair`
   - `customer_id` = customer
   - `unit_id` = their unit if applicable
   - `open_date` = today
   - `status = open`

3. **Log costs as work is done** → Transactions → Log Transaction
   - `transaction_type = materials_cost` or `labor_cost`
   - `repair_job_id` = this job
   - `customer_id` = customer
   - One transaction per vendor/cost event
   - Attach receipts

4. **When job is complete, update totals on the repair job** → Repair Jobs → Edit
   - Enter final `labor_amount`, `materials_amount`
   - Enter `total_billed_amount` = what the customer owes
   - Set `close_date` = today
   - Set `status = complete`

5. **Log the repair revenue transaction** → Transactions → Log Transaction
   - `transaction_type = repair_revenue`
   - `business_line` = match the job
   - `revenue_stream = car_repair` or `golf_cart_repair`
   - `customer_id` = customer
   - `repair_job_id` = this job
   - `amount` = total_billed_amount
   - `receipt_attached = true` (attach invoice)

6. **Record payment when collected** → Payments → Record Payment
   - `customer_id` = customer
   - `payment_date` = date collected
   - `amount` = amount paid
   - `payment_method` = cash / card / etc.
   - `repair_job_id` = this job

**Records created:** 1 repair job, N cost transactions, 1 revenue transaction, 1 payment.

---

## 8. Open a Customer Support Repair Job

**Trigger:** A goodwill, warranty-style, or retention repair is performed for a BHPH/lease customer at no charge or below cost.

### Steps

1. **Open the repair job** → Repair Jobs → New Job
   - `business_line` = car (typically)
   - `job_type = customer_support_repair`
   - `customer_id` = the lease customer
   - `unit_id` = the unit from their lease deal
   - `open_date` = today
   - `status = open`
   - Note in `notes` the reason (e.g., "goodwill repair to retain RTO customer")

2. **Log costs as work is done** → Transactions → Log Transaction
   - `transaction_type = materials_cost` or `labor_cost`
   - `repair_job_id` = this job
   - `unit_id` = their unit
   - Attach receipts — these costs flow against the lease portfolio

3. **Close the job** → Repair Jobs → Edit
   - Enter `labor_amount` and `materials_amount`
   - Set `total_billed_amount`:
     - If free to customer: `0`
     - If partially billed: enter the discounted amount
   - Set `close_date` and `status = complete`

4. **If any amount was billed**, log the revenue transaction → Transactions → Log Transaction
   - `transaction_type = repair_revenue`
   - `revenue_stream = car_repair`
   - `amount` = partial billed amount
   - `repair_job_id` = this job

5. **If `total_billed_amount = 0`**: no revenue transaction needed. The cost transactions are the complete record. The job is treated as a cost of maintaining the lease portfolio.

**Records created:** 1 repair job, N cost transactions, 0 or 1 revenue transaction.

---

## 9. Record a Parts Sale

**Trigger:** Golf cart parts are sold directly to a customer (no repair job involved).

### Steps

1. **Log the transaction** → Transactions → Log Transaction
   - `transaction_type = parts_revenue`
   - `business_line = golf_cart`
   - `revenue_stream = golf_cart_parts_sale`
   - `customer_id` = buyer (add customer if needed)
   - `amount` = sale price
   - `description` = parts sold
   - `payment_method` = cash / card / etc.
   - `receipt_attached = true`
   - `coding_complete = true`

2. **Record payment** → Payments → Record Payment
   - `customer_id` = buyer
   - `payment_date` = today
   - `amount` = amount collected
   - `payment_method` = match transaction

> Parts sales do not require a repair job record unless the parts are being installed as part of a job.

**Records created:** 1 transaction, 1 payment.

---

## 10. Upload a Receipt or Document

**Trigger:** A receipt, invoice, bill of sale, or supporting document needs to be attached to a record.

### Steps

1. **Go to Documents** → Documents
   - Select `linked_record_type` (transaction, unit, repair_job, sale, lease_account, payment)
   - Enter the `linked_record_id` (the numeric ID of the record)
   - Enter your name in `uploaded_by`
   - Select the file
   - Add a note if helpful (e.g., "Vendor invoice for AutoZone parts 2026-03-07")
   - Click Upload

2. **Update the linked transaction** → Transactions → Edit (if linking to a transaction)
   - Set `receipt_attached = true`

> Documents are stored at `storage/receipts/{record_type}/{record_id}/` on the server.
> Do not delete uploaded documents — upload a corrected version if needed and note the discrepancy.

**Records created:** 1 document record. File saved to disk.

---

## 11. Log a Transaction Manually

**Trigger:** Any financial event that needs to be recorded outside of an automated workflow (e.g., overhead expense, manual adjustment, miscellaneous cost).

### Steps

1. **Go to Transactions** → Log Transaction

2. **Fill in core fields:**
   - `transaction_date` = date the event occurred
   - `transaction_type` = what kind of event this is
   - `business_line` = car or golf_cart
   - `revenue_stream` = required if this is a revenue transaction
   - `amount`
   - `description` = what this is for

3. **Link to a record** (choose as applicable):
   - `vendor_id` for purchases and cost transactions
   - `customer_id` for revenue events
   - `unit_id` if tied to a specific unit
   - `repair_job_id`, `sale_id`, or `lease_account_id` if tied to a specific deal

4. **Set status fields:**
   - `receipt_attached` = check if receipt is in hand
   - `coding_complete` = check when category and linkage are confirmed
   - `review_status = pending` (leave for reviewer to advance)

5. **Upload receipt** if available → see Workflow 10

> If you are unsure how to classify a transaction, enter what you know and leave `coding_complete = false`. A reviewer will code it. Do not guess at revenue stream or category if uncertain.

**Records created:** 1 transaction.

---

## 12. Resolve an Exception

**Trigger:** An open exception appears in the queue and needs to be addressed.

### Steps

1. **Go to Exceptions** → find the exception

2. **Review the exception:**
   - Note the `exception_type` and `linked_record_type` / `linked_record_id`
   - Navigate to the linked record to understand the issue

3. **Take corrective action** on the underlying record:
   - Missing receipt → upload the document (Workflow 10), then set `receipt_attached = true` on the transaction
   - Missing coding → update `category`, `revenue_stream`, and set `coding_complete = true`
   - Unmatched payment → edit the payment record to set the correct `sale_id`, `lease_account_id`, or `repair_job_id`
   - Missing assignment → link the transaction to a unit, repair job, or set type to overhead
   - Balance inconsistency → review payments against the lease account; add a corrective payment with a negative amount and note if needed (remaining balance recalculates automatically)

4. **Update the exception** → Exceptions → Edit
   - Enter the action taken in `resolution_action`
   - Set `status = resolved`
   - Status change is recorded in `audit_history` automatically

> Use `dismissed` only when the exception is confirmed to not require action (e.g., a false duplicate flag). A note is required.

---

## 13. Month-End Close Process

**Trigger:** End of month. All activity for the period should be complete before running this process.

### Steps

#### Week before close — cleanup pass

1. **Review open repair jobs** → Repair Jobs → filter by `status = open` or `in_progress`
   - Confirm costs are entered and up to date
   - Close any jobs completed during the month

2. **Review open exceptions** → Exceptions → filter by `status = open`
   - Resolve or dismiss all exceptions
   - Escalate any `close_blocker` exceptions immediately

3. **Review uncoded transactions** → Transactions → filter by `coding_complete = false`
   - Code all transactions: set `category`, confirm `revenue_stream`, set `coding_complete = true`

4. **Review transactions missing receipts** → Transactions → filter by `receipt_attached = false`
   - Collect and upload missing receipts
   - Set `receipt_attached = true`

#### Close day

5. **Review lease accounts** → Lease / RTO
   - Confirm all payments received are posted (remaining balance recalculates automatically)
   - Update `delinquency_status` based on payment history

6. **Review unit statuses** → Units
   - Confirm all sold/leased units have correct status and linked customer
   - Flag any units stuck in `in_repair` or `in_inspection` with no recent activity

7. **Review all transactions for the period** → Transactions → filter by date range
   - Confirm all have `review_status = approved`
   - Advance any remaining from `pending` → `reviewed` → `approved`

8. **Confirm revenue by stream** → Transactions → filter by revenue streams one at a time
   - `golf_cart_sale`, `golf_cart_repair`, `golf_cart_parts_sale`
   - `car_sale`, `car_rto_lease`, `car_repair`
   - Check for missing or misclassified entries

#### Export

9. **Export the month-end pack** → use Export Excel on each module for the period:
   - Transactions (full register, filtered to the month)
   - Units (inventory snapshot)
   - Sales (sales for the month)
   - Lease Accounts (all active)
   - Payments (payments for the month)
   - Repair Jobs (jobs open or closed during the month)
   - Exceptions (all, including resolved)

10. **Save exports** to the monthly folder for accountant review.

**Outcome:** All records coded, receipted, reviewed, and approved. Export pack generated. Period is closed.

---

## Quick Reference: Record Creation by Event

| Business Event | Unit | Repair Job | Sale | Lease Acct | Payment | Transaction |
|---|---|---|---|---|---|---|
| Buy a unit | ✓ | — | — | — | — | ✓ purchase |
| Start recon | update | ✓ | — | — | — | — |
| Log recon cost | — | update | — | — | — | ✓ cost |
| Golf cart sale | update | — | ✓ | — | ✓ | ✓ sale |
| Car outright sale | update | — | ✓ | — | ✓ | ✓ sale |
| Setup RTO deal | update | — | — | ✓ | ✓ down pmt | ✓ collection |
| RTO payment | — | — | — | update | ✓ | ✓ collection |
| Customer repair | — | ✓ | — | — | ✓ | ✓ cost + ✓ revenue |
| Support repair (free) | — | ✓ | — | — | — | ✓ cost only |
| Parts sale | — | — | — | — | ✓ | ✓ parts_revenue |
| Upload receipt | — | — | — | — | — | update |
