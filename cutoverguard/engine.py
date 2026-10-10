"""Exact, deterministic snapshot reconciliation; supplied metadata is not proof."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from . import __version__

class ReconcileError(ValueError):
    pass

def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ReconcileError("Input must contain finite JSON values") from exc

def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an aware ISO string")
    if re.search(r"[.,][0-9]{7,}", value):
        raise ValueError("timestamp precision must not exceed six fractional digits")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires timezone")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp is outside supported UTC range") from exc

def reconcile(bundle):
    if not isinstance(bundle, dict):
        raise ReconcileError("Input must be a JSON object")
    raw = canonical(bundle)
    if bundle.get("schema_version") != "1.0" or bundle.get("data_kind") not in ("synthetic", "historical", "unverified"):
        raise ReconcileError("schema_version must be 1.0 and data_kind must be synthetic, historical or unverified")
    config = bundle.get("config")
    if not isinstance(config, dict) or set(config) != {"rule_version", "currency_scales"} or config.get("rule_version") != "1.0":
        raise ReconcileError("Supported config is rule_version 1.0 with declared scale-2 currencies")
    currency_scales = config.get("currency_scales")
    if not isinstance(currency_scales, dict) or not currency_scales or any(currency not in {"USD", "EUR", "GBP"} or type(scale) is not int or scale != 2 for currency, scale in currency_scales.items()):
        raise ReconcileError("Declare a nonempty subset of USD/EUR/GBP, each with minor-unit scale 2")
    issues, quarantine, coverage, manifests, tables = [], [], {}, {}, {}
    metadata_reasons = []
    def issue(check, typ, side, key, field, old, new, message, severity="critical", evidence=None):
        item = dict(check_id=check, type=typ, severity=severity, side=side, key=key,
                    field=field, source_value=old, candidate_value=new, message=message,
                    evidence=evidence or {})
        item["issue_id"] = hashlib.sha256(canonical(item).encode()).hexdigest()[:24]
        issues.append(item)
    for side in ("source", "candidate"):
        snapshot = bundle.get(side)
        if not isinstance(snapshot, dict):
            raise ReconcileError(f"{side} snapshot must be an object")
        for key in ("snapshot_id", "mapping_version"):
            if not isinstance(snapshot.get(key), str) or not snapshot[key].strip():
                metadata_reasons.append(f"{side}.{key} is missing")
        try:
            start, end = instant(snapshot.get("window_start")), instant(snapshot.get("window_end"))
            if start >= end:
                raise ValueError("window_start must precede window_end")
            captured = instant(snapshot.get("captured_at"))
            watermark = None if snapshot.get("complete_through") is None else instant(snapshot["complete_through"])
        except ValueError as exc:
            raise ReconcileError(f"{side} metadata: {exc}") from exc
        if watermark is None or watermark < end:
            metadata_reasons.append(f"{side} completion watermark does not establish coverage through window end")
        if captured < end or (watermark is not None and watermark > captured):
            metadata_reasons.append(f"{side} capture time is inconsistent with window/completion declaration")
        manifests[side] = (start, end)
        tables[side] = {}
        coverage[side] = {"profile_rows": 0, "transaction_rows": 0, "in_window_rows": 0, "excluded_rows": 0}
        for table in ("profiles", "transactions"):
            rows = snapshot.get(table)
            if not isinstance(rows, list) or len(rows) > 5000:
                raise ReconcileError(f"{side}.{table} must be an array of at most 5000 rows")
            coverage[side]["profile_rows" if table == "profiles" else "transaction_rows"] = len(rows)
            valid = []
            for number, record in enumerate(rows, 1):
                try:
                    if not isinstance(record, dict):
                        raise ValueError("record must be an object")
                    keys = ("account_id", "customer_id") if table == "profiles" else ("event_id", "account_id")
                    for key in keys:
                        if not isinstance(record.get(key), str) or not record[key].strip():
                            raise ValueError(f"{key} must be a nonempty string; raw identifiers are preserved")
                    normalized = dict(record)
                    if table == "transactions":
                        amount = record.get("amount_minor")
                        if not isinstance(amount, str) or not re.fullmatch(r"-?(0|[1-9][0-9]*)", amount) or len(amount) > 38:
                            raise ValueError("amount_minor must be an integer string of at most 38 characters")
                        if record.get("currency") not in currency_scales:
                            raise ValueError("currency must be one of the declared scale-2 currencies: " + ", ".join(sorted(currency_scales)))
                        normalized["_instant"] = instant(record.get("event_time"))
                        normalized["_amount"] = int(amount)
                        normalized["_in_window"] = start <= normalized["_instant"] < end
                        coverage[side]["in_window_rows" if normalized["_in_window"] else "excluded_rows"] += 1
                    valid.append(normalized)
                except ValueError as exc:
                    quarantine.append(dict(side=side, table=table, row=number, reason=str(exc), record=deepcopy(record)))
            tables[side][table] = valid
    unknown_keys = set()
    for q in quarantine:
        key = "account_id" if q["table"] == "profiles" else "event_id"
        record = q["record"]
        if not isinstance(record, dict) or not isinstance(record.get(key), str) or not record[key].strip():
            unknown_keys.add((q["side"], q["table"], key))
    raw_keys = {(side, table): {row.get(key) for row in bundle[side][table]
                              if isinstance(row, dict) and isinstance(row.get(key), str)}
                for side in ("source", "candidate")
                for table, key in (("profiles", "account_id"), ("transactions", "event_id"))}
    def unknown_quarantined_key(side, table, key):
        return (side, table, key) in unknown_keys
    same_window = manifests["source"] == manifests["candidate"]
    if not same_window:
        metadata_reasons.append("Source and candidate comparison windows differ; transaction parity is unavailable")
    ambiguous = False
    indexes = {}
    for side in ("source", "candidate"):
        indexes[side] = {}
        for table, key in (("profiles", "account_id"), ("transactions", "event_id")):
            rows = tables[side][table]
            counts = Counter(row[key] for row in rows)
            duplicates = {value for value, count in counts.items() if count > 1}
            for value in sorted(duplicates):
                ambiguous = True
                issue("identity" if table == "profiles" else "transactions", "duplicate_profile_key" if table == "profiles" else "duplicate_event_key", side, value, key,
                      value if side == "source" else None, value if side == "candidate" else None,
                      f"{side} has {counts[value]} records for this {key}; ambiguous joins are blocked", evidence={"row_count": counts[value]})
            indexes[side][table] = {row[key]: row for row in rows if row[key] not in duplicates}
        known = {row.get("account_id") for row in bundle[side]["profiles"] if isinstance(row, dict) and isinstance(row.get("account_id"), str)}
        for row in tables[side]["transactions"]:
            if row["account_id"] not in known and not unknown_quarantined_key(side, "profiles", "account_id"):
                ambiguous = True
                issue("identity", "invalid_account_link", side, row["event_id"], "account_id",
                      row["account_id"] if side == "source" else None, row["account_id"] if side == "candidate" else None,
                      "Transaction references an account absent from its profile snapshot", evidence={"account_id": row["account_id"]})
    def compare(table, check, keyfield, fields):
        old, new = indexes["source"][table], indexes["candidate"][table]
        if table == "transactions":
            old = {key: row for key, row in old.items() if row["_in_window"]}
            new = {key: row for key, row in new.items() if row["_in_window"]}
        for key in sorted(old.keys() | new.keys()):
            a, b = old.get(key), new.get(key)
            if a is None or b is None:
                # A quarantined/duplicated counterpart may exist. Never label that row missing.
                other_side = "source" if a is None else "candidate"
                counterpart_present = key in raw_keys[(other_side, table)] and key not in indexes[other_side][table]
                if counterpart_present or unknown_quarantined_key(other_side, table, keyfield):
                    continue
                typ = ("profile" if table == "profiles" else "event") + ("_extra" if a is None else "_missing")
                issue(check, typ, "candidate" if a is None else "source", key, keyfield,
                      key if a else None, key if b else None, "Key appears in only one comparable snapshot")
                continue
            for field, typ in fields:
                av, bv = a[field], b[field]
                if field == "event_time":
                    av, bv = a["_instant"], b["_instant"]
                if field == "amount_minor":
                    av, bv = a["_amount"], b["_amount"]
                if av != bv:
                    issue(check, typ, "both", key, field, a[field], b[field], f"{field} differs for the same stable key",
                          evidence={"source_account_id": a.get("account_id"), "candidate_account_id": b.get("account_id")})
    compare("profiles", "identity", "account_id", [("customer_id", "customer_mismatch")])
    if same_window:
        compare("transactions", "transactions", "event_id", [("account_id", "account_mismatch"), ("amount_minor", "amount_mismatch"), ("currency", "currency_mismatch"), ("event_time", "timestamp_mismatch")])
    impacts, totals = [], []
    unavailable = ambiguous or bool(quarantine) or not same_window
    sums = {side: defaultdict(int) for side in ("source", "candidate")}
    for side in sums:
        for row in tables[side]["transactions"]:
            if row["_in_window"]:
                sums[side][(row["account_id"], row["currency"])] += row["_amount"]
    keys = sorted(sums["source"].keys() | sums["candidate"].keys())
    for account, currency in keys:
        a, b = sums["source"][(account, currency)], sums["candidate"][(account, currency)]
        impacts.append(dict(account_id=account, currency=currency, source_minor=None if unavailable else str(a), candidate_minor=None if unavailable else str(b), delta_minor=None if unavailable else str(b-a)))
        if not unavailable and a != b:
            issue("business", "account_balance_mismatch", "both", account, "balance_minor", str(a), str(b), "Candidate minus source account balance differs; this is an allocation discrepancy, not a loss", evidence={"currency": currency})
    changed_events = {i["key"] for i in issues if i["check_id"] == "transactions" and i["type"] != "duplicate_event_key"}
    for currency in sorted({key[1] for key in keys}):
        a = sum(value for (account, curr), value in sums["source"].items() if curr == currency)
        b = sum(value for (account, curr), value in sums["candidate"].items() if curr == currency)
        absolute = sum(abs(sums["candidate"][key]-sums["source"][key]) for key in keys if key[1] == currency)
        affected = 0
        for event in sorted(changed_events):
            source = indexes["source"]["transactions"].get(event)
            candidate = indexes["candidate"]["transactions"].get(event)
            if source and source["_in_window"] and source["currency"] == currency:
                affected += abs(source["_amount"])
            elif candidate and candidate["_in_window"] and candidate["currency"] == currency:
                affected += abs(candidate["_amount"])
        totals.append(dict(currency=currency, source_minor=None if unavailable else str(a), candidate_minor=None if unavailable else str(b), net_delta_minor=None if unavailable else str(b-a), absolute_account_delta_minor=None if unavailable else str(absolute), affected_notional_minor=None if unavailable else str(affected)))
    for reason in metadata_reasons:
        issue("transactions", "metadata_incomplete", "both", "manifest", "metadata", None, None, reason, severity="warning")
    impacted = {r["account_id"] for r in impacts if r["delta_minor"] not in (None, "0")}
    for item in issues:
        if item["check_id"] == "transactions" and item["severity"] == "critical":
            for side in ("source", "candidate"):
                row = indexes[side]["transactions"].get(item["key"])
                if row:
                    impacted.add(row["account_id"])
    for item in issues:
        if item["severity"] != "critical":
            continue
        if item["type"] in ("profile_missing", "profile_extra", "customer_mismatch", "duplicate_profile_key"):
            impacted.add(item["key"])
        elif item["type"] == "invalid_account_link":
            impacted.add(item["evidence"]["account_id"])
        elif item["type"] == "duplicate_event_key":
            for side in ("source", "candidate"):
                impacted.update(row["account_id"] for row in tables[side]["transactions"] if row["event_id"] == item["key"])
    issues.sort(key=lambda item: (item["check_id"], item["type"], item["key"], item["side"], item["issue_id"]))
    incomplete = bool(metadata_reasons or quarantine or unavailable)
    critical = any(i["severity"] == "critical" for i in issues)
    checks = []
    for check, name in (("identity", "Identity integrity"), ("transactions", "Transaction parity"), ("business", "Financial business parity")):
        count = sum(i["check_id"] == check and i["severity"] == "critical" for i in issues)
        unknown = incomplete and (check != "identity" or bool(quarantine))
        checks.append(dict(id=check, name=name, status="fail" if count else "unknown" if unknown else "pass", count=count,
                           description="Exact comparison on declared snapshots; unavailable evidence remains visible."))
    primary = sum(i["severity"] == "critical" and i["check_id"] != "business" for i in issues)
    status = "blocked" if critical else "incomplete" if incomplete else "ready_for_review"
    graph = bundle.get("dependency_graph", {"nodes": [], "edges": []})
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ReconcileError("dependency_graph must contain nodes and edges arrays")
    if len(graph["nodes"]) > 100 or len(graph["edges"]) > 500:
        raise ReconcileError("dependency_graph supports at most 100 nodes and 500 edges")
    node_ids = set()
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"].strip() or not isinstance(node.get("label"), str):
            raise ReconcileError("dependency graph nodes require string id and label")
        if node["id"] in node_ids:
            raise ReconcileError("dependency graph node IDs must be unique")
        node_ids.add(node["id"])
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or not isinstance(edge.get("from"), str) or not isinstance(edge.get("to"), str) or edge["from"] not in node_ids or edge["to"] not in node_ids:
            raise ReconcileError("dependency graph edges must reference declared node IDs")
    return dict(schema_version="1.0", engine_version=__version__, run_id=hashlib.sha256((raw+__version__).encode()).hexdigest(),
                data_kind=bundle["data_kind"], execution={"mode": "local", "engine_version": __version__}, input_bundle=deepcopy(bundle), status=status,
                summary={"primary_issue_count": primary, "impacted_account_count": len(impacted), "quarantined_count": len(quarantine),
                         "explanation": "Critical discrepancies block review." if critical else "Evidence is incomplete." if incomplete else "Implemented checks pass under supplied declarations; human cutover review is still required."},
                checks=checks, issues=issues, account_impacts=impacts, currency_totals=totals, quarantine=quarantine, coverage=coverage,
                limitations=["Synthetic fixtures are designed examples, not observed migration failure rates.", "Completeness and lineage metadata are supplied declarations, not independently verified.", ("Declared scale-2 currencies: " + ", ".join(sorted(currency_scales)) + "; currencies are never combined."), "Timestamps require timezones, supported UTC range and at most six fractional digits; greater precision is quarantined rather than truncated.", "Candidate-minus-source deltas and affected notional are not losses or savings.", "Ambiguous keys, invalid links or quarantined rows make financial aggregates unavailable.", "Ready for review is not production approval, regulatory compliance or an automatic cutover."], dependency_graph=deepcopy(graph))
