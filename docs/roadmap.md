# RTO Accounting System — Roadmap

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ | Complete |
| 🔧 | In progress / partially built |
| 🔲 | Not started |

---

## Phase 1 — Core Data Entry (MVP) ✅

The foundational records and entry screens. Everything the business needs to capture daily activity.

| # | Feature | Status | Notes |
|---|---|---|---|
| 1.1 | Units register | ✅ | List, add, edit, export. All statuses and business lines. |
| 1.2 | Customer register | ✅ | List, add, edit, export. |
| 1.3 | Vendor register | ✅ | List, add, edit, Excel export. Registered at /vendors/. |
| 1.4 | Repair job entry | ✅ | Internal recon, customer repair, customer support repair. Close-job and record-payment workflows. |
| 1.5 | Sales entry | ✅ | Car and golf cart sales. Down payment, fees, status. Auto-creates transaction and payment on save. |
| 1.6 | Lease / RTO account entry | ✅ | Deal setup, balance tracking, delinquency status. Record-payment workflow. |
| 1.7 | Payments entry | ✅ | Applied to sale, lease, or repair job. |
| 1.8 | Transaction log | ✅ | All types, business lines, revenue streams, coding fields, date-range filter, export. |
| 1.9 | Receipt / document upload | ✅ | Upload and link to any record type. Download. |
| 1.10 | Exception queue | ✅ | Log, assign, resolve, audit history. |
| 1.11 | Dashboard | ✅ | Money metric cards (cash, delinquent balance, inventory investment, active exceptions). Delinquent leases panel. Quick actions. |
| 1.12 | Invoice system | ✅ | Auto-generated invoices on every revenue event (sale, repair, RTO payment). Printable HTML. Browsable list. |

---

## Phase 1.5 — Workflow Hardening

Quality of life improvements that reduce friction for daily users.

| # | Feature | Status | Notes |
|---|---|---|---|
| 1.5.1 | Inline form validation | 🔲 | Show errors next to fields, not just silent failure. |
| 1.5.2 | Confirmation messages | 🔲 | Flash message on every successful create/edit/delete. |
| 1.5.3 | Pagination | 🔧 | Purchases list is paged (50 per page, filters kept). Other list views still load all records. |
| 1.5.4 | Better search filters | 🔲 | Name search, date range, status filter across all list views. |
| 1.5.5 | Vendor UI | ✅ | List, add, edit, and Excel export. Registered at /vendors/. |
| 1.5.6 | Lease auto-calculated financed balance | ✅ | Financed balance derived server-side on create as agreed amount − down payment. Live JS display on form. Validation added. |
| 1.5.7 | Payment workflow — customer-first entry | 🔲 | Selecting a customer surfaces their active accounts, reducing lookup friction. |
| 1.5.8 | VIN enforcement | 🔲 | Make VIN required and unique for car units. Serial number required for golf carts. Prevents duplicate unit records. |
| 1.5.9 | Duplicate transaction guard on sale edit | 🔲 | Warn if sale amount is edited after the auto-created transaction is already on file. |

---

## Phase 2 — Reporting and Exports

Accountant-friendly views and month-end export packs. Enables external review without relying on the live app.

| # | Feature | Status | Notes |
|---|---|---|---|
| 2.1 | Per-module Excel export | ✅ | Units, customers, repair jobs, sales, leases, payments, transactions all have export. |
| 2.2 | Transaction register — date filter + export | ✅ | Date range filtering and export on transactions list. |
| 2.3 | Month-end export pack | 🔲 | One Excel workbook, multiple tabs: transaction register, revenue by stream, categorized spend, repair summary, lease collections, sales summary, unit profitability, exceptions, inventory snapshot. |
| 2.4 | Revenue by stream report | 🔲 | Summary view grouped by revenue stream with period totals. |
| 2.5 | Unit profitability view | 🔲 | Per-unit: acquisition cost + recon cost vs. sale amount or lease total collected. See `docs/unit_economics_vision.md` for full spec. |
| 2.6 | Repair job cost and revenue summary | 🔲 | Per job: costs in, revenue billed, margin. |
| 2.7 | Lease / RTO portfolio summary | 🔲 | All active accounts: balance, payment schedule, delinquency, total portfolio value. |
| 2.8 | Uncoded / unreviewed transaction report | 🔲 | Transactions with `coding_complete = false` or `review_status = pending`. |

---

## Phase 3 — Inventory and Variance

Physical count reconciliation and inventory management support.

| # | Feature | Status | Notes |
|---|---|---|---|
| 3.1 | Inventory count entry | 🔲 | Record a physical count of units on the lot. |
| 3.2 | Variance detection | 🔲 | Compare physical count to system unit records. Surface discrepancies. |
| 3.3 | Variance exception auto-creation | 🔲 | Auto-generate `inventory_variance` exception for each discrepancy found. |
| 3.4 | Inventory snapshot export | 🔲 | Point-in-time export of all unit statuses, costs, and locations. |

---

## Phase 4 — Dashboard and Cash Visibility

Operational awareness screens for the owner and site manager. Replace count-based widgets with money and control metrics.

| # | Feature | Status | Notes |
|---|---|---|---|
| 4.1 | Cash collected this month | ✅ | Sum of payments in current calendar month. Shown as metric card on dashboard. |
| 4.2 | Delinquent balance panel | ✅ | Total remaining balance on active late/delinquent/default accounts. Shown as metric card. |
| 4.3 | Inventory investment widget | ✅ | Sum of acquisition_cost for unsold units. Shown as metric card on dashboard. |
| 4.4 | Open exceptions panel | ✅ | Active exception count (open + in_review) shown as metric card. Panel preserved below. |
| 4.5 | Collections due this week | 🔲 | Lease accounts with scheduled payments due in next 7 days. |
| 4.6 | Units on lot summary | 🔲 | Count by status: frontline ready, in repair, waiting parts, etc. |
| 4.7 | Open repair jobs panel | 🔲 | Jobs open > N days with no status change. |
| 4.8 | Revenue trend | 🔲 | Month-over-month revenue by stream — simple table or chart. |

---

## Phase 5 — Authentication and Access Control

Role-based access for different user types. Required before any multi-user or external deployment.

| # | Feature | Status | Notes |
|---|---|---|---|
| 5.1 | User table and login | ✅ | Individual username/password sign-in, signed session cookie, forced password change on a temporary password. Accounts are created from the command line (`python -m app.scripts.users`); there is no signup page. |
| 5.2 | Role definitions | 🔧 | `admin` and `employee` only. Accountant, salesperson and mechanic roles are not built. |
| 5.3 | Role-based route protection | ✅ | Default-deny: every router requires sign-in, and everything except Purchases and receipt downloads is admin-only. Enforced on the server and covered by a route-inventory test. |
| 5.4 | Read-only accountant view | 🔲 | Accountant role: read-only access to registers and exports. |
| 5.5 | Mechanic limited view | 🔲 | Mechanic role: repair job status updates only. |
| 5.6 | Entered-by auto-populate | ✅ | Purchases record the signed-in user as entered-by and reviewer, separate from the purchaser. Other modules still use typed `entered_by`. |
| 5.7 | Audit log | 🔧 | Purchases only: `purchase_events` records create, edit (old → new), receipt, review and reopen. |
| 5.8 | Run under systemd | ✅ | `rto-web` service starts after reboot and restarts after a crash; Tailscale-only binding. Crash and reboot tests passed 2026-10-24. |
| 5.9 | HTTPS through Tailscale Serve | 🔲 | Then set `SESSION_COOKIE_SECURE=true` and update `ALLOWED_ORIGINS`. |
| 5.10 | Scheduled backups and an off-server copy | 🔲 | Backups are manual today (verified before the 009 deployment). |
| 5.11 | Belinda's office-computer onboarding | 🔲 | Waiting until she is available; the idle timeout already covers a computer that may be shared. |

---

## Phase 6 — Data Quality and Automation

Exception auto-detection and system-enforced data quality rules.

| # | Feature | Status | Notes |
|---|---|---|---|
| 6.1 | Auto-exception: missing receipt | 🔲 | Flag transactions with `receipt_attached = false` after 7 days. |
| 6.2 | Auto-exception: missing coding | 🔲 | Flag transactions > $500 with `coding_complete = false`. |
| 6.3 | Auto-exception: missing revenue stream | 🔲 | Flag revenue transactions with no `revenue_stream`. |
| 6.4 | Auto-exception: unmatched payment | 🔲 | Flag payments with no linked sale, lease, or repair job. |
| 6.5 | Duplicate detection | 🔲 | Flag same amount + date + vendor/customer appearing more than once. |
| 6.6 | Lease balance auto-update | ✅ | Remaining balance computed at runtime (`financed_balance − SUM(payments)`). `outstanding_balance` column deprecated in migration 002. |
| 6.7 | Unit status validation | 🔲 | Warn or block invalid status transitions (e.g., recon job on a sold unit). |
| 6.8 | Month-end close lock | 🔲 | Prevent edits to closed periods once approved. |

---

## Phase 7 — Acquisition Cost Breakdown

Improve unit cost tracking beyond a single `acquisition_cost` field.

| # | Feature | Status | Notes |
|---|---|---|---|
| 7.1 | Cost component fields on Unit | 🔲 | Add optional fields: purchase price, transport, title/fees, initial inspection. See `docs/unit_economics_vision.md`. |
| 7.2 | Unit economics report | 🔲 | Per-unit view: total investment vs. total revenue vs. collected to date. |
| 7.3 | Portfolio economics summary | 🔲 | All units in a sortable table with investment, agreed amount, collected, and margin. |

---

## Future — Square Integration

Square is used at the point of sale for card payments. It should eventually feed into this system for reconciliation, not replace it as the accounting layer.

**The model:**
- Square processes the physical card transaction at the counter
- This system is the system of record for all deal setup and financial history
- Square payment data (amount, date, card type, transaction ID) should flow into Payment records here
- A reconciliation queue should surface Square transactions not yet matched to a Payment in this system

**What this requires:**
- Square webhook or API polling to ingest completed transactions
- A review queue for unmatched Square activity
- Ability to link a Square transaction ID to an existing Payment record
- Reconciliation report: matched vs. unmatched Square activity for the period

**What Square is NOT:**
- Not the accounting system
- Not the deal setup layer
- Not the revenue classification layer
- Not the report source for month-end close

All of those remain in this system. Square feeds raw payment data; this system classifies, links, and reports it.

**Status:** Not started. Requires Phase 5 (authentication) to be in place first.

---

## Phase 8 — Mobile Usability

| Feature | Notes |
|---|---|
| Responsive layouts | Forms and lists readable on phone |
| Camera receipt upload | Take photo of receipt and upload directly |
| Quick payment entry | Streamlined form for recording a collection in the field |
| Large touch targets | Buttons sized for phone use |
| PWA install support | Optional — add to home screen on mobile |

---

## Immediate Next Steps (Suggested Order)

Business milestones (not yet done): capture November purchases and receipts in the app, then close November's books in December.

1. **Month-end export pack** — single workbook with all required tabs for accountant review
2. **Revenue by stream report** — summary view with period filter
3. **Unit profitability view** — investment vs. collected, per unit
4. **Lease auto-calculated financed balance** — compute from agreed amount minus down payment on form load
5. ~~**Authentication**~~ — done 2026-10-24 (individual sign-in, admin/employee roles). Still open: HTTPS, scheduled and off-server backups, Belinda's office computer (Phase 5)

---

## Out of Scope (Near-Term)

The following will not be built in the current phases:

- Full general ledger or double-entry accounting
- Payroll or employee management
- Full CRM (contact history, lead tracking, pipeline)
- Full loan servicing (amortization schedules, interest calculation)
- Advanced forecasting or financial modeling
- Automated payment reminders or SMS/email notifications
- QuickBooks or accounting software integration (out of scope; Square integration is the priority)
- Multi-location or multi-company support
