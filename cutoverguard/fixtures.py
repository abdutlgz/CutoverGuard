"""Independently constructed synthetic migration scenarios; no private records."""
from copy import deepcopy

_DESCRIPTIONS = {
    "defective": "Same USD 3000 total, two transactions assigned to the wrong accounts.",
    "clean": "Identical source and candidate snapshots.",
    "fixed": "Corrected candidate restores both transaction account associations.",
    "missing_metadata": "Candidate completion watermark is unknown.",
    "duplicate_keys": "Candidate has duplicate profile and event keys; joins are blocked.",
    "lost_identifier": "Candidate loses leading zeros on an account link.",
    "missing_extra": "Candidate replaces E2 with extra E3.",
    "compensating_amounts": "Equal/opposite amount changes cancel at firm level.",
    "mixed_currency": "Separate USD and EUR balances with a changed event currency.",
    "timezone_equivalent": "Different timezone strings represent the same instant.",
    "malformed": "Invalid amount and naive timestamp records require quarantine.",
}

def available_fixtures():
    return [{"id": key, "label": key.replace("_", " ").title(), "description": value} for key, value in _DESCRIPTIONS.items()]

def fixture(kind="defective"):
    if kind not in _DESCRIPTIONS:
        raise ValueError("Unknown synthetic fixture")
    source = dict(snapshot_id="synthetic-source-v1", captured_at="2026-10-09T18:00:00Z", mapping_version="source-map-v1",
                  window_start="2026-10-09T00:00:00Z", window_end="2026-10-10T00:00:00Z", complete_through="2026-10-10T00:00:00Z",
                  profiles=[{"account_id": "0001", "customer_id": "C1"}, {"account_id": "0002", "customer_id": "C2"}],
                  transactions=[{"event_id": "E1", "account_id": "0001", "amount_minor": "100000", "currency": "USD", "event_time": "2026-10-09T12:00:00Z"},
                                {"event_id": "E2", "account_id": "0002", "amount_minor": "200000", "currency": "USD", "event_time": "2026-10-09T13:00:00Z"}])
    source["captured_at"] = "2026-10-10T00:05:00Z"
    candidate = deepcopy(source)
    candidate.update(snapshot_id=f"synthetic-candidate-{kind}-v1", mapping_version="cloud-map-v1")
    transactions = candidate["transactions"]
    if kind == "defective":
        transactions[0]["account_id"], transactions[1]["account_id"] = "0002", "0001"
    elif kind == "missing_metadata":
        candidate["complete_through"] = None
    elif kind == "duplicate_keys":
        candidate["profiles"].append(deepcopy(candidate["profiles"][0]))
        transactions.append(deepcopy(transactions[0]))
    elif kind == "lost_identifier":
        transactions[0]["account_id"] = "1"
    elif kind == "missing_extra":
        transactions[1]["event_id"] = "E3"
    elif kind == "compensating_amounts":
        transactions[0]["amount_minor"], transactions[1]["amount_minor"] = "125000", "175000"
    elif kind == "mixed_currency":
        transactions[0]["currency"] = "EUR"
    elif kind == "timezone_equivalent":
        transactions[0]["event_time"] = "2026-10-09T08:00:00-04:00"
    elif kind == "malformed":
        transactions[0]["amount_minor"] = "1000.50"
        transactions[1]["event_time"] = "2026-10-09T13:00:00"
    return dict(schema_version="1.0", data_kind="synthetic", source=source, candidate=candidate,
                config={"rule_version": "1.0", "currency_scales": {"USD": 2, "EUR": 2}},
                dependency_graph={"nodes": [{"id": "profiles", "label": "Account master"}, {"id": "transactions", "label": "Cash events"}, {"id": "account_cash", "label": "Account cash report"}],
                                  "edges": [{"from": "profiles", "to": "transactions"}, {"from": "transactions", "to": "account_cash"}]})
