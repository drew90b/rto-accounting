"""
Parser tests for Frazer exports (app/services/frazer_parse.py): pure functions, no database.
Synthetic data only; see tests/frazer_fixtures.py. Spec: docs/specs/cash-financial-control-spec.md section 19.
"""
from datetime import date

import pytest

from app.services import frazer_parse as fp
from tests.frazer_fixtures import (
    BHPH, PAYMENT_HEADER, VEHICLE_HEADER, build, customer, customers_csv, late_fee, payment,
    payments_csv, vehicle, vehicles_csv, vin,
)


def codes(pf, severity=None):
    return sorted({e.code for e in pf.all_exceptions if severity in (None, e.severity)})


def by_line(pf, line):
    return next(r for r in pf.records if r.row_number == line)


# ---- AC-FZ-01: supplied formats parse, with the empty trailing column ---------

def test_vehicle_export_parses_with_an_empty_trailing_column():
    pf = fp.parse_export(vehicles_csv([vehicle(**{"Stock #": "10001"}), vehicle(**{"Stock #": "10002", "Vehicle VIN": vin(2)})]))
    assert pf.export_type == fp.VEHICLES and pf.status == "parsed"
    assert pf.summary["records"] == 2 and pf.summary["rows_with_errors"] == 0
    assert "trailing_empty_column" in codes(pf, "info")
    assert not codes(pf, "error")


def test_payment_export_parses_and_summarises_by_class():
    pf = fp.parse_export(payments_csv([payment(), payment(**{"Sequential ID": "5002", "Amount": "50.00", "Applied to Contract": "50.00"}),
                                       late_fee(**{"Sequential ID": "5003"})]))
    assert pf.export_type == fp.PAYMENTS and pf.summary["records"] == 3
    assert pf.summary["by_class"]["payment_entry"] == {"count": 2, "sum": "150.00"}
    assert pf.summary["by_class"]["fee_assessment"] == {"count": 1, "sum": "25.00"}
    assert pf.summary["sequential_id_unique_in_file"] is True


def test_customer_balance_export_parses():
    pf = fp.parse_export(customers_csv([customer(), customer(**{"Stock #": "10002", "Balance": "250.50"})]))
    assert pf.export_type == fp.CUSTOMERS and pf.status == "parsed"
    assert pf.summary["balance_sum_as_reported"] == "5250.50"
    assert pf.summary["account_labels"] == {BHPH: 2}


# ---- AC-FZ-02 / 03: identifiers and dates ------------------------------------

def test_identifiers_stay_text_with_zeros_and_prefixes():
    pf = fp.parse_export(payments_csv([payment(**{"Sequential ID": "0042", "Stock": "00123", "Receipt Number": "0042",
                                                   "Check Number": "000987", "VIN": "1abc"})]))
    f = pf.records[0].fields
    assert (f["sequential_id"], f["stock"], f["receipt_number"], f["check_number"], f["vin"]) == ("0042", "00123", "0042", "000987", "1abc")
    assert pf.records[0].identity == "0042"
    pv = fp.parse_export(vehicles_csv([vehicle(**{"Stock #": "F36140"}), vehicle(**{"Stock #": "00099", "Vehicle VIN": vin(9)})]))
    assert [r.identity for r in pv.records] == ["F36140", "00099"]
    assert pv.summary["stock_with_leading_zero"] == 1 and pv.summary["stock_letter_prefixed"] == 1


def test_transaction_date_and_entry_date_and_time_are_separate():
    pf = fp.parse_export(payments_csv([payment(**{"Transaction Date": "11/03/26", "Date Entered": "11/04/26", "Time Entered": "3:45PM"})]))
    f = pf.records[0].fields
    assert (f["transaction_date"], f["date_entered"], f["time_entered"]) == ("2026-11-03", "2026-11-04", "15:45")
    assert pf.records[0].raw["Time Entered"] == "3:45PM"          # the original is kept beside the parsed value


def test_four_digit_years_and_the_placeholder_date():
    pf = fp.parse_export(vehicles_csv([vehicle(**{"Purchase Date": "04/03/2026", "Ready To Sell Date": "  /  /  "})]))
    f = pf.records[0].fields
    assert f["purchase_date"] == "2026-04-03" and f["ready_to_sell_date"] is None
    assert not codes(pf, "error")


def test_morning_and_noon_times():
    for raw, want in (("9:05AM", "09:05"), ("12:00PM", "12:00"), ("12:30AM", "00:30"), ("11:59 PM", "23:59"), ("14:10", "14:10")):
        assert fp.parse_time(raw) == (want, None)
    assert fp.parse_time("later")[0] is None


# ---- AC-FZ-04: malformed rows and unexpected columns -------------------------

def test_malformed_and_unreadable_rows_are_errors_on_their_own_lines():
    good = payment(**{"Sequential ID": "1"})
    data = build(PAYMENT_HEADER, [good, payment(**{"Sequential ID": "2", "Amount": "ten dollars"}),
                                  payment(**{"Sequential ID": "3", "Transaction Date": "13/45/26"}),
                                  payment(**{"Sequential ID": ""}), payment(**{"Sequential ID": "5"})],
                 raw_rows=[["6", "11/03/26"]])                       # a short row
    pf = fp.parse_export(data)
    assert pf.summary["records"] == 6
    assert by_line(pf, 2).exceptions == []                         # the good row is untouched
    assert [e.code for e in by_line(pf, 3).exceptions] == ["unparseable_amount"]
    assert [e.code for e in by_line(pf, 4).exceptions] == ["unparseable_date"]
    assert "missing_identity" in [e.code for e in by_line(pf, 5).exceptions]
    assert not by_line(pf, 6).exceptions
    assert [e.code for e in by_line(pf, 7).exceptions][0] == "malformed_row"
    assert pf.summary["rows_with_errors"] == 4


def test_extra_cell_with_text_is_an_error_but_an_empty_one_is_not():
    stray = [*vehicle(**{"Stock #": "10003", "Vehicle VIN": vin(3)}).values(), "", "STRAY TEXT"]
    data = build(VEHICLE_HEADER, [vehicle(), vehicle(**{"Stock #": "10002", "Vehicle VIN": vin(2)})], raw_rows=[stray])
    pf = fp.parse_export(data)
    assert not any(e.severity == "error" for r in pf.records[:2] for e in r.exceptions)
    assert [e.code for e in pf.records[2].exceptions] == ["unexpected_nonempty_extra_column"]
    assert "STRAY TEXT" in str(pf.records[2].raw["(beyond header)"])


def test_unexpected_header_columns_are_kept_and_flagged():
    header = VEHICLE_HEADER + ["Surprise Column"]
    pf = fp.parse_export(build(header, [vehicle(**{"Surprise Column": "x"})]))
    assert "unexpected_columns" in codes(pf, "warning")
    assert pf.records[0].fields["other:Surprise Column"] == "x"


def test_blank_lines_are_skipped_and_counted():
    pf = fp.parse_export(vehicles_csv([vehicle()]) + b"\r\n\r\n")
    assert pf.summary["records"] == 1 and "blank_lines" in codes(pf, "info")


# ---- AC-FZ-05: duplicate identities in a file --------------------------------

def test_identical_duplicate_is_flagged_but_a_different_one_is_a_conflict():
    rows = [payment(**{"Sequential ID": "9"}), payment(**{"Sequential ID": "9"}),
            payment(**{"Sequential ID": "10"}), payment(**{"Sequential ID": "10", "Amount": "75.00"})]
    pf = fp.parse_export(payments_csv(rows))
    assert [e.code for e in by_line(pf, 3).exceptions] == ["duplicate_identical"]
    assert "duplicate_conflict" in [e.code for e in by_line(pf, 4).exceptions]
    assert "duplicate_conflict" in [e.code for e in by_line(pf, 5).exceptions]      # both sides are marked
    assert pf.summary["sequential_id_unique_in_file"] is False


# ---- AC-FZ-09 / FZ-11: vehicles ---------------------------------------------

def test_duplicate_vins_are_flagged_and_never_merged():
    rows = [vehicle(**{"Stock #": "10001", "Vehicle VIN": vin(7)}), vehicle(**{"Stock #": "10002", "Vehicle VIN": vin(7)}),
            vehicle(**{"Stock #": "10003", "Vehicle VIN": vin(8)})]
    pf = fp.parse_export(vehicles_csv(rows))
    assert pf.summary["records"] == 3 and pf.summary["unique_stock"] == 3
    assert pf.summary["duplicate_vin_groups"] == [{"vin": vin(7), "stocks": ["10001", "10002"]}]
    assert [e.code for e in by_line(pf, 2).exceptions] == ["duplicate_vin"]
    assert not by_line(pf, 4).exceptions


def test_zero_cost_and_blank_floor_plan_are_preserved_and_flagged_unconfirmed():
    header = VEHICLE_HEADER + ["Floor Plan Balance"]
    rows = [vehicle(**{"Stock #": "1", "Total Cost": "0", "Original Cost": "0", "Floor Plan Balance": "0", "Vehicle VIN": vin(1)}),
            vehicle(**{"Stock #": "2", "Vehicle VIN": vin(2), "Floor Plan Balance": ""}),
            vehicle(**{"Stock #": "3", "Vehicle VIN": vin(3), "Floor Plan Balance": "1234.5"})]
    pf = fp.parse_export(build(header, rows))
    assert pf.summary["zero_cost_records"] == 1 and "zero_cost_reported" in codes(pf, "info")
    col = pf.summary["floor_plan_columns"][0]
    assert (col["records_with_value"], col["records_blank"], col["records_zero"], col["sum_as_reported"]) == (2, 1, 1, "1234.50")
    assert pf.records[1].fields["floor_plan:Floor Plan Balance"] is None          # blank stays blank, never 0
    assert pf.records[0].fields["total_cost"] == "0.00"                            # a reported 0 stays 0
    assert "floor_plan_zero_reported" in codes(pf)


def test_vin_problems_are_warnings_and_the_value_is_kept():
    pf = fp.parse_export(vehicles_csv([vehicle(**{"Vehicle VIN": ""}), vehicle(**{"Stock #": "2", "Vehicle VIN": "SHORT"})]))
    assert {"vin_missing", "vin_unusual_length"} <= set(codes(pf, "warning"))
    assert pf.records[1].fields["vin"] == "SHORT"


# ---- AC-FZ-10: fee assessments are not receipts -------------------------------

def test_late_fee_with_receipt_zero_is_an_assessment_and_a_payment_with_receipt_zero_is_flagged():
    pf = fp.parse_export(payments_csv([late_fee(**{"Sequential ID": "1"}),
                                       payment(**{"Sequential ID": "2", "Receipt Number": "0"}),
                                       payment(**{"Sequential ID": "3", "Description": "Cash", "Transaction Method": "CA"})]))
    assert [r.entry_class for r in pf.records] == ["fee_assessment", "payment_entry", "payment_entry"]
    assert not by_line(pf, 2).exceptions or all(e.code != "payment_without_receipt_number" for e in by_line(pf, 2).exceptions)
    assert "payment_without_receipt_number" in [e.code for e in by_line(pf, 3).exceptions]
    assert pf.summary["fee_entries_with_nothing_applied"] == 1
    assert pf.summary["payment_entries_fully_applied"] == 2


def test_fee_with_money_applied_is_surfaced_not_assumed():
    pf = fp.parse_export(payments_csv([late_fee(**{"Applied to Fees": "25.00"})]))
    assert "fee_with_applied_amount" in codes(pf, "warning")


def test_cancelled_and_unknown_descriptions_are_kept_but_not_counted_as_payments_or_fees():
    pf = fp.parse_export(payments_csv([payment(**{"Sequential ID": "1", "Description": "CANCELLED", "Amount": "0.00", "Applied to Contract": "0.00"}),
                                       payment(**{"Sequential ID": "2", "Description": "Mystery Credit", "Transaction Method": "ZZ"})]))
    assert [r.entry_class for r in pf.records] == ["cancelled_label", "unclassified"]
    assert set(pf.summary["by_class"]) == {"cancelled_label", "unclassified"}
    assert {"cancelled_entry", "unclassified_description"} <= set(codes(pf, "warning"))


def test_a_method_code_on_several_descriptions_is_noted_not_interpreted():
    pf = fp.parse_export(payments_csv([payment(**{"Sequential ID": "1", "Description": "Cash", "Transaction Method": "CA"}),
                                       payment(**{"Sequential ID": "2", "Description": "Down Payment", "Transaction Method": "CA", "Transaction Type": "A"})]))
    e = [x for x in pf.file_exceptions if x.code == "method_code_on_several_descriptions"]
    assert len(e) == 1 and "CA" in e[0].message and "not confirmed" in e[0].message


def test_no_total_of_all_amounts_is_offered():
    pf = fp.parse_export(payments_csv([payment(), late_fee()]))
    assert not [k for k in pf.summary if "all_entries" in k or k.endswith("collections")]


# ---- AC-FZ-12: coverage ------------------------------------------------------

def test_declared_and_observed_coverage_are_separate_and_mismatches_flagged():
    rows = [payment(**{"Sequential ID": "1", "Transaction Date": "11/02/26", "Date Entered": "11/02/26"}),
            payment(**{"Sequential ID": "2", "Transaction Date": "11/07/26", "Date Entered": "11/07/26"}),
            payment(**{"Sequential ID": "3", "Transaction Date": "10/20/26", "Date Entered": "10/20/26"})]
    pf = fp.parse_export(payments_csv(rows), declared=fp.Declared(date(2026, 11, 8), date(2026, 11, 1), date(2026, 11, 8)))
    assert (pf.observed_start, pf.observed_end) == (date(2026, 10, 20), date(2026, 11, 7))
    assert "outside_declared_coverage" in codes(pf, "warning")
    pf2 = fp.parse_export(payments_csv(rows[:2]), declared=fp.Declared(date(2026, 11, 8), date(2026, 11, 1), date(2026, 11, 8)))
    assert "coverage_observed_narrower" in codes(pf2, "info") and not codes(pf2, "warning")
    pf3 = fp.parse_export(payments_csv(rows[:2]))
    assert "coverage_not_declared" in codes(pf3, "warning")


def test_dated_after_export_and_entered_before_transaction():
    pf = fp.parse_export(payments_csv([payment(**{"Transaction Date": "11/09/26", "Date Entered": "11/01/26"})]),
                         declared=fp.Declared(date(2026, 11, 8), date(2026, 11, 1), date(2026, 11, 8)))
    assert {"dated_after_export", "entered_before_transaction"} <= set(codes(pf, "warning"))


# ---- AC-FZ-15: detection and rejection ---------------------------------------

def test_unrecognized_layout_is_rejected_with_the_headers_shown():
    pf = fp.parse_export(build(["Colour", "Shape"], [{"Colour": "red", "Shape": "round"}]))
    assert pf.status == "rejected" and pf.export_type == fp.UNRECOGNIZED and not pf.records
    assert "unrecognized_layout" in codes(pf, "error") and "Colour, Shape" in pf.file_exceptions[0].message


def test_chosen_type_that_disagrees_with_the_headers_is_rejected():
    pf = fp.parse_export(payments_csv(), requested_type=fp.VEHICLES)
    assert pf.status == "rejected" and "type_mismatch" in codes(pf, "error") and not pf.records
    assert fp.parse_export(payments_csv(), requested_type=fp.PAYMENTS).status == "parsed"


def test_missing_required_column_rejects_without_guessing():
    header = [h for h in PAYMENT_HEADER if h != "Sequential ID"]
    pf = fp.parse_export(build(header, [payment()]))
    assert pf.status == "rejected" and not pf.records
    assert pf.detected_type == fp.UNRECOGNIZED


def test_binary_empty_and_cp1252_files():
    assert fp.parse_export(b"PK\x03\x04\x00\x00").status == "rejected"
    assert fp.parse_export(b"").status == "rejected"
    pf = fp.parse_export(vehicles_csv([vehicle(**{"Vehicle Notes": "caf\xe9"})], encoding="cp1252"))
    assert pf.status == "parsed" and pf.encoding == "cp1252" and "encoding_fallback" in codes(pf, "warning")
    assert pf.records[0].fields["vehicle_notes"] == "caf\xe9"


# ---- money and hashing -------------------------------------------------------

@pytest.mark.parametrize("raw,want", [("$1,234.5", "1234.50"), ("20", "20.00"), ("0", "0.00"), ("1.234", "1.234"), ("-5", "-5.00"), ("(50.00)", "-50.00")])
def test_money_parsing(raw, want):
    assert fp.parse_money(raw)[0] == want


def test_unreadable_money_and_blank():
    assert fp.parse_money("abc")[1] and fp.parse_money("")[0] is None


def test_formatting_only_difference_does_not_change_the_content_hash_but_a_value_does():
    a = fp.parse_export(payments_csv([payment(**{"Amount": "100"})])).records[0]
    b = fp.parse_export(payments_csv([payment(**{"Amount": "100.00"})])).records[0]
    c = fp.parse_export(payments_csv([payment(**{"Amount": "101.00"})])).records[0]
    assert a.content_hash == b.content_hash != c.content_hash


# ---- customer balances -------------------------------------------------------

def test_label_with_en_dash_or_hyphen_counts_as_buy_here_pay_here_and_others_are_flagged():
    pf = fp.parse_export(customers_csv([customer(), customer(**{"Stock #": "2", "Account Type": "Buy Here-Pay Here"}),
                                        customer(**{"Stock #": "3", "Account Type": "Lease"})]))
    assert [e.code for e in by_line(pf, 4).exceptions] == ["label_not_bhph"]
    assert not by_line(pf, 2).exceptions and not by_line(pf, 3).exceptions


def test_unnamed_label_column_is_inferred_from_its_values():
    header = ["Stock #", "Vehicle VIN", "Balance", "Mystery"]
    pf = fp.parse_export(build(header, [{"Stock #": "1", "Vehicle VIN": vin(1), "Balance": "10", "Mystery": BHPH},
                                         {"Stock #": "2", "Vehicle VIN": vin(2), "Balance": "20", "Mystery": BHPH}]))
    assert pf.export_type == fp.CUSTOMERS and "label_column_inferred" in codes(pf, "info")
    assert pf.summary["account_labels"] == {BHPH: 2}


def test_rows_without_a_stock_number_are_flagged_and_left_out_of_the_sum():
    pf = fp.parse_export(customers_csv([customer(**{"Balance": "100"}), customer(**{"Stock #": "", "Balance": "9999"})]))
    assert pf.summary["balance_sum_as_reported"] == "100.00" and pf.summary["balance_sum_excluded_rows"] == "9999.00"
    assert "missing_identity" in [e.code for e in pf.records[1].exceptions]


def test_a_total_row_has_no_identity_so_it_is_not_taken_for_a_customer():
    pf = fp.parse_export(customers_csv([customer(), {"Stock #": "", "Vehicle VIN": "", "Last Name": "TOTAL", "First Name": "", "Account Type": "", "Balance": "5000.00"}]))
    assert pf.summary["records_without_stock"] == 1


# --- the customer-balance layout seen in the 2026-11-08 export ------------------------------------------------------

def test_nov8_customer_layout_is_read_without_guessing():
    from tests.frazer_fixtures import customer_nov8, customers_nov8_csv
    pf = fp.parse_export(customers_nov8_csv([customer_nov8(), customer_nov8(**{"Stock #": "10002", "Vehicle VIN": vin(2), "Balance": "100.00",
                                                                              "Principal Balance": "100", "Fees Owed": "0", "Current Due": "-20"})]))
    assert pf.status == "parsed" and pf.export_type == fp.CUSTOMERS
    codes = {e.code for e in pf.all_exceptions}
    assert "duplicate_header" not in codes and "label_column_inferred" not in codes and "unexpected_columns" not in codes
    r = pf.records[0].fields
    assert r["account_label"] == "Buy Here - Pay Here" and r["balance"] == "5050.00" and r["principal_balance"] == "5000.00"
    assert r["fees_owed"] == "50.00" and r["contract_maturity_date"] == "2029-06-12" and r["sale_date"] == "2026-03-12"
    assert r["days_past_due"] == ""                        # blank stays blank, not 0
    assert pf.summary["balance_equals_components"] == 2
    assert pf.summary["principal_sum_as_reported"] == "5100.00"
    assert "negative_current_due" in codes and "negative_amount" not in codes


def test_balance_that_is_not_its_components_is_flagged_not_fixed():
    from tests.frazer_fixtures import customer_nov8, customers_nov8_csv
    pf = fp.parse_export(customers_nov8_csv([customer_nov8(**{"Balance": "5075.00"})]))
    assert [e.code for e in pf.records[0].exceptions] == ["balance_components_differ"]
    assert pf.records[0].fields["balance"] == "5075.00"
    assert pf.summary["balance_equals_components"] == 0


def test_nov8_vehicle_layout_keeps_both_floor_plan_columns():
    from tests.frazer_fixtures import VEHICLE_NOV8_HEADER, build, vehicle
    pf = fp.parse_export(build(VEHICLE_NOV8_HEADER, [vehicle(**{"Floor Plan Principal Owed": "0", "Floor Plan Company": "", "Total Cost": "0"})]))
    assert pf.status == "parsed"
    cols = {c["header"]: c for c in pf.summary["floor_plan_columns"]}
    assert cols["Floor Plan Company"]["records_blank"] == 1 and cols["Floor Plan Principal Owed"]["records_zero"] == 1
    assert pf.summary["zero_cost_records"] == 1


def test_payments_layout_without_sale_date_and_vin_columns():
    from tests.frazer_fixtures import PAYMENT_NOV8_HEADER, build, payment
    pf = fp.parse_export(build(PAYMENT_NOV8_HEADER, [payment(), payment(**{"Sequential ID": "5004"})]))
    assert pf.status == "parsed" and pf.summary["records"] == 2 and not any(e.severity == "error" for e in pf.all_exceptions)


def test_sequential_id_gap_is_information_not_missing_data():
    pf = fp.parse_export(payments_csv([payment(**{"Sequential ID": "1166"}), payment(**{"Sequential ID": "1168"}), payment(**{"Sequential ID": "1171"})]))
    assert pf.summary["sequential_id_gaps"] == [1167, 1169, 1170]
    gap = next(e for e in pf.all_exceptions if e.code == "sequential_id_gap")
    assert gap.severity == "info" and "does not show that anything is missing" in gap.message


def test_filename_supplies_the_declared_period_but_is_labelled_and_never_proof():
    d = fp.declared_from_filename("frazer-payments-2026-11-01-through-2026-11-08.csv.CSV", fp.Declared())
    assert (d.coverage_start, d.coverage_end, d.export_date) == (date(2026, 11, 1), date(2026, 11, 8), None)
    assert d.from_filename == ("coverage_start", "coverage_end")
    d = fp.declared_from_filename("frazer-customer-balances-as-od-2026-11-08.CSV", fp.Declared())
    assert d.export_date == date(2026, 11, 8) and d.coverage_start is None
    typed = fp.declared_from_filename("frazer-vehicles-2026-11-08.csv", fp.Declared(export_date=date(2026, 11, 9)))
    assert typed.export_date == date(2026, 11, 9) and typed.from_filename == ()          # what the user typed wins
    assert fp.declared_from_filename("export.csv", fp.Declared()).from_filename == ()
    pf = fp.parse_export(payments_csv([payment(**{"Transaction Date": "11/02/26"}), payment(**{"Sequential ID": "5002", "Transaction Date": "11/07/26"})]),
                         None, fp.declared_from_filename("x-2026-11-01-through-2026-11-08.csv", fp.Declared()))
    codes = {e.code: e for e in pf.all_exceptions}
    assert "coverage_not_declared" not in codes and codes["declared_from_filename"].severity == "info"
    assert "coverage_observed_narrower" in codes
    assert (pf.observed_start, pf.observed_end) == (date(2026, 11, 2), date(2026, 11, 7))
