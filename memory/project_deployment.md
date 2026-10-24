---
name: Deployment — ubuntu-dev over Tailscale
description: Where the RTO finance system actually runs; corrects an earlier note that said Render
type: project
---

The running instance is uvicorn in tmux session `rto` (window `web`) on ubuntu-dev, bound to its Tailscale address on port 8000, against `rto_accounting_dev`. It does not survive a reboot until `deploy/rto-web.service` is installed (prepared 2026-10-24, see README "Running as a service"). Reached only over the tailnet.

**Why:** An earlier version of this note said the server was deployed on Render. Nothing in the repository supports that, and the app is run from ubuntu-dev.
**How to apply:** Do not assume a Render environment. Treat restarts, migrations and secrets as manual steps on ubuntu-dev, and back up the database and `storage/receipts` first.
