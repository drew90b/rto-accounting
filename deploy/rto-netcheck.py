#!/usr/bin/env python3
"""Outage diagnostics for the RTO server (read-only; changes nothing).

  rto-netcheck.py run      log one sample every 30 s (the systemd service)
  rto-netcheck.py once     take one sample, print it, write nothing
  rto-netcheck.py report   outages in the last 24 h (--hours N, --utc)

Each sample records, with bounded timeouts: the app's /login (status and
time, on the address uvicorn is actually bound to), the default gateway,
internet by IP and by DNS (separately), Tailscale, and the rto-web unit.
It stores statuses, timings, addresses and short error class names only:
no cookies, headers, response bodies, credentials or business records.

A failed probe is evidence, not proof of a cause. The report separates what
was observed from explanations that are not confirmed. Details and removal:
README.md, "Outage diagnostics".
"""
import argparse
import concurrent.futures as cf
import glob
import gzip
import http.client
import json
import os
import random
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

INTERVAL = 30
GAP = 90  # seconds without a sample that counts as "not observed"
UNIT = "rto-web"
LOG_DIR = os.environ.get("RTO_NETCHECK_DIR", "/var/log/rto-netcheck")
CHECKS = ("app", "gateway", "internet_ip", "dns", "tailscale", "service")
SLOW_MS = 2000
CHICAGO = ZoneInfo("America/Chicago")


def sh(cmd, timeout):
    """Run a command; return stdout, or None on timeout/error."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def ping(host, wait=2):
    out = sh(["ping", "-n", "-c", "1", "-W", str(wait), host], wait + 2)
    m = re.search(r"time=([\d.]+) ms", out or "")
    return float(m.group(1)) if m else None


def probe_app():
    pid = (sh(["systemctl", "show", UNIT, "-p", "MainPID", "--value"], 4) or "").strip()
    out = sh(["ss", "-ltnpH"], 4) or ""
    found = None
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        if not m:
            continue
        if m.group(2) == pid or (found is None and m.group(1) == "uvicorn"):
            found = f[3]
            if m.group(2) == pid:
                break
    if not found:
        return {"ok": False, "err": "no uvicorn listener"}
    host, port = found.rsplit(":", 1)
    host = host.strip("[]").split("%")[0]
    probe_host = {"0.0.0.0": "127.0.0.1", "::": "::1", "*": "127.0.0.1"}.get(host, host)
    res = {"ok": False, "addr": f"{host}:{port}"}
    t0 = time.monotonic()
    try:
        c = http.client.HTTPConnection(probe_host, int(port), timeout=5)
        c.request("GET", "/login", headers={"User-Agent": "rto-netcheck"})
        r = c.getresponse()
        r.read(65536)
        c.close()
        res["status"] = r.status
        res["ok"] = 200 <= r.status < 400
    except Exception as e:  # class name only; messages may carry details
        res["err"] = "timeout" if isinstance(e, socket.timeout) else type(e).__name__
    res["ms"] = round((time.monotonic() - t0) * 1000)
    return res


def probe_gateway():
    out = sh(["ip", "-4", "route", "show", "default"], 4) or ""
    m = re.search(r"default via (\S+) dev (\S+)(?:.*? src (\S+))?", out)
    if not m:
        return {"ok": False, "err": "no default route"}
    gw, dev, src = m.groups()
    ms = ping(gw)
    res = {"ok": ms is not None, "gw": gw, "dev": dev, "src": src}
    if ms is not None:
        res["ms"] = ms
    return res


def probe_internet_ip():
    for n, target in enumerate(("1.1.1.1", "8.8.8.8"), 1):
        ms = ping(target)
        if ms is not None:
            return {"ok": True, "target": target, "ms": ms, "tried": n}
    return {"ok": False, "err": "no reply from 1.1.1.1 or 8.8.8.8"}


def probe_dns():
    """Ask the system resolver for a random name under example.com.

    A random label can't be answered from cache, so success means the resolver
    reached an upstream server. NXDOMAIN counts as success; SERVFAIL or no
    answer is a failure.
    """
    server = "127.0.0.53"
    try:
        for line in open("/etc/resolv.conf"):
            if line.startswith("nameserver"):
                server = line.split()[1]
                break
    except OSError:
        pass
    qid = random.randrange(65536)
    name = f"nc{random.getrandbits(40):010x}.example.com"
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    q += struct.pack(">HH", 1, 1)
    res = {"ok": False, "server": server}
    t0 = time.monotonic()
    s = socket.socket(socket.AF_INET6 if ":" in server else socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(1024)
        rid, flags = struct.unpack(">HH", data[:4])
        if rid == qid:
            res["rcode"] = flags & 0xF
            res["ok"] = res["rcode"] in (0, 3)
        else:
            res["err"] = "mismatched id"
    except Exception as e:
        res["err"] = "timeout" if isinstance(e, socket.timeout) else type(e).__name__
    finally:
        s.close()
    res["ms"] = round((time.monotonic() - t0) * 1000)
    return res


def probe_tailscale():
    out = sh(["tailscale", "status", "--json"], 6)
    if out is None:
        return {"ok": False, "err": "tailscale status failed or timed out"}
    try:
        d = json.loads(out)
        peers = (d.get("Peer") or {}).values()
        res = {"state": d.get("BackendState"), "self_online": (d.get("Self") or {}).get("Online"),
               "peers_online": sum(1 for p in peers if p.get("Online")), "peers": len(peers),
               "health": [str(h)[:160] for h in (d.get("Health") or [])[:3]]}
    except (ValueError, AttributeError):
        return {"ok": False, "err": "unparseable status"}
    res["ok"] = res["state"] == "Running" and res["self_online"] is True
    return res


def probe_service():
    out = sh(["systemctl", "show", UNIT, "-p", "ActiveState", "-p", "SubState",
              "-p", "NRestarts", "-p", "MainPID"], 4)
    if out is None:
        return {"ok": False, "err": "systemctl failed"}
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {"ok": kv.get("ActiveState") == "active" and kv.get("SubState") == "running",
            "active": kv.get("ActiveState"), "sub": kv.get("SubState"),
            "restarts": int(kv.get("NRestarts") or 0), "pid": int(kv.get("MainPID") or 0)}


PROBES = {"app": probe_app, "gateway": probe_gateway, "internet_ip": probe_internet_ip,
          "dns": probe_dns, "tailscale": probe_tailscale, "service": probe_service}


def safe(fn):
    try:
        return fn()
    except Exception as e:
        return {"ok": False, "err": "probe error: " + type(e).__name__}


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def take_sample():
    now = time.time()
    with cf.ThreadPoolExecutor(len(PROBES)) as ex:
        futs = {k: ex.submit(safe, fn) for k, fn in PROBES.items()}
        checks = {k: f.result() for k, f in futs.items()}
    try:
        boot = open("/proc/sys/kernel/random/boot_id").read().strip()[:8]
        up = int(float(open("/proc/uptime").read().split()[0]))
    except OSError:
        boot, up = "?", -1
    return {"ts": utc(now), "boot": boot, "up": up, "checks": checks}


def append(name, obj, log_dir):
    fd = os.open(os.path.join(log_dir, name), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, (json.dumps(obj, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def failing(sample):
    return sorted(k for k, v in sample["checks"].items() if not v.get("ok"))


def run(log_dir, interval):
    os.makedirs(log_dir, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    since = {}  # check -> epoch it was first seen failing
    outage = None  # {"start": epoch, "checks": set, "peak": int}
    append("events.jsonl", {"ts": utc(time.time()), "event": "start", "interval": interval}, log_dir)
    try:
        nxt = time.monotonic()
        while True:
            s = take_sample()
            append("samples.jsonl", s, log_dir)
            t = parse_ts(s["ts"]).timestamp()
            bad = failing(s)
            for c in CHECKS:
                if c in bad and c not in since:
                    since[c] = t
                    append("events.jsonl", {"ts": s["ts"], "event": "fail", "check": c,
                                            "failing": bad, "detail": s["checks"][c]}, log_dir)
                elif c not in bad and c in since:
                    append("events.jsonl", {"ts": s["ts"], "event": "recover", "check": c,
                                            "after_s": round(t - since.pop(c)), "failing": bad}, log_dir)
            if bad and outage is None:
                outage = {"start": t, "checks": set(), "peak": 0}
                append("events.jsonl", {"ts": s["ts"], "event": "outage_start"}, log_dir)
            if outage is not None:
                outage["checks"].update(bad)
                outage["peak"] = max(outage["peak"], len(bad))
                if not bad:
                    append("events.jsonl", {"ts": s["ts"], "event": "outage_end",
                                            "duration_s": round(t - outage["start"]),
                                            "checks": sorted(outage["checks"]),
                                            "max_simultaneous": outage["peak"]}, log_dir)
                    outage = None
            nxt += interval
            delay = nxt - time.monotonic()
            if delay < 0:
                nxt = time.monotonic()
                delay = 0
            time.sleep(delay)
    finally:
        append("events.jsonl", {"ts": utc(time.time()), "event": "stop"}, log_dir)


# ---- report ---------------------------------------------------------------

def read_jsonl(log_dir, base, since):
    rows = []
    for path in glob.glob(os.path.join(log_dir, base + "*")):
        if os.path.getmtime(path) < since.timestamp() - 60:
            continue
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if parse_ts(r["ts"]) >= since:
                        rows.append(r)
                except (ValueError, KeyError):
                    continue
    return sorted(rows, key=lambda r: r["ts"])


def analyze(samples, gap=GAP, interval=INTERVAL):
    """Return (outages, gaps). Pure function of the samples; no I/O."""
    outages, gaps, cur, prev = [], [], None, None

    def close(end, how):
        nonlocal cur
        if cur:
            cur["end"], cur["ended_by"] = end, how
            cur["last_fail"] = cur["last_fail_ts"]
            outages.append(cur)
            cur = None

    for s in samples:
        t = parse_ts(s["ts"])
        if prev is not None:
            dt = (t - parse_ts(prev["ts"])).total_seconds()
            if dt > gap or s["boot"] != prev["boot"]:
                gaps.append({"start": parse_ts(prev["ts"]), "end": t, "boot_changed": s["boot"] != prev["boot"],
                             "up_after": s["up"]})
                close(None, "gap")
        bad = failing(s)
        if bad:
            if cur is None:
                cur = {"start": t, "checks": {}, "samples": 0, "peak": 0, "last_fail_ts": t}
            cur["samples"] += 1
            cur["peak"] = max(cur["peak"], len(bad))
            cur["last_fail_ts"] = t
            for c in bad:
                d = cur["checks"].setdefault(c, {"first": t, "last": t, "n": 0, "err": None})
                d["last"], d["n"] = t, d["n"] + 1
                d["err"] = d["err"] or s["checks"][c].get("err") or s["checks"][c].get("status")
        elif cur is not None:
            close(t, "recovered")
        prev = s
    if cur is not None:
        cur["end"], cur["ended_by"], cur["last_fail"] = None, "ongoing", cur["last_fail_ts"]
        outages.append(cur)
    return outages, gaps


def explain(o, interval=INTERVAL):
    """Unconfirmed explanations for an outage, from its failure pattern."""
    c = set(o["checks"])
    out = []
    if "gateway" in c:
        out.append("Gateway unreachable from this VM. The extender's Wi-Fi link, the router, "
                   "or the Proxmox host's network could each do this; the logger can't tell which. "
                   "Simultaneous failures on another device would suggest a shared or server-side "
                   "problem, not prove one.")
    elif "internet_ip" in c:
        out.append("Gateway answered but public IPs did not: points past the local network "
                   "(router uplink or ISP), not at the extender link itself.")
    if "dns" in c and "internet_ip" not in c and "gateway" not in c:
        out.append("DNS failed while IP connectivity worked: resolver or upstream DNS problem.")
    if "tailscale" in c and not ({"gateway", "internet_ip"} & c):
        out.append("Tailscale reported unhealthy while the network was up: control-plane/DERP "
                   "reachability or tailscaled itself.")
    if c <= {"app", "service"}:
        out.append("Only the app/service failed while the network checks passed: look at "
                   "`journalctl -u rto-web` around this time.")
    if o["samples"] == 1:
        out.append("Single failed sample: could be a transient probe failure; treat as a blip.")
    return out


def fmt_dur(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if m else "") + (f"{s}s" if s or not (h or m) else "")


def journal(args, since, timeout=20):
    out = sh(["journalctl", "--no-pager", "-o", "short-iso", "--since",
              since.strftime("%Y-%m-%d %H:%M:%S UTC")] + args, timeout) or ""
    return [l for l in out.splitlines() if not l.startswith("-- ")]


def report(log_dir, hours, use_utc, with_journal=True, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    tz = timezone.utc if use_utc else CHICAGO

    def T(dt, date=True):
        return dt.astimezone(tz).strftime(("%b %d " if date else "") + "%H:%M:%S %Z")

    samples = read_jsonl(log_dir, "samples.jsonl", since)
    events = read_jsonl(log_dir, "events.jsonl", since)
    print(f"RTO outage report: last {hours:g} h, ending {T(now)}")
    if not samples:
        print(f"\nNo samples in {log_dir} for this window. Is rto-netcheck running? "
              "`systemctl status rto-netcheck`")
        return 1
    outages, gaps = analyze(samples)
    span = (now - max(since, parse_ts(samples[0]["ts"]))).total_seconds()
    expected = max(1, span / INTERVAL)
    print(f"Samples: {len(samples)} of ~{expected:.0f} expected "
          f"({100 * len(samples) / expected:.0f}%), first {T(parse_ts(samples[0]['ts']))}, "
          f"last {T(parse_ts(samples[-1]['ts']))}")

    print("\n== Outages (any check failing) ==")
    if not outages:
        print("None observed.")
    for i, o in enumerate(outages, 1):
        if o["end"]:
            dur, end = (o["end"] - o["start"]).total_seconds(), T(o["end"], False)
        else:
            dur = (o["last_fail"] - o["start"]).total_seconds() + INTERVAL
            end = "ongoing" if o["ended_by"] == "ongoing" else "unknown (observation gap)"
        pre = "at least " if not o["end"] else ""
        label = "  [blip: 1 sample]" if o["samples"] == 1 else ""
        print(f"\n#{i}  {T(o['start'])}  ->  {end}   {pre}{fmt_dur(dur)}{label}")
        for c, d in sorted(o["checks"].items(), key=lambda kv: kv[1]["first"]):
            why = f"  ({d['err']})" if d["err"] else ""
            print(f"      {c:<12} failed {d['n']} sample(s), {T(d['first'], False)} to {T(d['last'], False)}{why}")
        if o["peak"] > 1:
            print(f"      overlap: up to {o['peak']} checks failing at once")
        for line in explain(o):
            print(f"      unconfirmed: {line}")

    print("\n== Gaps with no samples (not observed) ==")
    stops = [parse_ts(e["ts"]) for e in events if e["event"] == "stop"]
    if not gaps:
        print("None.")
    for g in gaps:
        clean = any(g["start"] <= s <= g["start"] + timedelta(seconds=GAP) for s in stops)
        kind = ("host rebooted" if g["boot_changed"] else "same boot") + \
               (", logger stopped cleanly" if clean else ", no clean-stop record")
        print(f"  {T(g['start'])} -> {T(g['end'])}  {fmt_dur((g['end'] - g['start']).total_seconds())}  ({kind})")
    if gaps:
        print("  The logger cannot see its own host losing power: such a gap is consistent with power "
              "loss, a frozen/paused VM, a hung host, or a stopped service. It cannot distinguish them.")

    print("\n== Per-check totals ==")
    for c in CHECKS:
        f = [s for s in samples if not s["checks"][c].get("ok")]
        print(f"  {c:<12} failed {len(f):>4} of {len(samples)}")
    ms = [s["checks"]["app"]["ms"] for s in samples if s["checks"]["app"].get("ok")]
    if ms:
        ms.sort()
        slow = sum(1 for m in ms if m > SLOW_MS)
        print(f"  app /login   median {ms[len(ms) // 2]} ms, max {ms[-1]} ms, {slow} over {SLOW_MS} ms")
    nets = []
    for s in samples:
        g = s["checks"]["gateway"]
        key = (g.get("src"), g.get("gw"), g.get("dev"))
        if key != (None, None, None) and (not nets or nets[-1][0] != key):
            nets.append((key, parse_ts(s["ts"])))
    if len(nets) > 1:
        print("  OBSERVED network identity changes: " + "; ".join(
            f"{T(t)} src={k[0]} gw={k[1]}" for k, t in nets))

    print("\n== Service and boots ==")
    rs = [(parse_ts(s["ts"]), s["boot"], s["checks"]["service"].get("restarts")) for s in samples]
    rises = [(b[0], a[2], b[2]) for a, b in zip(rs, rs[1:])
             if a[1] == b[1] and None not in (a[2], b[2]) and b[2] > a[2]]
    print("  rto-web restart counter rises: " + (
        "; ".join(f"{T(t)} {a}->{b}" for t, a, b in rises) if rises else "none"))
    print(f"  boots seen in samples: {len({s['boot'] for s in samples})}; "
          f"uptime now {fmt_dur(samples[-1]['up'])}")
    if with_journal:
        life = [l for l in journal(["-u", UNIT], since) if " systemd[1]: " in l]
        n_err = len(journal(["-u", UNIT, "-t", "uvicorn", "-p", "err"], since))
        print(f"  rto-web unit messages in window: {len(life)}"
              + ("" if not life else " (latest 12):"))
        for l in life[-12:]:
            print("    " + l[:160])
        print(f"  app error-level log lines: {n_err} (read with `journalctl -u {UNIT} -p err`)")
        net = [l for l in journal(["-u", "systemd-networkd"], since)
               if re.search(r"ens\d+: (DHCP|Lost carrier|Gained carrier|Link )", l)
               and "DHCPv6" not in l and "delegated" not in l]
        print(f"  ethernet/DHCP events from systemd-networkd: {len(net)}" + ("" if not net else " (latest 12):"))
        for l in net[-12:]:
            print("    " + l[:160])

    print("\n== Observed versus unconfirmed ==")
    print(f"  Observed: {len(outages)} outage(s), {len(gaps)} observation gap(s) in the window.")
    print("  Observed: the app check probes the local socket, so it passing says the process answers "
          "on this machine, not that a remote device can reach it.")
    print("  Unconfirmed: any cause named above. Timestamps are accurate to one 30 s sample; "
          "durations can be off by that much.")
    print("  Not visible from here: the Proxmox host, the extender, the router, and the laptop's "
          "path over Tailscale. See README \"Outage diagnostics\" for the independent check.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("cmd", nargs="?", default="report", choices=("run", "once", "report"))
    p.add_argument("--dir", default=LOG_DIR)
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--utc", action="store_true", help="print UTC instead of America/Chicago")
    p.add_argument("--interval", type=int, default=INTERVAL)
    p.add_argument("--no-journal", action="store_true")
    a = p.parse_args()
    if a.cmd == "run":
        run(a.dir, a.interval)
    elif a.cmd == "once":
        print(json.dumps(take_sample(), indent=1))
    else:
        sys.exit(report(a.dir, a.hours, a.utc, not a.no_journal))


if __name__ == "__main__":
    main()
