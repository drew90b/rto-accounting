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
| 1.3 | Vendor register | 🔧 | Model and DB table exist. No UI (list/add/edit) yet. |
| 1.4 | Repair job entry | ✅ | Internal recon, customer repair, customer support repair. |
| 1.5 | Sales entry | ✅ | Car and golf cart sales. Down payment, fees, status. |
| 1.6 | Lease / RTO account entry | ✅ | Deal setup, balance tracking, delinquency status. |
| 1.7 | Payments entry | ✅ | Applied to sale, lease, or repair job. |
| 1.8 | Transaction log | ✅ | All types, business lines, revenue streams, coding fields. |
| 1.9 | Receipt / document upload | ✅ | Upload and link to any record type. Download. |
| 1.10 | Exception queue | ✅ | Log, assign, resolve, audit history. |
| 1.11 | Dashboard | 🔧 | Basic counts and open exceptions. Delinquent leases panel. Needs cash warning. |

---


## Phase 1.5 — Workflow Hardening
inline form validation

confirmation messages

pagination

better search filters

better error handling


## Phase 2 — Reporting and Exports

Accountant-friendly views and month-end export packs. Enables external review without relying on the live app.

| # | Feature | Status | Notes |
|---|---|---|---|
| 2.1 | Per-module Excel export | ✅ | Units, customers, repair jobs, sales, leases, payments, transactions all have export. |
| 2.2 | Transaction register — date filter + export | ✅ | Date range filtering and export on transactions list. |
| 2.3 | Month-end export pack | 🔲 | One Excel workbook, multiple tabs: transaction register, revenue by stream, categorized spend, repair summary, lease collections, sales summary, unit profitability, exceptions, inventory snapshot. |
| 2.4 | Revenue by stream report | 🔲 | Summary view grouped by revenue stream with period totals. |
| 2.5 | Unit profitability view | 🔲 | Acquisition cost + repair costs vs. sale amount or lease total. Per unit and summary. |
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

Operational awareness screens for the owner and site manager.

| # | Feature | Status | Notes |
|---|---|---|---|
| 4.1 | Cash warning panel | 🔲 | Highlight delinquent lease accounts, overdue exceptions, and uncollected payments. |
| 4.2 | Revenue trend chart | 🔲 | Simple month-over-month revenue by stream. |
| 4.3 | Units on lot summary | 🔲 | Count by status (frontline ready, in repair, waiting parts, etc.). |
| 4.4 | Open repair jobs panel | 🔲 | Jobs open > N days with no activity. |
| 4.5 | Collections due this week | 🔲 | Lease accounts with scheduled payments due in the next 7 days. |
| 4.6 | Exception aging | 🔲 | Open exceptions by age: < 7 days, 7–30 days, > 30 days. |

---

## Phase 5 — Authentication and Access Control

Role-based access for different user types. Required before any multi-user or external deployment.

| # | Feature | Status | Notes |
|---|---|---|---|
| 5.1 | User table and login | 🔲 | Username/password login. Session-based auth. |
| 5.2 | Role definitions | 🔲 | owner, admin, salesperson, mechanic, accountant |
| 5.3 | Role-based route protection | 🔲 | Restrict create/edit/delete by role. |
| 5.4 | Read-only accountant view | 🔲 | Accountant role: read-only access to registers and exports. |
| 5.5 | Mechanic limited view | 🔲 | Mechanic role: repair job status updates only. |
| 5.6 | Entered-by auto-populate | 🔲 | Auto-fill `entered_by` from logged-in user. |
| 5.7 | Audit log | 🔲 | Record who changed what and when on key records. |

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
| 6.6 | Lease balance auto-update | ✅ | Remaining balance is computed at runtime (`financed_balance − SUM(payments)`). `outstanding_balance` column deprecated in migration 002. |
| 6.7 | Unit status validation | 🔲 | Warn or block invalid status transitions (e.g., recon job on a sold unit). |
| 6.8 | Month-end close lock | 🔲 | Prevent edits to closed periods once approved. |

---

## Immediate Next Steps (Suggested Order)

1. **Vendor UI** — add list, add, and edit screens for vendors (model already exists)
2. **Month-end export pack** — single workbook with all required tabs for accountant review
3. **Revenue by stream report** — summary view with period filter
4. **Unit profitability view** — cost vs. revenue per unit
5. **Cash warning panel on dashboard** — delinquent leases, overdue exceptions, uncollected amounts
6. **Authentication** — login and basic role protection before any shared use

---

Phase 7 — Mobile usability

Features:

Responsive layouts
Camera receipt upload
Quick payment entry screen
Large buttons for phone
PWA install support

---


## Out of Scope (MVP and Near-Term)

The following will not be built in the current phases:

- Full general ledger or double-entry accounting
- Payroll or employee management
- Full CRM (contact history, lead tracking)
- Full loan servicing (amortization schedules, interest calculation)
- Mobile app
- Advanced forecasting or financial modeling
- Automated payment reminders or SMS/email notifications
- QuickBooks or accounting software integration
- Multi-location or multi-company support
