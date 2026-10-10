"""Hand-calculated cumulative ledger expectations, separate from replay logic."""
from copy import deepcopy
import hashlib
import json
import pytest
from cutoverguard.engine import ReconcileError, canonical, reconcile
from cutoverguard.fixtures import fixture
from cutoverguard.replay import replay


def deltas(frame):
    return {row["account_id"]: row["delta_minor"] for row in frame["account_deltas"]}


def test_defective_hand_calculated_frames():
    r = replay("defective")
    assert [f["step"] for f in r["frames"]] == [0, 1, 2]
    assert deltas(r["frames"][0]) == {"0001": "0", "0002": "0"}
    assert deltas(r["frames"][1]) == {"0001": "-100000", "0002": "100000"}
    assert deltas(r["frames"][2]) == {"0001": "100000", "0002": "-100000"}
    assert [f["net_delta_minor"] for f in r["frames"]] == ["0", "0", "0"]
    assert [f["ownership_mismatches"] for f in r["frames"]] == [0, 1, 2]
    assert [f["event_id"] for f in r["frames"]] == [None, "E1", "E2"]
    assert r["frames"][0]["event_time"] is None
    assert r["frames"][1]["source_account_id"] == "0001"
    assert r["frames"][1]["candidate_account_id"] == "0002"
    assert r["frames"][1]["amount_minor"] == "100000"
    assert {row["account_id"]: row["amount_minor"] for row in r["frames"][2]["source_balances"]} == {"0001": "100000", "0002": "200000"}
    assert {row["account_id"]: row["amount_minor"] for row in r["frames"][2]["candidate_balances"]} == {"0001": "200000", "0002": "100000"}


def test_fixed_preserves_every_intermediate_account():
    r = replay("fixed")
    for frame in r["frames"]:
        assert frame["source_balances"] == frame["candidate_balances"]
        assert deltas(frame) == {"0001": "0", "0002": "0"}
        assert frame["ownership_mismatches"] == 0
        assert frame["net_delta_minor"] == "0"


def test_fingerprints_and_scope_are_explicit():
    for scenario in ("defective", "fixed"):
        r = replay(scenario)
        assert r == replay(scenario)
        assert r["data_kind"] == "synthetic" and r["scope"] == "process_simulation"
        assert r["comparison_run_id"] == reconcile(fixture(scenario))["run_id"]
        assert r["source_run_id"] == hashlib.sha256(canonical(fixture(scenario)["source"]).encode()).hexdigest()
        assert r["candidate_run_id"] == hashlib.sha256(canonical(fixture(scenario)["candidate"]).encode()).hexdigest()
        assert len(r["replay_id"]) == 64
        payload = {key: value for key, value in r.items() if key != "replay_id"}
        assert r["replay_id"] == hashlib.sha256(canonical(payload).encode()).hexdigest()
        assert "status" not in r and "execution" not in r
        json.dumps(r, allow_nan=False)
    assert replay("defective")["replay_id"] != replay("fixed")["replay_id"]


def test_input_and_earlier_frames_remain_immutable(monkeypatch):
    import cutoverguard.replay as module
    b = fixture()
    before = deepcopy(b)
    monkeypatch.setattr(module, "fixture", lambda scenario: b)
    r = module.replay()
    assert b == before
    r["frames"][2]["source_balances"][0]["amount_minor"] = "changed"
    assert r["frames"][0]["source_balances"][0]["amount_minor"] == "0"
    assert r["frames"][1]["source_balances"][0]["amount_minor"] == "100000"


@pytest.mark.parametrize("scenario", ["clean", "malformed", "historical", "", None])
def test_only_two_controlled_scenarios_supported(scenario):
    with pytest.raises(ReconcileError, match="only defective or fixed"):
        replay(scenario)


def test_event_order_uses_utc_instants_not_input_order(monkeypatch):
    import cutoverguard.replay as module
    b = fixture()
    b["source"]["transactions"].reverse()
    b["candidate"]["transactions"].reverse()
    b["source"]["transactions"][1]["event_time"] = "2026-10-09T08:00:00-04:00"
    before = deepcopy(b)
    monkeypatch.setattr(module, "fixture", lambda scenario: b)
    result = module.build_replay()
    assert [frame["event_id"] for frame in result["frames"]] == [None, "E1", "E2"]
    assert result["frames"][1]["event_time"] == "2026-10-09T12:00:00+00:00"
    assert b == before
