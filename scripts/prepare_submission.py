"""Create a fresh, allowlisted CutoverGuard publication candidate; never publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TREES = {'cutoverguard': {'.py'}, 'cutover_static': {'.html', '.css', '.js'},
         'cutover_examples': {'.json'},
         'databricks_demo/ready': {'.sql', '.json'}, 'native_databricks/ready': {'.json','.md'},
         'databricks_demo/migration-final': {'.sql'}}
SCRIPTS = ('build_cutover_examples.py', 'build_cutover_preview.py', 'build_databricks_demo.py', 'build_retail_dashboard.py', 'build_retail_genie.py',
           'check_databricks.py', 'run_cutoverguard_databricks.py', 'run_cutoverguard_files.py',
           'setup_cutoverguard_native.py', 'setup_cutoverguard_volume.py', 'update_cutoverguard_dashboard.py',
           'inspect_cutoverguard_upload.py',
           'validate_public_retail.py', 'prepare_submission.py',
           'run_defect_experiment.py','run_tpch_scale.py','verify_aggregate_baseline.py',
           'build_tpch_dashboard.py','build_migration_gate.py','build_pipeline_dashboard.py')
TESTS = ('test_cutover_engine.py', 'test_cutover_databricks.py', 'test_cutover_http.py',
         'test_cutover_replay.py', 'test_databricks_demo.py', 'test_native_databricks.py',
         'test_public_retail.py', 'test_dashboard_dollars.py',
         'test_file_ingestion.py', 'test_volume_setup.py', 'test_upload_inspection.py',
         'test_historical_cloud_import.py', 'test_retail_dashboard.py', 'test_prepare_submission.py',
         'test_retail_genie.py','test_aggregate_baseline.py','test_defect_experiment.py',
         'test_tpch_scale.py','test_baseline_verification.py','test_tpch_dashboard.py','test_migration_gate.py','test_pipeline_dashboard.py')
DOCS = ('contract', 'databricks', 'native', 'real-data', 'file-ingestion', 'demo')
EVIDENCE = ('cutover_results/defective-cloud-v2.json', 'cutover_results/fixed-cloud.json',
            'databricks_files/retail-run-002/verification-summary.json',
            'experiments/randomized-002.json','experiments/randomized-002.md',
            'evidence/tpch-scale-summary.json','evidence/aggregate-checks-summary.json',
            'evidence/independent-crosswalk-count.sql','evidence/verify-pipeline-promotion.sql',
            'evidence/pipeline-promotion-summary.json', 'evidence/genie-benchmark-summary.json', 'evidence/genie-benchmark-questions.json',
            'assets/demo/dashboard-pipeline.png','assets/demo/promotion-gate-failed.png',
            'assets/demo/genie-initial-benchmark.png','assets/demo/genie-regression-benchmark.png',
            'assets/demo/genie-unit-conversion-failure.png','assets/demo/genie-blocked-explanation.png',
            'assets/demo/genie-totals-explanation.png','assets/demo/genie-top-customer-explanation.png')
EXCLUDED = ['.env and credentials/keys', '.venv and caches', 'prep except selected public guides',
            'AGENTS.md and SummaryAndReview', 'AlphaAudit app.py/alpha_audit/static/examples',
            'native run state and diagnostics', 'raw public dataset and generated retail extracts',
            'SSH probe instance metadata', 'tmp/practice/browser downloads']
RULES = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'databricks_token': re.compile(r'\bdapi[a-zA-Z0-9]{24,}\b'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'aws_access_key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sources(root):
    """An explicit allowlist, never a recursive copy of the workspace."""
    root = Path(root)
    selected = [('README.md', 'README.md'), ('cutover_app.py', 'cutover_app.py')]
    if (root/'pytest.ini').is_file():
        selected.append(('pytest.ini','pytest.ini'))
    for tree, extensions in TREES.items():
        folder = root / tree
        if folder.exists():
            selected += [(str(p.relative_to(root)), str(p.relative_to(root)))
                         for p in sorted(folder.rglob('*')) if p.is_file() and p.suffix in extensions
                         and '__pycache__' not in p.parts
                         and str(p.relative_to(root))!='databricks_demo/ready/lakeflow_pipeline.sql']
    selected += [('scripts/' + n, 'scripts/' + n) for n in SCRIPTS if (root / 'scripts' / n).is_file()]
    selected += [('tests/' + n, 'tests/' + n) for n in TESTS if (root / 'tests' / n).is_file()]
    selected += [(n, n) for n in EVIDENCE if (root / n).is_file()]
    selected += [('prep/cutoverguard-' + n + '.md', 'docs/cutoverguard-' + n + '.md')
                 for n in DOCS if (root / ('prep/cutoverguard-' + n + '.md')).is_file()]
    selected += [('docs/'+n,'docs/'+n) for n in ('ownership-evidence.md','tpch-scale.md','tpch-dashboard-and-gate.md','genie-benchmarks.md') if (root/'docs'/n).is_file()]
    return sorted(set(selected))


def transform(source, data):
    changes = []
    if source.endswith('.png'):
        return data, changes  # Visually reviewed crops; never rewrite compressed evidence bytes.
    configuration = ROOT / '.redactions.json'
    if configuration.is_file():
        rules = json.loads(configuration.read_text()).get('replacements', [])
        for before, after in rules:
            if not isinstance(before, str) or not before or not isinstance(after, str):
                raise ValueError('Invalid local redaction configuration')
            # Regex boundaries preserve numeric observations and hash fragments.
            pattern = rb'(?<![A-Za-z0-9])' + re.escape(before.encode()) + rb'(?![A-Za-z0-9])'
            updated = re.sub(pattern, lambda match: after.encode(), data)
            if updated != data:
                changes.append('Apply private local publication redaction')
            data = updated
    if source.startswith('databricks_demo/migration-final/') or source == 'evidence/independent-crosswalk-count.sql':
        data = re.sub(rb'tpch_swap_[a-f0-9]{16}_source',b'YOUR_FROZEN_TPCH_SOURCE',data)
        changes.append('Replace private snapshot table with a portable placeholder')
    if source.endswith('.md'):
        text = data.decode('utf-8')
        if source == 'prep/cutoverguard-contract.md':
            text = '# CutoverGuard input and report contract\n\n' + text[text.index('## Input bundle'):]
            changes.append('Remove workspace-only coordination and file ownership instructions')
        for doc in DOCS:
            text = text.replace('prep/cutoverguard-' + doc + '.md', 'docs/cutoverguard-' + doc + '.md')
        text = text.replace('--create --resume --output native_databricks/run-001',
                            '--create --output native_databricks/new-run')
        text = text.replace('native_databricks/run-001', 'native_databricks/new-run')
        text = text.replace('.venv/bin/python scripts/check_environment.py --core', '.venv/bin/python -m pip check')
        text = text.replace('This is the bounded next Databricks demonstration requested in `SummaryAndReview/review.md`.',
                            'This is a bounded, prepared Databricks file-ingestion demonstration.')
        if source == 'README.md':
            text = text.replace('The older AlphaAudit experiment is a separate\nfallback outside the CutoverGuard submission surface.', '')
            text += '\n## Reproducing this publication snapshot\n\nThe included cloud reports and native payloads capture the original synthetic demo; their table names do not imply access in another workspace. Run fresh SQL fixtures in your own workspace, then pass their report paths to native setup. Native update/recovery commands require your own saved state. Public-retail extracts are regenerated using the documented UCI download and attribution; raw or customer-derived extracts are excluded here. The included ingestion tests use invented invoice lines.\n'
            changes.append('Add clean-checkout cloud and public-data reproduction boundary')
        if source == 'prep/cutoverguard-native.md':
            text = '''# Native dashboard and Genie

Prepared payloads contain four counters, signed account effects, three comparison tables and a linked Genie space. Original project resources were created and SQL/configuration checked; those private run checkpoints are excluded here. Rendering and model answers require human inspection. This package does not publish or grant access.

In your own authenticated workspace, execute the synthetic defective/fixed runners first (see [platform workflow](cutoverguard-databricks.md)), then use explicit report paths and a fresh output directory:

```sh
.venv/bin/python scripts/setup_cutoverguard_native.py --help
.venv/bin/python scripts/setup_cutoverguard_native.py --prepare-only --output native_databricks/new-run
```

Inspect prepared requests and use the creator's --create options with your warehouse and report paths. Fresh creation validates all five SQL datasets against the exact oracle. Recovery --resume requires your own saved state; never assume the original author's run exists. Ambiguous creation outcomes block retries until existing IDs are recovered. Counter updates require that same saved state, validate SQL and draft content, and use an ETag. No publish operation is provided.

Genie instructions distinguish synthetic snapshots, supplied completeness declarations, currencies, exact units and discrepancies from losses. Ask the four prepared questions and inspect SQL/results. Benchmark configuration does not prove answer quality. Ready for review is not migration approval.
'''
            changes.append('Replace private run chronology with portable setup and verification boundaries')
        if text.encode() != data:
            changes.append('Public documentation links / fresh native-run example')
        data = text.encode()
    if source == 'tests/test_public_retail.py':
        text = data.decode('utf-8').replace("str(Path('.venv/bin/python'))", 'sys.executable')
        if text.encode() != data:
            text = 'import sys\n' + text
            changes.append('Use current interpreter in portable CLI test; no workspace .venv assumption')
            data = text.encode()
    return data, changes


def scan(folder):
    findings = []
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file():
            value = path.read_text(errors='replace')
            for name, expression in RULES.items():
                for match in expression.finditer(value):
                    findings.append({'file': str(path.relative_to(folder)), 'line': value.count('\n', 0, match.start()) + 1,
                                     'rule': name})  # No credential value or snippet is emitted.
    tool = shutil.which('gitleaks')
    status = 'unavailable_not_verified'
    if tool:
        result = subprocess.run([tool, 'dir', str(folder), '--no-banner', '--redact'],
                                capture_output=True, timeout=120)
        status = 'scanner_passed' if result.returncode == 0 else 'findings_or_tool_error'
    return {'limited_pattern_findings': findings, 'gitleaks': status,
            'boundary': 'Limited token/key patterns are not an exhaustive secret audit. Missing gitleaks is not a passed scanner.'}


def validate(folder, run_tests=True):
    folder = Path(folder)
    command = "import sys;sys.path.insert(0,'.');from cutover_app import validate_report;from cutoverguard.engine import reconcile;from cutoverguard.fixtures import fixture;assert reconcile(fixture())['summary']['primary_issue_count']==2;assert validate_report(reconcile(fixture('fixed')))['status']=='ready_for_review';print('runtime imports and reconciliation verified')"
    runtime = subprocess.run([sys.executable, '-I', '-c', command], cwd=folder, capture_output=True, text=True, timeout=30)
    evidence = {'runtime_import_and_reconcile': runtime.returncode == 0,
                'python': sys.version.split()[0], 'socket_and_browser': 'not_verified_by_this_check'}
    if not evidence['runtime_import_and_reconcile']:
        raise ValueError('Staged runtime/import verification failed')
    if run_tests:
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
                                cwd=folder, env=environment, capture_output=True, text=True, timeout=120)
        evidence.update(tests_passed=result.returncode == 0, test_summary=result.stdout.strip().splitlines()[-1:] or [])
        if result.returncode:
            raise ValueError('Staged tests failed; inspect staged files and dependencies with python -m pytest')
    else:
        evidence['tests_passed'] = 'not_run'
    return evidence


def prepare(root, destination, run_tests=True):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination exists; choose a fresh snapshot directory. No overwrites.')
    if destination == root or root.is_relative_to(destination):
        raise ValueError('Destination must not contain or replace the source workspace')
    destination.mkdir(parents=True)
    manifest = {'purpose': 'Reviewable public submission candidate; not published', 'files': [],
                'excluded_categories': EXCLUDED, 'git_initialization': 'not_performed',
                'publication': 'not_performed; repository destination/authorization still required'}
    for source, target in sources(root):
        path = root / source
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Source symlink/escape rejected: ' + source)
        raw = path.read_bytes()
        value, changes = transform(source, raw)
        out = destination / target
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(value)
        manifest['files'].append({'source': source, 'target': target, 'source_sha256': sha(raw),
                                  'staged_sha256': sha(value), 'transformations': changes})
    (destination / 'requirements.txt').write_text('pytest>=9,<10\n# Runtime uses the standard library. Optional: openpyxl for XLSX.\n')
    (destination / '.gitignore').write_text('.venv/\n.env\n.env.*\n__pycache__/\n.pytest_cache/\n*.pem\n*.key\nid_rsa*\nid_ed25519*\n.databrickscfg\n.redactions.json\npublication-manifest.json\n.DS_Store\nnative_databricks/*/\n!native_databricks/ready/\ndatabricks_files/\npublic_retail_validation/\nsubmission_build/\n')
    preview = subprocess.run([sys.executable, 'scripts/build_cutover_preview.py'], cwd=destination,
                             capture_output=True, timeout=30)
    if preview.returncode:
        raise ValueError('Staged offline preview generation failed')
    manifest['validation'] = validate(destination, run_tests)
    # Cache files are rehearsal artifacts, never publication content.
    for cache in destination.rglob('__pycache__'):
        shutil.rmtree(cache)
    manifest['secret_scan'] = scan(destination)
    missing_links=[]
    for page in destination.rglob('*.md'):
        for link in re.findall(r'\]\(([^\s)]+)', page.read_text()):
            if '://' in link or link.startswith('#'):
                continue
            target=(page.parent/link.split('#')[0]).resolve()
            if not target.is_relative_to(destination) or not target.exists():
                missing_links.append({'file':str(page.relative_to(destination)), 'target':link})
    manifest['missing_local_markdown_links']=missing_links
    manifest['generated_files'] = [{'target': str(p.relative_to(destination)), 'sha256': sha(p.read_bytes())}
                                   for p in sorted(destination.rglob('*')) if p.is_file()
                                   and str(p.relative_to(destination)) not in {e['target'] for e in manifest['files']}]
    manifest['source_changed_after_copy'] = [entry['source'] for entry in manifest['files']
                                           if sha((root / entry['source']).read_bytes()) != entry['source_sha256']]
    (destination / 'publication-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh directory, for example submission_build/review-001')
    parser.add_argument('--no-tests', action='store_true', help='Runtime check only; manifest records tests not run')
    args = parser.parse_args()
    try:
        result = prepare(ROOT, args.output, not args.no_tests)
        print(json.dumps({'output': str(args.output), 'files': len(result['files']),
                          'validation': result['validation'], 'secret_scan': result['secret_scan'],
                          'source_changed_after_copy': result['source_changed_after_copy'],
                          'publication': result['publication']}, indent=2))
        return 1 if result['secret_scan']['limited_pattern_findings'] or result['secret_scan']['gitleaks'] == 'findings_or_tool_error' or result['source_changed_after_copy'] or result['missing_local_markdown_links'] else 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print('Submission staging incomplete:', str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
