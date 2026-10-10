"""Seeded sensitivity experiment on designed synthetic defects, not accuracy claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import random
from .engine import canonical, reconcile

EXPERIMENT_VERSION = "1.0"
CATEGORIES = ("ownership_swap", "compensating_pennies", "missing_extra", "same_day_timestamp", "duplicate_key", "single_amount_change", "clean")


def _random(seed, trial, purpose):
    digest = hashlib.sha256(canonical([EXPERIMENT_VERSION, seed, trial, purpose]).encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def ledger(seed=20261010, trial=0, rows=100):
    """Generate a varied but conditioned ledger with guaranteed eligible defect pairs."""
    if type(seed) is not int or type(trial) is not int or trial < 0 or type(rows) is not int or not 4 <= rows <= 4999:
        raise ValueError("Require integer seed, nonnegative trial, and 4..4999 rows")
    rng = _random(seed, trial, "source")
    count = min(50, max(2, rows // 10))
    # Raw numeric-looking identifiers are retained as strings, including zeros.
    accounts = [str(value).zfill(9) for value in rng.sample(range(1, 10_000_000), count)]
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    events = []
    for i in range(rows):
        timestamp = start + timedelta(days=rng.randrange(7), seconds=rng.randrange(1, 86_398))
        events.append(dict(event_id=f"event-{i:06d}", account_id=rng.choice(accounts), amount_minor=str(rng.randint(-100_000, 100_000)),
                           currency=rng.choice(["USD", "EUR"]), event_time=timestamp.isoformat()))
    # Ensure even small ledgers contain a same-currency/day pair and distinct owners.
    events[0]["account_id"], events[1]["account_id"] = rng.sample(accounts, 2)
    events[1]["currency"] = events[0]["currency"]
    events[1]["event_time"] = events[0]["event_time"]
    return dict(snapshot_id=f"experiment-source-{seed}-{trial}-{rows}", captured_at=end.isoformat(), mapping_version="synthetic-map-v1",
                window_start=start.isoformat(), window_end=end.isoformat(), complete_through=end.isoformat(),
                profiles=[dict(account_id=account, customer_id="customer-" + account) for account in sorted(accounts)], transactions=events)


def make_trial(category, *, seed=20261010, trial=0, rows=100):
    if category not in CATEGORIES:
        raise ValueError("Unknown defect category")
    source = ledger(seed, trial, rows)
    candidate = deepcopy(source)
    candidate["snapshot_id"] = f"experiment-candidate-{seed}-{trial}-{rows}-{category}"
    rng = _random(seed, trial, category)
    entries = candidate["transactions"]
    expected = set()
    changed = []
    if category == "ownership_swap":
        left = rng.randrange(rows)
        right = rng.choice([i for i, row in enumerate(entries) if row["account_id"] != entries[left]["account_id"]])
        entries[left]["account_id"], entries[right]["account_id"] = entries[right]["account_id"], entries[left]["account_id"]
        expected = {("account_mismatch", entries[i]["event_id"]) for i in (left, right)}
        changed = [entries[left]["event_id"], entries[right]["event_id"]]
    elif category == "compensating_pennies":
        groups = {}
        for i, row in enumerate(entries):
            groups.setdefault((row["currency"], row["event_time"][:10]), []).append(i)
        eligible = [indices for indices in groups.values() if len(indices) >= 2]
        left, right = rng.sample(rng.choice(eligible), 2)
        entries[left]["amount_minor"] = str(int(entries[left]["amount_minor"]) + 1)
        entries[right]["amount_minor"] = str(int(entries[right]["amount_minor"]) - 1)
        expected = {("amount_mismatch", entries[i]["event_id"]) for i in (left, right)}
        changed = [entries[left]["event_id"], entries[right]["event_id"]]
    elif category == "missing_extra":
        index = rng.randrange(rows)
        old = entries[index]["event_id"]
        new = f"injected-extra-{trial:06d}"
        entries[index]["event_id"] = new
        expected = {("event_missing", old), ("event_extra", new)}
        changed = [old, new]
    elif category == "same_day_timestamp":
        index = rng.randrange(rows)
        entries[index]["event_time"] = (datetime.fromisoformat(entries[index]["event_time"]) + timedelta(seconds=1)).isoformat()
        expected = {("timestamp_mismatch", entries[index]["event_id"])}
        changed = [entries[index]["event_id"]]
    elif category == "duplicate_key":
        index = rng.randrange(rows)
        entries.append(deepcopy(entries[index]))
        expected = {("duplicate_event_key", entries[index]["event_id"])}
        changed = [entries[index]["event_id"]]
    elif category == "single_amount_change":
        index = rng.randrange(rows)
        entries[index]["amount_minor"] = str(int(entries[index]["amount_minor"]) + 1)
        expected = {("amount_mismatch", entries[index]["event_id"])}
        changed = [entries[index]["event_id"]]
    bundle = dict(schema_version="1.0", data_kind="synthetic", source=source, candidate=candidate,
                  config={"rule_version": "1.0", "currency_scales": {"USD": 2, "EUR": 2}})
    # Injection expectations stay outside detector input; this is the separate oracle.
    injection = dict(category=category, changed_event_ids=changed, expected_primary=[list(value) for value in sorted(expected)])
    return bundle, injection


def evaluate_trial(category, *, seed=20261010, trial=0, rows=100):
    from .aggregate_baseline import compute_bundle
    bundle, injection = make_trial(category, seed=seed, trial=trial, rows=rows)
    report = reconcile(bundle)
    baseline = compute_bundle(bundle)
    actual = [(item["type"], item["key"]) for item in report["issues"] if item["severity"] == "critical" and item["check_id"] != "business"]
    expected = {tuple(value) for value in injection["expected_primary"]}
    actual_set = set(actual)
    baseline_failed = [check.get("id", check.get("name", "unnamed")) for check in baseline["checks"] if not check["pass"]]
    has_critical = any(item["severity"] == "critical" for item in report["issues"])
    detected = bool(expected) and expected.issubset(actual_set) and report["status"] == "blocked"
    return dict(seed=seed, trial=trial, category=category, rows=rows, run_id=report["run_id"],
                ledger_sha256=hashlib.sha256(canonical(bundle["source"]).encode()).hexdigest(),
                candidate_sha256=hashlib.sha256(canonical(bundle["candidate"]).encode()).hexdigest(),
                injection=injection, expected_primary=injection["expected_primary"], actual_primary=[list(value) for value in sorted(actual)],
                expected_primary_found=expected.issubset(actual_set), exact_primary_match=actual_set == expected and len(actual) == len(expected),
                cutoverguard_detected=detected, cutoverguard_status=report["status"],
                cutoverguard_false_positive=(category == "clean" and has_critical),
                unusable_review_state=(report["status"] not in ("blocked", "ready_for_review")),
                baseline_detected=bool(expected) and bool(baseline_failed), baseline_false_positive=(category == "clean" and bool(baseline_failed)),
                baseline_failed_checks=baseline_failed, baseline=baseline)


def run_experiment(*, seed=20261010, trials_per_category=100, rows=100):
    if type(trials_per_category) is not int or not 1 <= trials_per_category <= 1000:
        raise ValueError("Require 1..1000 trials per category")
    trials = [evaluate_trial(category, seed=seed, trial=i, rows=rows) for category in CATEGORIES for i in range(trials_per_category)]
    summary = []
    for category in CATEGORIES:
        records = [row for row in trials if row["category"] == category]
        summary.append(dict(category=category, trials=len(records),
                            cutoverguard_detected=sum(row["cutoverguard_detected"] for row in records),
                            baseline_detected=sum(row["baseline_detected"] for row in records),
                            cutoverguard_false_positives=sum(row["cutoverguard_false_positive"] for row in records),
                            baseline_false_positives=sum(row["baseline_false_positive"] for row in records),
                            exact_primary_matches=sum(row["exact_primary_match"] for row in records),
                            unusable_review_states=sum(row["unusable_review_state"] for row in records)))
    for row in summary:
        row["cutoverguard_detection_fraction"] = None if row["category"] == "clean" else f"{row['cutoverguard_detected']}/{row['trials']}"
        row["baseline_detection_fraction"] = None if row["category"] == "clean" else f"{row['baseline_detected']}/{row['trials']}"
        row["cutoverguard_clean_false_positive_fraction"] = f"{row['cutoverguard_false_positives']}/{row['trials']}" if row["category"] == "clean" else None
        row["baseline_clean_false_positive_fraction"] = f"{row['baseline_false_positives']}/{row['trials']}" if row["category"] == "clean" else None
    result = dict(experiment_version=EXPERIMENT_VERSION, data_kind="synthetic", execution_mode="local", seed=seed,
                  trials_per_category=trials_per_category, rows_per_source_ledger=rows, total_trials=len(trials), summary=summary, trials=trials,
                  generator="Seeded Python Random, SHA-256-derived seeds; seven UTC days, USD/EUR integer minor units, numeric-looking string accounts. First two events conditioned to guarantee an eligible same-day/currency pair and distinct owners.",
                  success=all(row["exact_primary_match"] and not row["unusable_review_state"] and
                              (row["cutoverguard_status"] == "ready_for_review" and not row["cutoverguard_false_positive"] if row["category"] == "clean" else row["cutoverguard_detected"])
                              for row in trials),
                  limitations=["Designed synthetic defect sensitivity, not real-world detection accuracy, migration failure prevalence, or savings.",
                               "A totals/checksum baseline is illustrative; existing production reconciliation tools may already check event and ownership parity.",
                               "Clean-control false positives require critical findings; incomplete evidence alone is recorded separately, not counted as detection.",
                               "Timing, GPU acceleration, Databricks execution and statistical population inference are outside this experiment.",
                               "Baseline checks aggregate counts, separate currency/UTC-day totals and an account-multiset checksum; they do not prove individual event parity."])
    result["experiment_id"] = hashlib.sha256(canonical(result).encode()).hexdigest()
    return result


def markdown_report(result):
    lines = ["# Seeded synthetic defect experiment", "", f"Seed: {result['seed']}; rows per source ledger: {result['rows_per_source_ledger']}; trials: {result['total_trials']}.",
             "", "Counts below describe this designed experiment only. They are not real-world accuracy estimates.",
             "Exact count fractions are authoritative; percentages are rounded to two decimal places.", "",
             "| Category | Trials | CutoverGuard detected | Baseline detected | Exact primary match | Clean false positives (CG / baseline) |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    def fraction(count, total):
        percent = (Decimal(count) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        display = format(percent, ".2f").rstrip("0").rstrip(".")
        return f"{count}/{total} ({display}%)"
    for row in result["summary"]:
        detection = "N/A (control)" if row["category"] == "clean" else fraction(row["cutoverguard_detected"], row["trials"])
        baseline = "N/A (control)" if row["category"] == "clean" else fraction(row["baseline_detected"], row["trials"])
        false_positives = ("CG " + fraction(row["cutoverguard_false_positives"], row["trials"]) +
                           "; baseline " + fraction(row["baseline_false_positives"], row["trials"])) if row["category"] == "clean" else "N/A (defect trials)"
        lines.append(f"| {row['category']} | {row['trials']} | {detection} | {baseline} | {row['exact_primary_matches']} | {false_positives} |")
    lines.extend(["", "Experiment fingerprint: `" + result["experiment_id"] + "`", "", "## Limitations", ""])
    lines.extend("- " + item for item in result["limitations"])
    return "\n".join(lines) + "\n"
