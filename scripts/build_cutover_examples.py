"""Create synthetic inputs and separately specified expected primary findings."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cutoverguard.fixtures import fixture, available_fixtures

EXPECTED = {
    "defective": {"status": "blocked", "primary": [["account_mismatch", "E1"], ["account_mismatch", "E2"]], "account_deltas": {"0001/USD": "100000", "0002/USD": "-100000"}, "USD_net_minor": "0", "USD_absolute_account_delta_minor": "200000", "USD_affected_notional_minor": "300000"},
    "clean": {"status": "ready_for_review", "primary": []},
    "fixed": {"status": "ready_for_review", "primary": []},
    "missing_metadata": {"status": "incomplete", "primary": []},
    "duplicate_keys": {"status": "blocked", "primary": [["duplicate_event_key", "E1"], ["duplicate_profile_key", "0001"]], "aggregates": "unavailable"},
    "lost_identifier": {"status": "blocked", "primary": [["invalid_account_link", "E1"], ["account_mismatch", "E1"]], "aggregates": "unavailable"},
    "missing_extra": {"status": "blocked", "primary": [["event_missing", "E2"], ["event_extra", "E3"]]},
    "compensating_amounts": {"status": "blocked", "primary": [["amount_mismatch", "E1"], ["amount_mismatch", "E2"]], "account_deltas": {"0001/USD": "25000", "0002/USD": "-25000"}, "USD_net_minor": "0"},
    "mixed_currency": {"status": "blocked", "primary": [["currency_mismatch", "E1"]], "account_deltas": {"0001/USD": "-100000", "0001/EUR": "100000", "0002/USD": "0"}},
    "timezone_equivalent": {"status": "ready_for_review", "primary": []},
    "malformed": {"status": "incomplete", "primary": [], "quarantined_count": 2, "aggregates": "unavailable"},
}

def main():
    target = Path(__file__).resolve().parents[1] / "cutover_examples"
    target.mkdir(exist_ok=True)
    manifest = {"data_kind": "synthetic", "description": "Designed fixtures and separately specified oracle expectations. Detector inputs do not contain these expected findings. No client data or observed performance claims.", "fixtures": []}
    for item in available_fixtures():
        body = json.dumps(fixture(item["id"]), indent=2, ensure_ascii=False) + "\n"
        filename = item["id"] + ".json"
        (target / filename).write_text(body)
        manifest["fixtures"].append({**item, "file": filename, "file_sha256": hashlib.sha256(body.encode()).hexdigest(), "expected": EXPECTED[item["id"]]})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Created {len(manifest['fixtures'])} synthetic inputs and expected ledger manifest")

if __name__ == "__main__":
    main()
