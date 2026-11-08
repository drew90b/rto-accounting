"""
Synthetic data for the vehicle reconciliation tests. Column HEADERS are the real Frazer layouts (Nov 8 exports); every VALUE is
invented. No real vehicle, customer or amount appears here. The customer-balance rows deliberately carry a name and a balance so
tests can prove the feature never stores them.
"""
import csv
import io

VEHICLE_HEADER = ["Stock #", "Vehicle Year", "Vehicle Make", "Vehicle Model", "Vehicle VIN", "Mileage", "Vehicle Source",
                  "Buy Fee", "Location Code", "Original Cost", "Total Cost", "Floor Plan Company", "Purchase Date",
                  "Vehicle Notes", "Vendor", "Lot Fee", "Added Costs", "Labor Costs", "Purchase Notes", "Ready To Sell Date",
                  "Floor Plan Principal Owed", "Last 6 VIN"]
CUSTOMER_HEADER = ["Stock #", "First Name", "Last Name", "Sale Date", "Type of Sale", "Balance", "Vehicle Year", "Vehicle Make",
                   "Vehicle Model", "Vehicle VIN", "Fees Owed", "Principal Balance", "Repairs Balance"]
CUSTOMER_NAME = "ZZTESTCUSTOMER"
CUSTOMER_BALANCE = "7777.77"


def vin(n: int) -> str:
    return f"TSTVIN{n:011d}"


def vehicle(stock="10001", n=1, **over):
    d = {"Stock #": stock, "Vehicle Year": "2015", "Vehicle Make": "TESTMAKE", "Vehicle Model": "TESTMODEL",
         "Vehicle VIN": vin(n), "Mileage": "100000", "Vehicle Source": "Company", "Buy Fee": "0", "Location Code": "",
         "Original Cost": "5000", "Total Cost": "5000", "Floor Plan Company": "", "Purchase Date": "10/01/26",
         "Vehicle Notes": "", "Vendor": "", "Lot Fee": "0", "Added Costs": "0", "Labor Costs": "0", "Purchase Notes": "",
         "Ready To Sell Date": "/  /", "Floor Plan Principal Owed": "0", "Last 6 VIN": f"{n:06d}"}
    d.update(over)
    return d


def contract(stock="10001", n=1, **over):
    d = {"Stock #": stock, "First Name": "ZZFIRST", "Last Name": CUSTOMER_NAME, "Sale Date": "11/04/26",
         "Type of Sale": "Buy Here - Pay Here", "Balance": CUSTOMER_BALANCE, "Vehicle Year": "2015", "Vehicle Make": "TESTMAKE",
         "Vehicle Model": "TESTMODEL", "Vehicle VIN": vin(n), "Fees Owed": "0.00", "Principal Balance": CUSTOMER_BALANCE,
         "Repairs Balance": "0.00"}
    d.update(over)
    return d


def _csv(header, rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return out.getvalue().encode()


def vehicles_csv(rows) -> bytes:
    return _csv(VEHICLE_HEADER, rows)


def contracts_csv(rows) -> bytes:
    return _csv(CUSTOMER_HEADER, rows)
