"""
Seed the COMBINED synthetic demo through the real screens (HTTP), not the database: ordinary purchase entry, Frazer intake upload,
vehicle reconciliation, assignment. Every value is invented. Refuses a database that is not marked demo/test.

  DATABASE_URL=... python -m app.scripts.vehicle_recon_demo_http users CREDENTIALS_FILE
  (start the app on BASE_URL, then)
  python -m app.scripts.vehicle_recon_demo_http seed CREDENTIALS_FILE BASE_URL
  python -m app.scripts.vehicle_recon_demo_http newer CREDENTIALS_FILE BASE_URL    # later upload: new contradictory evidence
"""
import secrets
import sys
from urllib.parse import urlparse

from app.scripts.vehicle_recon_demo import _guard, contracts, vehicles
from tests import vr_fixtures as f

USERS = (("demo-drew", "admin"), ("demo-steve", "admin"), ("demo-belinda", "employee"))


def make_users(cred_path):
    _guard()
    from app.config import DATABASE_URL  # noqa: F401
    from app.database import SessionLocal
    from app.models.enums import UserRole
    from app.services import user_service
    db = SessionLocal()
    lines = []
    for name, role in USERS:
        pw = "vr-demo-" + secrets.token_hex(4)
        user_service.create_user(db, username=name, display_name=name.replace("demo-", "").title(), role=UserRole(role),
                                 password=pw, must_change_password=False)
        lines.append(f"{name}  {pw}  ({role})")
    db.commit()
    open(cred_path, "w").write("SYNTHETIC DEMO - not real data\n" + "\n".join(lines) + "\n")


def _login(httpx, base, cred_path, user):
    pw = {l.split()[0]: l.split()[1] for l in open(cred_path) if l.startswith("demo-")}[user]
    c = httpx.Client(base_url=base, headers={"Origin": base}, follow_redirects=False, timeout=30)
    assert c.post("/login", data={"username": user, "password": pw}).status_code == 303, "sign-in failed"
    return c


def _purchase(c, **kw):
    data = {"purchaser": "Demo Buyer", "purchase_date": "2026-10-02", "vendor": "DEMO AUCTION", "amount": "1000.00",
            "category": "AUCTION - Purchased Cars (RTO)", "site_location": "Eunice", "stock_number": "", "mileage": "",
            "description": "", "entered_by": "", "payment_source": "Cash"}
    data.update(kw)
    r = c.post("/purchases/new", data=data)
    assert r.status_code == 303, (kw, r.status_code, r.text[:300])


def seed(cred_path, base):
    import httpx
    drew, bel = _login(httpx, base, cred_path, "demo-drew"), _login(httpx, base, cred_path, "demo-belinda")
    # Ordinary purchase entry, one at a time through the real form (an employee enters two of them).
    _purchase(drew, stock_number="20300", vin=f.vin(300), year="2015", make="NISSISH", model="SUV", amount="4100.00", mileage="90000")
    _purchase(drew, stock_number="20300", vin=f.vin(301), year="2015", make="NISSISH", model="COUPE", amount="3900.00", mileage="80000")
    _purchase(bel, stock_number="20400", vin=f.vin(400), year="2015", make="KIAISH", model="HATCH", amount="2500.00", mileage="70000")
    _purchase(bel, category='PARTS - "RTO" car parts (Repair)', stock_number="457", amount="220.00", description="Alternator")
    _purchase(drew, category='PARTS - "RTO" car parts (Repair)', stock_number="99999", amount="150.00", description="Wipers")
    # Frazer intake upload: the real screen, original bytes stored by intake.
    for data, name in ((f.vehicles_csv(vehicles()), "SYNTHETIC-frazer-vehicles-2026-11-08.csv"),
                       (f.contracts_csv(contracts()), "SYNTHETIC-frazer-customer-balances-2026-11-08.csv")):
        r = drew.post("/finance/frazer/upload", data={}, files={"file": (name, data, "text/csv")})
        assert r.status_code == 303, (name, r.status_code, r.text[:300])
    assert drew.get("/vehicles/").status_code == 200         # reconciliation reads the uploads; no other step
    import re
    page = drew.get("/vehicles/queue?status=live").text
    users = {n: i for i, n in re.findall(r'<option value="(\d+)"[^>]*>(\w+)', page)}
    for ref in dict.fromkeys(re.findall(r"/vehicles/q/(VQ-\d+)", page)):
        q = drew.get(f"/vehicles/q/{ref}").text
        if ("20029, 20116" in q or "Stock 20400" in q) and "Belinda" in users:
            drew.post(f"/vehicles/q/{ref}/assign", data={"owner_user_id": users["Belinda"]})


def newer(cred_path, base):
    import httpx
    drew = _login(httpx, base, cred_path, "demo-drew")
    extra = [f.vehicle("20777", 63, **{"Vehicle Make": "FORDISH", "Vehicle Model": "SEDAN", "Purchase Date": "11/12/26"})]
    r = drew.post("/finance/frazer/upload", data={}, files={"file": ("SYNTHETIC-frazer-vehicles-2026-11-15.csv", f.vehicles_csv(vehicles(extra)), "text/csv")})
    assert r.status_code == 303, r.status_code
    drew.get("/vehicles/")


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"users": lambda: make_users(sys.argv[2]), "seed": lambda: seed(sys.argv[2], sys.argv[3]), "newer": lambda: newer(sys.argv[2], sys.argv[3])}[cmd]()
