# "Update RTO Records": operator, installation and recovery guide

**Status (2026-11-13):** release candidate on the local branch `release/frazer-helper-rc1` (current `main` plus this work); **not merged, not deployed**, no production upload. The first production runs were rehearsed on a disposable copy of production ("First production run" below); the steps to release are in "Release runbook". Server side and the helper's logic are tested (`tests/test_frazer_export_helper.py`, synthetic files). The packaged installer, driver and window were run on the Eunice computer against a protected staging copy on 2026-11-10 (results in "Staging demonstration" below). Spec: [`specs/frazer-export-helper-spec.md`](specs/frazer-export-helper-spec.md). Needs migration `fh01` (three small tables, no customer data).

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
2. **Create a token:** label it, choose who the uploads are attributed to, copy it. It is shown once. Attribute it to the helper's own account, not to a person: create that account once on the server with `python -m app.scripts.users create-attribution --username eunice-frazer-helper --display-name "Eunice Frazer Helper"`. An attribution-only account has no password: nobody can sign in as it, it cannot be given a password or made an administrator, and it is not offered where a review issue or a vehicle question is assigned to someone. Disabling it stops its token.

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

## First production run (rehearsed 2026-11-12 on a disposable copy of production)

Proposed settings, not yet configured anywhere: first transaction period starts **2026-11-01**; uploads attributed to **Eunice Frazer Helper**.

Production holds three hand uploads from Nov 8 (vehicles 108 rows, balances 64, transactions 22 rows declared Nov 1 to Nov 8). The rehearsal restored a fresh copy of production into a throwaway PostgreSQL, applied `fh01` with the candidate, and ran the helper's own client code over HTTP against the candidate, in its "I will run the exports myself" mode. The files were the real exports Frazer produced on Eunice on Nov 10; the transaction file was that export cut to each period the app asked for. So it rehearses what the app does with the files, not a fresh Frazer export (that part is the staging demonstration below). 27 of 27 checks passed. Counts and totals only:

| Step | Observed |
|---|---|
| First run, interrupted (vehicle export never appears) | The app asked for 2026-11-01 through the day of the run. Balances and transactions stored; run **partial**; **no checkpoint**. A new run started afterwards was again given the first-run period from 2026-11-01 |
| Same day, run again | Continued the interrupted update: vehicles stored, the other two carried and not sent again; run **complete**; checkpoint set to that period. Transaction uploads: the Nov 8 hand upload plus one new one |
| Double counting | The new transaction file held 26 entries: the 22 of the Nov 8 upload, all unchanged, plus 4 later ones. Monthly Review read 48 rows from the two uploads and counted 26 entries: 17 payment entries $7,868.70 and 9 Late Fee entries $419.00, exactly the new file's own totals (before: 14 / $6,700.70 and 8 / $369.00). No entry was "revised" |
| Attribution | All three new uploads show **Eunice Frazer Helper**; the three Nov 8 uploads keep their uploader. Signing in as that account is refused, also with the token as the password; the token opens no page |
| Second update | Period 2026-11-04 through the same day (7 days before the accepted end, never before 2026-11-01). Vehicles and balances identical, nothing stored; transactions stored, all 15 entries unchanged; November totals the same |
| A later partial run | An older balance file left in the folder was refused as stale; run partial; the checkpoint stayed with the last complete run |
| Everything else | Row counts of all 24 other tables (purchases, transactions, payments, units and so on) unchanged; no reconciliation question or decision created; reconciliation read the new snapshots |
| Rollback | Account disabled, `alembic downgrade vr01`: the three helper tables are dropped, the uploads stay, and the code on `main` reads them with the same November totals. `alembic upgrade head` applied again cleanly |

**What starting on 2026-11-01 means.** That date is the permanent floor: the helper never asks Frazer for anything earlier, including in the periodic recheck. October transactions therefore stay absent unless a person uploads an October export by hand in Frazer Intake (entries are matched by Sequential ID, so that is safe at any time).

**One narrow limit seen in rehearsal.** In "I will run the exports myself" mode a file written up to about two seconds before the update started is treated as fresh. A person cannot hit that by hand, and the one-click mode measures from the moment it starts driving Frazer.

## Known issue before release: a payment deleted and entered again in Frazer is counted twice (seen 2026-11-12, staging)

Checked 2026-11-13 on the staging copy, read-only, after the two complete Eunice updates of Nov 12 (periods Nov 3 to Nov 12, 35 rows, and Nov 5 to Nov 12, 26 rows):

| | Payment entries | Late Fee entries |
|---|---|---|
| The two Nov 12 exports, distinct Sequential IDs (61 rows, 35 distinct; the 26 shared entries are identical) | 22, $8,947.70 | 13, $619.00 |
| Monthly Review, November (5 transaction uploads, 247 rows read, 38 entries in the month, none revised or unreadable) | 25, $11,252.70 | 13, $619.00 |
| Difference | 3, $2,305.00 | 0 |

Overlap itself is handled: no entry is counted twice because it arrived in several files. The difference is three payment entries held from earlier uploads and not in the Nov 12 exports:

- **Two, dated Nov 2 ($705.00 together):** before the Nov 3 start of those exports. Correctly counted.
- **One, dated Nov 3, a $1,600.00 Down Payment:** it was in three earlier uploads, including the Nov 8 hand upload, and is **no longer in Frazer's export for a period that covers its date.** The Nov 12 export instead holds a Down Payment for the same stock number, date, amount and remaining balance under a new Sequential ID, entered in Frazer on Nov 12. The upload result said so ("1 entry that an earlier upload has inside this period is not in this file"), but Monthly Review still counts both: November payment entries are **$1,600.00 higher than what Frazer now holds** (24 entries, $9,652.70 for Nov 1 to Nov 12).

**Production will show the same thing on its first run**, because its Nov 8 upload contains that earlier entry. Nothing is posted and no record is changed by this; it is a wrong figure on the Monthly Review page until handled. Whether an entry that has disappeared from a later export of its own date should leave the totals (listed for a person, like a revised entry) is a rule for the owner to decide; it is not built.

## Release runbook (only after explicit approval; nothing here has been run on production)

Run on ubuntu-dev in `/home/drew/projects/rto` unless it says Eunice. Production is `rto-web` (systemd) on `100.123.116.111:8000`, schema `vr01`.

**1. Stop the app, then back up.** Nothing below runs while `rto-web` is serving: the checkout is not switched and the database is not migrated under a running app, and nothing can be entered between the backup and the change.
```
sudo systemctl stop rto-web && systemctl is-active rto-web      # must print: inactive
git status --short && git rev-parse --short HEAD                # clean apart from untracked notes; expected b3b1a2c
sudo systemctl start rto-backup && systemctl status rto-backup --no-pager | head -5
ls -t /home/drew/rto-backups/auto/ | head -1                    # a folder from this minute
```
The backup restores itself into a throwaway cluster and fails loudly if that does not match. Do not go on unless it succeeded.

**2. Code and migration (app still stopped).**
```
git switch main && git merge --ff-only release/frazer-helper-rc1
source .venv/bin/activate && alembic upgrade head        # vr01 -> fh01: three new tables, nothing else changes
alembic current                                           # fh01 (head)
python -m app.scripts.users create-attribution --username eunice-frazer-helper --display-name "Eunice Frazer Helper"
sudo systemctl start rto-web && systemctl is-active rto-web     # active; then open /login
```
**3. In the app, as an administrator** (*Finance > Frazer Intake > Helper page*).

*Approve the three column layouts.* In "Approved column layouts" choose each upload in turn and press *Approve this upload's columns*. After each, the list above the button must read exactly:

| Export | Approve | Must then show |
|---|---|---|
| Customer balances | `FU-0001` | approved from upload #1 (**38 columns**) |
| Payment transactions | `FU-0002` | approved from upload #2 (**24 columns**) |
| Vehicle list | `FU-0003` | approved from upload #3 (**22 columns**) |

Checked 2026-11-13 (header rows only): production's `FU-0002` has the same 24 column headings, in the same order, as the file the saved export `frazer-payment-transactions` produced on Eunice on Nov 12, and likewise 38 and 22 for the other two. Production holds no 26-column transaction upload; 26 columns is an older transaction layout (with two more columns at the end) that the current saved export does not produce. Transaction rows carry one empty cell after the last column; the parser notes it and it is not a column.

If the transaction line shows any number other than 24 (for example 26), **do not go on and do not approve a wider layout to make it pass.** Instead run the saved export *File 2, frazer-payment-transactions* by hand in Frazer, upload that file in Frazer Intake as a transaction export with its period, check its preview, and approve that upload. The helper compares every heading in order, so a 26-column approval would make it refuse every file the current export writes, and a 24-column approval refuses anything else.

*Create the token.* Label it for Eunice, attribute it to `eunice-frazer-helper`, and choose how long it is valid (the form offers 90 days, at most 365; the helper stops with "refused this computer's token" when it expires, and a new one goes into `config.json`). Copy it: it is shown once. Keep it out of Git, chat and this document.

**4. Switch the Eunice shortcut** (Windows PowerShell on Eunice, as `steve`). The shortcut itself does not change; it reads `C:\FrazerHelper\config.json`. Only three values change, and Frazer's saved exports are not touched. Do not run a staging update on the same day before switching.
```powershell
$c='C:\FrazerHelper\config.json'; Copy-Item $c 'C:\FrazerHelper\config.staging.json'
$j=Get-Content $c -Raw | ConvertFrom-Json
$j.server='http://100.123.116.111:8000'; $j.first_coverage_start='2026-11-01'; $j.token=(Read-Host 'Paste the production token')
[System.IO.File]::WriteAllText($c, ($j | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding $false))
& 'C:\FrazerHelper\python\python.exe' 'C:\FrazerHelper\app\frazer_helper.py' check --config $c
```
The check must print the three approved layouts, `coverage_checkpoint: null` and no `STOPPED`. This snippet writes the file the same way the installer does but has not itself been run on Eunice.

**5. First update.** With Frazer signed in on its main screen, double-click **Update RTO Records**. Expect: period 2026-11-01 through today, dates read back in Frazer; COMPLETE. Then in Frazer Intake: three new uploads attributed to Eunice Frazer Helper; the transaction upload shows the 22 Nov 8 entries as unchanged; on the Helper page the run is complete. If it ends PARTLY DONE, fix what it names and click again the same day.

**6. Afterwards.** Tag and write the release notes as `docs/releases.md` describes (a migration makes it a MINOR version); push only then. Delete `config.staging.json` on Eunice. The staging services can be stopped once nobody needs them.

**Rollback, mildest first.**
1. *Stop the helper only:* Helper page > Revoke (or `python -m app.scripts.users disable eunice-frazer-helper`). The shortcut then reports that the app refused its token and changes nothing. Uploads already stored are ordinary intake evidence and can stay.
2. *Point Eunice back at staging:* `Copy-Item C:\FrazerHelper\config.staging.json C:\FrazerHelper\config.json`.
3. *Remove the code and tables:* `sudo systemctl stop rto-web`, disable the account, `alembic downgrade vr01` **while the new code is still checked out**, then `git switch --detach b3b1a2c && sudo systemctl start rto-web`. Uploads the helper made stay and the old code reads them (rehearsed). Run history and tokens are lost. Fix forward on a branch; never force-push `main`.
4. *Restore the backup from step 1* (`docs/backup_recovery.md`) only if the data itself is wrong; it discards everything entered since the backup, including purchases, so prefer 1 to 3.

## Staging demonstration (2026-11-10, Eunice, staging copy only)

Counts and totals only; no production system was written to (production still had 3 Frazer uploads and no helper tables afterwards).

| Step | Observed |
|---|---|
| Setup | Python 3.12.10 installed for the user (signature valid). File 1 renamed `FRZDATA1` to `frazer-vehicles`, File 2 `frzr-transctions` to `frazer-payment-transactions`; Frazer confirmed "The Specifications have been saved"; names read back from a fresh window. 23 settings views pictured before and after: 20 identical; 3 differed only by display noise (at most 2 of 255 colour levels) or the focus outline, which the first version of the comparison wrongly reported as a difference. |
| Update 1 (first run) | Period 2026-10-01 through 2026-11-10, dates read back in Frazer. Vehicles 108 rows, balances 64 rows, transactions 140 rows, all stored; 0 unreadable rows; all 140 stock numbers found in the vehicle list; 102 payment entries, 37 Late Fee entries, 1 ungrouped. The 22 entries of the hand-uploaded Nov 8 file were all present and identical. Reconciliation synced; no new question. |
| Update 2 (minutes later) | Period 2026-11-03 through 2026-11-10 (7 days before the last accepted end). Vehicles and balances identical, nothing stored. Transactions stored (24 rows), every entry identical to one already held; distinct entries unchanged at 102 / 37 / 1. No new snapshot, question, decision or event. |
| Update 3 | Same period; all three files identical; nothing stored. Run with the Frazer window minimised, which the helper restored. |
| Nov 11 and 12 (driver fix) | Seven updates on Nov 11 stopped before touching Frazer with "Another Frazer screen is open": the remote display had an untitled 40x40 window parked at -10000,-10000. Nothing was stored and the checkpoint did not move. The driver now ignores only a window that is untitled apart from the session suffix, at most 100x100 and far off screen; any other extra window still stops it, described and pictured. With that driver two updates completed on Nov 12: period Nov 3 to Nov 12 (vehicles 108, balances 64, transactions 35 rows stored), then Nov 5 to Nov 12 (vehicles and balances identical, transactions 26 rows, all unchanged). |
| Failed attempts | Three attempts made with Frazer minimised (before the fix) stopped in about a second with "Frazer is not open"; nothing was stored and the checkpoint did not move. |
