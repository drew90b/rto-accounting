# Integration: vehicle reconciliation with Frazer intake and Monthly Review

Branch `integration/vehicle-recon-stack` (worktree `/home/drew/projects/rto-integration`), from `main` `1b54dd3`. **Local only. Not merged, pushed or deployed.**

## The base, and why it is a snapshot
Nothing from the other sessions is committed. Opening Snapshot (010), Frazer intake (011) and Monthly Review (012-013) all live as uncommitted files in the `rto-monthly-review` working tree. Commit `393f485` copies that tree byte-for-byte onto main (manifest with SHA-256 of every file: `docs/INTEGRATION_BASE.md`). No other worktree, branch or database was touched. If the owners later commit differently, **re-do the snapshot or rebase onto their commits**; compare against the manifest to see what drifted. Baseline of the snapshot alone: 523 passed, 4 skipped.

## What the integration adds on top (three commits)
1. Merge of `feature/vehicle-reconciliation` (commit `d78374c`): conflicts in `app/main.py`, `app/models/__init__.py`, `app/templates/base.html`, `app/static/css/style.css`, all additive. The employee nav link for vehicle questions was dropped: Belinda reaches them through My actions.
2. Migration: `vr01` now has `down_revision = "013"`. Chain 009 > 010 > 011 > 012 > 013 > vr01, one head, upgrade/downgrade verified on PostgreSQL. It claims no number.
3. Connections: `app/services/vr_intake_bridge.py` (Frazer uploads -> reconciliation), `my_actions` route/template (vehicle questions group), identity layers on the detail page, tests, demo seeder.

## Parser
`app/services/frazer_parse.py` is identical in both branches (the merge produced no conflict; same SHA-256 as the Frazer worktree). Reconciliation reuses it through intake's stored rows and does not parse a second time.

## Files of the other sessions that were NOT edited
`app/routes/frazer_intake.py`, all `frazer_*`, `monthly_review*`, `opening_*` services, models, migrations and templates are byte-identical to the snapshot. **Two shared files are edited**: `app/routes/my_actions.py` (+3 lines) and `app/templates/my_actions/index.html` (a "Vehicle questions" group), both marked.

## Decisions this integration surfaces
- Intake pull vs push (DEC-VR-08): pull, so REQ-FZ-06 holds.
- `vr_*` tables are not covered by intake's "writes only to Frazer tables" test, because intake never writes them.
- Opening Snapshot's `test_ac_os_12` is time-of-day fragile (reported by its owner); it passed in these runs.
