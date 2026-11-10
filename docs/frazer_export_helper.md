# "Update RTO Records": operator, installation and recovery guide

**Status (2026-11-10):** built on the unmerged branch `feature/frazer-export-helper`; **not deployed**, no production upload. Server side and the helper's logic are tested (`tests/test_frazer_export_helper.py`, synthetic files). The packaged installer, driver and window were run on the Eunice computer against a protected staging copy on 2026-11-10 (results in "Staging demonstration" below). Spec: [`specs/frazer-export-helper-spec.md`](specs/frazer-export-helper-spec.md). Needs migration `fh01` (three small tables, no customer data).

## What it is

A desktop shortcut on the Frazer computer (Eunice, `DESKTOP-A502OF8`). An employee who is signed in to Hosted Frazer double-clicks **Update RTO Records**. The helper then:

1. asks the RTO app to start an update; the app answers with the transaction period to export;
2. makes Frazer create the three saved exports (vehicles, customer balances, payment transactions);
3. accepts each file only if Frazer wrote it during this update, keeps a copy in the update's own dated folder, and sends it to the app;
4. the app stores each file through the normal Frazer Intake checks and brings reconciliation up to date;
5. the window shows a plain result for each file and for the whole update.

It never signs in to Frazer, stores no password, never opens a customer, never changes or deletes Frazer's files, and never accepts a reconciliation decision or posts a financial entry.

## What the employee does

1. Sign in to Hosted Frazer and leave it on its **main screen** (no customer or other Frazer window open).
2. Double-click **Update RTO Records** on the desktop.
3. **Do not touch the mouse or keyboard** until the window says it has finished using Frazer's screens (about two minutes). You will see the Export Data screen open and close three times.
4. Read the result:
   - **COMPLETE**: all three exports were created fresh, accepted, and reconciliation is up to date.
   - **PARTLY DONE** or **NOT DONE**: the window says which export or upload failed and why. Fix that, then double-click the shortcut again. Exports already accepted today are not created or sent again.

It is fine to run it more than once a day. If nothing changed in Frazer, the vehicle and balance files are reported as identical and add nothing. The transaction file is identical only when its period is the same as the last stored one: the update right after the very first one uses a shorter period (the last 7 days instead of the first-run start), so it is stored as a new upload whose entries all match ones already held and are counted once.

The shortcut is on the desktop, so Frazer is usually minimised to reach it. That is fine: the helper restores the Frazer window itself.

**If Frazer's screens cannot be driven** (see "When it stops"), press **I will run the exports myself** in the window. The helper then tells you which saved export to run and waits for each file; for payment transactions it tells you the exact Beginning and Ending dates to type. The same freshness and intake checks apply.

Never copy an old export into `C:\FrazerData` to "make it work". A file that Frazer did not write during this update is refused and never sent.

## What the result lines mean

Each file has two lines, reported separately, so a later problem never hides an earlier success:

| Line | Meaning |
|---|---|
| **Export: created at ...** | Frazer wrote a new file during this update |
| Export: FAILED / STALE / MISSING | Frazer did not create it, or only an older file was found (not used) |
| **Upload: accepted and stored as FU-...** | The app read it and stored it |
| Upload: accepted (identical ...) | Byte-for-byte the same as a file already stored: nothing to add, and the data is confirmed current |
| Upload: already accepted earlier today | Carried from the update this one continues |
| Upload: REFUSED ... | Wrong columns, wrong period, incomplete or not fresh: nothing stored; the reason is given |
| Period: A through B (dates confirmed in Frazer ...) | The transaction dates were typed and read back from Frazer before the export was created |
| Reconciliation: synced / not_needed / failed | Whether the vehicle reconciliation read the new snapshots |
| Diagnostics: ... | Screenshots and logs for troubleshooting. A problem here does **not** change the results above |

## Where things are kept on the Frazer computer

| Path | Contents |
|---|---|
| `C:\FrazerData\` | Frazer's working files: `frazer-vehicles.CSV`, `frazer-customer-balances.CSV`, `frazer-payment-transactions.CSV` (Frazer adds `.CSV`; each export overwrites the last) |
| `C:\FrazerHelper\runs\2026-11-09_223805\` | One folder per update: `frazer-vehicles-as-of-2026-11-09.csv`, `frazer-customer-balances-as-of-2026-11-09.csv`, `frazer-payment-transactions-2026-10-31-through-2026-11-09.csv`, `manifest.json`, `diagnostics\` |
| `C:\FrazerHelper\preserved\` | A copy of any working file that was about to be overwritten and was not already archived (kept once per distinct content, with its own written time) |
| `C:\FrazerHelper\preset-backup\<date_time>\` | Pictures of the three saved exports' settings and their names, taken at setup before anything was renamed |
| `C:\FrazerHelper\config.json` | Settings (app address, token, first period start, the three slot names) |
| `C:\FrazerHelper\app\`, `C:\FrazerHelper\python\` | The helper and its own copy of Python |

`manifest.json` records, per file: Frazer's original file name, the archive name, SHA-256, size, the time it was generated (local with UTC offset, and UTC), the time zone, and for transactions the period, the dates read back from Frazer and why that period was chosen. **"As of" is the date the file was generated. The exact moment Frazer's data was cut off is not known and is recorded as Unknown.** The three exports are created a few seconds apart and are never described as one snapshot.

## The transaction period

The app, not the helper, decides the period, and refuses a transaction file for any other period.

- **First update:** the start date is stated explicitly in `config.json` (`first_coverage_start`). Nothing is assumed. The period ends on the day of the update.
- **Every later update:** the period starts 7 days before the end of the last *accepted* period and ends today. Consecutive periods therefore always overlap; if nobody runs it for three weeks, the next period still starts before the last one ended, so there is no gap.
- **Accepted** means the whole update finished COMPLETE. A partial or failed update never moves the checkpoint.
- **Periodic recheck:** at least every 7 days the period reaches back 62 days (never before the first start date), so an entry that was backdated or edited after it was first uploaded is seen again.

How overlap is handled downstream (existing Monthly Review logic, unchanged): entries are matched by Frazer's **Sequential ID**. The same entry arriving in several files is counted **once**. An entry whose content differs between files is **not overwritten**: both versions are kept, it is listed as *revised* for a person to resolve, and it is counted in no total until then. Late Fee entries are grouped as fee assessments, apart from payment entries. If an earlier upload has an entry dated inside the new period that the new file no longer contains, the upload result says so (Sequential IDs only).

**Limits, stated plainly:**
- The dates are checked *before* the export by reading them back from Frazer's own date fields. The dates found inside the file are only a consistency check: a wrong, narrower period would look the same if no transactions fell on the missing days.
- An entry edited or backdated more than 62 days back is not re-read automatically.
- A deleted entry is noticed only when a later export covers its date.
- A transaction file identical to one already stored adds no upload; its newer period is recorded on the update, and the next stored file's period covers it.

## One-time setup

**In the app (administrator), after the branch is deployed:** *Finance > Frazer Intake > Helper page*.
1. **Approve the column layouts:** for each of the three exports choose an upload that was read correctly and press *Approve this upload's columns*. A file with different columns is refused.
2. **Create a token:** label it, choose the person uploads are attributed to (the token gives them no extra access), copy it. It is shown once.

**On the Frazer computer** (Windows PowerShell; no administrator rights): set the four values and run the installer's text. `$FzSource` is wherever the three files in `tools/frazer_helper/` are served from.

```powershell
$FzSource='http://SERVER:PORT'; $FzServer='http://RTO-APP:8000'; $FzToken='fxh_...'; $FzFirst='YYYY-MM-DD'
iex (iwr "$FzSource/f/Install-UpdateRtoRecords.ps1" -UseBasicParsing).Content
```

Frazer must be signed in and on its main screen, Caps Lock off. The installer:
1. creates `C:\FrazerHelper` and fetches `frazer_helper.py` and `FrazerDriver.ps1`;
2. installs Python 3.12 **for this Windows user only** into `C:\FrazerHelper\python` (python.org installer; its signature must be the Python Software Foundation's or it is not run);
3. takes a picture of every settings view of the three saved exports, then renames File 1 to `frazer-vehicles` and File 2 to `frazer-payment-transactions` (File 3 is already `frazer-customer-balances`). A new name is saved only after it reads back exactly from the name box; afterwards the names are read again from a fresh window and every settings view is compared with its earlier picture (ignoring the faint colour noise of the remote display and the dotted keyboard-focus outline, which are not settings);
4. writes `config.json` from the names Frazer actually shows;
5. creates the desktop shortcut **Update RTO Records**;
6. checks the connection to the app and prints the result. It ends with `SETUP COMPLETE` or a list of problems.

Variants: `$FzPresets=$false` installs without touching Frazer's saved exports (their current names are read and used). `$FzWant=@{'1'='FRZDATA1';'2'='frzr-transctions'}` puts the earlier names back.

To remove access: *Helper page > Revoke* (the helper stops working at once). To uninstall: delete the shortcut and `C:\FrazerHelper`. Frazer's saved exports keep whatever names they have.

## When it stops: what happened and what to do

| What the window says | Cause | What to do |
|---|---|---|
| Frazer is not open | Hosted Frazer is closed or signed out. (A minimised Frazer window is fine: the helper restores it) | Sign in, leave it on the main screen, run again |
| Another Frazer screen is open | A customer or other window is in front of the main screen | Close it, run again |
| The Frazer window could not be brought to the front | Screen locked, or another program is holding the front | Unlock, close the other window, run again |
| File N in Frazer is named X, not Y | A saved export was renamed in Frazer | Rename it back in Frazer, or run setup again so the settings match |
| The screen did not show the expected settings | A saved export's data type or options were changed | Restore the saved export in Frazer (the pictures in `preset-backup` show the original); nothing was created |
| The dates read back from Frazer are not the period asked for | Frazer did not accept the typed dates | Run again; if it repeats, use *I will run the exports myself* |
| Frazer did not create ... within the time allowed | Frazer was slow or showed something unexpected | Look at Frazer's screen, close any message, run again |
| The Export Data window is WxH, not 1016x656 | Display scaling or a Frazer update changed the screen | Use *I will run the exports myself* and report it |
| Only an older file was found. It was NOT used | Frazer wrote nothing new | Run again |
| REFUSED (columns changed) | The saved export's fields were edited | Restore the fields, or an administrator approves the new layout |
| This computer's clock differs from the RTO app's | Clock more than about 2 minutes off | Settings > Time & language > Sync now |
| Could not reach the RTO app | Network or Tailscale down | Check Tailscale, run again; nothing was lost |
| The RTO app refused this computer's token | Token revoked or expired | An administrator creates a new token; put it in `config.json` |
| Another update is already in progress | A second click, or a crashed update | Wait; an abandoned update stops blocking after 45 minutes (the local lock after 3 hours) |

After any failure, running it again the same day continues the unfinished update: exports already accepted are kept, only the missing ones are created and sent. Identical files are never stored twice.

## Operating requirements and limits

- **An unlocked, attended desktop.** Creating the exports works by sending keys and one click per export to the Hosted Frazer window. The Frazer computer must be signed in to Windows and unlocked, Hosted Frazer signed in, and nobody using the mouse or keyboard for about two minutes. The helper does not change lock, sleep or remote-access settings. Uploading afterwards needs no screen.
- **Hosted Frazer specifics.** Frazer runs on the vendor's server; each Frazer window appears on the Frazer computer as its own window with a title, but its controls are a picture. The helper recognises windows by title, checks the Export Data window is exactly 1016 x 656, reads which options are selected from the screen, reads text fields by copying them, and clicks Create File only where its green label is found. A Frazer update that moves these stops the helper before it clicks; the manual button is the fallback.
- **Exports land locally** because the saved exports write to `\\tsclient\C\FrazerData`, which is the Frazer computer's own `C:\FrazerData`. Verified for the Windows user `steve`; another Windows user would need the same check.
- **No supported export command** was found in Frazer; if the vendor provides one, put it in `config.json` (`"command"` per export, `"mode": "manual"`).
- **Entries the app does not group.** A transaction whose description is neither a payment nor a Late Fee (seen: one "Deposit") is stored with a warning and counted in neither total until a person looks at it. The update still reports COMPLETE.
- **Balances wording.** A new customer-balance file normally differs from the previous one in most rows (balances and days past due move daily). The result line says these records "differ from an earlier upload ... shown as revised"; for balances that is ordinary change, not a problem.
- **Not verified:** behaviour while the remote-control viewer is disconnected; a different display scaling; a Windows user other than `steve`; the corrected settings comparison (driver 1.1) has been checked against the real before/after pictures off the Frazer computer but setup has not been re-run with it.

## Staging demonstration (2026-11-10, Eunice, staging copy only)

Counts and totals only; no production system was written to (production still had 3 Frazer uploads and no helper tables afterwards).

| Step | Observed |
|---|---|
| Setup | Python 3.12.10 installed for the user (signature valid). File 1 renamed `FRZDATA1` to `frazer-vehicles`, File 2 `frzr-transctions` to `frazer-payment-transactions`; Frazer confirmed "The Specifications have been saved"; names read back from a fresh window. 23 settings views pictured before and after: 20 identical; 3 differed only by display noise (at most 2 of 255 colour levels) or the focus outline, which the first version of the comparison wrongly reported as a difference. |
| Update 1 (first run) | Period 2026-10-01 through 2026-11-10, dates read back in Frazer. Vehicles 108 rows, balances 64 rows, transactions 140 rows, all stored; 0 unreadable rows; all 140 stock numbers found in the vehicle list; 102 payment entries, 37 Late Fee entries, 1 ungrouped. The 22 entries of the hand-uploaded Nov 8 file were all present and identical. Reconciliation synced; no new question. |
| Update 2 (minutes later) | Period 2026-11-03 through 2026-11-10 (7 days before the last accepted end). Vehicles and balances identical, nothing stored. Transactions stored (24 rows), every entry identical to one already held; distinct entries unchanged at 102 / 37 / 1. No new snapshot, question, decision or event. |
| Update 3 | Same period; all three files identical; nothing stored. Run with the Frazer window minimised, which the helper restored. |
| Failed attempts | Three attempts made with Frazer minimised (before the fix) stopped in about a second with "Frazer is not open"; nothing was stored and the checkpoint did not move. |
