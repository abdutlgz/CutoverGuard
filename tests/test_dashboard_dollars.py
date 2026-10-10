from copy import deepcopy
import json
import pytest
from scripts.setup_cutoverguard_native import reports,dashboard,native_queries,expected_rows,CloudError
from scripts.update_cutoverguard_dashboard import update


def test_counter_dollar_values_and_bindings_match_exact_oracle():
    r=reports();body=dashboard(r,'a'*32)
    assert expected_rows(r)['defective_kpis']==[[2,'0','2000','3000']]
    sql=native_queries(r)['defective_kpis']
    assert 'WHERE currency=\'USD\'' in sql
    for field in ('net_delta','absolute_account_delta','affected_notional'):
        assert f'CAST({field}_minor/100 AS DECIMAL(38,2)) {field}_usd' in sql
    counters=[item['widget'] for item in body['pages'][0]['layout'] if item['widget'].get('spec',{}).get('widgetType')=='counter']
    assert len(counters)==4
    assert all('(USD)' in w['spec']['frame']['title'] for w in counters[1:])
    assert [w['spec']['encodings']['value']['fieldName'] for w in counters[1:]]==['net_delta_usd','absolute_account_delta_usd','affected_notional_usd']


class Runner:
    warehouse_id='515dff82a7a2c282'
    statement_ids=[]
    def execute(self,sql):
        r=reports();name=next(k for k,v in native_queries(r).items() if v==sql)
        return expected_rows(r)[name]


class Client:
    def __init__(self,body,etag='one'):
        self.body=body;self.etag=etag;self.calls=[]
    def request(self,method,path,payload=None):
        self.calls.append((method,path,payload))
        if method=='patch':
            assert payload['etag']==self.etag
            assert path.endswith('?dataset_catalog=workspace&dataset_schema=default')
            self.body=json.loads(payload['serialized_dashboard'])
        return {'serialized_dashboard':json.dumps(self.body),'etag':self.etag}


def state(tmp_path):
    (tmp_path/'state.json').write_text(json.dumps({'dashboard_id':'b'*32,'space_id':'a'*32,
        'warehouse_id':Runner.warehouse_id,'source_run_ids':{k:v['run_id'] for k,v in reports().items()}}))


def test_existing_draft_updated_once_with_backup_and_etag(tmp_path):
    state(tmp_path);client=Client(dashboard(reports(),'a'*32,dollars=False))
    result=update(reports(),client,Runner(),tmp_path)
    assert result['status']=='created_configuration_verified'
    assert len(list(tmp_path.glob('dashboard-before-dollars-*.json')))==1
    assert len([c for c in client.calls if c[0]=='patch'])==1
    update(reports(),client,Runner(),tmp_path)
    assert len([c for c in client.calls if c[0]=='patch'])==1


@pytest.mark.parametrize('failure',['edited','etag','sql'])
def test_other_edits_missing_etag_or_wrong_sql_block_update(tmp_path,failure):
    state(tmp_path);body=dashboard(reports(),'a'*32,dollars=False)
    if failure=='edited':body['pages'][0]['displayName']='Personal edits'
    client=Client(body,etag='' if failure=='etag' else 'one');runner=Runner()
    if failure=='sql':runner.execute=lambda sql:[['wrong']]
    with pytest.raises(CloudError):update(reports(),client,runner,tmp_path)
    assert not any(call[0]=='patch' for call in client.calls)


def test_workspace_change_blocks_before_any_remote_request(tmp_path):
    state(tmp_path);client=Client(dashboard(reports(),'a'*32,dollars=False))
    with pytest.raises(CloudError,match='Workspace host'):
        update(reports(),client,Runner(),tmp_path,host='https://other.databricks.com')
    assert client.calls==[]
