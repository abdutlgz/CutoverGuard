"""Independent invariants and expected finding types for randomized study design."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import pytest
from cutoverguard.engine import canonical, reconcile
from cutoverguard.defect_experiment import CATEGORIES, ledger, make_trial, evaluate_trial, run_experiment, markdown_report


@pytest.mark.parametrize('category,types,count', [
    ('ownership_swap', {'account_mismatch'}, 2),
    ('compensating_pennies', {'amount_mismatch'}, 2),
    ('missing_extra', {'event_missing', 'event_extra'}, 2),
    ('same_day_timestamp', {'timestamp_mismatch'}, 1),
    ('duplicate_key', {'duplicate_event_key'}, 1),
    ('single_amount_change', {'amount_mismatch'}, 1),
    ('clean', set(), 0),
])
def test_each_independent_injection_matches_material_primary_findings(category, types, count):
    bundle, expected = make_trial(category, seed=71, trial=4, rows=17)
    original = deepcopy(bundle)
    report = reconcile(bundle)
    findings = [item for item in report['issues'] if item['severity'] == 'critical' and item['check_id'] != 'business']
    assert len(findings) == count
    assert {item['type'] for item in findings} == types
    assert {(item['type'], item['key']) for item in findings} == {tuple(value) for value in expected['expected_primary']}
    assert bundle == original
    assert report['status'] == ('ready_for_review' if category == 'clean' else 'blocked')
    assert 'expected_primary' not in canonical(bundle)


def test_generated_accounts_preserve_zeros_and_ledgers_are_varied():
    snapshots = [ledger(seed=123, trial=i, rows=20) for i in range(10)]
    assert len({hashlib.sha256(canonical(snapshot).encode()).hexdigest() for snapshot in snapshots}) == 10
    for snapshot in snapshots:
        assert all(isinstance(profile['account_id'], str) and profile['account_id'].startswith('0') for profile in snapshot['profiles'])
        assert all(isinstance(event['amount_minor'], str) for event in snapshot['transactions'])
        assert all(event['event_time'].endswith('+00:00') for event in snapshot['transactions'])
    assert snapshots[0] == ledger(seed=123, trial=0, rows=20)
    assert snapshots[0] != ledger(seed=124, trial=0, rows=20)


def test_categories_share_source_but_never_mutate_source():
    source = ledger(seed=7, trial=2, rows=10)
    for category in CATEGORIES:
        bundle, _ = make_trial(category, seed=7, trial=2, rows=10)
        assert bundle['source'] == source
        assert bundle['candidate']['snapshot_id'] != source['snapshot_id']
        assert bundle['candidate'] is not bundle['source']


def sums(rows):
    output = {}
    for row in rows:
        key = (row['currency'], row['event_time'][:10])
        output[key] = output.get(key, 0) + int(row['amount_minor'])
    return output


@pytest.mark.parametrize('category', ['ownership_swap', 'compensating_pennies', 'missing_extra', 'same_day_timestamp', 'clean'])
def test_undetectable_aggregate_families_preserve_counts_daily_currency_and_owner_multiset(category):
    for trial in range(12):
        bundle, _ = make_trial(category, seed=2026, trial=trial, rows=30)
        source, candidate = bundle['source']['transactions'], bundle['candidate']['transactions']
        assert len(source) == len(candidate)
        assert sums(source) == sums(candidate)
        assert sorted(row['account_id'] for row in source) == sorted(row['account_id'] for row in candidate)


def test_timestamp_injection_is_one_second_and_same_utc_day():
    from datetime import datetime
    b, injection = make_trial('same_day_timestamp', seed=5, trial=8)
    key = injection['changed_event_ids'][0]
    source = next(r for r in b['source']['transactions'] if r['event_id'] == key)
    candidate = next(r for r in b['candidate']['transactions'] if r['event_id'] == key)
    assert (datetime.fromisoformat(candidate['event_time']) - datetime.fromisoformat(source['event_time'])).total_seconds() == 1
    assert source['event_time'][:10] == candidate['event_time'][:10]


def test_full_experiment_reproducible_counts_and_positive_controls():
    result = run_experiment(seed=33, trials_per_category=3, rows=12)
    assert result == run_experiment(seed=33, trials_per_category=3, rows=12)
    assert result['total_trials'] == 21 and result['success']
    summaries = {row['category']: row for row in result['summary']}
    for category in CATEGORIES:
        assert summaries[category]['exact_primary_matches'] == 3
        assert summaries[category]['unusable_review_states'] == 0
        if category == 'clean':
            assert summaries[category]['cutoverguard_false_positives'] == summaries[category]['baseline_false_positives'] == 0
        else:
            assert summaries[category]['cutoverguard_detected'] == 3
    assert summaries['single_amount_change']['baseline_detected'] == 3
    assert summaries['duplicate_key']['baseline_detected'] == 3
    for category in ('ownership_swap', 'compensating_pennies', 'missing_extra', 'same_day_timestamp'):
        assert summaries[category]['baseline_detected'] == 0
    assert all(len(row['ledger_sha256']) == len(row['candidate_sha256']) == 64 for row in result['trials'])
    assert 'not real-world accuracy' in markdown_report(result)


def test_nonready_alone_is_neither_detection_nor_clean_false_positive(monkeypatch):
    import cutoverguard.defect_experiment as module
    real = module.reconcile
    def incomplete(bundle):
        report = real(bundle)
        report['status'] = 'incomplete'
        report['issues'] = []
        return report
    monkeypatch.setattr(module, 'reconcile', incomplete)
    row = module.evaluate_trial('ownership_swap', rows=10)
    assert not row['cutoverguard_detected'] and row['unusable_review_state']
    clean = module.evaluate_trial('clean', rows=10)
    assert not clean['cutoverguard_false_positive'] and clean['unusable_review_state']


@pytest.mark.parametrize('kwargs', [{'trials_per_category': 0}, {'rows': 3}, {'rows': 5000}, {'seed': 'x'}])
def test_experiment_scope_bounds(kwargs):
    with pytest.raises(ValueError):
        run_experiment(**kwargs)


def test_cli_saves_json_and_markdown_without_overwrite(tmp_path):
    output = tmp_path / 'experiment.json'
    import sys
    command = [sys.executable, 'scripts/run_defect_experiment.py', '--trials', '2', '--rows', '10', '--output', str(output)]
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert json.loads(output.read_text())['total_trials'] == 14
    assert output.with_suffix('.md').exists()
    old = output.read_bytes()
    second = subprocess.run(command, text=True, capture_output=True)
    assert second.returncode != 0
    assert output.read_bytes() == old


def test_markdown_uses_exact_detection_fractions_and_clean_only_false_positive_rates():
    packet = {'seed': 123, 'rows_per_source_ledger': 10, 'total_trials': 6, 'experiment_id': 'abc', 'limitations': [],
              'summary': [{'category': 'ownership_swap', 'trials': 3, 'cutoverguard_detected': 3, 'baseline_detected': 1,
                           'exact_primary_matches': 3, 'cutoverguard_false_positives': 0, 'baseline_false_positives': 0},
                          {'category': 'clean', 'trials': 3, 'cutoverguard_detected': 0, 'baseline_detected': 0,
                           'exact_primary_matches': 3, 'cutoverguard_false_positives': 0, 'baseline_false_positives': 0}]}
    text = markdown_report(packet)
    assert '| ownership_swap | 3 | 3/3 (100%) | 1/3 (33.33%) | 3 | N/A (defect trials) |' in text
    assert '| clean | 3 | N/A (control) | N/A (control) | 3 | CG 0/3 (0%); baseline 0/3 (0%) |' in text
    assert 'Exact count fractions are authoritative' in text
