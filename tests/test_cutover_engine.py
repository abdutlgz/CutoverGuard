from copy import deepcopy
import json
import pytest
from cutoverguard.engine import reconcile, ReconcileError
from cutoverguard.fixtures import fixture, available_fixtures

def primary(report):
    return {(i["type"], i["key"]) for i in report["issues"] if i["severity"] == "critical" and i["check_id"] != "business"}

def test_hand_checked_ownership_and_money():
    r = reconcile(fixture())
    assert r["status"] == "blocked"
    assert primary(r) == {("account_mismatch", "E1"), ("account_mismatch", "E2")}
    assert r["summary"] == {"primary_issue_count": 2, "impacted_account_count": 2, "quarantined_count": 0, "explanation": "Critical discrepancies block review."}
    assert [(i["account_id"], i["delta_minor"]) for i in r["account_impacts"]] == [("0001", "100000"), ("0002", "-100000")]
    assert r["currency_totals"] == [{"currency": "USD", "source_minor": "300000", "candidate_minor": "300000", "net_delta_minor": "0", "absolute_account_delta_minor": "200000", "affected_notional_minor": "300000"}]

@pytest.mark.parametrize("kind", ["clean", "fixed", "timezone_equivalent"])
def test_controls(kind):
    r = reconcile(fixture(kind))
    assert r["status"] == "ready_for_review"
    assert not r["issues"]
    assert all(c["status"] == "pass" for c in r["checks"])

@pytest.mark.parametrize("kind,expected", [
    ("duplicate_keys", {("duplicate_event_key", "E1"), ("duplicate_profile_key", "0001")}),
    ("lost_identifier", {("invalid_account_link", "E1"), ("account_mismatch", "E1")}),
    ("missing_extra", {("event_missing", "E2"), ("event_extra", "E3")}),
    ("compensating_amounts", {("amount_mismatch", "E1"), ("amount_mismatch", "E2")}),
    ("mixed_currency", {("currency_mismatch", "E1")}),
])
def test_exact_defect_sets(kind, expected):
    assert primary(reconcile(fixture(kind))) == expected

def test_duplicate_finance_unavailable_and_no_multiplication():
    r = reconcile(fixture("duplicate_keys"))
    assert r["status"] == "blocked"
    assert all(i["delta_minor"] is None for i in r["account_impacts"])
    assert all(i["source_minor"] is None for i in r["currency_totals"])
    assert r["coverage"]["candidate"]["transaction_rows"] == 3

def test_quarantine_does_not_fabricate_missing_events():
    r = reconcile(fixture("malformed"))
    assert len(r["quarantine"]) == 2
    assert r["status"] == "incomplete"
    assert not primary(r)
    assert all(i["delta_minor"] is None for i in r["account_impacts"])
    assert r["coverage"]["candidate"]["transaction_rows"] == 2

def test_mixed_currencies_no_combined_money():
    r = reconcile(fixture("mixed_currency"))
    totals = {t["currency"]: t for t in r["currency_totals"]}
    assert totals["USD"]["net_delta_minor"] == "-100000"
    assert totals["EUR"]["net_delta_minor"] == "100000"
    assert totals["USD"]["affected_notional_minor"] == totals["EUR"]["affected_notional_minor"] == "100000"

def test_cancellation_preserves_row_evidence():
    r = reconcile(fixture("compensating_amounts"))
    assert r["currency_totals"][0]["net_delta_minor"] == "0"
    assert r["currency_totals"][0]["absolute_account_delta_minor"] == "50000"
    assert r["summary"]["primary_issue_count"] == 2

def test_unknown_metadata_and_status_priority():
    b = fixture("missing_metadata")
    assert reconcile(b)["status"] == "incomplete"
    b["candidate"]["transactions"][0]["account_id"] = "0002"
    r = reconcile(b)
    assert r["status"] == "blocked"
    assert any(i["type"] == "metadata_incomplete" for i in r["issues"])

def test_mismatched_windows_never_claim_missing_rows():
    b = fixture("clean")
    b["candidate"]["window_start"] = "2026-10-09T12:30:00Z"
    r = reconcile(b)
    assert r["status"] == "incomplete"
    assert not primary(r)
    assert all(i["delta_minor"] is None for i in r["account_impacts"])

def test_half_open_window_and_excluded_counts():
    b = fixture("clean")
    for side in ("source", "candidate"):
        row = deepcopy(b[side]["transactions"][0])
        row.update(event_id="OUT", event_time=b[side]["window_end"])
        b[side]["transactions"].append(row)
    r = reconcile(b)
    assert r["status"] == "ready_for_review"
    assert r["coverage"]["source"]["excluded_rows"] == 1
    assert r["currency_totals"][0]["source_minor"] == "300000"

def test_exact_large_integer_and_negative_money():
    b = fixture("clean")
    for side in ("source", "candidate"):
        b[side]["transactions"][0]["amount_minor"] = "90071992547409931234"
        b[side]["transactions"][1]["amount_minor"] = "-200000"
    r = reconcile(b)
    assert r["currency_totals"][0]["source_minor"] == "90071992547409731234"
    assert r["status"] == "ready_for_review"

@pytest.mark.parametrize("value", [1.5, 100000, "1e5", "1.0", "", None, "01", "+1"])
def test_non_integer_string_money_quarantined(value):
    b = fixture("clean")
    b["candidate"]["transactions"][0]["amount_minor"] = value
    assert reconcile(b)["quarantine"][0]["table"] == "transactions"

def test_id_strings_not_cast_or_stripped():
    b = fixture("clean")
    b["candidate"]["profiles"][0]["account_id"] = " 0001"
    r = reconcile(b)
    assert ("invalid_account_link", "E1") in primary(r)
    assert ("profile_extra", " 0001") in primary(r)

def test_customer_ownership_checked():
    b = fixture("clean")
    b["candidate"]["profiles"][0]["customer_id"] = "C2"
    assert ("customer_mismatch", "0001") in primary(reconcile(b))

def test_reproducibility_and_input_immutability():
    b = fixture()
    old = deepcopy(b)
    r = reconcile(b)
    assert b == old
    reordered = json.loads(json.dumps(b, sort_keys=True))
    assert r == reconcile(reordered)
    assert r["run_id"] != reconcile(fixture("fixed"))["run_id"]

@pytest.mark.parametrize("mutator", [lambda b: b.update(schema_version="2"), lambda b: b.update(data_kind="live"), lambda b: b["config"]["currency_scales"].update(USD=3), lambda b: b["source"].update(window_start="2026-10-11T00:00:00Z"), lambda b: b["source"].update(transactions={})])
def test_invalid_contract_rejected(mutator):
    b = fixture()
    mutator(b)
    with pytest.raises(ReconcileError):
        reconcile(b)

def test_all_fixtures_json_serializable():
    for item in available_fixtures():
        json.dumps(reconcile(fixture(item["id"])), allow_nan=False)

def test_quarantined_profile_is_unknown_not_false_missing_link():
    b = fixture("clean")
    b["candidate"]["profiles"][0]["customer_id"] = None
    r = reconcile(b)
    assert r["status"] == "incomplete"
    assert not primary(r)
    assert next(c for c in r["checks"] if c["id"] == "identity")["status"] == "unknown"

def test_known_event_outside_window_is_missing_from_window():
    b = fixture("clean")
    b["candidate"]["transactions"][0]["event_time"] = b["candidate"]["window_end"]
    r = reconcile(b)
    assert ("event_missing", "E1") in primary(r)
    assert r["currency_totals"][0]["net_delta_minor"] == "-100000"

def test_customer_changes_count_affected_accounts_without_cash_delta():
    b = fixture("clean")
    b["candidate"]["profiles"][0]["customer_id"] = "C2"
    assert reconcile(b)["summary"]["impacted_account_count"] == 1

def test_unknown_graph_reference_rejected():
    b = fixture()
    b["dependency_graph"]["edges"].append({"from": "profiles", "to": "nonexistent"})
    with pytest.raises(ReconcileError, match="declared node"):
        reconcile(b)

@pytest.mark.parametrize("timestamp", ["2026-10-09T12:00:00.1234567Z", "0001-01-01T00:00:00+14:00"])
def test_unsupported_timestamp_precision_and_range_quarantined(timestamp):
    b = fixture("clean")
    b["candidate"]["transactions"][0]["event_time"] = timestamp
    r = reconcile(b)
    assert r["status"] == "incomplete"
    assert len(r["quarantine"]) == 1

@pytest.mark.parametrize("side", ["source", "candidate"])
@pytest.mark.parametrize("missing_key", [None, 123, "", "  "])
def test_unknown_quarantined_event_keys_do_not_prove_absence(side, missing_key):
    b = fixture("clean")
    b[side]["transactions"][0]["event_id"] = missing_key
    r = reconcile(b)
    assert r["status"] == "incomplete"
    assert not primary(r)
    assert len(r["quarantine"]) == 1

@pytest.mark.parametrize("side", ["source", "candidate"])
@pytest.mark.parametrize("missing_key", [None, 123, "", "  "])
def test_unknown_profile_keys_do_not_prove_absent_profile_or_link(side, missing_key):
    b = fixture("clean")
    b[side]["profiles"][0]["account_id"] = missing_key
    r = reconcile(b)
    assert r["status"] == "incomplete"
    assert not primary(r)
    assert all(i["delta_minor"] is None for i in r["account_impacts"])

@pytest.mark.parametrize("table,key", [("transactions", "event_id"), ("profiles", "account_id")])
def test_known_event_discrepancy_remains_blocking_despite_unknown_quarantined_key(table, key):
    b = fixture("clean")
    b["source"][table][0][key] = None
    b["candidate"]["transactions"][1]["amount_minor"] = "200001"
    r = reconcile(b)
    assert r["status"] == "blocked"
    assert primary(r) == {("amount_mismatch", "E2")}
    assert len(r["quarantine"]) == 1


def test_currency_limitation_lists_exact_declared_subset():
    b = fixture('clean')
    b['config']['currency_scales'] = {'USD': 2}
    r = reconcile(b)
    assert 'Declared scale-2 currencies: USD; currencies are never combined.' in r['limitations']
    assert not any('EUR' in text for text in r['limitations'])


def test_full_size_quarantined_known_counterparts_do_not_become_missing_events():
    b = fixture('clean')
    source = b['source']['transactions'][0]
    b['source']['transactions'] = [{**source, 'event_id': 'event-' + str(i)} for i in range(5000)]
    b['candidate']['transactions'] = [{**row, 'amount_minor': 'not-money'} for row in b['source']['transactions']]
    r = reconcile(b)
    assert r['status'] == 'incomplete'
    assert r['summary']['primary_issue_count'] == 0
    assert r['summary']['quarantined_count'] == 5000
    assert r['coverage']['source']['in_window_rows'] == 5000
