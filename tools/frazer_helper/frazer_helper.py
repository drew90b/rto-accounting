"""
RTO Frazer export helper (client). Runs on the Frazer computer, started by a person. Python 3.9+ standard library only.

What it does: waits for the three saved Frazer exports to land in a folder, checks each one, copies it into a dated run folder,
and sends it to the RTO app with a revocable upload token. What it does NOT do: sign in to Frazer, store any password, change or
delete the files Frazer wrote, or touch anything outside its own work folder and the landing folder it reads.

The helper does not drive Frazer's screens. Each export is produced either by a command Frazer supports (config: "command"), or by
the operator running the saved preset in Frazer while the helper waits ("manual" mode, the default).

Exit status: 0 complete, 2 partial, 3 failed, 4 could not start (config, lock, network, another run active).
"""
import argparse
import csv
import fnmatch
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib import error as urlerror
from urllib import request as urlrequest

VERSION = "1.0"
TYPES = ("vehicles", "customer_balances", "payments")
LABEL = {"vehicles": "Vehicle list", "customer_balances": "Customer balances", "payments": "Transactions"}
GOOD = ("stored", "duplicate", "carried")
LOCK_STALE_SECONDS = 3 * 3600


class HelperError(Exception):
    pass


class ServerRefused(HelperError):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"{status}: {body.get('error') if isinstance(body, dict) else body}")


# --- config -------------------------------------------------------------------------------------------------------------

DEFAULTS = {
    "server": "", "token": "", "landing_folder": "", "work_folder": "",
    "payment_days": 10, "wait_minutes": 20, "command_timeout_minutes": 20, "settle_seconds": 3, "poll_seconds": 2, "min_age_seconds": 2,
    "exports": {
        "vehicles": {"label": "Vehicle list preset", "pattern": "*vehicle*.csv*", "command": None},
        "customer_balances": {"label": "Customer balances preset", "pattern": "*customer*.csv*", "command": None},
        "payments": {"label": "Transactions preset", "pattern": "*payment*.csv*", "command": None},
    },
}


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise HelperError(f"Cannot read the config file {path}: {e}")
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "exports"}}
    cfg["exports"] = {t: {**DEFAULTS["exports"][t], **(raw.get("exports", {}).get(t, {}))} for t in TYPES}
    for k in ("server", "token", "landing_folder", "work_folder"):
        if not cfg[k]:
            raise HelperError(f"The config file needs a value for {k!r}.")
    if not str(cfg["token"]).startswith("fxh_"):
        raise HelperError("The token should start with fxh_. Copy it again from the app's Helper page.")
    if not 1 <= int(cfg["payment_days"]) <= 62:
        raise HelperError("payment_days must be between 1 and 62.")
    cfg["server"] = cfg["server"].rstrip("/")
    return cfg


# --- talking to the app -------------------------------------------------------------------------------------------------

def _multipart(fields: dict, file_field: str, filename: str, data: bytes):
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
              "Content-Type: text/csv\r\n\r\n".encode())
    out.write(data + b"\r\n" + f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class Api:
    """HTTP to the app. Network failures are retried a few times; a refusal from the server is never retried blindly."""

    def __init__(self, server, token, attempts=3, pause=2.0):
        self.server, self.token, self.attempts, self.pause = server, token, attempts, pause

    def call(self, method, path, json_body=None, fields=None, file=None):
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "rto-frazer-helper/" + VERSION}
        body = None
        if file is not None:
            body, headers["Content-Type"] = _multipart(fields or {}, "file", file[0], file[1])
        elif fields is not None:
            from urllib.parse import urlencode
            body, headers["Content-Type"] = urlencode(fields).encode(), "application/x-www-form-urlencoded"
        elif json_body is not None:
            body, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        last = None
        for attempt in range(self.attempts):
            try:
                req = urlrequest.Request(self.server + path, data=body, method=method, headers=headers)
                with urlrequest.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urlerror.HTTPError as e:
                try:
                    payload = json.loads(e.read().decode("utf-8"))
                except ValueError:
                    payload = {"error": e.reason}
                if e.code >= 500 and attempt + 1 < self.attempts:
                    last = e
                else:
                    raise ServerRefused(e.code, payload)
            except (urlerror.URLError, TimeoutError, ConnectionError) as e:
                last = e
            time.sleep(self.pause)
        raise HelperError(f"Could not reach the RTO app at {self.server} ({last}). Check the network and try again; nothing was lost.")


# --- finding and checking a file ----------------------------------------------------------------------------------------

def _mtime_utc(path):
    return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)


def wait_for_file(folder, pattern, since, *, wait_seconds, settle_seconds, poll_seconds, min_age_seconds, log, sleep=time.sleep, now=time.time, stop=lambda: False):
    """-> (path, None) for a fresh, finished file; (None, (outcome, message)) otherwise.
    Fresh = written after `since` (the run's start). Finished = non-empty, readable, and unchanged across a settle period.
    A matching file that is older than the run is reported as stale, never used."""
    deadline = now() + wait_seconds
    seen_old = None
    while True:
        if stop():
            return None, ("failed", "Cancelled by the operator.")
        try:
            names = [n for n in os.listdir(folder) if fnmatch.fnmatch(n.lower(), pattern.lower())]
        except OSError as e:
            return None, ("failed", f"Cannot read the landing folder {folder}: {e}")
        cands = []
        for n in names:
            p = os.path.join(folder, n)
            try:
                cands.append((os.path.getmtime(p), p))
            except OSError:
                continue
        fresh = [c for c in cands if c[0] >= since.timestamp() - 1]
        old = [c for c in cands if c[0] < since.timestamp() - 1]
        if old:
            seen_old = max(old)[1]
        if fresh:
            mt, path = max(fresh)
            if now() - mt >= min_age_seconds:
                ok, why = _settled(path, settle_seconds, sleep)
                if ok:
                    return path, None
                log(f"  waiting: {os.path.basename(path)} is not finished ({why})")
        if now() >= deadline:
            break
        sleep(poll_seconds)
    if seen_old:
        return None, ("stale", f"Only an older file was found ({os.path.basename(seen_old)}, written "
                               f"{_mtime_utc(seen_old):%Y-%m-%d %H:%M} UTC, before this run started). It was NOT used. Run the export again.")
    return None, ("missing", f"No file matching {pattern} appeared in {folder} within {wait_seconds // 60} minutes.")


def _settled(path, settle_seconds, sleep):
    try:
        a = (os.path.getsize(path), os.path.getmtime(path))
        sleep(settle_seconds)
        b = (os.path.getsize(path), os.path.getmtime(path))
        if a != b:
            return False, "still changing"
        if b[0] == 0:
            return False, "empty"
        with open(path, "rb") as f:                      # fails on Windows while Frazer still holds the file open
            data = f.read()
    except OSError as e:
        return False, f"cannot be read yet: {e}"
    return _csv_complete(data)


def _csv_complete(data: bytes):
    text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return False, "empty"
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        return False, f"not readable as CSV: {e}"
    if not rows or len([c for c in rows[0] if c.strip()]) < 2:
        return False, "no column heading line"
    width = len([c for c in rows[0] if c.strip()])
    if len(rows) > 1 and 0 < len(rows[-1]) < width and not text.endswith(("\n", "\r")):
        return False, "the last line stops part-way through a row"
    return True, ""


# --- the run ------------------------------------------------------------------------------------------------------------

class Lock:
    """One run at a time on this computer. Created exclusively; an old lock left by a crash is replaced after three hours."""

    def __init__(self, folder):
        self.path = os.path.join(folder, "helper.lock")
        self.held = False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                if time.time() - os.path.getmtime(self.path) > LOCK_STALE_SECONDS:
                    os.remove(self.path)
                    continue
                raise HelperError("Another helper run is already in progress on this computer. Wait for it to finish.")
        raise HelperError("Could not take the run lock.")

    def __exit__(self, *a):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def _resume_candidate(work, today_iso):
    """The newest run folder of today whose server run ended partial/failed/abandoned: the one a retry continues."""
    best = None
    for n in sorted(os.listdir(work)) if os.path.isdir(work) else []:
        p = os.path.join(work, n, "run.json")
        if n.startswith(today_iso) and os.path.exists(p):
            try:
                meta = json.load(open(p, encoding="utf-8"))
            except ValueError:
                continue
            best = meta if meta.get("final_status") in ("partial", "failed", "abandoned") else None
    return best


def run(cfg, *, api=None, log=print, ask=None, stop=lambda: False, fresh=False, sleep=time.sleep, now=time.time, wait_seconds=None):
    """One closing-time run. Returns the final run record (dict). Raises HelperError if it could not start."""
    api = api or Api(cfg["server"], cfg["token"])
    work = cfg["work_folder"]
    with Lock(work):
        started_local = datetime.now()
        started_utc = datetime.now(timezone.utc)
        try:
            status = api.call("GET", "/api/frazer-helper/status")
        except ServerRefused as e:
            raise HelperError(f"The app refused the token ({e}). Ask an administrator for a new one.")
        missing = [t for t in TYPES if t not in status.get("layouts_approved", [])]
        if missing:
            raise HelperError("An administrator must approve the expected column layout in the app first (Helper page) for: " + ", ".join(missing))

        resume = None if fresh else _resume_candidate(work, started_local.strftime("%Y-%m-%d"))
        body = {"helper_version": VERSION, "host": platform.node(),
                "payment_days": int(cfg["payment_days"]), "retry_of": resume["server_run"] if resume else None}
        try:
            srv = api.call("POST", "/api/frazer-helper/runs", json_body=body)
        except ServerRefused as e:
            raise HelperError(f"The app would not start a run: {e.body.get('error', e)}")
        run_id, bdate = srv["run_id"], srv["business_date"]
        folder = os.path.join(work, f"{started_local:%Y-%m-%d_%H%M%S}")
        os.makedirs(os.path.join(folder, "files"), exist_ok=True)
        rec = {"helper_version": VERSION, "server_run": run_id, "retry_of": body["retry_of"], "business_date": bdate,
               "started_local": started_local.isoformat(timespec="seconds"), "started_utc": started_utc.isoformat(timespec="seconds"),
               "files": {}, "final_status": "running"}
        _write_json(os.path.join(folder, "run.json"), rec)
        log(f"Run {run_id} started" + (f" (retrying {body['retry_of']}; finished exports are kept)" if body["retry_of"] else "") + f". Files: {folder}")

        end = date.fromisoformat(bdate)
        period = (end - timedelta(days=int(cfg["payment_days"]) - 1)).isoformat(), bdate
        waitsec = wait_seconds if wait_seconds is not None else int(cfg["wait_minutes"]) * 60
        for t in TYPES:
            if stop():
                break
            carried = srv["results"].get(t)
            if carried and carried.get("outcome") == "carried":
                rec["files"][t] = {"outcome": "carried", "message": f"Already stored by {carried.get('carried_from')}; not sent again."}
                log(f"{LABEL[t]}: already done in {carried.get('carried_from')}, skipped.")
                continue
            rec["files"][t] = _one_export(cfg, api, run_id, t, bdate, period, started_utc, folder, log, ask, stop, sleep, now, waitsec)
            _write_json(os.path.join(folder, "run.json"), rec)

        try:
            fin = api.call("POST", f"/api/frazer-helper/runs/{run_id}/finish")
        except HelperError as e:
            rec["final_status"] = "unknown"
            rec["note"] = f"Could not finish the run on the server: {e}. Run again; stored files are not duplicated."
            _write_json(os.path.join(folder, "run.json"), rec)
            raise
        rec["final_status"], rec["server_results"], rec["sync"], rec["summary"] = fin["status"], fin["results"], fin["sync"], fin["summary"]
        _write_json(os.path.join(folder, "run.json"), rec)
        log("")
        log(f"RUN {run_id}: {fin['status'].upper()}. {fin['summary']}")
        for t in TYPES:
            r = fin["results"].get(t, {})
            log(f"  {LABEL[t]:18} {r.get('outcome', '?'):20} {r.get('upload') or ''}  {r.get('message', '')}")
        if fin.get("sync"):
            log(f"  Reconciliation: {fin['sync']['state']}. {fin['sync']['detail']}")
        if fin["status"] != "complete":
            log("Fix what is listed above, then press the button again: finished exports are not sent twice.")
        return rec


def _one_export(cfg, api, run_id, t, bdate, period, started_utc, folder, log, ask, stop, sleep, now, waitsec):
    ex = cfg["exports"][t]
    log(f"{LABEL[t]}: " + ("running the export command..." if ex.get("command") else f"in Frazer, run the saved export '{ex['label']}' now."))
    if ex.get("command"):
        try:
            cp = subprocess.run(ex["command"], shell=True, capture_output=True, text=True, timeout=int(cfg.get("command_timeout_minutes", 20)) * 60)
        except subprocess.TimeoutExpired:
            return _fail(api, run_id, t, "failed", "The export command did not finish in time.", log)
        if cp.returncode != 0:
            return _fail(api, run_id, t, "failed", f"The export command failed (exit {cp.returncode}): {(cp.stderr or cp.stdout).strip()[:200]}", log)
    elif ask:
        ask(t, ex["label"])
    path, problem = wait_for_file(cfg["landing_folder"], ex["pattern"], started_utc, wait_seconds=waitsec, settle_seconds=cfg["settle_seconds"],
                                  poll_seconds=cfg["poll_seconds"], min_age_seconds=cfg["min_age_seconds"], log=log, sleep=sleep, now=now, stop=stop)
    if problem:
        return _fail(api, run_id, t, problem[0], problem[1], log)
    # copy into the dated run folder; the original Frazer file is left exactly as Frazer wrote it
    dest = os.path.join(folder, "files", os.path.basename(path))
    with open(path, "rb") as src:
        data = src.read()
    with open(dest, "wb") as out:
        out.write(data)
    written = _mtime_utc(path).isoformat(timespec="seconds")
    fields = {"export_type": t, "sha256": hashlib.sha256(data).hexdigest(), "generated_at": written, "export_date": bdate,
              "coverage_start": period[0] if t == "payments" else "", "coverage_end": period[1] if t == "payments" else ""}
    try:
        res = api.call("POST", f"/api/frazer-helper/runs/{run_id}/files", fields=fields, file=(os.path.basename(path), data))
    except ServerRefused as e:
        return _fail(api, run_id, t, "failed", f"The app refused the file: {e}", log)
    except HelperError as e:
        log(f"  {LABEL[t]}: {e}")
        return {"outcome": "failed", "message": str(e), "file": dest}
    log(f"  {LABEL[t]}: {res['outcome']}. {res['message']}")
    return {"outcome": res["outcome"], "message": res["message"], "upload": res.get("upload"), "file": dest, "file_written_at": written}


def _fail(api, run_id, t, outcome, message, log):
    log(f"  {LABEL[t]}: {outcome.upper()}. {message}")
    try:
        api.call("POST", f"/api/frazer-helper/runs/{run_id}/failures", fields={"export_type": t, "outcome": outcome, "message": message})
    except HelperError as e:
        message += f" (also could not tell the app: {e})"
    return {"outcome": outcome, "message": message}


# --- command line and the one-button window ------------------------------------------------------------------------------

def _gui(cfg):
    import threading
    import tkinter as tk
    from tkinter import scrolledtext
    root = tk.Tk()
    root.title("RTO - send Frazer exports")
    root.geometry("760x520")
    state = {"stop": False}
    lbl = tk.Label(root, text="Close of day: press the button, then run each saved export in Frazer when told.", wraplength=720, justify="left")
    lbl.pack(padx=10, pady=8, anchor="w")
    btn = tk.Button(root, text="Send today's Frazer exports", font=("Segoe UI", 14, "bold"), padx=16, pady=10)
    btn.pack(padx=10, pady=4, anchor="w")
    result = tk.Label(root, text="", font=("Segoe UI", 12, "bold"))
    result.pack(padx=10, anchor="w")
    box = scrolledtext.ScrolledText(root, height=20, state="disabled")
    box.pack(fill="both", expand=True, padx=10, pady=8)

    def log(msg):
        def add():
            box.configure(state="normal"); box.insert("end", msg + "\n"); box.see("end"); box.configure(state="disabled")
        root.after(0, add)

    def work():
        try:
            rec = run(cfg, log=log, stop=lambda: state["stop"])
            colour = {"complete": "green", "partial": "orange", "failed": "red"}.get(rec["final_status"], "black")
            root.after(0, lambda: result.configure(text=f"{rec['final_status'].upper()}: {rec.get('summary', '')}", fg=colour))
        except HelperError as e:
            log(f"STOPPED: {e}")
            root.after(0, lambda: result.configure(text=f"Could not run: {e}", fg="red"))
        finally:
            root.after(0, lambda: btn.configure(state="normal", text="Send today's Frazer exports"))

    def go():
        state["stop"] = False
        btn.configure(state="disabled", text="Working...")
        result.configure(text="")
        threading.Thread(target=work, daemon=True).start()
    btn.configure(command=go)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Send today's Frazer exports to the RTO app.")
    ap.add_argument("command", choices=["window", "run", "check"], nargs="?", default="window")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ap.add_argument("--fresh", action="store_true", help="start a new run instead of retrying today's unfinished one")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.command == "check":
            for k in ("landing_folder", "work_folder"):
                print(f"{k}: {cfg[k]}  ->  {'exists' if os.path.isdir(cfg[k]) else 'NOT FOUND'}")
            print("server status:", json.dumps(Api(cfg["server"], cfg["token"]).call("GET", "/api/frazer-helper/status"), indent=1))
            return 0
        if a.command == "window":
            _gui(cfg)
            return 0
        rec = run(cfg, fresh=a.fresh, ask=lambda t, label: print(f"\n>>> Now run '{label}' in Frazer ({LABEL[t]}). Waiting for the file..."))
        return {"complete": 0, "partial": 2, "failed": 3}.get(rec["final_status"], 3)
    except HelperError as e:
        print("STOPPED:", e)
        return 4


if __name__ == "__main__":
    sys.exit(main())
