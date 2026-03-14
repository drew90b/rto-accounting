# Owner Demo Workflow

A practical walkthrough of the system from the owner's perspective. Use this to demo the system to a new staff member, a business partner, or an accountant. Also useful as onboarding for a new office administrator.

This workflow follows the natural lifecycle of a deal: unit in → customer in → repair → sale or RTO → payment → review.

---

## Scenario

A 2019 Honda Accord was purchased at auction for $6,500. It needs recon work before it goes frontline. A customer comes in and takes it on a rent-to-own deal for $12,000 agreed amount with $800 down. They make their first weekly payment of $200.

---

## Step 1 — Add the Unit

**Navigate:** Units → Add Unit

Fill in:
- Unit type: Car
- Business line: Car
- VIN: (enter VIN if available)
- Year / Make / Model: 2019 Honda Accord
- Purchase date: today
- Purchase source: Auction
- Acquisition cost: $6,500
- Status: Acquired

**What happens:** A unit record is created with ID `U-0001`. This is now the system record for this car.

**What to check:** The unit appears in the Units list with status "Acquired." All future activity — repair costs, sale, lease — will link back to this record.

---

## Step 2 — Log the Purchase Transaction

**Navigate:** Transactions → Add Transaction

Fill in:
- Transaction date: today
- Type: Purchase
- Business line: Car
- Amount: $6,500
- Description: "2019 Honda Accord — auction purchase"
- Unit: U-0001 (this car)
- Receipt attached: Yes (upload receipt in Documents if available)

**What happens:** A transaction record is created classifying this as a car purchase cost linked to the unit.

**What to check:** The transaction appears in the Transactions list. The unit's linked activity now includes this cost, which will feed into unit profitability reporting.

---

## Step 3 — Open a Recon Repair Job

**Navigate:** Repair Jobs → Add Repair Job

Fill in:
- Job type: Internal Recon
- Business line: Car
- Unit: U-0001
- Status: Open
- Notes: "Oil change, brakes, detail"

**What happens:** A repair job record is created with ID `J-0001`. The unit status can be updated to "In Repair."

As work is done, return here and log costs:
- Add transactions for labor and materials linked to this job
- Update labor amount and materials amount on the job record as work progresses

**What to check:** The repair job is visible on the Repair Jobs list. Costs accumulate against this job, not floating unattached.

---

## Step 4 — Add the Customer

**Navigate:** Customers → Add Customer

Fill in:
- Full name: Maria Gonzalez
- Phone: (863) 555-0122
- Address: Lakeland, FL

**What happens:** A customer record is created with ID `C-0001`.

**What to check:** The customer appears in the Customers list. All future sales, leases, and payments will link to this record.

---

## Step 5 — Record the Sale

**Navigate:** Sales → Add Sale

Fill in:
- Customer: C-0001 — Maria Gonzalez
- Unit: U-0001 — 2019 Honda Accord
- Sale date: today
- Business line: Car
- Sale amount: $12,000
- Down payment: $800
- Payment method: Cash

**What happens (automatic):**
- A sale record is created with status `Pending`
- A Payment record is created for the $800 down payment
- A Transaction is created classifying this as a `car_sale` revenue event
- An Invoice is auto-generated for the sale
- The unit status updates to `Sold` and links to Maria Gonzalez

**What to check:** The sale appears in the Sales list as Pending. Navigate to the sale and click "Print Receipt" to see the printable sale document.

---

## Step 6 — Set Up the RTO / Lease Account

**Navigate:** Lease Accounts → Add Lease Account

Fill in:
- Customer: C-0001 — Maria Gonzalez
- Unit: U-0001 — 2019 Honda Accord
- Deal date: today
- Original agreed amount: $12,000
- Down payment: $800
- Financed balance: $11,200 (system shows this)
- Scheduled payment: $200
- Frequency: Weekly
- Status: Active
- Delinquency: Current

**What happens:** A lease account record is created with ID `L-0001`. The financed balance is $11,200 (agreed amount minus down payment). The unit status updates to `Leased / RTO Active`.

**What to check:** Navigate to Lease Accounts. The account shows Maria Gonzalez, unit U-0001, and a remaining balance of $11,200.

---

## Step 7 — Record the First RTO Payment

**Navigate:** Lease Accounts → find L-0001 → Record Payment

Fill in:
- Payment date: today
- Amount: $200
- Payment method: Cash

**What happens (automatic):**
- A Payment record is created for $200
- A Transaction is created: type `collection`, revenue stream `car_rto_lease`
- An Invoice (RTO payment receipt) is auto-generated showing $200 collected and $11,000 remaining
- The remaining balance recalculates immediately

**What to check:** Return to the lease account. Remaining balance now shows $11,000. Navigate to Invoices and find the RTO payment receipt — it is ready to print for Maria.

---

## Step 8 — Review the Dashboard

**Navigate:** Dashboard (home screen)

What the dashboard shows today:
- Open exceptions, if any
- Delinquent lease accounts
- Unit counts by status
- Quick action links for common entry tasks

**What to look for:**
- No open exceptions on the records just created
- Maria's lease shows as Current (not delinquent)
- The unit shows as Leased / RTO Active

---

## Step 9 — Review the Transaction Register

**Navigate:** Transactions → filter by today's date

You should see:
- 1 purchase transaction: $6,500 car purchase (cost)
- 1 sale transaction: $12,000 car sale (revenue, car_sale stream)
- 1 collection transaction: $200 RTO payment (revenue, car_rto_lease stream)

**What to check:** Every transaction has a business line, type, and amount. The sale and collection transactions will have revenue streams assigned. The purchase transaction is a cost with no revenue stream.

This is what the accountant sees when reviewing for month-end close.

---

## Step 10 — Print an Invoice

**Navigate:** Invoices

Find the invoice for this RTO payment or sale. Click "Print" to open the printable document. The invoice shows:
- Customer name
- Unit description
- Line items
- Amount collected
- Remaining balance (for RTO accounts)

Print or save as PDF from the browser.

---

## What this demonstrates

After this walkthrough, the system has captured:

| Record | Description |
|---|---|
| Unit U-0001 | 2019 Honda Accord — $6,500 acquisition |
| Repair Job J-0001 | Internal recon (costs tracked separately) |
| Customer C-0001 | Maria Gonzalez |
| Sale S-0001 | $12,000 car sale, $800 down |
| Lease L-0001 | $11,200 financed balance, $200/week |
| Payment P-0001 | $800 down payment |
| Payment P-0002 | $200 first RTO payment |
| Transaction T-0001 | Purchase — $6,500 (cost) |
| Transaction T-0002 | Sale — $12,000 (car_sale revenue) |
| Transaction T-0003 | Collection — $200 (car_rto_lease revenue) |
| Invoice INV-0001 | Sale receipt |
| Invoice INV-0002 | RTO payment receipt |

Every dollar is accounted for, linked to a unit and customer, classified by business line and revenue stream, and available for month-end export.

---

## Common questions during a demo

**"What if a payment comes in and we don't know which lease it belongs to?"**
Record it as a Payment anyway and flag it as an exception (type: `unmatched_customer_payment`). Resolve the exception when the account is identified.

**"What if we make a mistake on a transaction amount?"**
Edit the transaction directly. The system does not prevent edits, but edits are visible and audit-trailed in the exception and review system.

**"How does the accountant get the monthly data?"**
Every list view has an Excel export button. The full month-end export pack (Phase 2) will produce a single workbook with all required tabs.

**"Can multiple people use this at once?"**
Yes — it is a web application. Authentication and role-based access are planned for Phase 5.
