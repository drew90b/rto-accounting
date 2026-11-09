"""
Load Frazer exports into the vehicle reconciliation records, refresh the questions, or just CHECK files.

  python -m app.scripts.vehicle_recon_load --check --vehicles FILE [--contracts FILE]
      Reads the files read-only into a throwaway in-memory database and prints what reconciliation would show.
      Touches no real database and writes nothing. Prints stock numbers and counts only; never customer names or balances.

  python -m app.scripts.vehicle_recon_load --confirm-database NAME --source-date 2026-11-08 \
         [--vehicles FILE] [--contracts FILE] [--refresh]
      Loads into the database named by DATABASE_URL (the name must be typed to confirm), then optionally refreshes the
      questions. Writes only vr_* tables. Loading identical bytes again is a no-op.

The original files are only read. They are never copied into the repository or modified.
"""
import argparse
import sys
from collections import Counter
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _db_name(url: str) -> str:
    return (urlparse(url).path or "").lstrip("/").split("?")[0]


def run_check(args) -> int:
    import app.models  # noqa: registers tables
    from app.database import Base
    from app.services import vehicle_recon_service as svc
    from app.services import vr_engine as eng
    from app.services import vr_sources as vr

    eng_ = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng_)
    db = sessionmaker(bind=eng_)()
    d = date.fromisoformat(args.source_date)
    if args.vehicles:
        r = vr.load_frazer_vehicles(db, _read(args.vehicles), args.vehicles.rsplit("/", 1)[-1], d)
        print(f"Vehicle list: {r['new']} records")
    if args.contracts:
        r = vr.load_frazer_contracts(db, _read(args.contracts), args.contracts.rsplit("/", 1)[-1], d)
        print(f"Customer-balance list: {r['new']} contract records (name and balance not stored)")
    db.commit()
    s = svc.refresh(db, d, observe_app=False)
    rows, w, _ = svc.reconciled(db)
    print(f"Episodes (distinct stock numbers): {len(eng.episode_stocks(w))}")
    print("Shared VINs:", {v[-6:]: ss for v, ss in eng.shared_vin_sets(w).items()})
    if args.contracts and args.vehicles:
        both = [s_ for s_ in w.contracts if s_ in w.vehicles]
        vin_ok = sum(1 for s_ in both if (w.contracts[s_].vin or "").upper() == (w.vehicles[s_].vin or "").upper())
        print(f"Contracts whose stock number is in the vehicle list: {len(both)} of {len(w.contracts)}; VIN agrees on {vin_ok}")
    print("Zero reported cost:", sum(1 for v in w.vehicles.values() if v.reported_cost is not None and v.reported_cost == 0),
          "| cost not reported:", sum(1 for v in w.vehicles.values() if v.reported_cost is None))
    print("Row states:", dict(Counter(r.state for r in rows)))
    from app.models.vehicle_recon import VrQuestion
    print("Questions raised:", s["raised"], dict(Counter(q.kind for q in db.query(VrQuestion))))
    return 0


def run_load(args) -> int:
    from app.config import DATABASE_URL
    from app.database import SessionLocal
    from app.services import vehicle_recon_service as svc
    from app.services import vr_sources as vr

    target = _db_name(DATABASE_URL)
    if args.confirm_database != target:
        print(f"Refusing: DATABASE_URL points at database '{target}'. Pass --confirm-database {target} to load into it.")
        return 2
    d = date.fromisoformat(args.source_date)
    db = SessionLocal()
    try:
        for kind, path, fn in (("vehicle list", args.vehicles, vr.load_frazer_vehicles),
                               ("customer-balance list", args.contracts, vr.load_frazer_contracts)):
            if path:
                r = fn(db, _read(path), path.rsplit("/", 1)[-1], d, cutoff_note=args.cutoff or "Unknown")
                print(f"{kind}: {'loaded' if r['created'] else 'already loaded (no change)'} "
                      f"new={r['new']} changed={r['changed']} unchanged={r['unchanged']}")
        if args.refresh:
            print("questions:", svc.refresh(db, date.today()))
        db.commit()
    except Exception as e:  # noqa
        db.rollback()
        print(f"Nothing was saved: {e}")
        return 1
    finally:
        db.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicles")
    ap.add_argument("--contracts")
    ap.add_argument("--source-date", default=str(date.today()), help="the export date (a label; the true cutoff stays Unknown)")
    ap.add_argument("--cutoff", help="a recorded actual cutoff, if known")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--confirm-database")
    args = ap.parse_args(argv)
    if not (args.vehicles or args.contracts):
        ap.error("give --vehicles and/or --contracts")
    return run_check(args) if args.check else run_load(args)


if __name__ == "__main__":
    sys.exit(main())
