# Vehicle Reconciliation and Question Queue — Companion Specification

**Status:** Draft VR-0.1, 2026-11-08. A *companion* to `cash-financial-control-spec.md`, not an edit of it. The governing spec is being revised in another session, so nothing here changes its text; this file only cites it. Cited versions: v0.3 (the only copy in the `main` checkout, untracked), v0.4.1 (`rto-frazer-intake` worktree) and **v0.5.1 (`rto-monthly-review` worktree, uncommitted, the latest)**. Every requirement cited below exists unchanged in all three unless noted.
**Increment:** V (Vehicle reconciliation). Release label **L** (REQ-AD-01 is the Later item this builds). Independent of Slices A, B and P.
**Boundary:** reconciliation records only. It reads source records and a few operational tables; it **writes to none of them** (REQ-VR-02).

## 1. Purpose

One understandable list of the business's cars: what each source says, what agrees, what is unresolved, and the next question a person needs to answer. It is the foundation for trustworthy vehicle economics (spec §13) and later labor allocation. It does **not** calculate profit, total cost per car, or allocate payroll.

## 2. Mapping to existing requirements

| Existing ID | What it says (short) | Where it lands here |
|---|---|---|
| REQ-AD-01 | Vehicles reconciled by VIN across Frazer inventory, app units, contracts; intake never merges | REQ-VR-03..06, 12 |
| REQ-FZ-04 | Stock numbers, VINs, IDs are text; leading zeros kept | REQ-VR-01; AC-VR-01 |
| REQ-FZ-06 | No operational writes | REQ-VR-02; AC-VR-14 |
| REQ-FZ-07, -09 | Snapshots stay distinct; changed content is a revision, not an overwrite | REQ-VR-07, 08 |
| REQ-FZ-11 | Duplicate VIN never merged; zero cost is not free; missing is not zero | REQ-VR-04, 10; AC-VR-02, 03, 12 |
| REQ-FZ-03, DEC-37 | Cutoff unknown unless recorded; a snapshot is not "now" | REQ-VR-09 |
| REQ-FZ-16, REQ-PRM-01, REQ-MR-13/24 | Server-side permission; assignee sees only what they need | REQ-VR-14..17; AC-VR-15..18 |
| REQ-AUD-01..03 | User from session; no edit in place; no hard delete | REQ-VR-11, 13; AC-VR-10, 11 |
| REQ-MR-23..25 | Restricted "My actions" for assignees, 404 for the unreachable | REQ-VR-16, 18 |
| DEC-12, §13 | Valuation basis and cost per car are later and need Steve and the CPA | REQ-VR-10, DEC-VR-04 |
| DEC-39 | Who gets My actions | DEC-VR-05 |

## 3. Vocabulary (kept apart on purpose)

- **Physical vehicle**: the actual car. Never stored as a record of its own; it exists only as a *group of stock-number episodes that a person has accepted are the same car*.
- **Stock-number episode**: one stock number as a source wrote it. Each distinct stock number is its own episode; it is never merged into another.
- **Contract**: a sale or rental record in a source (for example a Frazer customer-balance row). Only its *existence*, sale date and account label are kept here. **No customer name, balance or payment figure is stored by this feature.**
- **Reported by a source** (a source record, with its snapshot date) is separate from **confirmed by a person** (an accepted decision, with who/when/as-of).

## 4. Requirements

| ID | Requirement |
|---|---|
| REQ-VR-01 | **Identifiers are text.** Stock numbers, VINs and source keys are stored and shown exactly as written (`00123` stays `00123`, `F36140` stays text). Comparison is exact. A match that needs normalisation (leading zeros, case, spaces) is a **proposal**, never a match |
| REQ-VR-02 | **No operational writes.** Refresh, questions, decisions and corrections write only to `vr_*` tables. They never create or change a purchase, unit, transaction, payment, customer, vendor or document. Tests compare every non-`vr_` table before and after |
| REQ-VR-03 | **Sources.** Four, no others: Frazer vehicle export (file), Frazer customer-balance export (file, existence of a contract only), app units, app purchases (read-only). A source that has not been loaded is shown as "not loaded", never as "agrees" or "empty" |
| REQ-VR-04 | **VIN is evidence.** Two stock numbers with one VIN raise one question; they are never merged. Until a person answers, they remain two list rows, each flagged. A VIN is compared case-insensitively and trimmed for *grouping*, and the original text is kept |
| REQ-VR-05 | **Episodes and physical vehicles stay distinct.** An accepted "same car, repossessed and re-entered" decision groups episodes in the list but keeps every episode, its dates and its source records. Contracts stay attached to the stock number that carried them |
| REQ-VR-06 | **Purchases.** A purchase attaches to a vehicle row only when stock number and unit VIN agree (exact), or by an accepted decision. Conflicts and near-matches raise a question. A purchase with a stock number that matches no source vehicle raises nothing (it may be a golf cart or parts). Linking never copies or moves a cost |
| REQ-VR-07 | **Source records are immutable and dated.** Each is stored per snapshot with its source date and a content hash. Loading the identical file again is a no-op. A changed record is a new record; the old one remains |
| REQ-VR-08 | **Questions are generated only from evidence** by deterministic rules (§5). Each has a stable key and an evidence fingerprint. A repeat refresh with the same evidence creates nothing and changes nothing. No question is raised for a source that has not been loaded |
| REQ-VR-09 | **Dates.** Every statement of source status carries its snapshot date, labelled "as of". A Frazer record never says where a car physically is. The true cutoff of a file stays "Unknown" unless a person records it |
| REQ-VR-10 | **Cost.** Each source's cost is shown separately and labelled (`$0.00` = "reported zero, not confirmed free"; blank = "not reported"). No combined vehicle total exists until a reconciliation rule is decided (DEC-VR-04). Linking a purchase adds nothing |
| REQ-VR-11 | **A decision is explicit.** Each answer choice maps to a defined effect (§6). A note, or a question merely being closed, changes nothing. "Not sure" and "Needs someone else" record who said it and keep the question open (the latter returns it to the administrator queue). Every decision stores who, when, the evidence it relied on, the choice and its stated effect |
| REQ-VR-12 | **Approval.** *Operational* questions (status confirmation) are accepted when answered. *Identity* and *financial* questions (shared VIN, purchase match) take effect only when an **administrator** accepts the answer. An administrator's own answer is accepted at once and recorded as "answered and accepted by the same person" (DEC-VR-02) |
| REQ-VR-13 | **Corrections supersede.** An administrator can replace an accepted decision with a new one and a reason. The old decision becomes *superseded*, stays visible, and its effect stops. Nothing is edited in place or deleted |
| REQ-VR-14 | **Stale and simultaneous answers.** Each answer carries the question version and evidence fingerprint it was based on. If either changed, the answer is refused with the current state shown; nothing is overwritten. Two simultaneous answers: one wins, the other is refused |
| REQ-VR-15 | **New contradictory evidence surfaces.** If a refresh changes the evidence behind a question that has an accepted decision, the decision stays in force but the question is flagged **Review needed** and returns to the queue. If the evidence for an *unanswered* question changes, its evidence is updated and its version advances (so older answers are refused). If the condition disappears, an unanswered question is *withdrawn* with a reason; nothing is deleted |
| REQ-VR-16 | **Permissions (server).** Vehicle list, detail, full queue, approvals, corrections, assignment and refresh: administrator only. An employee reaches only `/vehicle-questions/mine`: questions *assigned to them* of an employee-eligible kind (shared VIN, status), with a restricted evidence view (no cost, purchase, customer, payroll or other-person data). Anything else answers 404 (existence not revealed). No new role |
| REQ-VR-17 | **Assignment.** A question may be assigned to an active user. Purchase-match questions can be assigned only to an administrator. Assignment grants no access beyond REQ-VR-16 |
| REQ-VR-18 | **Integration.** The question queue is its own small model so this feature works alone. `vehicle_recon_service.assigned_items(db, user)` returns neutral items (reference, title, due, link) that the Monthly Review "My actions" view can list once both are merged. No second task system is meant to survive that merge (DEC-VR-05) |
| REQ-VR-20 | **Row states.** *Needs review*: a question is open, waiting for approval or flagged for new evidence, or two records for one stock number disagree. *Resolved by a person*: no question is live and at least one was decided; the sources are unchanged and it is never shown as "agrees". *Agrees*: no live or decided question and two or more sources carry the car (labelled "both from Frazer" when all are Frazer). *Lacks evidence*: fewer than two sources. A car is never "verified" by this feature |
| REQ-VR-21 | **Frazer intake feeds reconciliation (integration build).** Reconciliation *reads* parsed vehicle and customer-balance uploads from Frazer intake, identified by the upload's SHA-256, when an administrator opens the vehicle list or presses Refresh. Intake is not modified and does not call reconciliation, so REQ-FZ-06 (an upload writes only to intake's tables) still holds. An upload with any unreadable row is **not used** and the list says why. The same bytes arriving by upload and by command line are one snapshot |
| REQ-VR-22 | **Assigned vehicle questions appear in My actions** (REQ-MR-23), as a separate "Vehicle questions" group, linking to the restricted question page. Items not assigned to the user, and purchase questions, are never listed or reachable. Reconciliation questions are not copied into `review_issues` (DEC-VR-05) |
| REQ-VR-23 | **Identity is shown in three levels**: the physical vehicle (only as confirmed by a person), each stock-number episode, and each episode's contract. Grouping affects the list row only; episodes, contracts and source records are never merged, renumbered or deleted, and a correction splits the row again |
| REQ-VR-19 | **Concise, phone-first screens.** The list is one compact row per vehicle. Summary and next action first; source records, history and evidence behind expandable sections or the detail page |

## 5. Question rules (deterministic)

| Kind | Raised when | Class | Choices |
|---|---|---|---|
| `shared_vin` | Two or more episodes (distinct stock numbers) in loaded sources have the same VIN | Identity (admin approves) | Repossession and re-entry of one car; Duplicate record of one car; One stock number has the wrong VIN (per stock); Still unknown; Not sure; Needs someone else |
| `purchase_match` | (a) a purchase's stock number matches episode E exactly but its unit's VIN differs from E's; (b) its stock number matches no episode exactly but matches one after normalisation; (c) its stock number and its unit's VIN point at different episodes | Financial (admin approves; admin only) | Belongs to this vehicle; Belongs to another vehicle (each candidate); Shared business expense; Not enough information; Not sure; Needs someone else |
| `status_conflict` | The app unit for an episode says *not sold / not leased* while Frazer's customer file lists a contract for that stock; or the app unit says *leased/RTO active* while a loaded Frazer customer file lists no contract for it | Operational (accepted when answered) | Sold or financed as of (date); On hand / available as of (date); Returned or repossessed as of (date); Something else (note); Not sure; Needs someone else |
| `decision_review` | Not a separate kind: any question above, flagged **Review needed** by REQ-VR-15 | as the question | Re-answer = a correction |

Not raised: one-source vehicles (shown as "Only one source", not a question); any Frazer-only vs app-only difference that is expected (the app holds few units); unit/Frazer VIN mismatch where no stock number links them.

## 6. Decision effects (defined, none touches operational data)

| Choice | Effect on reconciliation state only |
|---|---|
| Re-entry of one car | The episodes are grouped as one physical vehicle (relation `reentry`); each keeps its stock number, dates, contract and sources |
| Duplicate record | Grouped (relation `duplicate`); the list shows one row with both stock numbers; neither record is deleted |
| Wrong VIN on stock S | The episodes stay separate vehicles; S is marked "VIN reported as wrong by a person; correct it at the source"; the shared-VIN flag on the pair is cleared while that holds |
| Still unknown / Not enough information | Question closed as "Unknown as of <date>"; reopens on new evidence |
| Purchase belongs to E / another / shared | A reconciliation link (purchase → episode, or → "shared business expense") is recorded. The purchase and its transaction are unchanged; no cost is copied |
| Status confirmed (value, as-of date) | A **person-confirmed** status with who and as-of date is shown beside the source-reported statuses; the unit's own status is not changed |

## 7. Acceptance cases (automated with synthetic data unless marked Walk)

| ID | Case | Expected |
|---|---|---|
| AC-VR-01 | Stock `00123` and `F36140`; a purchase typed `123` | Stored/shown as written; `123` does not match `00123` as a link, it raises a near-match question |
| AC-VR-02 | Two stock numbers, one VIN | One `shared_vin` question; two list rows; neither merged |
| AC-VR-03 | Shared-VIN pair answered "re-entry" and accepted | List groups them under one vehicle row showing both stock numbers; both episodes' sources and contracts remain |
| AC-VR-04 | Purchase stock matches, unit VIN differs | `purchase_match` question; purchase not attached |
| AC-VR-05 | Purchase stock matches and unit VIN agrees | Attached as "matches by stock and VIN", no question, no decision |
| AC-VR-06 | Near-match stock (`0123` vs `123`, or case) | Proposal question only |
| AC-VR-07 | Purchase stock matches no source vehicle | No question |
| AC-VR-08 | The same files and refresh run three times | One question per key; no duplicate records; counts unchanged |
| AC-VR-09 | Accepted decision, then a new snapshot changes the evidence | Decision still in force; question flagged Review needed; one new event; no duplicate question |
| AC-VR-10 | Correction of an accepted decision | New decision supersedes; both visible with who/when/reason; effect follows the new one |
| AC-VR-11 | Every state change | Appended to the event log with user from session; nothing edited or deleted |
| AC-VR-12 | Frazer cost `0` vs blank vs app purchase amount | `Reported zero` vs `Not reported`; amounts side by side; no sum anywhere |
| AC-VR-13 | Two users answer from the same page | One accepted; the other refused with the current state |
| AC-VR-14 | Refresh, answer, correct, browse | Every non-`vr_` table unchanged (row counts and content hashes) |
| AC-VR-15 | Employee on admin pages, downloads, other people's questions | 403 / 404 |
| AC-VR-16 | Employee answers an identity question | Recorded as *proposed*; no effect until an administrator accepts |
| AC-VR-17 | Employee's evidence view | Contains no cost, purchase amount, customer data; purchase questions unassignable to employees and unreachable (404) |
| AC-VR-18 | Signed-out visitor on every route | Redirect to sign-in |
| AC-VR-19 | Status conflict answered by an employee | Accepted at once as a person-confirmed status with as-of date; the source statuses are unchanged |
| AC-VR-20 | A source not loaded | Shown as "not loaded"; no questions depend on it |
| AC-VR-23 | A Frazer vehicle file uploaded through intake | It appears in the vehicle list with its questions on the next list view; intake rows are unchanged; the identical file is refused by intake; a command-line load of the same bytes adds no snapshot |
| AC-VR-24 | An upload with an unreadable row | Not used; the list says "Not used: n unreadable row(s)" |
| AC-VR-25 | A question assigned to Belinda | Listed under "Vehicle questions" in My actions; answerable on its page; another user's or an unassigned question is invisible and 404 |
| AC-VR-26 | A grouped vehicle | The detail page lists the physical vehicle, each stock-number episode and each contract separately; stored records stay one per stock number; a correction splits the row |
| AC-VR-22 | A status conflict settled by a person | The row reads "Resolved by a person", not "Agrees"; the source lines and the person's dated confirmation are shown separately |
| AC-VR-21 (Walk) | Real Nov 8 Frazer files, read-only | Counts, the two shared-VIN pairs and contract matches agree with `REAL_FILE_FINDINGS.md`; nothing is hard-coded |

## 8. Decisions for Drew and Steve

| ID | Decision | Default used | Blocks |
|---|---|---|---|
| DEC-VR-01 | What **"agrees"** should require. Two reports from the same vendor (Frazer vehicle list + Frazer contract list) agree on VIN/year/make/model | Counted as two *records* but labelled "both from Frazer"; marked **Agrees (Frazer only)** | Calling a car "verified" |
| DEC-VR-02 | May an administrator accept their own answer to an identity or financial question? | Yes, recorded as the same person; a second-person rule can be switched on later | Segregation of duties for identity changes |
| DEC-VR-03 | Do a repossessed car's two stock numbers stay two rows by default until answered, and which stock number is the "primary" in a group | Two rows until answered; the earliest purchase date is shown first | List grouping |
| DEC-VR-04 | The rule for combining a Frazer-reported cost with app purchases (overlap) | None; shown separately, never summed | Any per-vehicle total, profit or labor allocation |
| DEC-VR-05 | Whether open questions become Monthly Review issues (one task system) or stay a separate queue | Separate queue; `assigned_items()` is the integration point | "My actions" showing vehicle questions |
| DEC-VR-06 | Who is allowed to answer which question kinds (Belinda for shared-VIN and status; admins for purchases) | As in §5 | Employee workload |
| DEC-VR-08 | Should a Frazer upload push into reconciliation (a call in intake's upload route) or should reconciliation pull? Pull was built because a push would write to reconciliation tables during an upload and break REQ-FZ-06 | Pull on opening the list / Refresh | Frazer intake owner | The wording of REQ-FZ-06 |
| DEC-VR-07 | Whether Frazer's `Vehicle Source` value (`Repossessio`, cut off) may be treated as evidence of repossession | Shown as reported, mentioned in the question, never used as an answer | Auto-suggesting an answer |

## 9. Out of scope

Profit, margin, total cost per car, payroll or labor allocation, valuation, floor-plan balances, importing or editing units/purchases/transactions, matching to Square or the bank, a file-upload screen (the Frazer Intake feature owns uploads), downloads, notifications.
