"""Deterministic, synthetic process replay of the two supported demo snapshots.

This animates a known ledger in source-event timestamp order. It is not a cloud
job, a reconstruction of actual migration chronology or a partial cutover verdict.
"""
import hashlib
from .engine import ReconcileError, canonical, instant, reconcile
from .fixtures import fixture

REPLAY_VERSION = "1.0"


def build_replay(scenario="defective"):
    """Return cumulative exact balances for the fixed two-event demonstration."""
    if scenario not in ("defective", "fixed"):
        raise ReconcileError("Replay supports only defective or fixed synthetic scenarios")
    bundle = fixture(scenario)
    comparison = reconcile(bundle)
    source_events = sorted(bundle["source"]["transactions"], key=lambda row: (instant(row["event_time"]), row["event_id"]))
    candidates = {row["event_id"]: row for row in bundle["candidate"]["transactions"]}
    accounts = sorted({row["account_id"] for side in ("source", "candidate") for row in bundle[side]["profiles"]})
    source_balances = {account: 0 for account in accounts}
    candidate_balances = {account: 0 for account in accounts}
    frames = []
    mismatches = 0

    def frame(step, source=None, candidate=None):
        # New arrays at every step preserve previously captured frame snapshots.
        return dict(step=step, event_id=None if source is None else source["event_id"],
                    event_time=None if source is None else instant(source["event_time"]).isoformat(),
                    source_account_id=None if source is None else source["account_id"],
                    candidate_account_id=None if candidate is None else candidate["account_id"],
                    amount_minor=None if source is None else source["amount_minor"],
                    currency=None if source is None else source["currency"],
                    source_balances=[dict(account_id=account, currency="USD", amount_minor=str(source_balances[account])) for account in accounts],
                    candidate_balances=[dict(account_id=account, currency="USD", amount_minor=str(candidate_balances[account])) for account in accounts],
                    account_deltas=[dict(account_id=account, currency="USD", delta_minor=str(candidate_balances[account]-source_balances[account])) for account in accounts],
                    net_delta_minor=str(sum(candidate_balances.values())-sum(source_balances.values())), ownership_mismatches=mismatches)

    frames.append(frame(0))
    for step, source in enumerate(source_events, 1):
        candidate = candidates[source["event_id"]]
        # Fail explicitly if fixture scope changes instead of depicting an unsupported
        # amount/currency/event chronology as this ownership-only simulation.
        if source["currency"] != "USD" or candidate["currency"] != "USD" or source["amount_minor"] != candidate["amount_minor"] or instant(source["event_time"]) != instant(candidate["event_time"]):
            raise ReconcileError("Replay requires matched USD amounts and equivalent event timestamps")
        source_balances[source["account_id"]] += int(source["amount_minor"])
        candidate_balances[candidate["account_id"]] += int(candidate["amount_minor"])
        mismatches += source["account_id"] != candidate["account_id"]
        frames.append(frame(step, source, candidate))
    packet = dict(data_kind="synthetic", scope="process_simulation", scenario=scenario,
                  replay_version=REPLAY_VERSION,
                  source_run_id=hashlib.sha256(canonical(bundle["source"]).encode()).hexdigest(),
                  candidate_run_id=hashlib.sha256(canonical(bundle["candidate"]).encode()).hexdigest(),
                  comparison_run_id=comparison["run_id"], frames=frames,
                  limitations=[
                      "This is a controlled synthetic process simulation in source-event timestamp order, not observed migration chronology.",
                      "Candidate account assignments are looked up by stable event ID; amounts and timestamps match in these two scenarios.",
                      "source_run_id and candidate_run_id are snapshot fingerprints; comparison_run_id is the reconciliation fingerprint. None is a cloud execution or source-system job ID.",
                      "Frames show cumulative USD allocation differences, not losses, savings or partial cutover readiness.",
                      "Replay runs locally; no Databricks job, live stream, production system or automatic remediation is implied.",
                  ])
    packet["replay_id"] = hashlib.sha256(canonical(packet).encode()).hexdigest()
    return packet

# Backwards-compatible local name; HTTP integration uses build_replay.
replay = build_replay
