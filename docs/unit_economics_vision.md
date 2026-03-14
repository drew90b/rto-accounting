# Unit Economics Vision

This document describes the planned per-unit profitability reporting capability — what it should show, where the data comes from, and what needs to be built to support it.

This feature is not yet built. It is documented here so that data model and workflow decisions today support it cleanly.

---

## What "Unit Economics" means in this context

For every vehicle or golf cart the business handles, the owner should be able to see:

- What did we put into this unit?
- What did we get out of it?
- Did we make money?

That's it. Simple, per-unit profit and loss — not accounting theory, not GAAP-compliant depreciation, just operational clarity.

---

## The Unit P&L View (Target)

A future "Unit Detail" or "Unit Economics" screen should show something like this for any selected unit:

```
Unit: U-0007 — 2019 Honda Accord
Status: Leased / RTO Active
Customer: Maria Gonzalez (C-0001)

INVESTMENT
  Acquisition cost          $6,500.00
  Recon labor               $  420.00
  Recon materials           $  180.00
  Transport / title fees    $  150.00
  ─────────────────────────────────
  Total investment          $7,250.00

REVENUE
  Sale / agreed amount      $12,000.00
  Collected to date         $ 3,400.00
  Remaining balance         $ 8,600.00

PROFIT SUMMARY
  Total investment          $7,250.00
  Total revenue (agreed)    $12,000.00
  Gross margin (agreed)     $4,750.00   (39%)

  Collected to date         $3,400.00
  Net cash position         ($3,850.00) — still in deficit; fully collected at payoff
```

For a completed (paid-off or sold outright) unit:

```
  Total investment          $7,250.00
  Total collected           $12,000.00
  Net profit                $4,750.00   (39%)
```

---

## Where the Data Comes From

All the data for this view already exists in the system. It just needs to be aggregated.

| Line Item | Source |
|---|---|
| Acquisition cost | `units.acquisition_cost` |
| Recon labor | `repair_jobs.labor_amount` (internal_recon jobs linked to unit) |
| Recon materials | `repair_jobs.materials_amount` (internal_recon jobs linked to unit) |
| Transport / fees | Manual transactions (type=purchase or overhead, linked to unit) |
| Sale / agreed amount | `sales.total_contract_amount` OR `lease_accounts.original_agreed_amount` |
| Collected to date | `SUM(payments.amount)` linked to the sale or lease account |
| Remaining balance | Computed: `lease_service.calculate_remaining_balance()` |

No new data entry is needed. This is purely a reporting and aggregation layer.

---

## What Needs to Be Built

### 1. Acquisition cost breakdown (future improvement)

Currently, `units.acquisition_cost` is a single field. For accurate unit economics, this should eventually expand to named components:

| Component | Description |
|---|---|
| Purchase price | What was paid at auction or private sale |
| Transport | Freight, towing, or delivery to lot |
| Title and fees | DMV title fees, dealer fees |
| Initial inspection | Any inspection costs before recon begins |

**Recommendation:** Add optional fields to the Unit model for these components. Keep `acquisition_cost` as the total for backward compatibility. The breakdown fields are optional and can be filled in over time.

### 2. VIN as a required field

VIN should become required and unique for cars. This ensures:
- No duplicate unit records for the same vehicle
- Clean linkage to any future title or DMV lookup
- Accurate unit identification in the P&L view

Golf carts use serial numbers, not VINs. The field is already `vin_serial` in the model to accommodate both.

### 3. Unit Economics report / view

A new screen at `/units/{id}/economics` or `/reports/unit-economics` that:
- Queries all cost transactions linked to the unit
- Queries all internal recon jobs and their cost amounts
- Queries the sale or lease account amounts
- Queries all payments received
- Renders the summary view shown above

An export option (Excel) should also be available.

### 4. Portfolio summary (batch view)

A summary version showing all units with their economics in a table:

| Unit | Status | Investment | Agreed | Collected | Remaining | Margin |
|---|---|---|---|---|---|---|
| U-0001 2019 Accord | Active RTO | $7,250 | $12,000 | $3,400 | $8,600 | 39% |
| U-0002 2020 Camry | Paid Off | $8,100 | $14,500 | $14,500 | $0 | 44% |
| U-0003 EzGo Golf Cart | Sold | $1,200 | $3,500 | $3,500 | $0 | 66% |

Sortable by margin, status, or investment amount.

---

## Known Limitations Today

| Limitation | Impact | Plan |
|---|---|---|
| `acquisition_cost` is a single field | Cannot distinguish purchase price vs. fees vs. transport | Add optional breakdown fields to the Unit model |
| Recon costs require manual transaction entry | No automated rollup from repair jobs to unit total investment | Build the aggregation query in the report layer |
| No unit economics screen exists | Owner must manually pull numbers from multiple views | Build as part of Phase 2 reporting |
| Overhead costs (lot costs, insurance) not allocated to units | Unit P&L shows direct costs only; shared overhead not allocated | Acceptable for MVP; could be added later as optional allocation |

---

## Design Principle

Unit economics reporting should be **read-only and derived** — it reads from existing records and presents a summary. It should never require operators to enter data in a new or different way. Every number in the unit P&L should trace back to a specific transaction, payment, or unit field that already exists.

This keeps the entry workflow simple and lets the reporting layer do the aggregation work.

---

## Relationship to Month-End Export

The unit economics view should feed into the month-end export pack as a tab called "Unit Profitability" — one row per unit, same columns as the portfolio summary above. This gives the accountant a clean picture of where the portfolio stands at close.
