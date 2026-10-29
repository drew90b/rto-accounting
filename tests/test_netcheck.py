"""Outage grouping for deploy/rto-netcheck.py (pure functions; no network, no database)."""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

_spec = importlib.util.spec_from_file_location(
    "rto_netcheck", pathlib.Path(__file__).resolve().parents[1] / "deploy" / "rto-netcheck.py")
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def sample(n, bad=(), boot="a"):
    checks = {c: {"ok": c not in bad} for c in nc.CHECKS}
    for c in bad:
        checks[c]["err"] = "timeout"
    ts = (T0 + timedelta(seconds=30 * n)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ts": ts, "boot": boot, "up": 30 * n, "checks": checks}


def test_overlapping_failures_form_one_outage_with_duration():
    s = [sample(0), sample(1, ["gateway", "dns"]), sample(2, ["dns"]), sample(3)]
    outages, gaps = nc.analyze(s)
    assert not gaps and len(outages) == 1
    o = outages[0]
    assert o["start"] == T0 + timedelta(seconds=30)
    assert o["end"] == T0 + timedelta(seconds=90)
    assert set(o["checks"]) == {"gateway", "dns"} and o["peak"] == 2
    assert o["checks"]["dns"]["n"] == 2 and o["checks"]["gateway"]["n"] == 1


def test_single_failed_sample_is_marked_as_blip_evidence():
    outages, _ = nc.analyze([sample(0), sample(1, ["app"]), sample(2)])
    assert outages[0]["samples"] == 1
    assert any("blip" in line for line in nc.explain(outages[0]))


def test_gap_closes_open_outage_without_inventing_an_end():
    s = [sample(0, ["gateway"]), sample(1, ["gateway"]), sample(10, boot="b")]
    outages, gaps = nc.analyze(s)
    assert outages[0]["end"] is None and outages[0]["ended_by"] == "gap"
    assert len(gaps) == 1 and gaps[0]["boot_changed"]


def test_trailing_failure_is_ongoing():
    outages, _ = nc.analyze([sample(0), sample(1, ["tailscale"])])
    assert outages[0]["ended_by"] == "ongoing" and outages[0]["end"] is None
