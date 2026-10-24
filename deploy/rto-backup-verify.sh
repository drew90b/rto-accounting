#!/bin/bash
# Prove a backup directory restores: checksums, then a restore into a throwaway
# PostgreSQL cluster (unix socket only, no TCP port) and a temporary receipts folder.
# Never touches the live database or live receipts.
#   usage: rto-backup-verify.sh BACKUP_DIR
set -euo pipefail
umask 077
dir=$(cd "${1:?usage: rto-backup-verify.sh BACKUP_DIR}" && pwd)
bin=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)

cd "$dir"
sha256sum -c --quiet SHA256SUMS

tmp=$(mktemp -d)
cleanup() { "$bin/pg_ctl" -D "$tmp/pg" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT

"$bin/initdb" -D "$tmp/pg" -U postgres -A trust >/dev/null
"$bin/pg_ctl" -D "$tmp/pg" -w -l "$tmp/pg.log" \
  -o "-c listen_addresses= -c unix_socket_directories=$tmp -c port=55999" start >/dev/null
q() { "$bin/psql" -h "$tmp" -p 55999 -U postgres -d restore_check -Atq "$@"; }
"$bin/createdb" -h "$tmp" -p 55999 -U postgres restore_check
"$bin/pg_restore" -h "$tmp" -p 55999 -U postgres -d restore_check --no-owner --no-privileges --exit-on-error db.dump

mkdir "$tmp/receipts"
tar -xzf receipts.tar.gz -C "$tmp/receipts"

want_rev=$(sed -n 's/^alembic_revision=//p' manifest.txt)
got_rev=$(q -c 'select version_num from alembic_version')
[ "$want_rev" = "$got_rev" ] || { echo "verify: revision $got_rev, manifest says $want_rev" >&2; exit 1; }

# Row counts after restore must lie between the counts taken before and after the dump.
IFS=, read -r b1 b2 b3 b4 <<<"$(sed -n 's/^counts_before=//p' manifest.txt | tr '|' ,)"
IFS=, read -r a1 a2 a3 a4 <<<"$(sed -n 's/^counts_after=//p' manifest.txt | tr '|' ,)"
IFS=, read -r r1 r2 r3 r4 <<<"$(q -F, -c 'select (select count(*) from purchases), (select count(*) from transactions), (select count(*) from units), (select count(*) from documents)')"
for n in 1 2 3 4; do
  eval "lo=\$b$n hi=\$a$n got=\$r$n"
  { [ "$got" -ge "$lo" ] && [ "$got" -le "$hi" ]; } || { echo "verify: table $n has $got rows, expected $lo..$hi" >&2; exit 1; }
done

# Every document row whose file exists on this server must also be in the archive.
# A row whose file is missing from the live folder too (D-0001 was uploaded on the
# original Windows machine and never copied here) cannot be backed up: warn, don't fail.
APP=${RTO_APP_DIR:-/home/drew/projects/rto}
missing=0 warned=0
while IFS= read -r p; do
  rel=${p#*storage/receipts/}
  [ -f "$tmp/receipts/receipts/$rel" ] && continue
  if [ -f "$APP/storage/receipts/$rel" ]; then
    echo "verify: live receipt file not in the archive: $rel" >&2; missing=1
  else
    warned=$((warned + 1))
  fi
done < <(q -c 'select file_path from documents')
[ "$missing" -eq 0 ] || exit 1
[ "$warned" -eq 0 ] || echo "verify: note: $warned document row(s) point to a file that is not on this server (already missing before the backup)"

echo "verify: OK revision=$got_rev purchases=$r1 transactions=$r2 units=$r3 documents=$r4"
