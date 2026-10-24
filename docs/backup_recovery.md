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
| Second independent copy of the private key | **PENDING (follow-up, not a prerequisite for uploads).** No second USB stick or dependable password manager yet. Today the key exists as USB copy 1 plus the retained export on the laptop; the laptop file is a convenience copy, not an independent one (section 3). The owner authorized encrypted uploads on this basis, knowing a single place can still be lost. |
| Passphrase storage | **Recorded** by the owner on paper at home (2026-10-24). A second place for it is still advisable. |
| Public key imported on ubuntu-dev | **Done 2026-10-24.** Primary fingerprint matched; `gpg --list-secret-keys` there lists 0 keys; a test file encrypted on the server could not be decrypted there. |
| `rclone` (1.60.1) installed on ubuntu-dev; remote `b2rto` configured with a B2 application key limited to the bucket | **Done 2026-10-24.** Listing the bucket works (it is empty); any other bucket name is refused. The config file is mode 600. **The key is Read and Write and therefore *can delete* files in the bucket** (section 7). |
| `deploy/rto-backup-offsite.sh` (encrypt and upload) | **NOT WRITTEN.** No upload of any kind has happened. **Nightly uploads are disabled.** |
| Trial encrypted upload, download, laptop decryption, isolated database and receipt restore (section 8) | **PENDING / UNTESTED.** Do not treat the off-server copy as protection until this has passed once. |
| Document D-0001 attachment | **Known missing** (section 9) |

Until the pending items are done, the only backups are on the same machine and disk as the live data.

## 2. Keys: what each one is for

- **Public key** (`rto-public-key.asc`): lets someone *encrypt* to us. It goes on ubuntu-dev. It cannot decrypt anything, so a stolen server or bucket reveals nothing.
- **Private key** (`rto-private-key.asc`, protected by a passphrase): the only thing that can *decrypt* the off-server backups. It must never be on ubuntu-dev or in the bucket. Lose every copy of it and every off-server backup is unreadable for good.
- **Passphrase:** unlocks the private key file. Keep it somewhere dependable and separate from the key files (a password manager, once one is chosen). A key copy without the passphrase is useless, and the reverse.
- **Fingerprint** of the key (identifies it; safe to share):

```
288D 0C35 9BDE 2DCA 218D  5F94 A251 C38D F361 E451
```
(written as one string: `288D0C359BDE2DCA218D5F94A251C38DF361E451`)

## 3. Recovery copies (described, not exposed)

| Copy | Where | State |
|---|---|---|
| Private key, USB copy 1 | A USB stick kept apart from the laptop and from ubuntu-dev | Hash-verified, decrypt-tested |
| Private key export on the laptop (`C:\Users\drewb\RTO-key-recovery\`) | The laptop (also in the laptop's GPG keyring) | **Retained on purpose; do not delete yet.** It shares the laptop's fate, so it does not count as the second independent copy. Delete the private export only after copy 2 is verified and recovery instructions are complete. |
| Private key, copy 2 | A place that is still reachable if the entire Proxmox server (and ubuntu-dev with it) is lost | **PENDING.** Location not chosen (section 11). |
| Passphrase | Not yet confirmed (section 1) | **PENDING** |
| Recovery note (fingerprint, bucket name, where copies are kept; no secrets) | To be kept with the passphrase | Recommended once a place exists |

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

## 6. Put the public key on ubuntu-dev (pending)

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

## 7. Backup contents and planned off-server layout

Each nightly run is a folder `rto_YYYYMMDD_HHMMSS/` (UTC) holding `db.dump`, `receipts.tar.gz`, `env` (a copy of `.env`, which is why anything off-server is encrypted first), `manifest.txt` and `SHA256SUMS`. The planned off-server job tars the new folder, encrypts it to the public key, and uploads `rto_YYYYMMDD_HHMMSS.tar.gpg` to `b2:rto-backups-drew/rto/`. Retention is **keep everything**: no lifecycle rule, no pruning, no automatic deletion. Storage will grow by about one encrypted backup per night (about 13 MB each at today's size, more as receipts accumulate); review the cost in Backblaze periodically and revisit pruning later, as a deliberate decision that keeps at least the six monthly backups the local policy keeps.

**Delete access, stated plainly.** Backblaze's key form offers only Read and Write, Read Only or Write Only. The key `rto-ubuntu-dev-upload` is limited to the one bucket but is **Read and Write, which includes deleting files**. Anyone who gets onto ubuntu-dev (the key is in `~/.config/rclone/rclone.conf`, mode 600) could delete or overwrite the cloud backups. Rules that follow from this: the upload job uses `rclone copy` only, never `sync`, `move`, `delete` or `purge`; `hard_delete` stays off; nothing schedules a deletion; each upload has a new unique name so nothing is overwritten. Possible later hardening, not done: a Write Only key for the nightly job (check that it can still list and copy), keeping the Read key only on the laptop, or Object Lock. Setup steps: README "Off-server copy: Backblaze B2 setup".

## 8. Recovering on a replacement computer (UNTESTED for the cloud download; the local restore check runs every night)

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

## 10. Before enabling uploads

All of these must be true, and recorded in `docs/progress.md`:

1. The USB recovery key decrypts in an isolated test (done). A second independent key copy is a follow-up, not required for uploads.
2. The public key is imported on ubuntu-dev, its fingerprint matches, and `gpg --list-secret-keys` there is empty (done).
3. Retention is the recorded initial decision (keep every upload, no deletion), no lifecycle rule exists, and the job uses `rclone copy` only.
4. A trial upload has been downloaded, decrypted on the laptop with the USB key, unpacked, and passed section 8's verification.

## 11. Progress and next steps (paused 2026-10-24; nightly uploads NOT enabled)

Done so far: Backblaze account and private bucket; key created, USB-copy-tested and passphrase recorded on paper; public key on ubuntu-dev (no private key there); `rclone` installed and the restricted `b2rto` remote verified. No backup has been uploaded.

Resume with these, one at a time:

- [ ] Write `deploy/rto-backup-offsite.sh`: tar the newest `rto_*` folder, encrypt it to the public key (`gpg --trust-model always --recipient 288D0C359BDE2DCA218D5F94A251C38DF361E451`), `rclone copy` it to `b2rto:rto-backups-drew/rto/` under a unique name, then check the remote size. Copy only; never sync, move or delete. Run it by hand first; do not wire it to the timer yet.
- [ ] **Trial upload** of one backup, by hand.
- [ ] **Download** it (laptop, web console or rclone), **decrypt on the laptop** with the USB key (section 8), unpack, and run `sha256sum -c SHA256SUMS`.
- [ ] **Isolated database and receipt restore** (`deploy/rto-backup-verify.sh` and the scratch-database steps in section 8). Record the date and result in `docs/progress.md`.
- [ ] Only after that passes: enable nightly uploads (service/timer hook), then update section 1.
- [ ] Follow-ups, not blockers: a second independent private-key copy and a second place for the passphrase; then decide whether to delete the laptop's private export; consider a Write Only upload key or Object Lock; decide pruning later.
