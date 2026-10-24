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

---

## Current status

MVP complete and operational as of March 2026; the running instance is on ubuntu-dev, reached over Tailscale. Individual sign-in and employee permissions are built on `feature/employee-access` and not yet deployed. Phase 1.5 (workflow hardening) is in progress. Phase 2 (reporting and exports) is next.

See [`docs/roadmap.md`](docs/roadmap.md) for what is built, in progress, and planned.
