# RTO Accounting System

Internal operating and finance system for a rent-to-own vehicle and golf cart dealership.

---

## What this is

A web-based system of record for daily business operations and monthly financial review. It tracks inventory, repairs, sales, RTO/lease accounts, collections, and receipts in one place — and connects all financial activity to the units and customers it belongs to.

This is not a general ledger or accounting software. It is the operational layer that feeds clean, organized data to an accountant.

---

## Who it's for

| Role | Primary use |
|---|---|
| Owner / operator | Dashboard, deal review, exception management, monthly review |
| Office administrator | Daily data entry, receipts, payments, invoice lookup |
| Salesperson | Unit lookup, sale entry, customer setup |
| Site manager | Repair job tracking, inventory status |
| Mechanic | Repair job status updates |
| Accountant | Monthly exports and register review |

---

## What it tracks

- **Inventory** — cars and golf carts, with status, acquisition cost, and full activity history
- **Customers** — buyers and RTO customers with contact info and linked records
- **Vendors** — parts suppliers, auction houses, and service vendors
- **Repair jobs** — internal recon, customer-paid repairs, and BHPH support repairs
- **Sales** — car and golf cart outright sales with payments and printable receipts
- **RTO / Lease accounts** — rent-to-own deal setup, payment schedule, and live balance
- **Payments** — individual collection events linked to sales, leases, and repair jobs
- **Transactions** — the full financial event log, classified by business line and revenue stream
- **Invoices** — auto-generated printable invoices for every completed revenue event
- **Documents** — receipts and supporting files linked to any record
- **Exceptions** — open items that need review, resolution, or follow-up

---

## Two business lines. Six revenue streams.

| Business Line | Revenue Streams |
|---|---|
| Cars | Car sales · Car RTO/lease collections · Car repair revenue |
| Golf Carts | Golf cart sales · Golf cart repairs · Golf cart parts sales |

Revenue streams are tracked explicitly throughout the system and never collapsed into a single category.

---

## Quick start

```bash
# 1. Copy environment file and set DATABASE_URL
cp .env.example .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run migrations
alembic upgrade head

# 4. Load sample data
python scripts/seed.py

# 5. Start the server
uvicorn app.main:app --reload
```

Open `http://localhost:8000` in a browser.

Sign-in: set `SESSION_SECRET` and `ALLOWED_ORIGINS` in `.env` (see `.env.example`), then create the first administrator. The app will not start without a secret.

```bash
python -m app.scripts.users create --username drew --display-name "Drew" --role admin --temporary
```

`--temporary` prints a one-time password to change at first sign-in. There is no signup page; see `docs/system_workflows.md` (11b) for managing accounts.

---

## Running as a service (ubuntu-dev)

`deploy/rto-web.service` runs the app under systemd so it starts after a reboot and restarts after a crash. It uses the same checkout, `.venv`, `.env` and `storage/receipts` as the tmux setup, binds only to the Tailscale address `100.123.116.111:8000`, and waits up to two minutes at boot for that address to exist. Only one process may use port 8000, so stop the tmux app before starting the service.

Install and switch over (once):

```
sudo install -m 644 /home/drew/projects/rto/deploy/rto-web.service /etc/systemd/system/rto-web.service
sudo systemctl daemon-reload
sudo systemctl enable rto-web            # starts at boot; does not start it now
tmux kill-window -t rto:web              # stop the tmux app first
sudo systemctl start rto-web
```

Verify:

```
systemctl status rto-web --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://100.123.116.111:8000/login        # 200
curl -s -o /dev/null -w "%{http_code}\n" http://100.123.116.111:8000/purchases/   # 303 to /login
sudo kill -9 "$(systemctl show -p MainPID --value rto-web)"; sleep 8; systemctl is-active rto-web   # active again
journalctl -u rto-web -n 30 --no-pager
sudo reboot                               # then repeat the first two checks
```

Day to day: `sudo systemctl restart rto-web` after a deploy, `journalctl -u rto-web -f` for the log. Back up first, as before.

Roll back to tmux:

```
sudo systemctl disable --now rto-web
sudo rm /etc/systemd/system/rto-web.service && sudo systemctl daemon-reload
tmux new-window -d -t rto -n web -c /home/drew/projects/rto "source .venv/bin/activate && uvicorn app.main:app --host 100.123.116.111 --port 8000"
```

## Outage diagnostics

`deploy/rto-netcheck.py` is a small read-only logger, separate from the app, for chasing intermittent interruptions. It restarts and changes nothing. Every 30 seconds it records: the app's `/login` (HTTP status and time, on the address uvicorn is actually bound to), reachability of the default gateway, internet by IP (1.1.1.1, then 8.8.8.8) and by DNS (a random name under example.com via the system resolver), Tailscale state, and the `rto-web` unit (state, restart counter) with system uptime. Every probe has a timeout of at most 6 seconds. It stores statuses, timings, addresses and error class names only: no cookies, headers, response bodies or business records. Each probe is a plain `GET /login`, so it adds one line per sample to the `rto-web` journal.

Files: `deploy/rto-netcheck.py`, `rto-netcheck.service`, `rto-netcheck.logrotate`, tests in `tests/test_netcheck.py`. Logs are in `/var/log/rto-netcheck/` (they survive reboots): `samples.jsonl` (one line per sample, about 1.5 MB a day) and `events.jsonl` (start/stop, each check failing or recovering with its duration, and when an outage ends, which checks were involved). Logrotate keeps 14 days, compressed.

Install (once):

```
sudo install -m 644 /home/drew/projects/rto/deploy/rto-netcheck.service /etc/systemd/system/rto-netcheck.service
sudo install -m 644 /home/drew/projects/rto/deploy/rto-netcheck.logrotate /etc/logrotate.d/rto-netcheck
sudo systemctl daemon-reload
sudo systemctl enable --now rto-netcheck
systemctl status rto-netcheck --no-pager
```

View (no sudo needed):

```
/home/drew/projects/rto/deploy/rto-netcheck.py report            # last 24 h, times in America/Chicago
/home/drew/projects/rto/deploy/rto-netcheck.py report --hours 6 --utc
tail -n 5 /var/log/rto-netcheck/events.jsonl
```

The report lists each outage (start, end, duration, which checks failed, and which failed together), periods with no samples, rto-web restarts and unit messages, DHCP/carrier events from systemd-networkd, and a closing split of what was observed from what is only a possible explanation. Times and durations are accurate to one 30-second sample, and a single failed sample is labelled a blip, not an outage.

Limits: the logger runs on this server, so it cannot see this server losing power. That shows up only as a gap with no samples and no clean-stop record, which looks the same as a frozen VM, a hung host or a stopped service. It also cannot prove the app is reachable from another device: the `/login` check goes to a local socket, so it passes even when the extender, router or Tailscale path to the outside is down. It cannot see the Proxmox host, the extender or the router.

Smallest independent check, from the Windows laptop (PowerShell), while it is on Tailscale and not on this server's network:

```
while ($true) { $t = Get-Date -Format s; try { $r = Invoke-WebRequest http://100.123.116.111:8000/login -UseBasicParsing -TimeoutSec 5; "$t $($r.StatusCode)" } catch { "$t FAIL" }; Start-Sleep 30 }
```

Line it up with `rto-netcheck.py report`. FAIL on the laptop while the server logged all checks passing suggests the problem is somewhere between them, or on the laptop's side. FAIL on both at the same time suggests a shared or server-side problem. Neither proves the cause; it narrows where to look next. A Tailscale device showing offline says only that it is not currently connected, not why it disconnected (sleep, no network, Tailscale stopped and a lost route are all possible).

Remove:

```
sudo systemctl disable --now rto-netcheck
sudo rm /etc/systemd/system/rto-netcheck.service /etc/logrotate.d/rto-netcheck && sudo systemctl daemon-reload
sudo rm -r /var/log/rto-netcheck        # optional: deletes the collected logs
```

## Backups

A systemd timer backs up the live app every night at 02:30 America/Chicago (`Persistent=true`, so a run missed while the machine was off happens at the next start). Files: `deploy/rto-backup.sh`, `rto-backup-verify.sh`, `rto-backup-prune.py`, `rto-backup.service`, `rto-backup.timer`.

Each run writes `/home/drew/rto-backups/auto/rto_YYYYMMDD_HHMMSS/` (UTC; folders mode 700, files 600) with `db.dump` (pg_dump custom format), `receipts.tar.gz`, `env` (a copy of `.env`; it holds secrets, so treat the folder as sensitive), `manifest.txt` (git commit, alembic revision, row counts, package list) and `SHA256SUMS`. The database is dumped before the receipts are archived, so every document row in the dump has its file in the archive; a receipt uploaded during the run may be in the archive without a row, which is harmless. The run then restores the dump into a throwaway PostgreSQL cluster (unix socket only) and a temporary receipts folder, and fails if checksums, revision, row counts or receipt files do not match. It never touches the live database or receipts.

Retention applies only to `rto_*` folders under `auto/`: all runs from the last 14 days, the oldest run of each of the last 8 weeks, the oldest of each of the last 6 months, and always the newest. Other files in `~/rto-backups` (the manual pre-deployment dumps) are never touched. A second run cannot overlap the first (`flock`). A failed run exits non-zero, so systemd marks the unit failed and the reason is in the journal; nothing partial is kept.

Install (once):

```
sudo install -m 644 /home/drew/projects/rto/deploy/rto-backup.service /home/drew/projects/rto/deploy/rto-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rto-backup.timer
```

Check:

```
systemctl list-timers rto-backup.timer --no-pager      # next run
systemctl status rto-backup.service --no-pager         # last run: "Succeeded" or "failed"
journalctl -u rto-backup.service -n 20 --no-pager
ls -l ~/rto-backups/auto
sudo systemctl start rto-backup.service                # run one now
```

Restore (to a scratch place first; never over the live data without a fresh backup of it):

```
cd ~/rto-backups/auto/rto_YYYYMMDD_HHMMSS && sha256sum -c SHA256SUMS
createdb -h 127.0.0.1 -U postgres rto_restore_test && pg_restore -h 127.0.0.1 -U postgres -d rto_restore_test --no-owner --no-privileges db.dump
mkdir /tmp/receipts_restore && tar -xzf receipts.tar.gz -C /tmp/receipts_restore
```
To recover the real system, restore `db.dump` into `rto_accounting_dev` (`pg_restore --clean --if-exists --no-owner`), extract `receipts/` into `storage/`, put `env` back as `.env`, check out the `git_commit` in `manifest.txt`, and restart `rto-web`.

Off-server copy: after each successful local backup, `rto-backup-offsite.service` encrypts it to a GPG public key (the private key is not on this server) and copies it to a private Backblaze B2 bucket with `rclone copy`; nothing is ever pruned. Setup and the plan are below, and the recovery runbook is [`docs/backup_recovery.md`](docs/backup_recovery.md). Retention differs: locally 14 days of runs plus weekly and monthly ones (about six months), in the cloud everything is kept, so the cloud currently holds every upload. Limits: the upload key can delete files in the bucket; only the newest run is uploaded each night, with no backfill of a missed one; there is no proactive alert on failure (it only shows in systemd and the journal); two verified copies of the private key exist but a geographically separate recovery location is pending. The first unattended 02:30 run had not happened yet when this was written. These are **application and data backups, not a Proxmox-host or VM-image backup**; the local copies share the live machine's disk, and the encrypted B2 copy is what survives losing the machine. A server move has a before/after checklist in the runbook (sections 12 and 13). **Known gap:** document D-0001 (purchase 2, uploaded 2026-09-13) points to a file on the original Windows machine that was never copied to this server, so no backup contains it and it is **not protected**. The verification prints a note about it each run and does not fail. If the original file can be found (for example in the old project's `storage/receipts/purchase/2/image3.jpg`), copy it into `storage/receipts/purchase/2/` and update the row deliberately; until then treat that attachment as missing.

### Off-server copy: Backblaze B2 setup (done 2026-10-25; kept as the reference for rebuilding it)

The canonical key and recovery runbook, with current status, is [`docs/backup_recovery.md`](docs/backup_recovery.md). Read it before enabling uploads.

Nothing below has been bought, installed or configured. It protects against losing this machine, its disk or its host. Nightly cost should be small (each run is about 13 MB); check Backblaze's current pricing and free tier before relying on that.

The design: the server can encrypt but cannot decrypt. It holds only a GPG **public** key. The **private** key lives off the server, so someone who gets into the server (or the bucket) cannot read the backups, and losing the server does not lose the ability to restore.

1. **Backblaze:** create a B2 account (turn on two-factor sign-in) and a **private** bucket, for example `rto-backups-<something-unique>`. Do **not** add a lifecycle (deletion) rule: initial off-server retention keeps everything. Create an **application key restricted to that one bucket**. Backblaze's form only offers Read and Write, Read Only or Write Only, so a Read and Write key *can delete* files; the upload job therefore uses `rclone copy` only and nothing schedules a deletion. Initial retention is to keep every upload with no automatic deletion, so the bucket grows; revisit pruning later (`docs/backup_recovery.md`). Save the key ID and key in your password manager (the key is shown once).
2. **Make the encryption key on your own computer, not on this server** (for example `drewhome`): `gpg --quick-generate-key "RTO backups" rsa4096 encr never` and note the key ID. Export the public half (`gpg --armor --export KEYID > rto-backup-public.asc`) and copy that file to this server. Export the private half (`gpg --armor --export-secret-keys KEYID`) and store it **in two separate places** that are not this server, for example your password manager and a printed or USB copy kept somewhere else, with its passphrase stored separately from the key. If every copy of the private key is lost, every off-server backup becomes unreadable; the server cannot help.
3. **On the server (needs your approval):** `sudo apt install rclone`, `rclone config` (type `b2`, the restricted key; the config file is mode 600 and holds the B2 key, which can write but not delete), and `gpg --import rto-backup-public.asc`. Then `gpg --list-secret-keys` must print nothing: there must be no private key on the server.
4. **What the job will do** (`deploy/rto-backup-offsite.sh`, to be written once you approve): after each local backup, `tar` the new `rto_*` folder, encrypt it to the public key (`gpg --encrypt --recipient KEYID --trust-model always`), upload it with `rclone copy` to `b2:<bucket>/rto/`, then read back its size and checksum from B2. Failure makes the unit fail (visible in `systemctl status` and the journal). The `env` file inside the backup holds secrets, which is why nothing is uploaded unencrypted.
5. **Restore drill (do it once before relying on it):** on your own computer, download the newest `.tar.gpg` from B2, decrypt it with the private key, unpack, and run `sha256sum -c SHA256SUMS` and the restore steps above (or `deploy/rto-backup-verify.sh <folder>` on any machine with PostgreSQL). Record the date of the drill in `docs/progress.md`.

You need to choose or provide: the B2 account and bucket name, the application key (never paste it into chat; it goes straight into `rclone config` on the server), the key ID of the GPG public key, and approval to install `rclone`.

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/CLAUDE.md`](docs/CLAUDE.md) | Governing spec — business model, rules, and AI coding guidelines |
| [`docs/owner_demo_workflow.md`](docs/owner_demo_workflow.md) | Step-by-step walkthrough for owners and onboarding |
| [`docs/financial_record_architecture.md`](docs/financial_record_architecture.md) | Payment vs Transaction vs Invoice vs Lease Balance — explained |
| [`docs/unit_economics_vision.md`](docs/unit_economics_vision.md) | Future vision for per-unit profitability reporting |
| [`docs/business_rules.md`](docs/business_rules.md) | All business logic rules by module |
| [`docs/system_workflows.md`](docs/system_workflows.md) | 13 step-by-step operational workflows |
| [`docs/data_model.md`](docs/data_model.md) | Database schema — all tables, columns, and relationships |
| [`docs/architecture.md`](docs/architecture.md) | Technical architecture, service layer, and test design |
| [`docs/roadmap.md`](docs/roadmap.md) | Development phases and current status |
| [`docs/progress.md`](docs/progress.md) | Development log — what was built and when |
| [`docs/backup_recovery.md`](docs/backup_recovery.md) | Backup encryption keys, recovery copies, and how to recover from an off-server backup |

---

## Current status

MVP complete and operational as of March 2026; the running instance is on ubuntu-dev, reached over Tailscale. Individual sign-in with admin and employee roles has been live since 2026-10-24, and systemd keeps the app running (restart after a crash, start after a reboot). Remaining before wider use: Belinda's office-computer setup, HTTPS through Tailscale Serve, a geographically separate recovery location for the backup encryption key, and a failed-backup alert. Nightly local backups and encrypted copies to Backblaze B2 are enabled (the cloud path was restore-tested on 2026-10-25; the first unattended nightly run is still to be confirmed). Immediate business milestones, not yet done: November purchase capture, then closing November's books in December. Phase 1.5 (workflow hardening) is in progress. Phase 2 (reporting and exports) is next.

See [`docs/roadmap.md`](docs/roadmap.md) for what is built, in progress, and planned.
