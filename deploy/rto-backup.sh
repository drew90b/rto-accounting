#!/bin/bash
# Nightly backup of the live RTO database, receipts and recovery configuration.
# Run by rto-backup.service (see README "Backups"). Safe to run by hand.
#
# Each run writes ROOT/rto_YYYYMMDD_HHMMSS/ (UTC) containing:
#   db.dump           pg_dump custom format
#   receipts.tar.gz   storage/receipts
#   env               copy of .env (contains secrets; directory is mode 700)
#   manifest.txt      git commit, alembic revision, row counts, receipt count
#   SHA256SUMS
# then restores it into a throwaway database (rto-backup-verify.sh) and prunes old
# runs (rto-backup-prune.py). Only rto_* directories under ROOT are ever deleted.
#
# Order matters: the database is dumped BEFORE the receipts are archived. A receipt
# uploaded in between is in the archive without a database row (harmless); the
# reverse, a row without its file, cannot happen because receipt files are never
# changed or deleted once written.
set -euo pipefail
umask 077

APP=${RTO_APP_DIR:-/home/drew/projects/rto}
ROOT=${RTO_BACKUP_ROOT:-/home/drew/rto-backups/auto}
ENV_FILE=${RTO_ENV_FILE:-$APP/.env}
HERE=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$ROOT"
chmod 700 "$ROOT"
exec 9>"$ROOT/.lock"
flock -n 9 || { echo "rto-backup: another backup is already running" >&2; exit 1; }

# Database connection settings come from .env and are exported as PG* variables,
# so the password never appears on a command line or in output.
pgenv=$(python3 - "$ENV_FILE" <<'PY'
import shlex, sys
from urllib.parse import unquote, urlparse
url = ""
for line in open(sys.argv[1]):
    if line.startswith("DATABASE_URL="):
        url = line.split("=", 1)[1].strip().strip("'\"")
if not url:
    sys.exit("DATABASE_URL not found in " + sys.argv[1])
u = urlparse(url.replace("postgresql+psycopg2", "postgresql", 1))
for k, v in (("PGHOST", u.hostname or "localhost"), ("PGPORT", str(u.port or 5432)),
             ("PGUSER", unquote(u.username or "")), ("PGPASSWORD", unquote(u.password or "")),
             ("PGDATABASE", u.path.lstrip("/"))):
    print("export %s=%s" % (k, shlex.quote(v)))
PY
)
eval "$pgenv"

free_mb=$(df -Pm "$ROOT" | awk 'NR==2 {print $4}')
[ "$free_mb" -ge 1024 ] || { echo "rto-backup: only ${free_mb} MB free in $ROOT" >&2; exit 1; }

stamp=$(date -u +%Y%m%d_%H%M%S)
work="$ROOT/.partial_$stamp"
final="$ROOT/rto_$stamp"
trap 'rm -rf "$work"' EXIT
mkdir "$work"

counts() {
  psql -Atq -c "select (select count(*) from purchases), (select count(*) from transactions), (select count(*) from units), (select count(*) from documents)"
}
counts_before=$(counts)

pg_dump -Fc -f "$work/db.dump"

# tar exits 1 when a file changed or appeared while it ran (an upload); that is expected.
tar -czf "$work/receipts.tar.gz" -C "$APP/storage" receipts || [ $? -eq 1 ]

counts_after=$(counts)
cp "$ENV_FILE" "$work/env"
{
  echo "created_utc=$stamp"
  echo "git_commit=$(git -C "$APP" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "alembic_revision=$(psql -Atq -c 'select version_num from alembic_version')"
  echo "postgres_server=$(psql -Atq -c 'show server_version')"
  echo "counts_before=$counts_before"
  echo "counts_after=$counts_after"
  echo "counts_order=purchases,transactions,units,documents"
  echo "receipt_files_in_archive=$(tar -tzf "$work/receipts.tar.gz" | grep -vc '/$' || true)"
  echo "python_packages:"
  "$APP/.venv/bin/pip" freeze 2>/dev/null | sed 's/^/  /'
} > "$work/manifest.txt"
(cd "$work" && sha256sum db.dump receipts.tar.gz env manifest.txt > SHA256SUMS)

"$HERE/rto-backup-verify.sh" "$work"

mv "$work" "$final"
trap - EXIT
size=$(du -sh "$final" | cut -f1)
echo "rto-backup: OK $final ($size)"
"$HERE/rto-backup-prune.py" "$ROOT"
