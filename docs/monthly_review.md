# Monthly Review (Increment M): the workspace for reviewing a month

**Status:** built on an unmerged feature branch (`feature/monthly-review`); not deployed. **Governing spec:** [`specs/cash-financial-control-spec.md`](specs/cash-financial-control-spec.md) v0.5.2, section 20 (REQ-MR-01..30, AC-MR-01..47), decisions DEC-39..DEC-43. The specification governs business behavior; this page says what was built and how to use it.

## What it is for

Drew and Steve need one place to answer: what have we received this month, what is missing, what must be done and by whom, what do the supported figures say, and what did we decide. **Finance > Monthly Review** (`/finance/review/?month=2026-11`) is that page. It **reviews** a month. It does not reconcile, verify or close one: the spec makes in-app close a later feature (§6), and November is closed by hand outside the app.

## The page, top to bottom

- **Close workday indicator** (the calendar month end and WD0, the last business day, are separate anchors; the spec's "BD n" and WD+n name the same day, verified for 2024-2032 and arbitrary holiday sets). The current close workday for the selected month (WD−3, WD0, WD+2), workdays left until WD0, today's date in America/Chicago, and the next scheduled close activity (flagged Overdue when late). On weekends and holidays it says **Non-workday**, with the reason and the next close workday; it does not advance the counter. *View closing schedule* sits beside it.
- **Progress.** Preparation and Close readiness, the next action, and a short alert list. The seven steps are behind "Close steps": tap one for its milestones, who owns the next action, why it is blocked, and the forms to record a review or propose an exclusion.
- **1 Month status**, **2 Records received and missing** (plus a form to record a source the app cannot hold), **3 Needs attention** (issues with owner, status, due date, source links, notes and history), **4 Collections and spending**, **5 Reconciliation status**, **6 Owner review and actions**.
- **Closing schedule** (`/finance/review/schedule?month=...`): date, workday, activity, who, prerequisites, status and a link for each step. The business calendar in use is listed there.

## How progress works: preparation and close readiness

One list of steps and milestones lives in `app/services/close_progress.py`, derived from spec §6.2: CL-1 bank and physical cash, CL-2 Frazer and Square activity, CL-3 inventory/portfolio/debt, CL-4 payroll (restricted), CL-5 management P&L inputs, CL-6 cash needs and sign-off, then **Formal close** as a separate final step. Two bars, never mixed:

- **Preparation** counts work people can finish now: **Received to date** (e.g. the Frazer export covers Nov 2 through the last finished workday; a partial export counts here), **Received** for the whole period (month-end items open at WD0), **Reviewed** (Steve), **Owner-reviewed** (Drew). Each is derived from uploads or from by-hand records, not from a say-so. Finishing preparation says "the paperwork is ready", never "reconciled" or "closed".
- **Close readiness** counts what formal close needs on top: **Matched**, **Reconciled**, **Decisions confirmed**, **Approved**, then **Formal close**. Each needs a feature that is not built (`CAPABILITIES`) or a decision not made (`DECISIONS_CONFIRMED`), so it shows **Blocked** and the bar stays at 0% today. It can never show 100% or "Closed" until Formal close itself is complete (capped at 99%).

Resolving an issue, saving a note, an upload on its own, or a passing date satisfies no milestone. Review and Owner-review can be recorded only once the preparation records are in place, and in that order; "Approved" only after everything before it.

### Not applicable (controlled)

A reason alone never excludes a required step.
- **Never excludable:** CL-1, CL-2, CL-3, CL-5, CL-6, Formal close.
- **CL-4 payroll** only when no payroll was paid in the period: a reason, where the evidence is kept, one administrator proposes, a **different administrator concurs**, and it stops counting if any weekly payroll record is noted for the month.
- **By-hand records:** only Square (not used) and the floor-plan statement (no loan) may be "not required", with reason, evidence location and a second administrator's confirmation. Bank statements, cash counts and weekly payroll records are mandatory.
- A pending proposal counts as nothing and raises no progress. Everything is insert-only with attribution.

## The close workday and the calendar (provisional)

`app/services/business_calendar.py` is the single place that defines workdays, holidays and WD0.

- The spec defines "BD n" (the n-th business day after month end, §6.2 deadlines) but **not WD0 or any holiday**. Provisional convention: **WD0 = the last business day of the reporting month**; WD−1 and WD+1 are the neighbouring business days. BD n therefore equals WD+n.
- **Holidays are an assumption.** The built-in calendar is labelled *Provisional, not confirmed by Mudbugs*: weekends plus New Year's Day, Memorial Day, Independence Day, Labor Day, Thanksgiving and Christmas, each observed on the nearest weekday when it falls on a weekend. The active calendar is shown on the page. To change it without code, set `BUSINESS_CALENDAR_FILE` to a JSON file (format in the module docstring).
- Dates use America/Chicago, so 11:30 pm on Nov 30 Chicago time is still Nov 30 even though UTC says Dec 1.

## Records, issues and figures

- **Records table:** Frazer transactions (coverage gaps shown; a declared range is "as declared, not verified"), customer-balance and vehicle snapshots (a snapshot date is not transaction coverage; only one dated month end counts), purchases (entry is not completeness), reported opening position (Nov 2026; keeps its date and unknown cutoff), and by-hand records for bank statements, Square, cash counts, floor-plan statement and weekly payroll. A by-hand record only notes that the record exists, what dates it covers and where it is kept.
- **Issues:** manual or automatic. Automatic ones are limited to: Frazer transaction coverage, missing month-end snapshots, missing bank/Square/cash-count/floor-plan record, a Friday with no payroll record, an unconfirmed method code or late-fee treatment, and Frazer entries that differ between uploads. One issue per month and key, so refreshes and repeat uploads never duplicate. Automatic *evidence* is kept apart from human notes. If the condition clears, the issue is flagged "condition no longer detected" and stays open for a person to resolve; if it returns after being resolved, it reopens. Resolving needs a note and changes nothing else.
- **Figures:** Frazer transaction totals use the union of uploads by Sequential ID, so overlapping or repeated exports count once; entries whose content differs between uploads are left out and listed; late fees stay separate from money received; no revenue or profit; unsupported figures say what input is missing instead of $0.

## My actions (for Belinda and other assignees)

`/my-actions/`, open to any signed-in user, shows only the review issues **assigned to that user** (it reuses the same issues and history; there is no second queue). She sees the title, due date, status, the administrators' **message to the owner**, her own replies and files. She can reply, mark a question In progress or Waiting, attach a file (PDF, photo, CSV, Excel or text, up to 10 MB), and, for a missing-record question, note that she has the record and where it is kept. She cannot resolve a question. She never sees the internal description, automatic evidence, internal notes, source links, other people's items, any financial figure, or any payroll item (those cannot be assigned to her and would 404 if they were). Files live in their own folder, are not in Documents, and download through a permission-checked route as attachments. Administrators write the message to the owner separately from internal notes; a reply flags the issue "reply to review" at the top of the Monthly Review page.

## Phone layout (no clipped columns)

**What was wrong:** the app's shared `.table-container` is `overflow: hidden`, so a table wider than the phone was silently cut off at the right edge (the page itself never scrolled, so nothing hinted that columns were missing). **The fix** (scoped to `.mr-page`, phones only at 700px and below): wide tables become **labelled rows** (each cell shows its column name; the header stays for screen readers), long text wraps, inputs cap at the screen width, and any region that could still overflow is an explicit scroll region (`.mr-scroll`, focusable, with edge shadows). Text sizes are not reduced and nothing is hidden. The stylesheet version was bumped (`?v=4`) so iPhones fetch the new file. Desktop geometry was measured before and after and is identical.

**To re-measure** (Chromium via Playwright, iPhone 375/390/430): `/home/drew/rto-reviews/2026-11-08-monthly-review/mobile/measure.py` and `desktop_geometry.py`.

## The compact overview

The first screen holds the close workday, both progress bars, the next action and a short list of overdue, blocked and reply-to-review items. The six sections are collapsed to one summary line each (open one, or use "Open all"); long tables, step detail and forms are behind them. Nothing was removed.

## Access and restriction

The Monthly Review page and its writes are administrators only, enforced on the server. An issue can be assigned to any active user, but assignment grants no access to that page; assignees use My actions. **Payroll is restricted:** no amounts or names appear anywhere; issues, notes, records and step notes that mention payroll are refused if they contain money amounts or large numbers (a guard, not a guarantee); no payroll file is linked. Who may see payroll detail is DEC-09, still open.

## Tables

`review_issues`, `review_issue_events`, `review_notes`, `review_source_records`, `review_step_marks` (migration 012); `review_issue_files` and a wider `review_source_records.status` (migration 013). Events, notes, records and marks are insert-only; an issue's identity and text are fixed and only its status, owner and due date change (each change is also an event). The workspace writes nothing else: not uploads, snapshots, purchases, units, payments, transactions or documents.

## Integration base

This branch was assembled from main plus the **Opening Snapshot** (migration 010) and **Frazer intake** (migration 011) work so the combined code and migration sequence is tested for real: `009 > 010 > 011 > 012`, one head. Merge order: Opening Snapshot, then Frazer intake, then this. The clock-dependent Opening Snapshot test (`test_ac_os_12`, which asserted the absence of "7 AM" on a page that prints the time) was fixed here with distinctive tokens. The source worktrees were not modified, and nothing is committed.

## Tests

`tests/test_monthly_review.py` (workflow, restriction, progress, N/A policy, schedule, compact page), `tests/test_my_actions.py` (the restricted view), `tests/test_business_calendar.py` (anchors, holidays, boundaries, Chicago rollover, BD/WD equivalence). Two tests need a disposable PostgreSQL (`FRAZER_INTAKE_PG_URL`).

## Not built here

In-app close and reopen, Slice B (counts, deposits, custody), bank and Square import, matching, payroll capture, and decisions DEC-33..43.
