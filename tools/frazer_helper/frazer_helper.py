"""
RTO Frazer export helper ("Update RTO Records"). Runs on the Frazer computer, started by a person. Python 3.9+ standard library only.

One run does, in order:
  1. asks the RTO app to start a run; the app answers with the transaction period this run must export (catch-up from the last
     accepted period, with overlap; the first run's start is stated explicitly in config.json);
  2. makes Frazer produce the three saved exports. In "auto" mode a small PowerShell driver (FrazerDriver.ps1) presses the keys in
     the Hosted Frazer window, confirms the preset on screen, types and reads back the transaction dates, and clicks Create File.
     In "manual" mode the operator runs each saved export and the helper waits;
  3. accepts a file only if it was written during this run, copies it into the run's own dated folder under a descriptive name,
     and sends it to the RTO app with a revocable upload token;
  4. asks the app to finish the run (reconciliation reads the new uploads) and shows a plain result.

What it never does: sign in to Frazer, store any password, change or delete the files Frazer wrote, or touch customer records.
Export, upload and diagnostic outcomes are reported separately: a screenshot that could not be saved never hides which business
step succeeded.

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

VERSION = "2.0"
TYPES = ("vehicles", "customer_balances", "payments")
LABEL = {"vehicles": "Vehicle list", "customer_balances": "Customer balances", "payments": "Payment transactions"}
GOOD = ("stored", "duplicate", "carried")
LOCK_STALE_SECONDS = 3 * 3600
MAX_CLOCK_SKEW_SECONDS = 150            # the app refuses a file whose time is more than 3 minutes from its own clock


class HelperError(Exception):
    pass


class ServerRefused(HelperError):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"{status}: {body.get('error') if isinstance(body, dict) else body}")


# --- config -------------------------------------------------------------------------------------------------------------

DEFAULTS = {
    "server": "", "token": "", "landing_folder": "", "work_folder": "",
    "mode": "manual",                   # "auto": the driver makes Frazer export; "manual": the operator runs each saved export
    "driver": "",                       # path of FrazerDriver.ps1 (auto mode)
    "first_coverage_start": "",         # explicit start of the FIRST transaction period (YYYY-MM-DD); later periods come from the app
    "wait_minutes": 20, "driver_timeout_seconds": 420, "settle_seconds": 3, "poll_seconds": 2, "min_age_seconds": 2,
    "exports": {
        # "file" is the exact name Frazer writes (preset name + .CSV); "pattern" is only a fallback for manual mode
        "vehicles": {"label": "File 1 (vehicles)", "slot": 1, "preset": "frazer-vehicles", "file": "", "pattern": "*vehicle*.csv*", "command": None},
        "customer_balances": {"label": "File 3 (customer balances)", "slot": 3, "preset": "frazer-customer-balances", "file": "", "pattern": "*customer*.csv*", "command": None},
        "payments": {"label": "File 2 (payment transactions)", "slot": 2, "preset": "frazer-payment-transactions", "file": "", "pattern": "*payment*.csv*", "command": None},
    },
}


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise HelperError(f"Cannot read the settings file {path}: {e}")
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "exports"}}
    cfg["exports"] = {t: {**DEFAULTS["exports"][t], **(raw.get("exports", {}).get(t, {}))} for t in TYPES}
    for k in ("server", "token", "landing_folder", "work_folder"):
        if not cfg[k]:
            raise HelperError(f"The settings file needs a value for {k!r}.")
    if not str(cfg["token"]).startswith("fxh_"):
        raise HelperError("The token should start with fxh_. Copy it again from the app's Helper page.")
    if cfg["mode"] not in ("auto", "manual"):
        raise HelperError('mode must be "auto" or "manual".')
    if cfg["mode"] == "auto" and not cfg["driver"]:
        raise HelperError('mode "auto" needs "driver": the path of FrazerDriver.ps1.')
    if cfg.get("first_coverage_start"):
        try:
            date.fromisoformat(cfg["first_coverage_start"])
        except ValueError:
            raise HelperError("first_coverage_start must be a date such as 2026-10-01.")
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


def _matches(name, ex):
    """The exact file name Frazer writes for this preset when it is known; otherwise the fallback pattern."""
    if ex.get("file"):
        return name.lower() == ex["file"].lower()
    return fnmatch.fnmatch(name.lower(), (ex.get("pattern") or "").lower())


def _describe(ex):
    return ex.get("file") or ex.get("pattern")


def wait_for_file(folder, ex, since, *, wait_seconds, settle_seconds, poll_seconds, min_age_seconds, log, sleep=time.sleep, now=time.time, stop=lambda: False):
    """-> (path, None) for a fresh, finished file; (None, (outcome, message)) otherwise.
    Fresh = written after `since`. Finished = non-empty, readable, and unchanged across a settle period.
    A matching file that is older is reported as stale, never used."""
    deadline = now() + wait_seconds
    seen_old = None
    while True:
        if stop():
            return None, ("failed", "Cancelled by the operator.")
        try:
            names = [n for n in os.listdir(folder) if _matches(n, ex)]
        except OSError as e:
            return None, ("failed", f"Cannot read the folder {folder}: {e}")
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
                               f"{_mtime_utc(seen_old):%Y-%m-%d %H:%M} UTC, before this export was asked for). It was NOT used.")
    return None, ("missing", f"No file named {_describe(ex)} appeared in {folder}.")


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


# --- generation by the PowerShell driver (auto mode) --------------------------------------------------------------------

def _mmddyy(iso):
    return date.fromisoformat(iso).strftime("%m/%d/%y")


class PowerShellDriver:
    """Runs FrazerDriver.ps1 ONCE for all the exports of a run (one process keeps the right to bring Frazer's windows forward).
    The driver reports what it saw and did; this helper still decides from the files themselves."""

    def __init__(self, cfg):
        self.cfg = cfg

    def generate(self, types, period, diag_dir, log):
        """-> {"exports": {type: {...}}, "diagnostics": {...}, "stopped": str | None}"""
        os.makedirs(diag_dir, exist_ok=True)
        request = {"folder": self.cfg["landing_folder"], "out_dir": diag_dir,
                   "exports": [{"type": t, "slot": int(self.cfg["exports"][t]["slot"]), "preset": self.cfg["exports"][t]["preset"],
                                "file": self.cfg["exports"][t].get("file") or self.cfg["exports"][t]["preset"] + ".CSV",
                                **({"begin": _mmddyy(period[0]), "end": _mmddyy(period[1])} if t == "payments" else {})} for t in types]}
        req_path, res_path = os.path.join(diag_dir, "driver-request.json"), os.path.join(diag_dir, "driver-result.json")
        with open(req_path, "w", encoding="utf-8") as f:
            json.dump(request, f, indent=1)
        if os.path.exists(res_path):
            os.remove(res_path)
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", self.cfg["driver"],
               "-Action", "Export", "-RequestPath", req_path, "-ResultPath", res_path]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=int(self.cfg["driver_timeout_seconds"]), creationflags=flags)
        except subprocess.TimeoutExpired:
            return {"exports": {}, "diagnostics": {"errors": ["The Frazer driver did not finish in time."]}, "stopped": "The Frazer driver did not finish in time."}
        except OSError as e:
            return {"exports": {}, "diagnostics": {"errors": [str(e)]}, "stopped": f"The Frazer driver could not be started: {e}"}
        try:
            with open(res_path, "r", encoding="utf-8-sig") as f:
                out = json.load(f)
        except (OSError, ValueError) as e:
            tail = ((cp.stderr or "") + (cp.stdout or "")).strip()[-300:]
            return {"exports": {}, "diagnostics": {"errors": [f"no driver result ({e}); {tail}"]},
                    "stopped": "The Frazer driver stopped without a result. " + tail}
        out.setdefault("exports", {})
        out.setdefault("diagnostics", {})
        return out


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
                raise HelperError("Another update is already in progress on this computer. Wait for it to finish.")
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_iso(ts: float) -> str:
    """Local time with its UTC offset, e.g. 2026-11-09T22:38:28-06:00."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _tz_label() -> str:
    off = datetime.now().astimezone().strftime("%z")
    return f"{time.tzname[time.localtime().tm_isdst > 0]} (UTC{off[:3]}:{off[3:]})"


def _resume_candidate(work, today_iso):
    """The newest run folder of today whose server run ended partial/failed/abandoned: the one a retry continues."""
    best = None
    for n in sorted(os.listdir(work)) if os.path.isdir(work) else []:
        p = os.path.join(work, n, "manifest.json")
        if n.startswith(today_iso) and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    meta = json.load(f)
            except ValueError:
                continue
            best = meta if meta.get("final_status") in ("partial", "failed", "abandoned") else None
    return best


def preserve_original(work, path, log):
    """Before Frazer overwrites a working file, keep a copy of it (once per distinct content) with its own written time.
    Files this helper archived itself are already kept in their run folder and are not copied again."""
    keep = os.path.join(os.path.dirname(os.path.abspath(work)), "preserved") if os.path.basename(os.path.normpath(work)) == "runs" else os.path.join(work, "_preserved")
    index_path = os.path.join(keep, "index.json")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    os.makedirs(keep, exist_ok=True)
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    sha = _sha(data)
    if sha in index:
        return index[sha]
    mt = os.path.getmtime(path)
    dest = os.path.join(keep, f"{datetime.fromtimestamp(mt):%Y-%m-%d_%H%M%S}_{os.path.basename(path)}")
    with open(dest, "wb") as out:
        out.write(data)
    os.utime(dest, (mt, mt))
    index[sha] = dest
    _write_json(index_path, index)
    log(f"  kept a copy of the earlier {os.path.basename(path)} (written {datetime.fromtimestamp(mt):%Y-%m-%d %H:%M}) in {keep}")
    return dest


def _remember_archived(work, sha, dest):
    keep = os.path.join(os.path.dirname(os.path.abspath(work)), "preserved") if os.path.basename(os.path.normpath(work)) == "runs" else os.path.join(work, "_preserved")
    index_path = os.path.join(keep, "index.json")
    os.makedirs(keep, exist_ok=True)
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    index.setdefault(sha, dest)
    _write_json(index_path, index)


def archive_name(t, generated_ts, period):
    """Descriptive name inside the run folder. "as-of" is the local date the file was generated; the exact moment Frazer's data
    was cut off is not known. The transaction name carries the period that was confirmed in Frazer."""
    d = datetime.fromtimestamp(generated_ts).strftime("%Y-%m-%d")
    if t == "payments":
        return f"frazer-payment-transactions-{period[0]}-through-{period[1]}.csv"
    return {"vehicles": f"frazer-vehicles-as-of-{d}.csv", "customer_balances": f"frazer-customer-balances-as-of-{d}.csv"}[t]


def run(cfg, *, api=None, log=print, ask=None, stop=lambda: False, fresh=False, sleep=time.sleep, now=time.time, wait_seconds=None,
        driver=None, mode=None):
    """One update. Returns the manifest (dict). Raises HelperError if it could not start."""
    api = api or Api(cfg["server"], cfg["token"])
    mode = mode or cfg.get("mode", "manual")
    work = cfg["work_folder"]
    with Lock(work):
        started_local = datetime.now()
        try:
            status = api.call("GET", "/api/frazer-helper/status")
        except ServerRefused as e:
            raise HelperError(f"The RTO app refused this computer's token ({e}). Ask an administrator for a new one.")
        missing = [t for t in TYPES if t not in status.get("layouts_approved", [])]
        if missing:
            raise HelperError("An administrator must approve the expected column layout in the app first (Helper page) for: " + ", ".join(missing))
        if status.get("server_time"):
            skew = abs((datetime.now(timezone.utc) - datetime.fromisoformat(status["server_time"].replace("Z", "+00:00"))).total_seconds())
            if skew > MAX_CLOCK_SKEW_SECONDS:
                raise HelperError(f"This computer's clock differs from the RTO app's by {int(skew // 60)} minutes. Correct the clock "
                                  "(Settings > Time & language > Sync now) and try again. Nothing was exported.")

        resume = None if fresh else _resume_candidate(work, started_local.strftime("%Y-%m-%d"))
        body = {"helper_version": VERSION, "host": platform.node(), "timezone": _tz_label(), "mode": mode,
                "first_coverage_start": cfg.get("first_coverage_start") or None, "retry_of": resume["server_run"] if resume else None}
        try:
            srv = api.call("POST", "/api/frazer-helper/runs", json_body=body)
        except ServerRefused as e:
            raise HelperError(f"The RTO app would not start an update: {e.body.get('error', e) if isinstance(e.body, dict) else e}")
        started_utc = datetime.fromisoformat(srv["started_at"].replace("Z", "+00:00")) if srv.get("started_at") else datetime.now(timezone.utc)
        started_utc = min(started_utc, datetime.now(timezone.utc))
        run_id, bdate, plan = srv["run_id"], srv["business_date"], srv.get("plan") or {}
        period = (plan.get("coverage_start"), plan.get("coverage_end"))
        folder = os.path.join(work, f"{started_local:%Y-%m-%d_%H%M%S}")
        n = 1
        while os.path.exists(folder):                    # every update gets its own folder, even two started in the same second
            n += 1
            folder = os.path.join(work, f"{started_local:%Y-%m-%d_%H%M%S}-{n}")
        diag_dir = os.path.join(folder, "diagnostics")
        os.makedirs(diag_dir, exist_ok=True)
        rec = {"helper_version": VERSION, "server_run": run_id, "retry_of": body["retry_of"], "business_date": bdate, "mode": mode,
               "computer": platform.node(), "timezone": _tz_label(),
               "started_local": started_local.astimezone().isoformat(timespec="seconds"), "started_utc": started_utc.isoformat(timespec="seconds"),
               "transaction_period": {"start": period[0], "end": period[1], "basis": plan.get("basis"), "explanation": plan.get("explanation")},
               "source_cutoff": "Unknown: 'as of' is the date each file was generated, not a confirmed cutoff inside Frazer.",
               "files": {}, "diagnostics": {"status": "none", "folder": diag_dir, "errors": []}, "final_status": "running"}
        manifest = os.path.join(folder, "manifest.json")
        _write_json(manifest, rec)
        log(f"Update {run_id} started" + (f" (continuing {body['retry_of']}; exports already accepted are kept)" if body["retry_of"] else "") + ".")
        log(f"Payment transactions period: {period[0]} through {period[1]}. {plan.get('explanation', '')}")

        waitsec = wait_seconds if wait_seconds is not None else int(cfg["wait_minutes"]) * 60
        todo = []
        for t in TYPES:
            carried = (srv.get("results") or {}).get(t)
            if carried and carried.get("outcome") == "carried":
                rec["files"][t] = {"export": {"status": "carried", "message": f"Already accepted in {carried.get('carried_from')}; not exported again."},
                                   "upload": {"outcome": "carried", "message": f"Already stored by {carried.get('carried_from')}.", "upload": carried.get("upload")}}
                log(f"{LABEL[t]}: already accepted in {carried.get('carried_from')}, skipped.")
            else:
                todo.append(t)
                rec["files"][t] = {"export": {"status": "pending"}, "upload": {"outcome": "not sent", "message": ""}}

        # keep a copy of whatever is in the working files now, before Frazer overwrites them
        for t in todo:
            ex = cfg["exports"][t]
            try:
                for n in os.listdir(cfg["landing_folder"]):
                    if _matches(n, ex):
                        preserve_original(work, os.path.join(cfg["landing_folder"], n), log)
            except OSError as e:
                raise HelperError(f"Cannot read the folder {cfg['landing_folder']}: {e}")

        # ---- 1. export (generation)
        gen_started = datetime.now(timezone.utc)
        gen = {"exports": {}, "diagnostics": {}, "stopped": None}
        if mode == "auto" and todo and not stop():
            log("Making Frazer create the exports. Please do not touch the mouse or keyboard until this says it is finished.")
            drv = driver or PowerShellDriver(cfg)
            try:
                gen = drv.generate(todo, period, diag_dir, log)
            except Exception as e:                      # noqa: BLE001  a driver crash is an export failure, never a helper crash
                gen = {"exports": {}, "diagnostics": {"errors": [f"driver crashed: {e}"]}, "stopped": f"The Frazer driver failed: {e}"}
            d = gen.get("diagnostics") or {}
            rec["diagnostics"] = {"status": "problem" if d.get("errors") else "saved", "folder": diag_dir, "errors": list(d.get("errors") or []),
                                  "screenshots": len(d.get("screenshots") or [])}
            if gen.get("stopped"):
                log(f"  Frazer driver stopped: {gen['stopped']}")
            log("Finished using Frazer's screens. You can use the computer again.")

        # ---- 2. check each file and send it
        for t in todo:
            if stop():
                break
            rec["files"][t] = _one_export(cfg, api, run_id, t, bdate, period, started_utc, gen_started, folder, work, log, ask, stop, sleep, now,
                                          waitsec, mode, gen)
            _write_json(manifest, rec)

        # ---- 3. finish: the app synchronises reconciliation and decides the run's status
        try:
            fin = api.call("POST", f"/api/frazer-helper/runs/{run_id}/finish")
        except HelperError as e:
            rec["final_status"] = "unknown"
            rec["note"] = f"Could not finish the update on the RTO app: {e}. Start it again; files already stored are not duplicated."
            _write_json(manifest, rec)
            raise
        rec["final_status"], rec["server_results"], rec["sync"], rec["summary"] = fin["status"], fin["results"], fin["sync"], fin["summary"]
        _write_json(manifest, rec)
        for line in result_lines(rec):
            log(line)
        return rec


def _one_export(cfg, api, run_id, t, bdate, period, started_utc, gen_started, folder, work, log, ask, stop, sleep, now, waitsec, mode, gen):
    ex = cfg["exports"][t]
    out = {"export": {"status": "failed", "message": ""}, "upload": {"outcome": "not sent", "message": ""}}
    since = started_utc
    drv = (gen.get("exports") or {}).get(t) or {}
    if mode == "auto":
        since = gen_started
        out["export"]["driver"] = {k: drv.get(k) for k in ("ok", "message", "preset_name", "confirmations", "readback", "typed", "dialog", "seconds") if k in drv}
        if not drv.get("ok"):
            msg = drv.get("message") or gen.get("stopped") or "Frazer did not create this export."
            out["export"]["message"] = msg
            _fail(api, run_id, t, "failed", msg, log)
            return out
        if t == "payments":
            rb = drv.get("readback") or {}
            want = (_mmddyy(period[0]), _mmddyy(period[1]))
            if (rb.get("begin"), rb.get("end")) != want:
                msg = (f"The dates read back from Frazer ({rb.get('begin')} to {rb.get('end')}) are not the period asked for "
                       f"({want[0]} to {want[1]}). The file was NOT used.")
                out["export"]["message"] = msg
                _fail(api, run_id, t, "failed", msg, log)
                return out
        wait = min(30, waitsec)
    elif ex.get("command"):
        log(f"{LABEL[t]}: running the export command...")
        try:
            cp = subprocess.run(ex["command"], shell=True, capture_output=True, text=True, timeout=1200)
        except subprocess.TimeoutExpired:
            out["export"]["message"] = "The export command did not finish in time."
            _fail(api, run_id, t, "failed", out["export"]["message"], log)
            return out
        if cp.returncode != 0:
            out["export"]["message"] = f"The export command failed (exit {cp.returncode}): {(cp.stderr or cp.stdout).strip()[:200]}"
            _fail(api, run_id, t, "failed", out["export"]["message"], log)
            return out
        wait = waitsec
    else:
        extra = f" with Beginning Date {_mmddyy(period[0])} and Ending Date {_mmddyy(period[1])}" if t == "payments" else ""
        log(f"{LABEL[t]}: in Frazer, run the saved export {ex['label']}{extra} now.")
        if ask:
            ask(t, ex["label"])
        wait = waitsec
    path, problem = wait_for_file(cfg["landing_folder"], ex, since, wait_seconds=wait, settle_seconds=cfg["settle_seconds"],
                                  poll_seconds=cfg["poll_seconds"], min_age_seconds=cfg["min_age_seconds"], log=log, sleep=sleep, now=now, stop=stop)
    if problem:
        out["export"]["status"] = problem[0]
        out["export"]["message"] = problem[1]
        _fail(api, run_id, t, problem[0], problem[1], log)
        return out

    # the export exists and is fresh: copy it into this run's folder under a descriptive name; Frazer's file is left as written
    with open(path, "rb") as src:
        data = src.read()
    mt = os.path.getmtime(path)
    name = archive_name(t, mt, period)
    dest = os.path.join(folder, name)
    with open(dest, "wb") as o:
        o.write(data)
    os.utime(dest, (mt, mt))
    sha = _sha(data)
    try:
        _remember_archived(work, sha, dest)
    except OSError:
        pass
    written_utc = datetime.fromtimestamp(mt, tz=timezone.utc).isoformat(timespec="seconds")
    readback = (drv.get("readback") or {}) if mode == "auto" else {}
    out["export"].update({
        "status": "created", "message": f"Created {datetime.fromtimestamp(mt):%I:%M:%S %p}.",
        "original_filename": os.path.basename(path), "original_path": path, "archive_name": name, "archive_path": dest,
        "generated_local": _local_iso(mt), "generated_utc": written_utc, "timezone": _tz_label(), "sha256": sha, "size_bytes": len(data),
        "as_of_date": datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
        "period": ({"start": period[0], "end": period[1],
                    "confirmed_in_frazer": bool(readback) and (readback.get("begin"), readback.get("end")) == (_mmddyy(period[0]), _mmddyy(period[1])),
                    "read_back": readback or None} if t == "payments" else None),
    })
    log(f"{LABEL[t]}: export created ({os.path.basename(path)}, {len(data):,} bytes, {datetime.fromtimestamp(mt):%I:%M:%S %p}).")
    fields = {"export_type": t, "sha256": sha, "generated_at": written_utc, "export_date": bdate,
              "coverage_start": period[0] if t == "payments" else "", "coverage_end": period[1] if t == "payments" else "",
              "original_filename": os.path.basename(path), "generated_local": _local_iso(mt), "timezone": _tz_label(),
              "period_readback": (f"{readback.get('begin')} to {readback.get('end')}" if readback else ("typed by the operator" if t == "payments" else "")),
              "generation": mode}
    try:
        res = api.call("POST", f"/api/frazer-helper/runs/{run_id}/files", fields=fields, file=(name, data))
    except ServerRefused as e:
        out["upload"] = {"outcome": "failed", "message": f"The RTO app refused the file: {e}"}
        _fail(api, run_id, t, "failed", out["upload"]["message"], log, quiet=True)
        log(f"  {LABEL[t]}: upload FAILED. {out['upload']['message']}")
        return out
    except HelperError as e:
        out["upload"] = {"outcome": "failed", "message": str(e)}
        log(f"  {LABEL[t]}: upload FAILED. {e}")
        return out
    out["upload"] = {"outcome": res["outcome"], "message": res.get("message", ""), "upload": res.get("upload"), "rows": res.get("rows"),
                     "compare": res.get("compare"), "absent_earlier_count": res.get("absent_earlier_count")}
    log(f"  {LABEL[t]}: upload {res['outcome']}. {res.get('message', '')}")
    return out


def _fail(api, run_id, t, outcome, message, log, quiet=False):
    if not quiet:
        log(f"  {LABEL[t]}: export {outcome.upper()}. {message}")
    try:
        api.call("POST", f"/api/frazer-helper/runs/{run_id}/failures", fields={"export_type": t, "outcome": outcome if outcome in ("failed", "incomplete", "stale", "missing") else "failed", "message": message})
    except HelperError as e:
        log(f"  (also could not tell the RTO app: {e})")


UPLOAD_WORDS = {"stored": "accepted and stored", "duplicate": "accepted (identical to a file already stored; nothing new to add)",
                "carried": "already accepted earlier today", "stored_with_errors": "stored, but some rows could not be read",
                "rejected": "REFUSED", "layout": "REFUSED (columns changed)", "stale": "REFUSED (not a fresh export)",
                "incomplete": "REFUSED (file incomplete)", "failed": "FAILED", "missing": "not sent", "not sent": "not sent"}


def result_lines(rec):
    """The plain-language result: one block per file with export and upload separately, then reconciliation, then diagnostics."""
    status = rec.get("final_status", "unknown")
    head = {"complete": "COMPLETE. All three exports were created fresh, accepted by the RTO app, and reconciliation is up to date.",
            "partial": "PARTLY DONE. See below for what worked and what did not.",
            "failed": "NOT DONE. Nothing was stored."}.get(status, status.upper())
    lines = ["", f"RESULT: {head}"]
    for t in TYPES:
        f = rec["files"].get(t, {})
        ex, up = f.get("export", {}), f.get("upload", {})
        srv = (rec.get("server_results") or {}).get(t, {})
        lines.append(f"  {LABEL[t]}")
        lines.append(f"      Export: {'created' if ex.get('status') == 'created' else ex.get('status', '?').upper()}"
                     + (f" at {ex['generated_local'][11:19]} ({ex.get('original_filename')})" if ex.get("generated_local") else "")
                     + (f". {ex['message']}" if ex.get("status") not in ("created", "carried") and ex.get("message") else ""))
        outcome = up.get("outcome") or srv.get("outcome") or "not sent"
        lines.append(f"      Upload: {UPLOAD_WORDS.get(outcome, outcome)}" + (f" as {up['upload']}" if up.get("upload") else "")
                     + (f", {up['rows']} rows" if up.get("rows") else "")
                     + (f". {up['message']}" if outcome not in ("stored", "carried") and up.get("message") else ""))
        if t == "payments" and ex.get("period"):
            p = ex["period"]
            lines.append(f"      Period: {p['start']} through {p['end']}" + (" (dates confirmed in Frazer before the export)" if p.get("confirmed_in_frazer") else " (dates as declared; not read back from Frazer)"))
    if rec.get("sync"):
        lines.append(f"  Reconciliation: {rec['sync'].get('state')}. {rec['sync'].get('detail', '')}")
    d = rec.get("diagnostics") or {}
    if d.get("status") == "problem":
        lines.append(f"  Diagnostics: a problem saving screenshots or logs ({'; '.join(d.get('errors', [])[:2])}). This does not change the results above.")
    elif d.get("status") == "saved":
        lines.append(f"  Diagnostics: saved ({d.get('screenshots', 0)} screenshots).")
    if status != "complete":
        lines.append("What to do: fix what is listed above, then click Update RTO Records again. Exports already accepted are not sent twice.")
    return lines


# --- command line and the one-button window ------------------------------------------------------------------------------

def _gui(cfg, autostart):
    import threading
    import tkinter as tk
    from tkinter import scrolledtext
    root = tk.Tk()
    root.title("Update RTO Records")
    w, h = 620, 560
    try:                                                 # bottom-right, clear of the middle of the screen where Frazer's export window opens
        root.geometry(f"{w}x{h}+{max(root.winfo_screenwidth() - w - 12, 0)}+{max(root.winfo_screenheight() - h - 90, 0)}")
    except tk.TclError:
        root.geometry(f"{w}x{h}")
    state = {"stop": False, "folder": cfg["work_folder"]}
    auto = cfg.get("mode") == "auto"
    head = tk.Label(root, text=("Sign in to Hosted Frazer and leave it on its main screen. Then press the button and do not touch the "
                                "mouse or keyboard until it finishes (about two minutes)." if auto else
                                "Press the button, then run each saved export in Frazer when told."), wraplength=w - 30, justify="left")
    head.pack(padx=10, pady=8, anchor="w")
    row = tk.Frame(root)
    row.pack(padx=10, anchor="w")
    btn = tk.Button(row, text="Update RTO Records", font=("Segoe UI", 13, "bold"), padx=14, pady=8)
    btn.pack(side="left")
    alt = tk.Button(row, text="I will run the exports myself", padx=8, pady=6)
    if auto:
        alt.pack(side="left", padx=10)
    result = tk.Label(root, text="", font=("Segoe UI", 11, "bold"), wraplength=w - 30, justify="left")
    result.pack(padx=10, pady=4, anchor="w")
    box = scrolledtext.ScrolledText(root, height=22, state="disabled", font=("Consolas", 9))
    box.pack(fill="both", expand=True, padx=10, pady=6)

    def log(msg):
        def add():
            box.configure(state="normal")
            box.insert("end", msg + "\n")
            box.see("end")
            box.configure(state="disabled")
        root.after(0, add)

    def work(mode):
        try:
            rec = run(cfg, log=log, stop=lambda: state["stop"], mode=mode)
            colour = {"complete": "green", "partial": "#b36b00", "failed": "red"}.get(rec["final_status"], "black")
            text = {"complete": "COMPLETE: RTO records are up to date.", "partial": "PARTLY DONE: read the result below.",
                    "failed": "NOT DONE: read the result below."}.get(rec["final_status"], rec["final_status"])
            root.after(0, lambda: result.configure(text=text, fg=colour))
        except HelperError as e:
            log(f"STOPPED: {e}")
            root.after(0, lambda: result.configure(text=f"Could not run: {e}", fg="red"))
        except Exception as e:                           # noqa: BLE001  never leave the window silent
            log(f"STOPPED by an unexpected error: {e!r}")
            root.after(0, lambda: result.configure(text="Stopped by an unexpected error. Nothing was reported as done.", fg="red"))
        finally:
            def done():
                btn.configure(state="normal", text="Update RTO Records")
                alt.configure(state="normal")
                root.lift()
            root.after(0, done)

    def go(mode=None):
        state["stop"] = False
        btn.configure(state="disabled", text="Working...")
        alt.configure(state="disabled")
        result.configure(text="")
        threading.Thread(target=work, args=(mode or cfg.get("mode", "manual"),), daemon=True).start()
    btn.configure(command=go)
    alt.configure(command=lambda: go("manual"))
    if autostart:
        root.after(600, go)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Update RTO Records: export from Frazer and send to the RTO app.")
    ap.add_argument("command", choices=["window", "run", "check"], nargs="?", default="window")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ap.add_argument("--fresh", action="store_true", help="start a new run instead of continuing today's unfinished one")
    ap.add_argument("--go", action="store_true", help="window: start the update as soon as the window opens")
    ap.add_argument("--manual", action="store_true", help="the operator runs each saved export; the helper waits")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.command == "check":
            for k in ("landing_folder", "work_folder"):
                print(f"{k}: {cfg[k]}  ->  {'exists' if os.path.isdir(cfg[k]) else 'NOT FOUND'}")
            if cfg["mode"] == "auto":
                print(f"driver: {cfg['driver']}  ->  {'exists' if os.path.isfile(cfg['driver']) else 'NOT FOUND'}")
            print("server status:", json.dumps(Api(cfg["server"], cfg["token"]).call("GET", "/api/frazer-helper/status"), indent=1))
            return 0
        if a.command == "window":
            _gui(cfg, a.go)
            return 0
        rec = run(cfg, fresh=a.fresh, mode="manual" if a.manual else None,
                  ask=lambda t, label: print(f"\n>>> Now run '{label}' in Frazer ({LABEL[t]}). Waiting for the file..."))
        return {"complete": 0, "partial": 2, "failed": 3}.get(rec["final_status"], 3)
    except HelperError as e:
        print("STOPPED:", e)
        return 4


if __name__ == "__main__":
    sys.exit(main())
