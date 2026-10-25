#!/bin/bash
# Encrypt one local backup run and COPY it to Backblaze B2. Run after rto-backup.sh.
#   usage: rto-backup-offsite.sh [RUN_DIR]     (default: the newest rto_* run)
#
# The run folder (which includes a copy of .env) is tarred, encrypted to the backup
# PUBLIC key (the private key is not on this machine and cannot decrypt it), and
# uploaded as rto_YYYYMMDD_HHMMSS.tar.gpg. Rules, because the B2 key can delete:
# only `rclone copyto` is used, never sync, move, delete or purge; an existing remote
# file is never overwritten (--immutable); nothing here ever deletes anything.
# Runbook: docs/backup_recovery.md
set -euo pipefail
umask 077

ROOT=${RTO_BACKUP_ROOT:-/home/drew/rto-backups/auto}
REMOTE=${RTO_B2_REMOTE:-b2rto:rto-backups-drew/rto}
KEY=${RTO_BACKUP_GPG_KEY:-288D0C359BDE2DCA218D5F94A251C38DF361E451}

run=${1:-$(ls -d "$ROOT"/rto_[0-9]*_[0-9]* 2>/dev/null | sort | tail -1)}
[ -n "$run" ] && [ -d "$run" ] || { echo "rto-offsite: no local backup found in $ROOT" >&2; exit 1; }
name=$(basename "$run")
[[ $name =~ ^rto_[0-9]{8}_[0-9]{6}$ ]] || { echo "rto-offsite: unexpected folder name $name" >&2; exit 1; }

# The private key must never be on this machine.
if gpg --list-secret-keys --with-colons 2>/dev/null | grep -q '^sec'; then
  echo "rto-offsite: a secret GPG key is present on this machine; refusing to upload" >&2
  exit 1
fi

exec 9>"$ROOT/.offsite.lock"
flock -n 9 || { echo "rto-offsite: another upload is running" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
file="$name.tar.gpg"

if rclone lsf "$REMOTE" --files-only --include "$file" | grep -qx "$file"; then
  echo "rto-offsite: $file is already in the cloud; nothing to do"
  exit 0
fi

tar -C "$ROOT" -cf - "$name" \
  | gpg --batch --yes --trust-model always --encrypt --recipient "$KEY" --output "$tmp/$file"
size=$(stat -c %s "$tmp/$file")

rclone copyto "$tmp/$file" "$REMOTE/$file" --immutable
# Compare size and checksum of what is now in the cloud with the local encrypted file.
rclone check "$tmp" "$REMOTE" --one-way --include "$file" >/dev/null 2>&1 \
  || { echo "rto-offsite: cloud copy of $file does not match the local file" >&2; exit 1; }

echo "rto-offsite: OK uploaded and checked $file ($size bytes)"
