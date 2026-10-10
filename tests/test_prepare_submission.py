from pathlib import Path
import pytest
from scripts.prepare_submission import sources, transform, scan, prepare


def test_allowlist_excludes_credentials_fallback_and_private_state(tmp_path):
    for name in ('README.md', 'cutover_app.py', '.env', 'app.py',
                 'native_databricks/run-001/state.json', 'cutoverguard/engine.py',
                 'prep/cutoverguard-contract.md', 'prep/private-research.md'):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    chosen = dict(sources(tmp_path))
    assert 'cutoverguard/engine.py' in chosen
    assert chosen['prep/cutoverguard-contract.md'] == 'docs/cutoverguard-contract.md'
    assert not set(chosen) & {'.env', 'app.py', 'native_databricks/run-001/state.json', 'prep/private-research.md'}


def test_existing_destination_and_source_ancestor_refused(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    with pytest.raises(ValueError, match='exists'):
        prepare(root, root)
    assert not list(root.iterdir())


def test_document_transform_removes_workspace_coordination():
    value, changes = transform('prep/cutoverguard-contract.md', b'# Private\nAlphaAudit coordination\n## Input bundle\nPublic contract\n')
    assert b'AlphaAudit' not in value
    assert b'Public contract' in value and changes
    value, changes = transform('README.md', b'[Contract](prep/cutoverguard-contract.md)')
    assert b'[Contract](docs/cutoverguard-contract.md)' in value
    assert b'raw or customer-derived extracts are excluded' in value
    assert b'invented invoice lines' in value


def test_scan_reports_locations_without_secret_values(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.prepare_submission.shutil.which', lambda name: None)
    token = 'dapi' + 'x'*32
    (tmp_path/'bad.txt').write_text('line one\n'+token)
    result = scan(tmp_path)
    assert result['limited_pattern_findings'] == [{'file':'bad.txt','line':2,'rule':'databricks_token'}]
    assert token not in str(result)
    assert result['gitleaks'] == 'unavailable_not_verified'


def test_resolved_destination_cannot_hide_source_via_symlink(tmp_path):
    root=tmp_path/'source'; root.mkdir()
    link=tmp_path/'alias'; link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='exists'):
        prepare(root, link)


def test_escaping_allowlisted_source_rejected_before_copy(tmp_path):
    root=tmp_path/'source';root.mkdir()
    (root/'README.md').write_text('public')
    external=tmp_path/'outside.py';external.write_text('private')
    (root/'cutover_app.py').symlink_to(external)
    with pytest.raises(ValueError, match='symlink'):
        prepare(root,tmp_path/'stage',run_tests=False)
    assert not (tmp_path/'stage/cutover_app.py').exists()
