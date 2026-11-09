"""
Seed a DISPOSABLE database with a synthetic demo for vehicle reconciliation. Every vehicle, VIN, stock number and amount is
invented. Refuses to run unless the database name contains "demo" or "test", and never touches Frazer files on disk.

  DATABASE_URL=... python -m app.scripts.vehicle_recon_demo seed  CREDENTIALS_FILE
  DATABASE_URL=... python -m app.scripts.vehicle_recon_demo newer      # loads a later synthetic list (new contradictory evidence)
"""
import secrets
import sys
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import text

from app.config import DATABASE_URL
from app.database import SessionLocal
from app.models.enums import BusinessLine, PurchaseCategory, UnitStatus, UnitType, UserRole
from app.models.unit import Unit
from app.models.user import User
from app.models.vendor import Vendor
from app.services import user_service
from app.services import vehicle_recon_service as svc
from app.services import vr_sources as vr
from tests import vr_fixtures as f      # demo only: synthetic builders with the real Frazer headers


def _guard():
    name = (urlparse(DATABASE_URL).path or "").lstrip("/")
    if "demo" not in name and "test" not in name:
        raise SystemExit(f"Refusing: database '{name}' is not marked demo/test.")


def vehicles(extra=()):
    rows = [
        f.vehicle("20063", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Purchase Date": "01/05/26", "Total Cost": "4800", "Original Cost": "4800"}),
        f.vehicle("20118", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Purchase Date": "11/02/26", "Vehicle Source": "Repossessio", "Total Cost": "0", "Original Cost": "0"}),
        f.vehicle("20029", 29, **{"Vehicle Make": "CHEVISH", "Vehicle Model": "TRUCK", "Purchase Date": "03/10/26"}),
        f.vehicle("20116", 29, **{"Vehicle Make": "CHEVISH", "Vehicle Model": "TRUCK", "Purchase Date": "11/01/26", "Vehicle Source": "Repossessio", "Total Cost": "0", "Original Cost": "0"}),
        f.vehicle("00457", 457, **{"Vehicle Make": "TOYOTISH", "Vehicle Model": "COMPACT"}),
        f.vehicle("F36140", 36, **{"Vehicle Make": "HONDISH", "Vehicle Model": "VAN"}),
        f.vehicle("20300", 300, **{"Vehicle Make": "NISSISH", "Vehicle Model": "SUV", "Total Cost": "5000", "Original Cost": "5000"}),
        f.vehicle("20301", 301, **{"Vehicle Make": "NISSISH", "Vehicle Model": "COUPE"}),
        f.vehicle("20400", 400, **{"Vehicle Make": "KIAISH", "Vehicle Model": "HATCH"}),
        f.vehicle("20401", 401, **{"Vehicle Make": "KIAISH", "Vehicle Model": "WAGON", "Total Cost": "", "Original Cost": ""}),
        f.vehicle("20500", 500, **{"Vehicle Make": "DODGISH", "Vehicle Model": "VAN", "Total Cost": "0", "Original Cost": "0"}),
        f.vehicle("20501", 501), f.vehicle("20502", 502), f.vehicle("20503", 503),
    ]
    return rows + list(extra)


def contracts():
    return [f.contract("20063", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Sale Date": "02/20/26"}),
            f.contract("20118", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Sale Date": "11/04/26"}),
            f.contract("20400", 400, **{"Vehicle Make": "KIAISH", "Vehicle Model": "HATCH", "Sale Date": "10/12/26"}),
            f.contract("20500", 500, **{"Vehicle Make": "DODGISH", "Vehicle Model": "VAN", "Sale Date": "09/01/26"}),
            f.contract("20501", 501), f.contract("20502", 502), f.contract("20503", 503)]


def _unit(db, n, status, cost):
    u = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, vin_serial=f.vin(n), year=2015, make="X", model="Y",
             status=status, acquisition_cost=Decimal(cost))
    db.add(u)
    db.flush()
    u.unit_id = f"U-{u.id:04d}"
    return u


def _purchase(db, vendor, stock, unit, amount, ref):
    # Raw SQL on purpose: the migrated PostgreSQL schema stores purchases.category as varchar while the ORM model declares a
    # native enum, so an ORM insert fails on a migrated database. That mismatch pre-dates this feature (see the handoff).
    db.execute(text(
        "INSERT INTO purchases (purchase_id, purchase_date, purchaser, vendor_id, amount, category, stock_number, unit_id, "
        "review_status, receipt_attached, created_unit, created_at, updated_at) VALUES (:ref, :d, 'Demo', :v, :amt, "
        "'auction_car_rto', :stock, :unit, 'pending', false, false, now(), now())"),
        {"ref": ref, "d": date(2026, 10, 2), "v": vendor.id, "amt": Decimal(amount), "stock": stock, "unit": unit.id if unit else None})


def seed(cred_path):
    _guard()
    db = SessionLocal()
    try:
        creds = []
        for name, role in (("demo-drew", "admin"), ("demo-steve", "admin"), ("demo-belinda", "employee")):
            pw = "vr-demo-" + secrets.token_hex(4)
            user_service.create_user(db, username=name, display_name=name.replace("demo-", "").title(), role=UserRole(role),
                                     password=pw, must_change_password=False)
            creds.append(f"{name}  {pw}  ({role})")
        v = Vendor(name="DEMO AUCTION")
        db.add(v)
        db.flush()
        u300 = _unit(db, 300, UnitStatus.frontline_ready, "4100.00")      # agrees with Frazer
        u301 = _unit(db, 301, UnitStatus.frontline_ready, "3900.00")
        u400 = _unit(db, 400, UnitStatus.frontline_ready, "2500.00")      # app says available; Frazer lists a contract
        _purchase(db, v, "20300", u300, "4100.00", "PO-DEMO-1")           # stock + unit VIN agree
        _purchase(db, v, "20300", u301, "3900.00", "PO-DEMO-2")           # stock says 20300, unit VIN says 20301
        _purchase(db, v, "457", None, "2200.00", "PO-DEMO-3")             # near-match of 00457
        _purchase(db, v, "99999", None, "150.00", "PO-DEMO-4")            # a stock nobody lists: no question
        db.commit()
        vr.load_frazer_vehicles(db, f.vehicles_csv(vehicles()), "SYNTHETIC-vehicles-2026-11-08.csv", date(2026, 11, 8))
        vr.load_frazer_contracts(db, f.contracts_csv(contracts()), "SYNTHETIC-balances-2026-11-08.csv", date(2026, 11, 8))
        db.commit()
        print("refresh:", svc.refresh(db, date(2026, 11, 8)))
        db.commit()
        belinda = db.query(User).filter_by(username="demo-belinda").one()
        admin = db.query(User).filter_by(username="demo-drew").one()
        from app.models.vehicle_recon import VrQuestion
        for q in db.query(VrQuestion).filter(VrQuestion.kind.in_(["shared_vin", "status_conflict"])).order_by(VrQuestion.id):
            if q.subject_json.get("stocks") in (["20029", "20116"], ["20400"]):
                svc.assign(db, admin, q.ref, belinda.id)
        db.commit()
        with open(cred_path, "w") as fh:
            fh.write("SYNTHETIC DEMO - not real data\n" + "\n".join(creds) + "\n")
    finally:
        db.close()


def newer():
    """A later synthetic vehicle list in which a third stock number carries the 20063 VIN: new contradictory evidence."""
    _guard()
    db = SessionLocal()
    try:
        extra = [f.vehicle("20777", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Purchase Date": "11/12/26"})]
        r = vr.load_frazer_vehicles(db, f.vehicles_csv(vehicles(extra)), "SYNTHETIC-vehicles-2026-11-15.csv", date(2026, 11, 15))
        print("loaded", {k: r[k] for k in ("new", "changed", "unchanged")})
        print("refresh:", svc.refresh(db, date(2026, 11, 15)))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    {"seed": lambda: seed(sys.argv[2]), "newer": newer}[sys.argv[1]]()
