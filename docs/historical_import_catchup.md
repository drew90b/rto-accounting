# Catch-up imports of the Google Form purchase register

The one-time historical importer (`app/scripts/import_historical_purchases.py`) can also load rows
that were missing from an earlier run. This note records the rules; it holds no purchase data.

## Identity
`source_reference = historical-import:<sheet row>`, where the sheet row is the spreadsheet row number with the
header as row 1. A catch-up batch uses the same scheme (no new prefix, no renumbering), so a re-run, or an
overlap with an earlier import, is skipped by the unique `source_reference` check.

## What differs from the original import
| | Original import | Catch-up batch |
|---|---|---|
| `entered_by` | `Historical Import` | `--entered-by "Historical Import (catch-up YYYY-MM-DD)"` |
| Review status | Reviewed (the old importer forced it) | The CSV's `review_status` column, **required**; catch-up rows are `pending` |
| Payment source | NULL, never flagged | CSV `payment_source`; the app's wording for unknown is `Unknown - needs review` |
| Review queue | Exempt from the missing-source / missing-receipt flags | Flagged like any other purchase |
| New vehicles | Created for any AUCTION row | `--no-new-vehicles` fails a row that would create a unit |

Only purchases with `source_reference` set **and** `entered_by = "Historical Import"` are exempt from the
review-queue flags (`purchase_service.is_original_historical_import`).

## Rules for preparing a batch
- Include only rows that are missing from the app, have a numeric amount that agrees with the sheet's cleaned
  value, a category the app knows, and no overlap with a purchase entered by hand. Hold everything else.
- Matching date, vendor and amount alone is not proof of a duplicate; look for notes text or a real stock tag.
- Never sum or pick between conflicting amounts; hold the row and ask.
- Keep stock identifiers exactly as written (leading zeros, letter prefixes). Do not invent VINs or link a
  parts row to a vehicle by stock tag.
- AUCTION-labelled rows can be parts; they create vehicles, so hold them for classification.
- Receipt links (Google Drive) are not attachments. No importer fetches them; do not claim photos were imported.
- Keep the source workbook, reports and the import CSV outside Git (they contain purchase data and private links).

## Running
Never run `--dry-run` against the live database (it executes the write paths, then rolls back). Rehearse on a
disposable database or a restored backup, confirm the target database name, then run `--commit` once, and check
the result read-only.
