# Frazer Export Helper: Companion Specification

**Status:** Draft FX-0.2, 2026-11-10 (adds automatic generation for Hosted Frazer, transaction coverage, naming and the run manifest). Companion to `cash-financial-control-spec.md` (Frazer intake, s19; REQ-FZ-*) and `vehicle-reconciliation-spec.md` (REQ-VR-*). Changes no text in either. **Writes:** its own three tables (`frazer_helper_*`) and whatever the existing intake and reconciliation services write. **Increment:** closing-time upload helper.

## 1. Why
Frazer exports are uploaded by hand through an administrator's browser, so data goes stale unnoticed. The business wants one button, pressed by an authorised employee already signed in to Frazer, that sends the three configured exports (vehicle list, customer balances, transactions). No automated Frazer login; not an unattended schedule. Since FX-0.2 the same click also makes Frazer create the three exports (REQ-FX-14).

## 2. Discovery status (what is and is not known)
| Question | Status |
|---|---|
| Is Frazer local Windows software, hosted, or RDP? | **Hosted** (confirmed 2026-11-10): `HostedClient.exe` embeds a remote-desktop connection to the vendor's server. The helper runs on the Eunice computer (DESKTOP-A502OF8, Console session, Tailscale reaches the app, no Python). `C:\` inside Frazer is the hosted server's; a `\\tsclient` path inside the hosted session is expected to be the Eunice computer (to be verified). |
| Where does the helper run? | On the Eunice computer, in the signed-in user's desktop session. |
| Landing folder, file names, saved presets | **Verified 2026-11-09 on Eunice.** The saved exports write to `\\tsclient\C\FrazerData`, which is Eunice's own `C:\FrazerData`. Screen M-8 Export Data: File 1 = vehicles (option C, Type of Sale All, Vehicle Status Both), File 2 = payment transactions (option F, dates on screen; "Include Sale Date", "Include VIN" and "Include Cancelled Transactions" unticked, hence 24 columns and no VIN), File 3 = customer balances (option A). Frazer names the file after the slot and adds `.CSV`. Header fingerprints: vehicles 22 columns `4b648b85681dc4e8`, balances 38 `84d6e573ee243298`, transactions 24 `cbd33bcd9df90361`. |
| A supported export mechanism (command line, scheduler)? | **None found.** Generation is by driving the Hosted Frazer windows (REQ-FX-14); a configured command per export and the operator-runs-it mode remain. |
| What can be automated in a hosted session? | Hosted Frazer is RemoteApp: each Frazer window is a local window with a title (`Frazer`, `M-8  Export Data`, `File Created`), its contents a picture. Proven on Eunice: Alt+M, 8 opens Export Data; Alt+1/2/3 select a slot; text fields can be read by copy; Frazer closes Export Data after each export; Alt+C is NOT Create File (it selects option C). |
| Existing machine authentication in the app? | **None**: only cookie sessions for signed-in users, and the CSRF check requires a browser Origin. Hence REQ-FX-04. |

## 3. Requirements
| ID | Requirement |
|---|---|
| REQ-FX-01 | **User-started, one button.** A person starts a run; nothing runs unattended. The helper never signs in to Frazer and stores no Frazer or app password. |
| REQ-FX-02 | **Existing presets, preserved.** The helper uses the saved Frazer exports as they are. The app holds an administrator-approved header row per export type; a file whose headings differ (added, missing, reordered) is refused, naming the columns. |
| REQ-FX-03 | **Fresh files only.** A file is accepted only if written after the run started (in automatic mode: after generation was asked for) and, checked again by the app, within the run, on the run's business date, with a matching SHA-256 of what was read. A matching older file is reported **stale** and never sent. A driver or operator saying "done" proves nothing: only a rewritten file does. The transaction period is REQ-FX-13. |
| REQ-FX-04 | **Narrow, revocable authentication.** An administrator creates a token (shown once, only its hash stored, optional expiry, attributable to a named user). A token authenticates only five endpoints under `/api/frazer-helper/`: status, start run, send file, report failure, finish run. It is not a session: it opens no page, and cannot read customers, balances, vehicles or any other file. The CSRF Origin check is skipped only for those paths and only when a bearer header is present. Revoking stops it at once and closes any open run. Responses carry no customer data. |
| REQ-FX-05 | **Existing services only.** Files are stored by `frazer_intake_service.store_upload` (size, type, encoding, hashing, parsing, duplicate refusal, original kept). Reconciliation is synchronised through `vr_intake_bridge`. No direct writes to intake or reconciliation tables. |
| REQ-FX-06 | **Honest times.** Each result keeps the file's own written time and the declared period; `actual_cutoff` stays "Unknown"; nothing is described as one simultaneous snapshot. |
| REQ-FX-07 | **One active run.** Enforced by a partial unique index in the database (not only a check), by a local lock on the helper computer, and by a lease (45 minutes without activity abandons a run so it cannot block forever). |
| REQ-FX-08 | **Safe retry.** Starting a run on the same business day after a partial/failed/abandoned one carries forward the finished exports (not sent again). Sending identical bytes again is a harmless **duplicate**. Revised content is stored as a later upload beside the earlier. |
| REQ-FX-09 | **Per-file results and run status.** Outcomes: stored, duplicate, carried (good); stored_with_errors, rejected, stale, incomplete, layout, failed, missing (not good). Run status: **complete** (all three good and reconciliation synchronised), **partial**, **failed**, plus abandoned. |
| REQ-FX-10 | **Visible freshness.** Last confirmed-current time per export type (from the newest upload or a helper run whose fresh file was identical to an earlier upload), a STALE warning after 30 hours or if never uploaded, on the Helper page and Frazer Intake. |
| REQ-FX-11 | **No side effects beyond intake.** The helper and its endpoints never resolve a reconciliation question, approve a match, change a source record or post a purchase, payment, transaction, customer or cash entry. |
| REQ-FX-12 | **Permissions.** The Helper page and token management are administrator-only. An employee whose name a token carries gains nothing from it. |
| REQ-FX-13 | **Transaction coverage is decided by the app.** Starting a run returns the period to export; a transaction file for any other period is refused. First run: the start is stated explicitly by configuration (`first_coverage_start`); without it the run does not start. Later runs: start = end of the last accepted period minus `OVERLAP_DAYS` (7), end = the run's business date, so periods always overlap and a missed day is caught up. Accepted = a run that finished **complete**; a partial, failed or abandoned run never advances the checkpoint. At least every `DEEP_RECHECK_EVERY_DAYS` (7) the period reaches back `DEEP_RECHECK_DAYS` (62), never before the first accepted start. Stated limits: older edits are not re-read; observed transaction dates are a consistency check only. |
| REQ-FX-14 | **Automatic generation, verified before and after.** In automatic mode a driver opens Export Data, selects the slot, and before creating confirms on screen: the slot's saved name, the selected slot, data type and options. For transactions it types the period's dates and reads both back from Frazer's fields; a mismatch creates nothing. It clicks Create File only where the button is found, sends no input unless the expected window is in front, never types a password, never opens a customer, and never saves specifications during an update. The helper still decides from the file. The employee may instead run the exports by hand (manual mode). |
| REQ-FX-15 | **Names and archive.** Working files are `frazer-vehicles`, `frazer-customer-balances`, `frazer-payment-transactions` (+ `.CSV`, added by Frazer; no doubled extension). Each run has its own date-and-time folder holding `frazer-vehicles-as-of-DATE.csv`, `frazer-customer-balances-as-of-DATE.csv`, `frazer-payment-transactions-START-through-END.csv` and `manifest.json` (original file name, archive name, SHA-256, size, generation time local and UTC, time zone, period, date read-back, basis of the period). DATE is the generation date; the source cutoff is recorded as Unknown. A working file about to be overwritten that is not already archived is preserved first. |
| REQ-FX-16 | **Separate outcomes.** Export (generation), upload (intake) and diagnostics are reported separately per file. A diagnostic failure (a screenshot that could not be saved) never changes or hides an export or upload result. |
| REQ-FX-17 | **Overlap downstream.** Overlapping transaction uploads are united by Sequential ID (existing Monthly Review logic): an entry present in several uploads counts once; an entry whose content differs is kept in both versions, listed as revised and counted in no total; fee assessments are grouped apart from payment entries. The upload result reports revised counts and entries an earlier upload has inside the period that the new file lacks. |

## 4. Acceptance cases (`tests/test_frazer_export_helper.py`, synthetic files)
| ID | Case | Expected |
|---|---|---|
| AC-FX-01 | Complete run | Three uploads through intake, attributed to the token's user; run complete; reconciliation synced; no decision/purchase/unit/payment/transaction/customer rows added; cutoff Unknown; Frazer's files untouched |
| AC-FX-02 | First transaction period | Declared coverage = the explicit first start through the run date; without an explicit start the run does not start |
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
| AC-FX-20 | Catch-up and recheck | Next period starts before the last accepted end, also after a three-week pause; periodic recheck reaches 62 days back but never before the first start |
| AC-FX-21 | Partial run and the checkpoint | Transactions stored in a partial run do not advance the checkpoint; the completing retry does |
| AC-FX-22 | Wrong period | The app refuses a transaction file for a period other than the run's; nothing stored |
| AC-FX-23 | Overlap counted once; fees apart | Two overlapping uploads: each entry counted once; Late Fee in its own group |
| AC-FX-24 | Changed entry | Stored beside the earlier version, listed as revised, in no total; result says so |
| AC-FX-25 | Entry missing from a later export | Reported by Sequential ID; earlier record kept |
| AC-FX-26 | Second update the same day | Complete; all three identical; no new uploads, snapshots or questions; its own run folder |
| AC-FX-27 | Automatic run | One driver pass; archive names; manifest fields; upload carries the archive name; earlier original preserved |
| AC-FX-28 | Driver says done, no fresh file | Stale; nothing sent |
| AC-FX-29 | Dates do not read back | That export only is not used; others stored; run partial |
| AC-FX-30 | Preset not confirmed, then retry | Partial; the retry generates only the missing export |
| AC-FX-31 | Diagnostic problem; driver crash | Result unchanged and stated; a crash is an export failure, not a helper crash |
| AC-FX-19 | Admin page | Token shown once, hash stored, revoke works, employee 403, visitor sent to sign in, stale warning on Helper and Intake pages |

## 5. Decisions needed
| ID | Decision | Default used |
|---|---|---|
| DEC-FX-01 | How Frazer runs and where exports land (the check in the guide) | Helper waits for files in a configured folder |
| DEC-FX-02 | Whether Frazer has a supported export command or scheduler | None found; the driver creates the exports (REQ-FX-14) |
| DEC-FX-03 | Which employee the uploads are attributed to | A named user chosen when the token is made |
| DEC-FX-04 | Overlap, recheck interval and depth (days) | 7, 7, 62 |
| DEC-FX-06 | The explicit start of the first transaction period in production | **Open.** Staging demonstration uses 2026-10-01; production is not configured |
| DEC-FX-05 | Closing time and what counts as stale | 30 hours |

## 6. Out of scope
Storing Frazer credentials; unattended scheduling; changing lock, sleep or remote-access settings; reading Frazer's screens beyond the Export Data window; importing into purchases, payments or the ledger; resolving or approving anything in reconciliation; unit profit.
