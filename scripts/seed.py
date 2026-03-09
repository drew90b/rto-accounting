"""
Seed script — populates the database with realistic sample data.
Run from the project root: python scripts/seed.py
"""
import sys
import os
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, Base, engine
import app.models  # noqa: register all models

Base.metadata.create_all(bind=engine)

from app.models.customer import Customer
from app.models.vendor import Vendor
from app.models.unit import Unit
from app.models.repair_job import RepairJob
from app.models.sale import Sale
from app.models.lease_account import LeaseAccount
from app.models.payment import Payment
from app.models.transaction import Transaction
from app.models.exception_record import ExceptionRecord

db = SessionLocal()

today = date.today()

print("Seeding customers...")
customers = [
    Customer(full_name="Marcus Williams", phone="555-101-2020", email="marcus.w@email.com", address="112 Oak St, Lake City, FL", status="active"),
    Customer(full_name="Denise Hargrove", phone="555-202-3030", email="denise.h@email.com", address="88 Pine Ave, Lake City, FL", status="active"),
    Customer(full_name="Tony Bellows", phone="555-303-4040", address="45 Cedar Rd, Live Oak, FL", status="active"),
    Customer(full_name="Sandra Patel", phone="555-404-5050", email="sandra.p@email.com", address="200 Maple Dr, Gainesville, FL", status="active"),
    Customer(full_name="Robert Dunn", phone="555-505-6060", status="inactive"),
]
for c in customers:
    db.add(c)
db.flush()
for i, c in enumerate(customers, 1):
    c.customer_id = f"C-{c.id:04d}"

print("Seeding vendors...")
vendors = [
    Vendor(name="AAA Auto Auction", phone="800-111-2222", address="I-75 Mile 300, Gainesville, FL", notes="Primary auction source"),
    Vendor(name="AutoZone #4412", phone="352-555-0100", notes="Parts supplier"),
    Vendor(name="Coker Tire & Wheel", phone="352-555-0200", notes="Tire and wheel work"),
    Vendor(name="Southeast Golf Carts", phone="352-555-0300", notes="Golf cart parts and wholesale"),
]
for v in vendors:
    db.add(v)
db.flush()
for i, v in enumerate(vendors, 1):
    v.vendor_id = f"V-{v.id:04d}"

print("Seeding units...")
units_data = [
    dict(unit_type="car", business_line="car", year=2018, make="Honda", model="Accord", vin_serial="1HGCV1F34JA012345", purchase_date=today - timedelta(days=60), purchase_source="AAA Auto Auction", acquisition_cost=Decimal("8200.00"), status="frontline_ready"),
    dict(unit_type="car", business_line="car", year=2016, make="Toyota", model="Camry", vin_serial="4T1BF1FK6GU512888", purchase_date=today - timedelta(days=45), purchase_source="AAA Auto Auction", acquisition_cost=Decimal("7100.00"), status="in_repair"),
    dict(unit_type="car", business_line="car", year=2019, make="Ford", model="Fusion", vin_serial="3FA6P0HD3KR102934", purchase_date=today - timedelta(days=90), purchase_source="Private seller", acquisition_cost=Decimal("9500.00"), status="sold"),
    dict(unit_type="car", business_line="car", year=2015, make="Chevrolet", model="Malibu", vin_serial="1G11C5SA5FF281901", purchase_date=today - timedelta(days=30), purchase_source="AAA Auto Auction", acquisition_cost=Decimal("5800.00"), status="leased_rto_active"),
    dict(unit_type="golf_cart", business_line="golf_cart", year=2020, make="Club Car", model="Onward", vin_serial="CC20001234", purchase_date=today - timedelta(days=20), purchase_source="Southeast Golf Carts", acquisition_cost=Decimal("3200.00"), status="frontline_ready"),
    dict(unit_type="golf_cart", business_line="golf_cart", year=2019, make="E-Z-GO", model="RXV", vin_serial="EZ19005678", purchase_date=today - timedelta(days=55), purchase_source="Southeast Golf Carts", acquisition_cost=Decimal("2800.00"), status="sold"),
    dict(unit_type="golf_cart", business_line="golf_cart", year=2021, make="Yamaha", model="Drive2", vin_serial="YM21009012", purchase_date=today - timedelta(days=10), purchase_source="Southeast Golf Carts", acquisition_cost=Decimal("4100.00"), status="in_inspection"),
]
units = []
for d in units_data:
    u = Unit(**d)
    db.add(u)
    units.append(u)
db.flush()
for u in units:
    u.unit_id = f"U-{u.id:04d}"

# Link customer to leased unit
units[3].linked_customer_id = customers[1].id

print("Seeding repair jobs...")
jobs = [
    RepairJob(business_line="car", unit_id=units[1].id, job_type="internal_recon", open_date=today - timedelta(days=40), status="in_progress", labor_amount=Decimal("320.00"), materials_amount=Decimal("180.00"), notes="Brakes, oil, tires"),
    RepairJob(business_line="golf_cart", customer_id=customers[0].id, job_type="customer_repair", open_date=today - timedelta(days=15), status="complete", close_date=today - timedelta(days=5), labor_amount=Decimal("150.00"), materials_amount=Decimal("60.00"), total_billed_amount=Decimal("280.00"), notes="Battery replacement and motor service"),
    RepairJob(business_line="car", unit_id=units[0].id, job_type="internal_recon", open_date=today - timedelta(days=58), close_date=today - timedelta(days=50), status="complete", labor_amount=Decimal("210.00"), materials_amount=Decimal("95.00"), notes="Detail and minor body work"),
]
for j in jobs:
    db.add(j)
db.flush()
for j in jobs:
    j.job_id = f"J-{j.id:04d}"

print("Seeding sales...")
sales = [
    Sale(customer_id=customers[2].id, unit_id=units[2].id, sale_date=today - timedelta(days=30), business_line="car", sale_amount=Decimal("12500.00"), down_payment=Decimal("2000.00"), fees=Decimal("300.00"), total_contract_amount=Decimal("12800.00"), status="complete"),
    Sale(customer_id=customers[0].id, unit_id=units[5].id, sale_date=today - timedelta(days=20), business_line="golf_cart", sale_amount=Decimal("4200.00"), down_payment=Decimal("500.00"), fees=Decimal("0.00"), total_contract_amount=Decimal("4200.00"), status="complete"),
]
for s in sales:
    db.add(s)
db.flush()
for s in sales:
    s.sale_id = f"S-{s.id:04d}"

print("Seeding lease accounts...")
lease = LeaseAccount(
    customer_id=customers[1].id,
    unit_id=units[3].id,
    deal_date=today - timedelta(days=28),
    original_agreed_amount=Decimal("9000.00"),
    down_payment=Decimal("1000.00"),
    financed_balance=Decimal("8000.00"),
    scheduled_payment_amount=Decimal("350.00"),
    payment_frequency="bi_weekly",
    status="active",
    delinquency_status="current",
    notes="2 payments received on time",
)
db.add(lease)
db.flush()
lease.lease_id = f"L-{lease.id:04d}"

print("Seeding payments...")
payments = [
    Payment(customer_id=customers[2].id, payment_date=today - timedelta(days=30), amount=Decimal("2000.00"), payment_method="cash", sale_id=sales[0].id, entered_by="Admin", notes="Down payment on Fusion"),
    Payment(customer_id=customers[0].id, payment_date=today - timedelta(days=20), amount=Decimal("500.00"), payment_method="check", sale_id=sales[1].id, entered_by="Admin", notes="Down payment on golf cart"),
    Payment(customer_id=customers[1].id, payment_date=today - timedelta(days=14), amount=Decimal("350.00"), payment_method="cash", lease_account_id=lease.id, entered_by="Admin", notes="First RTO payment"),
    Payment(customer_id=customers[1].id, payment_date=today - timedelta(days=0), amount=Decimal("350.00"), payment_method="cash", lease_account_id=lease.id, entered_by="Admin", notes="Second RTO payment"),
    Payment(customer_id=customers[0].id, payment_date=today - timedelta(days=5), amount=Decimal("280.00"), payment_method="card", repair_job_id=jobs[1].id, entered_by="Admin", notes="Golf cart repair payment"),
]
for p in payments:
    db.add(p)
db.flush()
for p in payments:
    p.payment_id = f"P-{p.id:04d}"

print("Seeding transactions...")
txns = [
    Transaction(transaction_date=today - timedelta(days=60), entry_date=today - timedelta(days=60), transaction_type="purchase", business_line="car", amount=Decimal("8200.00"), description="Honda Accord purchase at auction", unit_id=units[0].id, vendor_id=vendors[0].id, category="inventory_purchase", receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=45), entry_date=today - timedelta(days=45), transaction_type="purchase", business_line="car", amount=Decimal("7100.00"), description="Toyota Camry purchase at auction", unit_id=units[1].id, vendor_id=vendors[0].id, category="inventory_purchase", receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=58), entry_date=today - timedelta(days=58), transaction_type="materials_cost", business_line="car", amount=Decimal("95.00"), description="Parts for Accord recon", unit_id=units[0].id, repair_job_id=jobs[2].id, vendor_id=vendors[1].id, category="parts", receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=30), entry_date=today - timedelta(days=30), transaction_type="sale", business_line="car", revenue_stream="car_sale", amount=Decimal("12500.00"), description="Ford Fusion sale", unit_id=units[2].id, customer_id=customers[2].id, sale_id=sales[0].id, receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=20), entry_date=today - timedelta(days=20), transaction_type="sale", business_line="golf_cart", revenue_stream="golf_cart_sale", amount=Decimal("4200.00"), description="E-Z-GO RXV sale", unit_id=units[5].id, customer_id=customers[0].id, sale_id=sales[1].id, receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=14), entry_date=today - timedelta(days=14), transaction_type="collection", business_line="car", revenue_stream="car_rto_lease", amount=Decimal("350.00"), description="RTO payment - Denise Hargrove", customer_id=customers[1].id, lease_account_id=lease.id, payment_method="cash", receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=5), entry_date=today - timedelta(days=5), transaction_type="repair_revenue", business_line="golf_cart", revenue_stream="golf_cart_repair", amount=Decimal("280.00"), description="Battery replace / motor service - Marcus Williams cart", customer_id=customers[0].id, repair_job_id=jobs[1].id, receipt_attached=True, coding_complete=True, review_status="approved", entered_by="Admin"),
    Transaction(transaction_date=today - timedelta(days=3), entry_date=today - timedelta(days=3), transaction_type="materials_cost", business_line="car", amount=Decimal("180.00"), description="Parts for Camry recon - needs receipt", unit_id=units[1].id, repair_job_id=jobs[0].id, vendor_id=vendors[1].id, category="parts", receipt_attached=False, coding_complete=False, review_status="pending", entered_by="Admin"),
]
for t in txns:
    db.add(t)
db.flush()
for t in txns:
    t.transaction_id = f"T-{t.id:05d}"

print("Seeding exceptions...")
excs = [
    ExceptionRecord(exception_type="missing_receipt", linked_record_type="transaction", linked_record_id=txns[7].id, opened_date=today - timedelta(days=2), owner="Admin", status="open", notes="Parts invoice not attached for Camry recon job", target_resolution_date=today + timedelta(days=5)),
    ExceptionRecord(exception_type="missing_coding", linked_record_type="transaction", linked_record_id=txns[7].id, opened_date=today - timedelta(days=2), owner="Admin", status="open", notes="Transaction needs category coding review"),
]
for e in excs:
    db.add(e)
db.flush()
for e in excs:
    e.exception_id = f"E-{e.id:04d}"

db.commit()
db.close()
print("\nSeed complete.")
print(f"  {len(customers)} customers")
print(f"  {len(vendors)} vendors")
print(f"  {len(units)} units")
print(f"  {len(jobs)} repair jobs")
print(f"  {len(sales)} sales")
print(f"  1 lease account")
print(f"  {len(payments)} payments")
print(f"  {len(txns)} transactions")
print(f"  {len(excs)} exceptions")
