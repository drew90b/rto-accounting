# Frazer export helper: operator and installation guide

**Status:** built on the unmerged branch `feature/frazer-export-helper` (from main `b3b1a2c`); not deployed. Spec: [`specs/frazer-export-helper-spec.md`](specs/frazer-export-helper-spec.md). Needs migration `fh01` (three small tables, no customer data).

## What it is

A small program for the Frazer computer. A person presses **one button** at closing time. The helper waits for the three saved Frazer exports to be written, checks each, and sends them to the RTO app, which stores them through the normal Frazer Intake and lets reconciliation read them. It never signs in to Frazer, never stores anyone's password, and never changes or deletes Frazer's files.

**What the button does *not* do (yet):** make Frazer produce the exports. Whether Frazer can be asked to export by command line, schedule or hotkey is not known (see "Check on the Frazer computer"). Until it is, the helper tells the operator which saved export to run and waits for the file. If a command exists, put it in `config.json` (`"command"`) and the helper runs it. Controlling Frazer's screens is not assumed and is not built.

## What an operator does (every closing time)

1. Sign in to Frazer as usual.
2. Double-click **Send Frazer exports** on the desktop. A window opens.
3. Press **Send today's Frazer exports**.
4. When the window says *"in Frazer, run the saved export 'X' now"*, run that saved export in Frazer. It does this three times (vehicles, customer balances, transactions).
5. Read the result line: **COMPLETE** (done), **PARTIAL** (some are done; the window says which and why) or **FAILED**. For PARTIAL or FAILED, fix what it says and press the button again: finished exports are not sent twice.

Never copy an old export into the folder to "make it work". The helper refuses a file written before the button was pressed and says so.

## One-time setup (an administrator)

**In the app (administrator):** *Finance > Frazer Intake > Helper page* (`/finance/frazer/helper/`).
1. **Approve the column layouts.** For each of the three exports, pick an upload you already know was read correctly and press *Approve this upload's columns*. The helper refuses any file whose columns differ (a preset edited in Frazer, the wrong report).
2. **Create a token**: label it ("Frazer PC"), choose the person uploads are attributed to (an employee is fine: the token gives them no extra access), and copy the token shown. It is shown once.

**On the Frazer computer:**
1. Install Python 3.9+ from python.org (tick *Add python.exe to PATH*).
2. Copy the folder `tools/frazer_helper/` to the computer and double-click `install.bat`.
3. Edit `C:\FrazerHelper\config.json`: `server` (the app's address), `token`, `landing_folder` (where Frazer saves exports), the three `pattern`s (file-name patterns of your saved exports) and `payment_days` (how many days of transactions each run asks for; they overlap on purpose, duplicates are handled by the app).
4. Test: `python C:\FrazerHelper\frazer_helper.py check` prints whether the folders exist and the app accepts the token.

To remove access: *Helper page > Revoke*. The helper stops working at once. To uninstall, delete `C:\FrazerHelper`.

## What you see afterwards

*Helper page*: last confirmed-current time for each export type (red **STALE** after 30 hours or if never), every run with per-export results and file-written times, reconciliation status. *Frazer Intake* shows the same stale warning. A run folder `C:\FrazerHelper\runs\2026-11-09_173300\` keeps copies of the files and `run.json` (what happened).

## What the helper checks, and what it does about it

| Problem | What happens |
|---|---|
| File written before the button was pressed (yesterday's) | Refused as **stale**, never uploaded; the run is not complete |
| File still being written, empty, locked, or cut off | Waited for; refused as **incomplete** if it never settles |
| Wrong export (balances under the vehicles name), or columns differ from the approved layout | Refused (**rejected** / **layout**) naming the columns; nothing stored |
| Export command fails, or no file appears in time | Reported as **failed** / **missing** |
| Identical file already uploaded | **duplicate**: counts as done, nothing new stored |
| Changed file for the same period | Stored as a new, later upload beside the old one (existing revision handling); nothing overwritten |
| Network drops mid-way | Retried a few times; if still failing, press the button again |
| Two people press it at once / twice | One run at a time (on this computer, and in the app) |
| Reconciliation could not read the new uploads | Run is **partial**, with the reason |

The three exports are taken at different moments. The app records each file's own written time and the period the helper declared; it never describes them as one snapshot, and Frazer's true cutoff stays "Unknown".

## Check on the Frazer computer (the smallest thing needed from you)

Please answer or check these on the Frazer PC (a phone photo of each is plenty):
1. **Where does Frazer run?** Is it installed on that Windows PC, or do you open it through Remote Desktop (a window titled with an address) or a browser? In Remote Desktop, the redirected path `\\tsclient\C\...` is the *remote* computer's view of **your own** PC's C: drive.
2. **Where do exports land?** Run one saved export and note the exact folder it is saved in. Open that folder and tell us the exact file names.
3. **Which computer should run the helper?** The helper must run where it can read that folder as a normal path. If Frazer is remote and exports land on your own PC, install the helper on **your own PC** (the folder is local there). If Frazer is local, install it on the Frazer PC.
4. **Are the three saved exports right?** In Frazer's export list, are there three saved presets (vehicles, customer balances, transactions)? Do they keep the same columns every time? Does the transaction preset ask for a date range when it runs?
5. **Is there an automatic way to run an export?** Look in Frazer's export screen/menus and manual for "schedule", "automatic export", "command line" or "batch". If nothing exists, the helper keeps the "operator runs it, helper waits" mode, which already works.
