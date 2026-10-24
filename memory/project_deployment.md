---
name: Deployment — ubuntu-dev over Tailscale
description: Where the RTO finance system actually runs; corrects an earlier note that said Render
type: project
---

The running instance is the systemd service `rto-web` (since 2026-10-24) on ubuntu-dev, bound to its Tailscale address on port 8000, against `rto_accounting_dev`. It no longer depends on tmux. Restart with `sudo systemctl restart rto-web`; unit file is `deploy/rto-web.service` (README "Running as a service"). Crash-restart and reboot tests passed on 2026-10-24. Employee sign-in (migration 009, admin and employee accounts) has been live since the same day; the app is plain HTTP over Tailscale only. Still pending: Belinda's office-computer onboarding, HTTPS through Tailscale Serve, and scheduled plus off-server backups (backups are manual). Reached only over the tailnet.

**Why:** An earlier version of this note said the server was deployed on Render. Nothing in the repository supports that, and the app is run from ubuntu-dev.
**How to apply:** Do not assume a Render environment. Treat restarts, migrations and secrets as manual steps on ubuntu-dev, and back up the database and `storage/receipts` first.
