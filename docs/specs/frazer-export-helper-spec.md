# Frazer Export Helper: Companion Specification

**Status:** Draft FX-0.1, 2026-11-09. Companion to `cash-financial-control-spec.md` (Frazer intake, s19; REQ-FZ-*) and `vehicle-reconciliation-spec.md` (REQ-VR-*). Changes no text in either. **Writes:** its own three tables (`frazer_helper_*`) and whatever the existing intake and reconciliation services write. **Increment:** closing-time upload helper.

## 1. Why
Frazer exports are uploaded by hand through an administrator's browser, so data goes stale unnoticed. The business wants one button, pressed by an authorised employee already signed in to Frazer, that sends the three configured exports (vehicle list, customer balances, transactions). No automated Frazer login; not an unattended schedule.

## 2. Discovery status (what is and is not known)
| Question | Status |
|---|---|
| Is Frazer local Windows software, hosted, or RDP? | **Unknown.** Earlier screenshots showed a `\\tsclient` path, which is how an RDP session sees the *client* PC's drives. That suggests Frazer runs remotely and exports land on the operator's own PC. Not verified. |
| Where does the helper run? | **Where it can read the landing folder as an ordinary path.** If exports land on the operator's PC, there; if Frazer is local, on the Frazer PC. |
| Landing folder, file names, saved presets | **Unknown**; the guide lists the smallest check (`docs/frazer_export_helper.md`). The observed 2026-11-08 files were named `frazer-vehicles-…csv.CSV`, `frazer-customer-balances-as-od-….CSV`, `frazer-payments-… through ….csv.CSV`. |
| A supported export mechanism (command line, scheduler)? | **Unknown.** Not assumed. The helper supports a configured command per export; otherwise the operator runs the saved preset and the helper waits. Desktop automation of Frazer's native UI is not assumed and not built. |
| Existing machine authentication in the app? | **None**: only cookie sessions for signed-in users, and the CSRF check requires a browser Origin. Hence REQ-FX-04. |

## 3. Requirements
| ID | Requirement |
|---|---|
| REQ-FX-01 | **User-started, one button.** A person starts a run; nothing runs unattended. The helper never signs in to Frazer and stores no Frazer or app password. |
| REQ-FX-02 | **Existing presets, preserved.** The helper uses the saved Frazer exports as they are. The app holds an administrator-approved header row per export type; a file whose headings differ (added, missing, reordered) is refused, naming the columns. |
| REQ-FX-03 | **Fresh files only.** A file is accepted only if written after the run started (helper) and, checked again by the app, within the run, on the run's business date, with a matching SHA-256 of what was read. A matching older file is reported **stale** and never sent. Transaction exports declare a configurable period (`payment_days`) ending on the run date; overlapping periods are expected and handled by existing duplicate/revision logic. |
| REQ-FX-04 | **Narrow, revocable authentication.** An administrator creates a token (shown once, only its hash stored, optional expiry, attributable to a named user). A token authenticates only five endpoints under `/api/frazer-helper/`: status, start run, send file, report failure, finish run. It is not a session: it opens no page, and cannot read customers, balances, vehicles or any other file. The CSRF Origin check is skipped only for those paths and only when a bearer header is present. Revoking stops it at once and closes any open run. Responses carry no customer data. |
| REQ-FX-05 | **Existing services only.** Files are stored by `frazer_intake_service.store_upload` (size, type, encoding, hashing, parsing, duplicate refusal, original kept). Reconciliation is synchronised through `vr_intake_bridge`. No direct writes to intake or reconciliation tables. |
| REQ-FX-06 | **Honest times.** Each result keeps the file's own written time and the declared period; `actual_cutoff` stays "Unknown"; nothing is described as one simultaneous snapshot. |
| REQ-FX-07 | **One active run.** Enforced by a partial unique index in the database (not only a check), by a local lock on the helper computer, and by a lease (45 minutes without activity abandons a run so it cannot block forever). |
| REQ-FX-08 | **Safe retry.** Starting a run on the same business day after a partial/failed/abandoned one carries forward the finished exports (not sent again). Sending identical bytes again is a harmless **duplicate**. Revised content is stored as a later upload beside the earlier. |
| REQ-FX-09 | **Per-file results and run status.** Outcomes: stored, duplicate, carried (good); stored_with_errors, rejected, stale, incomplete, layout, failed, missing (not good). Run status: **complete** (all three good and reconciliation synchronised), **partial**, **failed**, plus abandoned. |
| REQ-FX-10 | **Visible freshness.** Last confirmed-current time per export type (from the newest upload or a helper run whose fresh file was identical to an earlier upload), a STALE warning after 30 hours or if never uploaded, on the Helper page and Frazer Intake. |
| REQ-FX-11 | **No side effects beyond intake.** The helper and its endpoints never resolve a reconciliation question, approve a match, change a source record or post a purchase, payment, transaction, customer or cash entry. |
| REQ-FX-12 | **Permissions.** The Helper page and token management are administrator-only. An employee whose name a token carries gains nothing from it. |

## 4. Acceptance cases (`tests/test_frazer_export_helper.py`, synthetic files)
| ID | Case | Expected |
|---|---|---|
| AC-FX-01 | Complete run | Three uploads through intake, attributed to the token's user; run complete; reconciliation synced; no decision/purchase/unit/payment/transaction/customer rows added; cutoff Unknown; Frazer's files untouched |
| AC-FX-02 | Payment period | Declared coverage = configured days ending on the run date |
| AC-FX-03 | Missing export, then retry | Partial; retry resumes, carries the two finished exports, stores one, no duplicates |
| AC-FX-04 | Lost response / start over | Identical bytes → duplicate; no new uploads |
| AC-FX-05 | Export command fails | Failed per file; nothing stored |
| AC-FX-06 | Yesterday's files in the folder | Stale, not sent; run failed |
| AC-FX-07 | Careless helper: old time, wrong date, future time, bad hash | Server refuses each; nothing stored |
| AC-FX-08 | Wrong export type; changed columns | Rejected / layout, naming columns; good files still stored; run partial |
| AC-FX-09 | File still being written, empty, cut off | Waited for / not settled |
| AC-FX-10 | Reconciliation cannot read | Run partial with the reason |
| AC-FX-11 | Unchanged file on a later day | Duplicate counts as done and freshness becomes current |
| AC-FX-12 | Second run while one is active; abandoned lease; database index | 409 / lock error; abandoned run stops blocking; database refuses two |
| AC-FX-13 | Layouts not approved | Run will not start |
| AC-FX-14 | Token reach | Only the five calls; every page sends to sign-in; no upload through the admin route |
| AC-FX-15 | Missing, wrong, expired, revoked token | 401; revoking closes an open run |
| AC-FX-16 | CSRF | Browser-style POST without Origin refused; bearer exemption only on helper paths |
| AC-FX-17 | Responses | No customer name, VIN or balance |
| AC-FX-18 | Another token's run | Not reachable |
| AC-FX-19 | Admin page | Token shown once, hash stored, revoke works, employee 403, visitor sent to sign in, stale warning on Helper and Intake pages |

## 5. Decisions needed
| ID | Decision | Default used |
|---|---|---|
| DEC-FX-01 | How Frazer runs and where exports land (the check in the guide) | Helper waits for files in a configured folder |
| DEC-FX-02 | Whether Frazer has a supported export command or scheduler | Operator runs the three saved exports |
| DEC-FX-03 | Which employee the uploads are attributed to | A named user chosen when the token is made |
| DEC-FX-04 | The overlapping payment period (days) | 10 |
| DEC-FX-05 | Closing time and what counts as stale | 30 hours |

## 6. Out of scope
Controlling Frazer's screens; storing Frazer credentials; unattended scheduling; importing into purchases, payments or the ledger; resolving or approving anything in reconciliation; unit profit.
