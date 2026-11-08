# Versions and releases

This is the live app's version history. Each version is an **annotated Git tag** on a commit of `main` that is **running on ubuntu-dev**, plus a **GitHub Release** with the same notes. Until `v0.1.0` (2026-11-08) the repository had no version labels.

## How versions are labelled

Format: `vMAJOR.MINOR.PATCH` (for example `v0.1.0`).

| Part | Increase it when | Examples |
|---|---|---|
| **MAJOR** | The owner declares the system ready for routine use by all staff. Stays `0` until then | `v1.0.0` |
| **MINOR** | A deployment adds or changes something users or the books can see: a feature, a workflow, a business rule, a permission, or any **Alembic migration** | Opening Snapshot; a new review flag; schema 010 |
| **PATCH** | A deployment only fixes or tidies: a bug fix, a safe importer or script change, a configuration fix, no new migration, no change to what users can do | A corrected total; a wrong label |

While MAJOR is `0`, a MINOR release may change behaviour in ways that need a step from the operator (a backup, a migration, a `.env` change). The release notes must say so.

Rules:

1. **Tag what is deployed.** Merge to `main`, run the migration if there is one, restart `rto-web`, check it, then tag **that commit**. The tag and the running code must be the same commit (or differ only in documentation).
2. **Never move, re-use or delete a published tag.** If a release is wrong, fix forward with the next version and say so in its notes.
3. **Documentation-only, test-only and exploratory commits are not versions.** They ride along with the next release.
4. **One tag per deployment.** Do not tag a branch that has not been merged and deployed.
5. **The release notes** (tag annotation = GitHub Release body = the section below) say: what changed, what was validated, the deployment status (commit, schema revision, when it started running), any data effect (counts and totals only), and what is still open.
6. **Keep private information out of GitHub.** The repository is public. Never put purchase rows, customer or employee details, receipt links, credentials, reconciliation files or database snapshots in a commit, tag, or release. Aggregate counts and totals are fine.

To cut a release, from `/home/drew/projects/rto` after the deployment is verified:

```
git switch main && git pull --ff-only
git tag -a vX.Y.Z -m "<release notes>"          # annotated; never use -f
git push origin main vX.Y.Z
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file <notes.md>
git describe --tags                              # should print exactly vX.Y.Z on the deployed commit
```

Then add a row to the table and a section below, in the same commit as the next change (not as a separate version).

## Releases

| Version | Date | Schema (Alembic) | Summary |
|---|---|---|---|
| [`v0.1.0`](https://github.com/drew90b/rto-accounting/releases/tag/v0.1.0) | 2026-11-08 | 009 | First labelled release. Google Form purchase catch-up: importer and review-queue changes; 52 purchases imported as Pending |

## v0.1.0 (2026-11-08)

**First version label.** It names the application as deployed on ubuntu-dev on 2026-11-08, including the purchase catch-up below. The application itself (units, customers, purchases and the review queue, repair jobs, sales, lease/RTO, payments, transactions, invoices, documents, exceptions, individual sign-in, the `rto-web` service, nightly backups with an encrypted off-server copy, and the outage diagnostics logger) was built before versions were labelled; see `README.md` and `docs/progress.md`.

### What changed in this release (since the last published commit, `4419611`)

- **The one-time purchase importer can now load later catch-up batches.** It requires a `review_status` column (`pending` or `reviewed`, never assumed; the old importer forced every row to Reviewed), accepts an optional `payment_source` column, and takes `--entered-by` and `--no-new-vehicles` (fails any row that would create a vehicle). Receipt links are no longer printed. Row identity is unchanged: `source_reference = historical-import:<sheet row>`, so duplicate protection holds across batches. **Operator note:** an import file without a `review_status` column is now refused.
- **The review queue now exempts only the original historical import** (a `source_reference` **and** `entered_by = "Historical Import"`, which is all 959 original rows) from the missing-payment-source and missing-receipt flags. Catch-up rows are flagged like any other purchase.
- Documentation: `docs/historical_import_catchup.md` (rules for catch-up batches), this file, and the progress log.
- No migration. Schema stays at revision `009`.

### Data effect (aggregates only)

- **52 purchases totalling $4,626.92** were imported into the live app from the old Google Form register, one purchase and one transaction each. All are **Pending**, with payment source `Unknown - needs review` and no receipt attached. No vehicles and no vendors were created. A second run skipped all 52.
- **59 rows from that register remain unresolved and were not imported** (amounts written as text, parts labelled as vehicle auctions, an unmapped category, vehicle purchases that would create units, and two rows needing correction).
- The four existing purchases whose amounts differ from the corrected sheet were left unchanged. The 959 originally imported purchases are untouched.
- Receipts: the register's Google Drive links were inventoried only. No receipt was downloaded or attached.

### Validation

- Full automated suite: 302 passed, 1 skipped (a PostgreSQL-only test). Three deliberate breakages of the new rules were each caught by a test.
- The importer ran twice on a disposable PostgreSQL database rebuilt from read-only metadata; the second run created nothing.
- Before the live import: a fresh restore-checked backup, and the target database name was confirmed.
- After: a read-only verifier confirmed every imported purchase field-by-field against the import file; purchases 965 to 1,017; vendors, units and documents unchanged; a second live run skipped all 52; the Review Queue showed the 52 with both flags.

### Deployment status

Deployed to ubuntu-dev on 2026-11-08: merged to `main`, `rto-web` restarted at about 13:44 UTC, imported afterwards. The code running since then is identical to this tag; the commits after the restart changed documentation only.

### Still open

Review the 52 Pending purchases (set each real payment source, attach each receipt); decide the 59 held rows; decide whether to build a receipt attachment importer. The Opening Snapshot work is on a branch and is **not** part of this release.
