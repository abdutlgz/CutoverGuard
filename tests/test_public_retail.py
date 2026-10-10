import sys
"""Manual tiny CSV controls for the adapter; these are NOT downloaded UCI data."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import pytest
from cutoverguard.engine import reconcile, ReconcileError
from cutoverguard.fixtures import fixture
from scripts.validate_public_retail import build_bundle, csv_rows, invoice_minor, london_time, validate_cases, controlled_cases

CSV = """InvoiceNo,Quantity,InvoiceDate,UnitPrice,CustomerID
INV1,3,2011-01-04T10:00:00,2.55,00001
INV1,-2,2011-01-04T10:01:00,1.25,00002
INV2,1,2011-01-04T10:02:00,0.005,00003
INV3,1,2011-01-04T10:03:00,5.00,
INV4,2,2011-07-04T10:00:00,0.10,00001
"""


def input_bundle(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text(CSV)
    return build_bundle(csv_rows(path), source_sha256="a" * 64, captured_at="2026-10-10T05:00:00Z")


def test_manual_csv_exact_values_ids_and_exclusions(tmp_path):
    bundle, manifest = input_bundle(tmp_path)
    rows = bundle["source"]["transactions"]
    assert [(r["event_id"], r["account_id"], r["amount_minor"], r["currency"]) for r in rows] == [
        ("uci-row-000000002", "receivable:00001", "765", "GBP"),
        ("uci-row-000000003", "receivable:00002", "-250", "GBP"),
        ("uci-row-000000006", "receivable:00001", "20", "GBP")]
    assert rows[0]["event_time"] == "2011-01-04T10:00:00+00:00"
    assert rows[2]["event_time"] == "2011-07-04T09:00:00+00:00"
    assert manifest["scanned_records"] == 5 and manifest["selected_records"] == 3 and manifest["excluded_records"] == 2
    assert [r["original_record_number"] for r in manifest["exclusions"]] == [4, 5]
    assert "fractional pennies" in manifest["exclusions"][0]["reason"]
    assert "CustomerID" in manifest["exclusions"][1]["reason"]
    assert manifest["selected_observations"][0]["raw_record"]["InvoiceNo"] == "INV1"
    assert manifest["selected_observations"][1]["raw_record"]["Quantity"] == "-2"
    assert bundle["dataset_info"]["extraction_counts"] == {"scanned_records": 5, "selected_records": 3, "excluded_records": 2}
    assert bundle["data_kind"] == "historical"
    assert bundle["dataset_info"]["validation_kind"] == "historical_source_controlled_candidate"
    report = reconcile(bundle)
    assert report["status"] == "ready_for_review"
    assert report["currency_totals"][0]["source_minor"] == "535"
    assert report["currency_totals"][0]["currency"] == "GBP"


def test_all_separate_cases_and_source_immutable(tmp_path):
    baseline, _ = input_bundle(tmp_path)
    original = deepcopy(baseline)
    summary = validate_cases(baseline, tmp_path / "reports")
    assert baseline == original
    assert summary["status"] == "verified"
    assert len(summary["cases"]) == 6
    cases = {c["case"]: c for c in summary["cases"]}
    assert cases["baseline"]["actual_primary_count"] == 0
    assert cases["ownership_swaps"]["actual_primary_count"] == 2
    assert cases["missing_extra"]["actual_primary_count"] == 2
    assert cases["compensating_penny_changes"]["actual_primary_count"] == 2
    assert cases["duplicate_keys"]["actual_primary_count"] == 2
    assert cases["customer_mapping"]["actual_primary_count"] == 1
    assert len(list((tmp_path / "reports").glob("*-report.json"))) == 6
    for name, bundle, expected in controlled_cases(baseline):
        assert bundle["source"] == original["source"]
        if name == "compensating_penny_changes":
            totals = reconcile(bundle)["currency_totals"][0]
            assert totals["net_delta_minor"] == "0"


@pytest.mark.parametrize("quantity,price,expected", [("3", "2.55", "765"), ("-2", "1.25", "-250"), ("2", "0.005", "1"), ("10", "0.1", "100"), ("0", "10", "0")])
def test_decimal_money_hand_values(quantity, price, expected):
    assert invoice_minor(quantity, price) == expected


@pytest.mark.parametrize("quantity,price", [("1", "0.005"), ("1.5", "1"), ("NaN", "1"), ("1", "Infinity"), ("1", "-1"), ("1e13", "1")])
def test_invalid_money_not_rounded(quantity, price):
    with pytest.raises(ValueError):
        invoice_minor(quantity, price)


@pytest.mark.parametrize("date", ["2011-10-30T01:30:00", "2011-03-27T01:30:00", "2011-01-04T10:00:00.123456789"])
def test_ambiguous_nonexistent_and_excess_precision_dates_rejected(date):
    with pytest.raises(ValueError):
        london_time(date)


def test_explicit_offset_not_overridden_and_csv_format_declared():
    assert london_time("2011-07-04T10:00:00+02:00").isoformat() == "2011-07-04T08:00:00+00:00"
    assert london_time("12/1/2010 08:26").month == 12
    assert london_time("1/12/2010 08:26", "%d/%m/%Y %H:%M").month == 12


def test_scan_limit_disclosed_and_no_silent_selection(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text(CSV)
    bundle, manifest = build_bundle(csv_rows(path), source_sha256="b" * 64, captured_at="2026-10-10T05:00:00Z", limit=2)
    assert manifest["scanned_records"] == manifest["selected_records"] == 2
    assert len(bundle["source"]["transactions"]) == 2
    assert "not counted" in manifest["unseen_records"]


def test_gbp_opt_in_and_existing_fingerprints_unchanged():
    old = fixture()
    old_id = reconcile(old)["run_id"]
    same = deepcopy(old)
    assert reconcile(same)["run_id"] == old_id
    same["candidate"]["transactions"][0]["currency"] = "GBP"
    assert len(reconcile(same)["quarantine"]) == 1
    same["config"]["currency_scales"]["GBP"] = 2
    assert not reconcile(same)["quarantine"]
    same["config"]["currency_scales"]["GBP"] = 3
    with pytest.raises(ReconcileError):
        reconcile(same)


def test_cli_csv_runs_without_extra_dependency(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text(CSV)
    result = subprocess.run([sys.executable, 'scripts/validate_public_retail.py', '--csv', str(path), '--output', str(tmp_path / 'out')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '"status": "verified"' in result.stdout
    assert (tmp_path / 'out' / 'extract-manifest.json').exists()
    assert (tmp_path / 'out' / 'validation-summary.json').exists()


def test_underflow_never_turns_nonzero_fractional_price_into_zero():
    with pytest.raises(ValueError):
        invoice_minor('1', '1e-100000000')


def test_selected_snapshot_identity_changes_with_extract_content(tmp_path):
    path = tmp_path / 'manual.csv'
    path.write_text(CSV)
    short, _ = build_bundle(csv_rows(path), source_sha256='a' * 64, captured_at='2026-10-10T05:00:00Z', limit=2)
    long, _ = input_bundle(tmp_path)
    assert short['source']['snapshot_id'] != long['source']['snapshot_id']
    repeated, _ = input_bundle(tmp_path)
    assert repeated['source']['snapshot_id'] == long['source']['snapshot_id']
