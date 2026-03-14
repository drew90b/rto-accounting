# Financial Record Architecture

This document explains the four primary financial object types in this system and how they work together. Understanding the distinction is important for operators using the system daily, owners reviewing records, and developers building on top of it.

---

## The Four Objects

### 1. Payment

A **Payment** is a cash collection event — money that actually changed hands.

- Recorded when a customer pays: a down payment, a weekly RTO installment, or a repair bill
- Has a date, an amount, and a payment method (cash, check, card, etc.)
- Always linked to exactly one source: a sale, a lease account, or a repair job
- Does not carry business-line or revenue-stream classification — that lives on the Transaction

**A Payment answers:** *"When did the customer pay, how much, and how?"*

**Examples:**
- $500 cash down payment on a car RTO deal
- $150 weekly collection on an active RTO account
- $240 card payment for a golf cart repair job

---

### 2. Transaction

A **Transaction** is a classified financial event — a single entry in the business's financial event log.

- Created automatically by the system for every significant revenue event (sale finalized, repair job closed, RTO payment collected)
- Or created manually for costs: overhead, parts purchases, auction fees, etc.
- Carries full accounting classification: `business_line`, `transaction_type`, `revenue_stream`
- Links to the relevant business records: unit, customer, repair job, sale, or lease account
- Has review fields: `coding_complete`, `receipt_attached`, `review_status`
- This is what the accountant reviews and what appears in monthly exports

**A Transaction answers:** *"What type of financial activity happened, for which business line and revenue stream, and is it reviewed?"*

**Examples:**
- Sale transaction: `car_sale` revenue stream, linked to sale S-0012 and unit U-0007
- Collection transaction: `car_rto_lease` revenue stream, linked to lease L-0003
- Cost transaction: `labor_cost` type, linked to repair job J-0009, no revenue stream

Every revenue event generates at least one Transaction automatically. Costs are entered manually or via repair job workflows.

---

### 3. Invoice

An **Invoice** is a customer-facing billing document — a persistent, printable record of what was charged and what was collected.

- Auto-generated for every completed revenue event: sale finalization, repair job close, RTO payment
- Has line items describing what was charged
- Shows the amount collected and any remaining balance
- Printed directly from the browser (HTML — no PDF library required)
- Stored as a record in the database with a status of `open` or `paid`
- Each invoice gets a human-readable number: `INV-00001`

**An Invoice answers:** *"What is the customer's receipt or billing document for this transaction?"*

**Examples:**
- Sale invoice: line item for the vehicle, down payment applied, remaining balance shown
- Repair invoice: labor and materials line items, payment history, balance due
- RTO payment receipt: amount collected this week, running lease balance

**Important:** Invoices are the customer-facing artifact. The Transaction is the accounting record. They are related but serve different purposes. Do not treat an invoice as the record of revenue — look at Transactions for that.

---

### 4. Lease Balance (Remaining Balance)

A **Lease Balance** is not a stored field — it is computed on demand and answers: *"How much does this customer still owe on their RTO deal right now?"*

```
remaining_balance = financed_balance − SUM(all payments linked to this lease)
```

Where:
- `financed_balance = original_agreed_amount − down_payment`
- `SUM(payments)` = all Payment records with `lease_account_id` matching this account

The system computes this at runtime via `lease_service.calculate_remaining_balance(lease, db)`. It is never stored as a column because a stored balance could drift out of sync with the payment history. The payment records are always the source of truth.

**A Lease Balance answers:** *"What is the live payoff amount for this account?"*

---

## How They Work Together

A single RTO payment event creates all four objects:

```
Customer pays $150 weekly RTO installment
        │
        ├── Payment record created
        │     customer_id, lease_account_id, date, amount=$150, method=cash
        │
        ├── Transaction record created (auto)
        │     type=collection, business_line=car, revenue_stream=car_rto_lease
        │     amount=$150, linked to lease + customer
        │
        ├── Invoice record created (auto)
        │     type=rto_payment, invoice_number=INV-00047
        │     line item: "Weekly RTO payment — $150"
        │     shows remaining lease balance
        │
        └── Lease Balance recalculates at next read
              remaining = financed_balance − (all prior payments + $150)
```

---

## Summary Table

| Object | Created by | What it stores | Primary audience |
|---|---|---|---|
| Payment | User action / service | Collection event: date, amount, method | Owner, office admin |
| Transaction | Auto or manual entry | Financial event + accounting classification | Accountant, owner |
| Invoice | Auto on revenue event | Printable billing document with line items | Customer, office admin |
| Lease Balance | Computed at runtime | Not stored — derived from payments | Owner, collections staff |

---

## Common Misconceptions

**"The lease balance is the `outstanding_balance` column."**
No. That column was deprecated in migration 002 and renamed to `outstanding_balance_deprecated`. The live balance is always computed from payment history, never stored directly.

**"The invoice is the accounting record."**
No. The Invoice is the customer-facing document. The Transaction is the accounting record that gets reviewed, coded, and exported. An invoice can be voided or reprinted without affecting the transaction log.

**"I need to create a Transaction manually every time a payment is made."**
No. Services auto-create Transactions on every automated revenue event (sale, repair close, RTO payment). Manual Transaction entry is reserved for costs, overhead, and edge cases outside the standard workflows.

**"Payment amount and transaction amount should always match."**
Usually yes for collection transactions. But a repair job will have one Transaction for the full billed revenue at close time, and separate Payment records as the customer pays over time — those amounts may differ if the job is partially paid.
