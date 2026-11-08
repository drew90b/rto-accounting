# Vehicle Reconciliation — the reconciled vehicle list and question queue

**Status:** built on an unmerged feature branch (`feature/vehicle-reconciliation`, from `main` `1b54dd3`); not deployed, merged or pushed. **Governing spec:** `cash-financial-control-spec.md` (REQ-AD-01, REQ-FZ-04/-06/-11, REQ-AUD-*, REQ-PRM-01) as cited in the companion [`specs/vehicle-reconciliation-spec.md`](specs/vehicle-reconciliation-spec.md) (REQ-VR-01..20, AC-VR-01..22, DEC-VR-01..07). The spec governs; this page says what was built and how to use it.

## What it is for

One understandable list of the business's cars: what each source says, what agrees, what is unresolved, and the next question someone has to answer. It is the foundation for trustworthy vehicle economics and later labor allocation. It does **not** compute profit, total cost per car, or labor.

## Screens

| Who | Screen | What it does |
|---|---|---|
| Administrators | **Vehicle Reconciliation** (`/vehicles/`) | Compact list: stock number(s), VIN, year/make/model, what each source reports and its date, state, next action and who is responsible. Search by stock, VIN, make or model; filter by state, open question, kind, responsible person |
| Administrators | Vehicle detail (`/vehicles/v/<stock>`) | Summary and next action first. Then *Confirmed by a person* (kept apart from) *Reported by sources*; stock-number episodes; source records; each source's cost separately (never summed); purchases; questions and history |
| Administrators | **Questions** (`/vehicles/queue`, `/vehicles/q/VQ-0001`) | One question with why it was raised, its evidence, answer choices (always including *Not sure* and *Needs someone else*), owner, status, history. Admins answer, approve or reject an employee's answer, correct a decision, assign |
| Any signed-in user | **My vehicle questions** (`/vehicle-questions/mine`) | Only questions assigned to them, of an employee-eligible kind, with restricted evidence (no cost, purchase amount, customer data). Anything else answers 404 |

## States

*Needs review* · *Resolved by a person* (the sources are unchanged; a person settled the question) · *Agrees* (two or more sources carry the car; "both from Frazer" is stated when all are Frazer) · *Lacks evidence* (fewer than two sources). Nothing here says a car is "verified", and a Frazer listing never says where a car physically is.

## Question kinds (raised only when evidence supports them)

| Kind | Raised when | Who may answer | Takes effect |
|---|---|---|---|
| Shared VIN (identity) | two stock numbers carry one VIN | employee proposes; admin accepts. An admin's own answer is accepted at once and recorded as the same person | on acceptance |
| Purchase match (financial) | a purchase's stock number and its unit's VIN point at different cars, or the stock number only matches ignoring zeros/capitals | administrators only (it shows purchase amounts) | on acceptance |
| Status (operational) | the app's unit says unsold/available but Frazer lists a contract (or the unit says leased but Frazer lists none) | assigned employee or admin | as soon as saved, as a person's dated confirmation |

What an answer changes is stated on the question and stored with the decision. None of it touches units, purchases, transactions or any ledger (tested: every non-`vr_` table is byte-identical before and after).

## Loading data

There is no upload screen: the Frazer Intake feature owns uploads. Until the two are merged, load files from the command line:

    # read-only check of real files; in-memory database, writes nothing, prints stock numbers and counts only
    python -m app.scripts.vehicle_recon_load --check --vehicles VEHICLES.csv --contracts BALANCES.csv --source-date 2026-11-08

    # load into the database named by DATABASE_URL (type its name to confirm); writes only vr_* tables
    python -m app.scripts.vehicle_recon_load --confirm-database NAME --source-date 2026-11-08 --vehicles V.csv --contracts C.csv --refresh

The customer-balance file contributes only: stock number, VIN, year/make/model, sale date and account label. **No customer name, balance or payment figure is stored.** The same bytes loaded twice are a no-op. The **Refresh** button re-reads the app's units and purchases (read-only) and regenerates questions; repeating it changes nothing.

## Rules it follows

Identifiers are text and exact (a normalised match is a proposal). VIN is evidence, never permission to merge. Source records are immutable and dated; a re-export never overwrites. Zero reported cost is "reported zero", never "free"; missing is "not reported", never zero. Costs from different sources are shown side by side and **never added** (no rule exists: DEC-VR-04). Every decision stores who, when, evidence fingerprint and effect. A correction supersedes and keeps the old decision. Each answer carries the question's version and evidence fingerprint; a stale or simultaneous answer is refused (compare-and-swap, tested with real concurrent sessions on PostgreSQL). New contradictory evidence flags the question "Review: new evidence"; the decision stays in force until reviewed.

## Integration points and dependencies (read before merging)

1. **Parser dependency.** `app/services/frazer_parse.py` is a **byte-identical copy** of the pure parser from the unmerged `feature/frazer-intake-preview` worktree (SHA-256 `05eb472cc6350fadcf395f726a39da28de389f8605a95fa6b800388dc42f8553`). Merge Frazer Intake first or in either order; if the file differs at merge time, take the Frazer branch's version (this feature uses only `parse_export`, `VEHICLES`, `CUSTOMERS`, `ERROR`). After merge, the loader can read from Frazer Intake's stored uploads instead of the command line.
2. **Migration.** `alembic/versions/vr01_vehicle_reconciliation.py`, `down_revision = "009"`. It claims **no number**; other branches carry 010–013. Whoever merges later re-points `down_revision` at the then-current single head and checks `alembic heads`. No operational table is touched.
3. **Shared files with small edits** (expect trivial textual conflicts): `app/main.py` (one import, two `include_router`, the cookie name), `app/config.py` and `app/main.py` (`SESSION_COOKIE_NAME` and `STORAGE_DIR` overrides, written to match the Frazer branch's lines exactly so they merge cleanly), `app/models/__init__.py` (one import), `app/templates/base.html` (two nav links), `app/static/css/style.css` (an appended `.vr-*` block), `tests/test_access.py` (three employee-allowed routes, each answering 404 to anything not assigned).
4. **Monthly Review "My actions".** This feature has its own small question model so it works alone. `vehicle_recon_service.assigned_items(db, user)` returns neutral items (reference, title, status, link) for "My actions" to list after both are merged (DEC-VR-05). The two issue models (`review_issues` vs `vr_questions`) were not merged, to avoid depending on unmerged tables.
5. **Permissions.** No new role. Administrator-only routes by router dependency; `/vehicle-questions` is open to any signed-in user but filters server-side to the user's own assigned, eligible questions.

## Known pre-existing issue found (not changed)

On a database built by the Alembic chain, `purchases.category` is `varchar`, but the ORM model declares a native enum, so an ORM `INSERT` of a `Purchase` fails with `type "purchasecategory" does not exist`. The demo seed uses raw SQL for that reason. Reading purchases works. I did not verify whether the live database differs.
