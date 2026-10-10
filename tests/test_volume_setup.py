import json
import pytest
from cutoverguard.databricks import CloudError
from scripts.setup_cutoverguard_volume import list_volumes,create_volume

class Runner:
    warehouse_id='w1'
    def __init__(self,fail=False): self.sql=[];self.statement_ids=[];self.fail=fail
    def execute(self,sql):
        self.sql.append(sql);self.statement_ids.append('s'+str(len(self.sql)))
        if sql.startswith('SHOW'): return [['default','existing']]
        if sql.startswith('CREATE'):
            if self.fail: raise CloudError('unknown create outcome')
            return []
        volume=sql.split('.')[-1].strip('`')
        return [[volume,'workspace','default','owner-hidden',None,'MANAGED','comment']]

def test_listing_read_only():
    r=Runner();assert list_volumes(r,'workspace','default')==['workspace.default.existing']
    assert r.sql==['SHOW VOLUMES IN `workspace`.`default`']

def test_unique_create_and_resume_describe_only(tmp_path):
    r=Runner();p=tmp_path/'state.json';s=create_volume(r,p,'workspace','default')
    assert s['status']=='managed_volume_verified'
    assert 'IF NOT EXISTS' not in r.sql[0] and 'DROP' not in ''.join(r.sql)
    assert len(s['statement_ids'])==2
    resumed=Runner();create_volume(resumed,p,'workspace','default',resume=True)
    assert len(resumed.sql)==1 and resumed.sql[0].startswith('DESCRIBE')
    with pytest.raises(FileExistsError): create_volume(Runner(),p,'workspace','default')

def test_failed_create_checkpoint_can_only_describe(tmp_path):
    p=tmp_path/'state.json'
    with pytest.raises(CloudError):create_volume(Runner(True),p,'workspace','default')
    assert json.loads(p.read_text())['status']=='unverified'
    r=Runner();create_volume(r,p,'workspace','default',resume=True)
    assert not any(sql.startswith('CREATE') for sql in r.sql)

def test_unsafe_names_rejected_before_requests(tmp_path):
    r=Runner()
    with pytest.raises(CloudError): create_volume(r,tmp_path/'s.json','a;DROP','default')
    assert not r.sql
