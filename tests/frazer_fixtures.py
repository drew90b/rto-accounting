"""
Synthetic Frazer exports for tests. The column HEADERS are the real Frazer layouts (observed in two
exports dated 2026-10-22); every VALUE is invented. No real customer, vehicle or amount appears here.

The customer-balance layout is an ASSUMED one: the real Nov 8 export has not been seen yet.
"""
import csv
import io

VEHICLE_HEADER = ["Stock #", "Vehicle Year", "Vehicle Make", "Vehicle Model", "Vehicle VIN", "Mileage", "Vehicle Source",
                  "Buy Fee", "Location Code", "Original Cost", "Total Cost", "Purchase Date", "Vehicle Notes", "Vendor",
                  "Lot Fee", "Added Costs", "Labor Costs", "Purchase Notes", "Ready To Sell Date", "Last 6 VIN"]
PAYMENT_HEADER = ["Sequential ID", "Transaction Date", "Due Date", "Stock", "Last Name", "First Name", "Amount",
                  "Transaction Type", "Transaction Method", "Description", "How Paid", "Check Number", "Receipt Number",
                  "Applied to Contract", "Applied to Interest", "Applied to Fees", "Applied to Pick-Up Note",
                  "Applied to Other Receivables", "Applied to Repairs", "Applied to Recurring Fees", "Remaining Balance",
                  "Date Entered", "Time Entered", "Initials", "Sale Date", "VIN"]
CUSTOMER_HEADER = ["Stock #", "Vehicle VIN", "Last Name", "First Name", "Account Type", "Balance"]
BHPH = "Buy Here–Pay Here"          # with the en dash used in the task brief


def vin(n: int) -> str:
    return f"TSTVIN{n:011d}"             # 17 characters, obviously fake


def vehicle(**over) -> dict:
    d = {"Stock #": "10001", "Vehicle Year": "2015", "Vehicle Make": "TESTMAKE", "Vehicle Model": "TESTMODEL",
         "Vehicle VIN": vin(1), "Mileage": "100000", "Vehicle Source": "AUCTION", "Buy Fee": "0", "Location Code": "",
         "Original Cost": "5000", "Total Cost": "5000", "Purchase Date": "10/01/26", "Vehicle Notes": "", "Vendor": "",
         "Lot Fee": "0", "Added Costs": "0", "Labor Costs": "0", "Purchase Notes": "", "Ready To Sell Date": "/  /",
         "Last 6 VIN": "000001"}
    d.update(over)
    return d


def payment(**over) -> dict:
    d = {"Sequential ID": "5001", "Transaction Date": "11/03/26", "Due Date": "11/10/26", "Stock": "10001",
         "Last Name": "TESTCUSTOMER", "First Name": "FAKE", "Amount": "100.00", "Transaction Type": "P",
         "Transaction Method": "EL", "Description": "PayMyCar (CC)", "How Paid": "", "Check Number": "",
         "Receipt Number": "7001", "Applied to Contract": "100.00", "Applied to Interest": "0.00",
         "Applied to Fees": "0.00", "Applied to Pick-Up Note": "0.00", "Applied to Other Receivables": "0.00",
         "Applied to Repairs": "0.00", "Applied to Recurring Fees": "0.00", "Remaining Balance": "5000.00",
         "Date Entered": "11/04/26", "Time Entered": "3:45PM", "Initials": "XX", "Sale Date": "05/01/26", "VIN": vin(1)}
    d.update(over)
    return d


def late_fee(**over) -> dict:
    base = {"Description": "Late Fee", "Transaction Type": "L", "Transaction Method": "LF", "Receipt Number": "0",
            "Applied to Contract": "0.00", "Amount": "25.00"}
    base.update(over)
    return payment(**base)


def customer(**over) -> dict:
    d = {"Stock #": "10001", "Vehicle VIN": vin(1), "Last Name": "TESTCUSTOMER", "First Name": "FAKE",
         "Account Type": BHPH, "Balance": "5000.00"}
    d.update(over)
    return d


def build(header, rows, trailing_empty=True, eol="\r\n", encoding="utf-8", raw_rows=None) -> bytes:
    """CSV bytes. Like the real exports: the header has no trailing cell, data rows carry one empty trailing cell."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator=eol)
    w.writerow(header)
    for r in rows:
        cells = [r.get(h, "") for h in header]
        w.writerow(cells + ([""] if trailing_empty else []))
    for cells in (raw_rows or []):
        w.writerow(cells)
    return buf.getvalue().encode(encoding)


def vehicles_csv(rows=None, **kw) -> bytes:
    return build(VEHICLE_HEADER, rows if rows is not None else [vehicle()], **kw)


def payments_csv(rows=None, **kw) -> bytes:
    return build(PAYMENT_HEADER, rows if rows is not None else [payment()], **kw)


def customers_csv(rows=None, header=None, **kw) -> bytes:
    return build(header or CUSTOMER_HEADER, rows if rows is not None else [customer()], **kw)


# --- Layouts observed in the 2026-11-08 exports (headers real, every value invented) -----------------------------
CUSTOMER_NOV8_HEADER = ["Stock #", "First Name", "Last Name", "Sale Date", "Type of Sale", "Balance", "Payment Amt", "Pay Schedule",
                        "Total Due at Time of Sale", "Amt Financed", "Down Payment", "Number of Payments", "Current Date Due",
                        "Vehicle Year", "Vehicle Make", "Vehicle Model", "Vehicle VIN", "Mileage", "Days past Due", "Total Sales Tax",
                        "Current Due", "Pick-up Note Payment Amount", "Pick-up Note Payment Due Date", "Fees Owed", "Last Pay Date",
                        "Promise Date", "Principal Balance", "Repairs Balance", "1st Payment Date", "Contract Balance Received",
                        "Payments Remaining", "Sales Price", "Purchased Account Balance", "Last Payment Amount",
                        "Total of Payments Received", "Days Since Last Payment", "Last 6 VIN", "Contract Maturity Date"]
VEHICLE_NOV8_HEADER = ["Stock #", "Vehicle Year", "Vehicle Make", "Vehicle Model", "Vehicle VIN", "Mileage", "Vehicle Source",
                       "Buy Fee", "Location Code", "Original Cost", "Total Cost", "Floor Plan Company", "Purchase Date", "Vehicle Notes",
                       "Vendor", "Lot Fee", "Added Costs", "Labor Costs", "Purchase Notes", "Ready To Sell Date",
                       "Floor Plan Principal Owed", "Last 6 VIN"]
PAYMENT_NOV8_HEADER = PAYMENT_HEADER[:24]       # this export has no Sale Date / VIN columns


def customer_nov8(**over) -> dict:
    d = {"Stock #": "10001", "First Name": "FAKE", "Last Name": "TESTCUSTOMER", "Sale Date": "03/12/26", "Type of Sale": "Buy Here - Pay Here",
         "Balance": "5050.00", "Payment Amt": "400", "Pay Schedule": "MONTHLY", "Total Due at Time of Sale": "6000", "Amt Financed": "6000",
         "Down Payment": "1000", "Number of Payments": "15", "Current Date Due": "11/12/26", "Vehicle Year": "2015", "Vehicle Make": "TESTMAKE",
         "Vehicle Model": "TESTMODEL", "Vehicle VIN": vin(1), "Mileage": "100000", "Days past Due": "", "Total Sales Tax": "0",
         "Current Due": "0", "Pick-up Note Payment Amount": "0", "Pick-up Note Payment Due Date": "", "Fees Owed": "50",
         "Last Pay Date": "10/25/26", "Promise Date": "11/12/26", "Principal Balance": "5000", "Repairs Balance": "0",
         "1st Payment Date": "04/12/26", "Contract Balance Received": "1000", "Payments Remaining": "13", "Sales Price": "7000",
         "Purchased Account Balance": "0", "Last Payment Amount": "400.00", "Total of Payments Received": "1000",
         "Days Since Last Payment": "14", "Last 6 VIN": "000001", "Contract Maturity Date": " 6/12/29"}
    d.update(over)
    return d


def customers_nov8_csv(rows=None, **kw) -> bytes:
    return build(CUSTOMER_NOV8_HEADER, rows if rows is not None else [customer_nov8()], **kw)
