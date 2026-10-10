import json
from types import SimpleNamespace
import pytest
from cutoverguard.databricks import CloudError
from scripts.inspect_cutoverguard_upload import inspect,listing


def test_only_read_only_lists_and_state_unchanged(tmp_path):
    state={'remote_directory':'/Volumes/workspace/default/workshop/cutoverguard_'+'a'*16,
           'profile':'chosen','attempted':'upload_manifest','completed':[],'status':'unverified'}
    path=tmp_path/'state.json';path.write_text(json.dumps(state));before=path.read_bytes()
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stdout=json.dumps([{'path':'manifest.csv'}]),stderr='')
    result=inspect(tmp_path,run)
    assert len(calls)==2
    assert all(c[1:3]==['fs','ls'] and '--profile' in c for c in calls)
    assert path.read_bytes()==before
    assert result['run_listing']['prepared_files_present']['manifest.csv']
    assert not result['remote_file_contents_verified']
    assert not result['write_permissions_verified']


def test_raw_error_and_listing_metadata_never_saved(tmp_path):
    state={'remote_directory':'/Volumes/a/b/c/cutoverguard_'+'b'*16}
    (tmp_path/'state.json').write_text(json.dumps(state))
    def run(*args,**kwargs):
        return SimpleNamespace(returncode=1,stdout='',stderr='PERMISSION_DENIED bearer PRIVATE_VALUE')
    result=inspect(tmp_path,run)
    assert result['run_listing']['status']=='permission_denied'
    assert 'PRIVATE_VALUE' not in (tmp_path/'diagnostics/upload-inspection.json').read_text()


def test_bad_checkpoint_rejected_before_remote_calls(tmp_path):
    (tmp_path/'state.json').write_text(json.dumps({'remote_directory':'/Volumes/a/b/c/../other'}))
    with pytest.raises(CloudError):
        inspect(tmp_path,lambda *a,**k:pytest.fail('remote call'))


def test_unrecognized_output_is_not_claimed_missing():
    result=listing('/Volumes/a/b/c',run=lambda *a,**k:SimpleNamespace(returncode=0,stdout='{}',stderr=''))
    assert result=={'status':'unexpected_response'}
