# Backup Encryption and Recovery Runbook

The one place to read when something has gone wrong, or before trusting the off-server backups. The nightly local backup job is described in the README ("Backups"). This file covers the encrypted off-server copy and how to recover from it.

**Never put in Git, chat or this file:** private keys, key passphrases, B2 application keys, `.env` contents, recovery codes. Public keys and fingerprints are not secret.

---

## 1. Status

| Item | State |
|---|---|
| Nightly local backups (database, receipts, `.env`, manifest, checksums) on ubuntu-dev, restore-verified every run | **Active** (README "Backups") |
| Backblaze B2 account and private bucket `rto-backups-drew` (Object Lock off, no lifecycle rule) | **Created.** Initial retention decision (owner, 2026-10-24): keep every uploaded backup, no automatic deletion; the bucket will grow; revisit pruning later. See section 7. |
| Encryption key "RTO Backup Recovery" created on the owner's Windows laptop | **Done** (fingerprint below) |
| Private key exported and copied to USB copy 1; SHA-256 of the copy matched | **Verified** |
| Isolated decrypt test: the USB copy decrypted a file encrypted with only the public key; fingerprint matched | **Verified** |
| Key recovery copies | **Two copies exist and both were verified:** USB copy 1 (SHA-256 matched the export; decrypted a test file in an isolated GPG home) and the copy on the owner's laptop (the export file the USB copy was hashed against, and the key in the laptop's GPG keyring, which decrypted the cloud backup on 2026-10-25). **PENDING:** a second, *geographically separate* recovery location, so the key survives losing the laptop, the USB stick and the server's site together. Not a prerequisite for uploads; the owner authorized them on this basis. |
| Passphrase storage | **Recorded** by the owner on paper (2026-10-24); where is deliberately not written here. A second, separate record is advisable and is part of the pending recovery-location item. |
| Public key imported on ubuntu-dev | **Done 2026-10-24.** Primary fingerprint matched; `gpg --list-secret-keys` there lists 0 keys; a test file encrypted on the server could not be decrypted there. |
| `rclone` (1.60.1) installed on ubuntu-dev; remote `b2rto` configured with a B2 application key limited to the bucket | **Done 2026-10-24.** Listing the bucket works (it is empty); any other bucket name is refused. The config file is mode 600. **The key is Read and Write and therefore *can delete* files in the bucket** (section 7). |
| `deploy/rto-backup-offsite.sh` and `rto-backup-offsite.service` (encrypt, upload with `rclone copyto`, verify the checksum) | **Done and ENABLED 2026-10-25.** `rto-backup.service` starts it after each successful, restore-verified local backup (`OnSuccess=`). Uploads only the newest run each night; a night whose upload fails is not backfilled by the next one. |
| Trial encrypted upload, download, laptop decryption, isolated database and receipt restore (section 8) | **PASSED 2026-10-25** for the 2026-10-24 13:37 UTC backup: uploaded (12,967,710 bytes), downloaded by browser, size and SHA-1 matched B2's, decrypted on the laptop with the USB key, all four SHA256SUMS entries OK, then restored on ubuntu-dev into a throwaway cluster: revision 009, 963 purchases, 967 transactions, 157 units, 2 documents, 44 receipt files (D-0001 noted as missing). The live database and app were not touched. |
| First automatic upload | **Verified 2026-10-25:** starting `rto-backup.service` by hand made `rto_20261025_005138.tar.gpg` upload itself (12,967,711 bytes) and pass the script's SHA-1 comparison with B2. That backup was not downloaded or restored. |
| First unattended nightly run (02:30 America/Chicago timer) | **Not yet occurred** as of 2026-10-25 00:56 UTC (19:56 CDT 2026-10-24): `rto-backup.timer` had not fired (`LastTrigger` empty, no timer-started run in the journal); next elapse 2026-10-25 07:30 UTC. Check the journal after it and record the result. |
| Document D-0001 attachment | **Known missing** (section 9) |

The local backups are on the same machine and disk as the live data. The encrypted B2 copy is the off-server protection. Both are **application and data backups only**, not a Proxmox-host or VM-image backup (section 13).

## 2. Keys: what each one is for

- **Public key** (`rto-public-key.asc`): lets someone *encrypt* to us. It goes on ubuntu-dev. It cannot decrypt anything, so a stolen server or bucket reveals nothing.
- **Private key** (`rto-private-key.asc`, protected by a passphrase): the only thing that can *decrypt* the off-server backups. It must never be on ubuntu-dev or in the bucket. Lose every copy of it and every off-server backup is unreadable for good.
- **Passphrase:** unlocks the private key file. Keep it on its own record, separate from the key files. A key copy without the passphrase is useless, and the reverse.
- **Fingerprint** of the key (identifies it; safe to share):

```
288D 0C35 9BDE 2DCA 218D  5F94 A251 C38D F361 E451
```
(written as one string: `288D0C359BDE2DCA218D5F94A251C38DF361E451`)

## 3. Recovery copies (described, not exposed)

| Copy | Where | State |
|---|---|---|
| Private key, USB copy 1 | A USB stick kept apart from the laptop and from ubuntu-dev | Hash-verified and decrypt-tested |
| Private key, laptop (`C:\Users\drewb\RTO-key-recovery\` export, and the key in the laptop's GPG keyring) | The owner's laptop | Verified: the keyring key decrypted the cloud backup; the export file is the one the USB copy was hashed against. Retained on purpose; do not delete it until a geographically separate copy is verified. |
| Private key, geographically separate location | A place still reachable if the laptop, the USB stick and the server's site are all lost | **PENDING.** Not chosen. |
| Passphrase | The owner's own paper record (location not documented here) | Recorded; a second, separate record is pending |
| Recovery note (fingerprint, bucket name, where copies are kept; no secrets) | Kept with the passphrase record | Recommended |

## 4. Verify the key fingerprint

The fingerprint must be identical everywhere the key is used. Compare it character for character with section 2.

**Windows PowerShell:**
```
gpg --fingerprint "RTO Backup Recovery"
```

**Ubuntu (ubuntu-dev) after the public key is imported:**
```
gpg --fingerprint 288D0C359BDE2DCA218D5F94A251C38DF361E451
```

If the fingerprints differ, stop: it is a different key.

## 5. What was verified, and how to repeat it

**USB hash check (Windows PowerShell).** The hash of the key file on the USB stick matched the hash of the original export:
```
(Get-FileHash "C:\Users\drewb\RTO-key-recovery\rto-private-key.asc" -Algorithm SHA256).Hash
(Get-FileHash "D:\RTO-key-recovery\rto-private-key.asc" -Algorithm SHA256).Hash
```
Repeat this for copy 2 (replace the path with that copy's drive); all hashes must be identical.

**Isolated decryption test (Windows PowerShell).** Act as the server (public key only), then as a disaster recovery (private key from the USB copy), in a throwaway GPG home that does not touch the normal keyring. Passed with the USB copy at `D:\RTO-key-recovery\rto-private-key.asc`; the decrypted text was correct and the fingerprint matched.
```
$t = Join-Path $env:TEMP "gpgtest"; New-Item -ItemType Directory $t | Out-Null
$fpr = "288D0C359BDE2DCA218D5F94A251C38DF361E451"
gpg --homedir $t --import "C:\Users\drewb\RTO-key-recovery\rto-public-key.asc"
"restore test" | Out-File "$t\test.txt" -Encoding ascii
gpg --homedir $t --trust-model always --encrypt --recipient $fpr --output "$t\test.gpg" "$t\test.txt"
gpg --homedir $t --import "D:\RTO-key-recovery\rto-private-key.asc"     # asks for the passphrase
gpg --homedir $t --decrypt "$t\test.gpg"                                # prints: restore test
gpg --homedir $t --fingerprint $fpr
gpgconf --homedir $t --kill gpg-agent
Remove-Item -Recurse -Force $t
```
Type the passphrase only at the prompt. Run the same test against copy 2 before relying on it.

## 6. Put the public key on ubuntu-dev (done 2026-10-24; kept for rebuilding the server)

**Windows PowerShell** (copy only the public file; confirm the name says `public`):
```
scp "C:\Users\drewb\RTO-key-recovery\rto-public-key.asc" drew@ubuntu-dev:~/rto-public-key.asc
```
**Ubuntu:**
```
gpg --import ~/rto-public-key.asc
gpg --fingerprint 288D0C359BDE2DCA218D5F94A251C38DF361E451     # compare with section 2
gpg --list-secret-keys                                         # must print nothing
rm ~/rto-public-key.asc
```
If `--list-secret-keys` shows anything, remove it (`gpg --delete-secret-keys <fingerprint>`) and find out how it got there.

## 7. Backup contents and off-server layout

Each nightly run is a folder `rto_YYYYMMDD_HHMMSS/` (UTC) holding `db.dump`, `receipts.tar.gz`, `env` (a copy of `.env`, which is why anything off-server is encrypted first), `manifest.txt` and `SHA256SUMS`. The off-server job (`deploy/rto-backup-offsite.sh`) tars the new folder, encrypts it to the public key, and uploads `rto_YYYYMMDD_HHMMSS.tar.gpg` to `b2:rto-backups-drew/rto/`. **Retention, local versus cloud.** Locally, `rto-backup-prune.py` keeps all runs from the last 14 days, the oldest run of each of the last 8 weeks and each of the last 6 months, and the newest; it only touches `rto_*` folders under `~/rto-backups/auto`. In the cloud, retention is **keep everything**: no lifecycle rule, no pruning, no automatic deletion, so the cloud currently holds every upload even after the local copy has been pruned. Storage will grow by about one encrypted backup per night (about 13 MB each at today's size, more as receipts accumulate); review the cost in Backblaze periodically and revisit pruning later, as a deliberate decision that keeps at least the six monthly backups the local policy keeps.

**Delete access, stated plainly.** Backblaze's key form offers only Read and Write, Read Only or Write Only. The key `rto-ubuntu-dev-upload` is limited to the one bucket but is **Read and Write, which includes deleting files**. Anyone who gets onto ubuntu-dev (the key is in `~/.config/rclone/rclone.conf`, mode 600) could delete or overwrite the cloud backups. Rules that follow from this: the upload job uses `rclone copy` only, never `sync`, `move`, `delete` or `purge`; `hard_delete` stays off; nothing schedules a deletion; each upload has a new unique name so nothing is overwritten. Possible later hardening, not done: a Write Only key for the nightly job (check that it can still list and copy), keeping the Read key only on the laptop, or Object Lock. Setup steps: README "Off-server copy: Backblaze B2 setup".

## 8. Recovering on a replacement computer (the cloud path was tested once, 2026-10-25, from the laptop; a full replacement-machine rebuild has not been rehearsed)

Assume ubuntu-dev is gone. You need: the private key file (USB copy 1 or 2), its passphrase, the B2 login (for the web download) or a B2 key, and GitHub access to the repository. Use Ubuntu with PostgreSQL 18 installed.

**Ubuntu, replacement machine.**
```
# 1. Code
git clone https://github.com/drew90b/rto-accounting.git ~/projects/rto && cd ~/projects/rto

# 2. Throwaway GPG home; import the private key from the USB stick (asks for the passphrase)
export GNUPGHOME=$(mktemp -d); chmod 700 "$GNUPGHOME"
gpg --import /media/$USER/<usb>/rto-private-key.asc
gpg --fingerprint 288D0C359BDE2DCA218D5F94A251C38DF361E451     # compare with section 2

# 3. Download the newest encrypted backup: in the Backblaze web console (bucket
#    rto-backups-drew, folder rto/), or with rclone configured for B2:
mkdir -p ~/restore && rclone copy b2:rto-backups-drew/rto/ ~/restore/ --include "rto_*.tar.gpg"

# 4. Decrypt and unpack
gpg --output ~/restore/backup.tar --decrypt ~/restore/rto_YYYYMMDD_HHMMSS.tar.gpg
mkdir ~/restore/run && tar -xf ~/restore/backup.tar -C ~/restore/run
```
Then remove the private key from the machine you used: `rm -rf "$GNUPGHOME"` (and `unset GNUPGHOME`) once the backup is decrypted.

**Windows PowerShell alternative for step 4,** if you decrypt on the laptop:
```
gpg --output "$HOME\restore\backup.tar" --decrypt "$HOME\restore\rto_YYYYMMDD_HHMMSS.tar.gpg"
tar -xf "$HOME\restore\backup.tar" -C "$HOME\restore\run"
```
Copy the unpacked folder to the Ubuntu machine for the database steps.

### Verify the restore before replacing anything live

Work only in a scratch database and a scratch receipts folder. Nothing here touches live data.

**Ubuntu:**
```
cd ~/restore/run/rto_YYYYMMDD_HHMMSS
sha256sum -c SHA256SUMS                                        # every line OK
cd ~/projects/rto && RTO_APP_DIR=$HOME/projects/rto deploy/rto-backup-verify.sh ~/restore/run/rto_YYYYMMDD_HHMMSS
```
The verify script restores into a throwaway PostgreSQL cluster and a temporary receipts folder and checks the revision, the row counts and the receipt files. On a replacement machine there is no live receipts folder to compare with, so also check by hand: `receipt_files_in_archive` in `manifest.txt` equals `tar -tzf receipts.tar.gz | grep -vc '/$'`, and the manifest's `alembic_revision` equals the repository's `alembic heads`.

For a closer look, restore to a scratch database and folder yourself:
```
createdb rto_restore_check
pg_restore -d rto_restore_check --no-owner --no-privileges db.dump
psql -d rto_restore_check -c "select count(*) from purchases; select count(*) from documents;"
mkdir ~/receipts_check && tar -xzf receipts.tar.gz -C ~/receipts_check
```
Open a few receipt files from `~/receipts_check/receipts/purchase/` and confirm they are the right images. Note that `documents.file_path` stores an absolute path (`/home/drew/projects/rto/storage/receipts/...`), so receipt downloads in the app only work when the files are restored to that same path.

### Replace live data (only after the checks above pass)

1. If the old machine still runs, take a fresh local backup first (`sudo systemctl start rto-backup.service`) and keep it.
2. `sudo systemctl stop rto-web`
3. Restore the database: `pg_restore --clean --if-exists --no-owner -d rto_accounting_dev db.dump` (create the `rto_app` role and database first on a new machine).
4. Restore receipts: `tar -xzf receipts.tar.gz -C /home/drew/projects/rto/storage`
5. Restore `.env` from the backup's `env` file (`chmod 600`), and check out the `git_commit` in `manifest.txt` (or a newer commit that lists the same alembic revision).
6. `alembic current` must show the manifest's revision; then `sudo systemctl start rto-web` and sign in.
7. Check: purchase counts match the manifest, a known receipt downloads, anonymous routes redirect to `/login`.
8. Re-create the systemd units and timers from `deploy/` (README "Running as a service" and "Backups").

## 9. Known missing attachment: D-0001

Document D-0001 (purchase 2, uploaded 2026-09-13 as `image3.jpg`) records a path on the original Windows machine (`C:\Users\drewb\OneDrive\...\storage\receipts\purchase\2\image3.jpg`). The file was never copied to ubuntu-dev, so **no backup contains it and it is not protected.** Each backup run prints a note about it. If the original image is found, copy it to `storage/receipts/purchase/2/` on ubuntu-dev and update that row deliberately; until then treat the attachment as lost.

## 10. Conditions for enabling uploads (all met 2026-10-25)

1. The USB recovery key decrypts in an isolated test. Done. A second independent key copy is a follow-up, not required.
2. The public key is on ubuntu-dev, its fingerprint matches, and `gpg --list-secret-keys` there is empty. Done; the upload script also refuses to run if a secret key ever appears.
3. Retention is the recorded initial decision (keep every upload, no deletion), no lifecycle rule exists, and the job uses `rclone copy` only. Done.
4. A trial upload was downloaded, decrypted on the laptop with the USB key, unpacked and restored in isolation. Done (section 1).

## 11. Remaining limitations and follow-ups

Be accurate about what the off-server copy does and does not give you:

- **Key recovery location.** Two verified copies of the private key exist (USB copy 1 and the laptop), and the passphrase is recorded on paper. A geographically separate recovery location is still pending. If every copy and the passphrase record were lost together, every off-server backup would be unreadable.
- **The upload key can delete.** It is limited to the bucket but Read and Write. Someone with access to ubuntu-dev could delete or overwrite the cloud backups; the scripts never do, but the key could. Possible hardening: a Write Only upload key, or Object Lock.
- **Only the newest run is uploaded each night.** If a night's upload fails, that night is not backfilled automatically. Failures show as a failed `rto-backup-offsite.service` in systemd and the journal; **there is no email or other alert**, so check `systemctl status rto-backup-offsite.service` now and then.
- **Restore-tested once.** Only the 2026-10-24 backup has been downloaded, decrypted and restored. Repeat the drill (section 8) periodically, and rehearse a full rebuild on a replacement machine.
- **Storage grows.** Everything is kept and nothing is pruned; revisit pruning later (keep at least six monthly backups).
- **Not protected:** document D-0001 (section 9).
- **First unattended run not yet seen.** The 02:30 America/Chicago timer had not fired as of 2026-10-25 00:56 UTC. Check `journalctl -u rto-backup.service -u rto-backup-offsite.service` after it and record the result.

Follow-up checklist:

- [ ] Choose a geographically separate recovery location for the private key and a second record of the passphrase; verify by hash and an isolated decrypt test; then decide about the laptop's private export.
- [ ] Look at the first unattended nightly run (`journalctl -u rto-backup.service -u rto-backup-offsite.service`).
- [ ] Decide off-server pruning; consider a Write Only key or Object Lock.
- [ ] Decide on an alert for a failed backup or upload.

## 12. Planned server move: before and after checklist

A move of the application to another server (or a rebuild) is planned. These backups make it recoverable; this list makes it checkable. Write the numbers down before, and compare after.

**Before the move (on the old server):**
- [ ] `sudo systemctl start rto-backup.service`, then confirm the local run, its restore check and the automatic upload all succeeded (`journalctl -u rto-backup.service -u rto-backup-offsite.service -n 40`).
- [ ] Record: `git rev-parse HEAD`, `alembic current`, and row counts for purchases, transactions, units and documents (also in the newest `manifest.txt`); the receipt file count.
- [ ] Download that newest cloud backup and confirm it decrypts on the laptop (section 8) so the move does not depend on the old server's disk.
- [ ] Note what is *not* in the backups (section 13): Tailscale identity and settings, the exact address people bookmark (`ALLOWED_ORIGINS` must list it), PostgreSQL version and roles, systemd unit files (they are in `deploy/`), and OS packages.
- [ ] Stop `rto-web` before the final backup so nothing is entered after it, if a clean cut-over is wanted.

**After the move (on the new server):**
- [ ] Rebuild per section 8: clone the repository, install PostgreSQL and the Python requirements, restore the database and `storage/receipts` to the same absolute path, restore `.env`, check out the commit, `alembic current` equals the recorded revision.
- [ ] Set `ALLOWED_ORIGINS` to the address people will really use, bind to the right Tailscale address, install `rto-web.service` and the backup units from `deploy/`, import the **public** key only and confirm `gpg --list-secret-keys` is empty, and configure `rclone` with a new restricted B2 key (revoke the old one).
- [ ] Compare counts with the "before" numbers; open a few receipts; sign in as an admin and an employee; confirm anonymous routes redirect to `/login`.
- [ ] Run `rto-backup.service` once and confirm the local backup, its restore check and the cloud upload; check that the 02:30 timer is enabled and reboot once to confirm the app starts by itself.
- [ ] Only then retire the old server (keep its last backup and the cloud copies).

## 13. What these backups are not

They back up the **application and its data**: the database, the receipt files, a copy of `.env`, and a manifest. They are **not** a Proxmox-host backup and **not** a VM image: they do not contain the operating system, installed packages, Tailscale's node identity or settings, PostgreSQL's installation and roles, the systemd unit files (those are in Git under `deploy/`), or anything else on the machine. To come back from losing the server you rebuild a machine and restore into it (section 8); if you also want a quick whole-machine restore, take Proxmox/VM snapshots or image backups separately. Those are not set up and are not covered here.
