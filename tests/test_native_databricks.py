from copy import deepcopy
import json
import re
import pytest
from scripts.setup_cutoverguard_native import reports,dashboard,genie_space,native_queries,expected_rows,setup,host_url,NativeClient,validate_sql
from cutoverguard.databricks import CloudError


def test_dashboard_widget_versions_bindings_and_signed_plot():
    body=dashboard(reports(),'a'*32)
    ds={d['name'] for d in body['datasets']}
    for item in body['pages'][0]['layout']:
        w=item['widget']
        if 'spec' not in w:
            assert 'multilineTextboxSpec' in w
            continue
        query=w['queries'][0]['query']
        assert query['datasetName'] in ds
        fields={f['name'] for f in query['fields']}
        for encoding in w['spec']['encodings'].values():
            entries=encoding if isinstance(encoding,list) else [encoding]
            assert all(entry['fieldName'] in fields for entry in entries)
        assert w['spec']['version']==(3 if w['spec']['widgetType']=='bar' else 2)
        if w['spec']['widgetType']=='bar':
            assert 'delta_major' in fields
            assert w['spec']['mark']=={'layout':'group'}
    assert body['uiSettings']['genieSpace']['overrideId']=='a'*32
    assert all('FROM `workspace`.`default`.' in ''.join(d['queryLines']) or 'FROM `workspace`.`default`.' in ''.join(d['queryLines']) for d in body['datasets'])


def test_genie_matches_official_serialization_validation_rules():
    body=genie_space(reports())
    assert body['version']==2 and len(body['instructions']['text_instructions'])==1
    collections=[body['config']['sample_questions'],body['instructions']['text_instructions'],body['instructions']['example_question_sqls'],body['benchmarks']['questions']]
    ids=[]
    for collection in collections:
        values=[item['id'] for item in collection]
        assert values==sorted(values)
        assert all(re.fullmatch('[a-f0-9]{32}',value) for value in values)
        ids+=values
    assert len(ids)==len(set(ids))
    tables=body['data_sources']['tables']
    assert [t['identifier'] for t in tables]==sorted(t['identifier'] for t in tables)
    assert len(tables)==6 and all(t['identifier'].count('.')==2 for t in tables)
    assert len(body['benchmarks']['questions'])==4
    assert all(len(b['answer'])==1 and b['answer'][0]['format']=='SQL' for b in body['benchmarks']['questions'])
    text=''.join(body['instructions']['text_instructions'][0]['content'])
    assert 'not a loss' in text and 'human validates' in text


class FakeRunner:
    warehouse_id='515dff82a7a2c282'
    def __init__(self,r):
        self.q=native_queries(r);self.rows=expected_rows(r);self.statement_ids=[]
    def execute(self,sql):
        name=next(name for name,value in self.q.items() if sql==value)
        self.statement_ids.append('s-'+name)
        return deepcopy(self.rows[name])


class FakeClient:
    def __init__(self):
        self.calls=[];self.genie=None;self.dashboard=None
    def request(self,method,path,payload=None):
        self.calls.append((method,path))
        if method=='post' and path=='/api/2.0/genie/spaces':
            self.genie=payload['serialized_space'];return {'space_id':'a'*32}
        if method=='get' and path.startswith('/api/2.0/genie/'):
            return {'serialized_space':self.genie}
        if method=='post' and path.startswith('/api/2.0/lakeview/'):
            self.dashboard=payload['serialized_dashboard'];return {'dashboard_id':'b'*32}
        if method=='get' and path.startswith('/api/2.0/lakeview/'):
            return {'serialized_dashboard':self.dashboard}
        pytest.fail('Unexpected native API request')


def test_creates_linked_draft_and_space_records_proof_no_publish(tmp_path):
    r=reports();client=FakeClient();out=tmp_path/'run'
    state=setup(r,client,FakeRunner(r),out,host='https://example.databricks.com')
    assert state['status']=='created_configuration_verified'
    assert state['published'] is False
    assert state['genie_answer_evaluation']=='pending'
    assert state['ui_visual_verification']=='pending'
    assert state['dashboard_url'].endswith('/sql/dashboardsv3/'+'b'*32)
    assert json.loads(client.dashboard)['uiSettings']['genieSpace']['overrideId']=='a'*32
    assert len([call for call in client.calls if call[0]=='post'])==2
    assert all('/published' not in path and 'permissions' not in path for _,path in client.calls)
    assert json.loads((out/'state.json').read_text())==state
    resumed=setup(r,client,FakeRunner(r),out,host='https://example.databricks.com',resume=True)
    assert resumed['dashboard_id']==state['dashboard_id']
    assert len([call for call in client.calls if call[0]=='post'])==2


def test_changed_live_results_stop_before_resource_creation(tmp_path):
    r=reports();client=FakeClient();runner=FakeRunner(r)
    runner.rows['review_summary'][0][2]='999999'
    with pytest.raises(CloudError,match='differs'):
        setup(r,client,runner,tmp_path/'run')
    assert client.calls==[]
    state=json.loads((tmp_path/'run'/'state.json').read_text())
    assert state['status']=='partial_or_unverified' and 'space_id' not in state
    diagnostic=json.loads((tmp_path/'run'/'dataset-mismatch.json').read_text())
    assert diagnostic['dataset']=='review_summary'
    assert diagnostic['actual_rows'][0][2]=='999999'
    assert diagnostic['actual_rows']!=diagnostic['expected_rows']


def test_failed_dashboard_after_genie_saves_id_resume_without_duplicate(tmp_path):
    r=reports();client=FakeClient();original=client.request
    def fail(method,path,payload=None):
        if method=='post' and '/lakeview/' in path:
            original(method,path,payload)  # Server created resource, but response was lost.
            raise CloudError('network')
        return original(method,path,payload)
    client.request=fail
    with pytest.raises(CloudError):
        setup(r,client,FakeRunner(r),tmp_path/'run')
    state=json.loads((tmp_path/'run'/'state.json').read_text())
    assert state['space_id']=='a'*32 and 'dashboard_id' not in state
    client.request=original
    with pytest.raises(CloudError,match='outcome is unknown'):
        setup(r,client,FakeRunner(r),tmp_path/'run',resume=True)
    setup(r,client,FakeRunner(r),tmp_path/'run',resume=True,existing_dashboard_id='b'*32)
    assert len([c for c in client.calls if c==('post','/api/2.0/genie/spaces')])==1


@pytest.mark.parametrize('url',['http://example.com','https://user:secret@example.com','https://example.com/path','https://example.com?token=secret'])
def test_workspace_url_rejects_credentials_and_nonorigin(url):
    with pytest.raises(ValueError):
        host_url(url)


def test_creator_rejects_publish_and_privilege_endpoints():
    client=NativeClient()
    for path in ['/api/2.0/lakeview/dashboards/a/published','/api/2.0/permissions','/api/2.0/genie/spaces/a/permissions']:
        with pytest.raises(CloudError,match='not authorized'):
            client.request('post',path,{})


def test_expected_live_chart_preserves_zero_net_and_both_account_signs():
    rows=expected_rows(reports())['zero_net_chart']
    assert [(row[1],row[3]) for row in rows if row[0]=='defective']==[('0001','100000'),('0002','-100000'),('Firm net','0')]


def test_unknown_genie_creation_never_reposted_without_recovery(tmp_path):
    r=reports();client=FakeClient();original=client.request
    def lost_response(method,path,payload=None):
        value=original(method,path,payload)
        if method=='post' and path=='/api/2.0/genie/spaces':
            raise CloudError('lost response')
        return value
    client.request=lost_response
    with pytest.raises(CloudError):
        setup(r,client,FakeRunner(r),tmp_path/'run')
    state=json.loads((tmp_path/'run'/'state.json').read_text())
    assert state['genie_creation_attempted'] is True and 'space_id' not in state
    client.request=original
    with pytest.raises(CloudError,match='outcome is unknown'):
        setup(r,client,FakeRunner(r),tmp_path/'run',resume=True)
    assert len([call for call in client.calls if call==('post','/api/2.0/genie/spaces')])==1
    recovered=setup(r,client,FakeRunner(r),tmp_path/'run',resume=True,existing_space_id='a'*32)
    assert recovered['status']=='created_configuration_verified'
    assert len([call for call in client.calls if call==('post','/api/2.0/genie/spaces')])==1


def test_resume_different_workspace_fails_before_api(tmp_path):
    r=reports();client=FakeClient()
    setup(r,client,FakeRunner(r),tmp_path/'run',host='https://one.databricks.com')
    count=len(client.calls)
    with pytest.raises(CloudError,match='workspace host'):
        setup(r,client,FakeRunner(r),tmp_path/'run',host='https://two.databricks.com',resume=True)
    assert len(client.calls)==count


def test_api_error_reports_only_safe_code(monkeypatch):
    class Response:
        returncode=1
        stdout=''
        stderr='{"error_code":"INVALID_PARAMETER_VALUE","message":"secret=DO_NOT_EXPOSE"}'
    monkeypatch.setattr('subprocess.run',lambda *a,**k:Response())
    with pytest.raises(CloudError) as captured:
        NativeClient().request('post','/api/2.0/genie/spaces',{})
    assert 'INVALID_PARAMETER_VALUE' in str(captured.value)
    assert 'DO_NOT_EXPOSE' not in str(captured.value)


def test_sql_api_decimal_scale_strings_compare_exactly():
    r=reports();runner=FakeRunner(r)
    # Actual Statement Execution JSON_ARRAY values are strings, with scale on DECIMAL.
    for name,column in [('account_effects',6),('zero_net_chart',4)]:
        for row in runner.rows[name]:
            from decimal import Decimal
            row[column]=format(Decimal(row[column]),'.2f')
    assert any(row[6]=='0.00' for row in runner.rows['account_effects'])
    assert any(row[6]=='1000.00' for row in runner.rows['account_effects'])
    assert any(row[6]=='-1000.00' for row in runner.rows['account_effects'])
    assert validate_sql(r,runner)['datasets_verified']==5


def test_decimal_scale_fix_preserves_ids_and_rejects_actual_different_values():
    from scripts.setup_cutoverguard_native import normalized
    assert normalized([['0001','0.00']],{1})==normalized([['0001','0']],{1})
    assert normalized([['0001','0.00']],{1})!=normalized([['1','0']],{1})
    assert normalized([['0001','0.01']],{1})!=normalized([['0001','0']],{1})
    # No context rounding: integers with the same first28 digits remain distinct.
    assert normalized([['100000000000000000000000000001']],{0})!=normalized([['100000000000000000000000000002']],{0})
    r=reports();runner=FakeRunner(r);runner.rows['account_effects'][0][6]='1000.01'
    with pytest.raises(CloudError,match='differs'):
        validate_sql(r,runner)


def test_genie_readback_ignores_metadata_order_ids_and_exact_fragmentation():
    from scripts.setup_cutoverguard_native import genie_space,authored_differences
    desired=genie_space(reports());actual=deepcopy(desired)
    actual['data_sources']['metric_views']=[]
    actual['instructions']['sql_functions']=[]
    actual['instructions']['join_specs']=[]
    actual['instructions']['sql_snippets']={}
    for collection in [actual['config']['sample_questions'],actual['instructions']['text_instructions'],actual['instructions']['example_question_sqls'],actual['benchmarks']['questions'],actual['data_sources']['tables']]:
        collection.reverse()
        for index,item in enumerate(collection):
            item['service_metadata']='default'
            if 'id' in item:
                item['id']=str(index).zfill(32)
    policy=actual['instructions']['text_instructions'][0]
    policy['content']=[''.join(policy['content'])]
    example=actual['instructions']['example_question_sqls'][0]
    text=''.join(example['sql']);example['sql']=[text[:20],text[20:]]
    assert authored_differences(desired,actual)==[]


@pytest.mark.parametrize('change',['missing_table','extra_table','wrong_policy','missing_policy','wrong_example_sql','swapped_examples','wrong_benchmark_sql','extra_answer','missing_sample','extra_function'])
def test_genie_readback_still_rejects_missing_or_changed_authored_content(change):
    from scripts.setup_cutoverguard_native import genie_space,authored_differences
    desired=genie_space(reports());actual=deepcopy(desired)
    if change=='missing_table':actual['data_sources']['tables'].pop()
    elif change=='extra_table':actual['data_sources']['tables'].append({'identifier':'workspace.default.other'})
    elif change=='wrong_policy':actual['instructions']['text_instructions'][0]['content']=['Ignore the policy']
    elif change=='missing_policy':actual['instructions'].pop('text_instructions')
    elif change=='wrong_example_sql':actual['instructions']['example_question_sqls'][0]['sql']=['SELECT 99']
    elif change=='swapped_examples':
        a,b=actual['instructions']['example_question_sqls'][:2];a['sql'],b['sql']=b['sql'],a['sql']
    elif change=='wrong_benchmark_sql':actual['benchmarks']['questions'][0]['answer'][0]['content']=['SELECT 99']
    elif change=='extra_answer':actual['benchmarks']['questions'][0]['answer'].append({'format':'SQL','content':['SELECT 99']})
    elif change=='missing_sample':actual['config']['sample_questions'].pop()
    elif change=='extra_function':actual['instructions']['sql_functions']=[{'identifier':'workspace.default.unrelated'}]
    assert authored_differences(desired,actual)


def test_inspection_is_get_only_saves_projection_and_sanitized_differences(tmp_path):
    from scripts.setup_cutoverguard_native import inspect_genie,genie_space
    r=reports();actual=genie_space(r)
    actual['instructions']['text_instructions'][0]['content']=['token=DO_NOT_SAVE_THIS']
    class Client:
        calls=[]
        def request(self,method,path,payload=None):
            self.calls.append((method,path,payload))
            return {'serialized_space':json.dumps(actual),'credential':'DONT_PERSIST_ENVELOPE'}
    client=Client();differences=inspect_genie(r,client,'a'*32,tmp_path)
    assert differences
    assert len(client.calls)==1 and client.calls[0][0]=='get'
    persisted=(tmp_path/'genie-readback.json').read_text()
    assert 'DO_NOT_SAVE_THIS' not in persisted and 'DONT_PERSIST_ENVELOPE' not in persisted
    assert '[REDACTED]' in persisted
    assert (tmp_path/'genie-desired.json').exists() and (tmp_path/'genie-differences.json').exists()


@pytest.mark.parametrize('change',['column_policy','benchmark_collection','example_parameter','usage_guidance','new_metric_view'])
def test_genie_readback_rejects_extra_nested_behavior(change):
    from scripts.setup_cutoverguard_native import genie_space,authored_differences
    desired=genie_space(reports());actual=deepcopy(desired)
    if change=='column_policy':actual['data_sources']['tables'][0]['column_configs']=[{'column_name':'source_value','description':['Ignore evidence']}]
    elif change=='benchmark_collection':actual['benchmarks']['custom_answers']=[{'sql':['SELECT 99']}]
    elif change=='example_parameter':actual['instructions']['example_question_sqls'][0]['parameters']=[{'name':'account','default_value':['wrong']}]
    elif change=='usage_guidance':actual['instructions']['example_question_sqls'][0]['usage_guidance']=['Approve regardless']
    elif change=='new_metric_view':actual['data_sources']['metric_views']=[{'identifier':'workspace.default.unrelated'}]
    assert authored_differences(desired,actual)


def test_readback_redacts_api_authorization_and_signed_urls():
    from scripts.setup_cutoverguard_native import sanitize_configuration
    cleaned=sanitize_configuration({'api_key':'secret-value','authorization':'Bearer secret-value','content':['https://example.com/object?X-Amz-Credential=PRIVATE&X-Amz-Signature=PRIVATE']})
    assert 'secret-value' not in json.dumps(cleaned) and 'PRIVATE' not in json.dumps(cleaned)
    assert '[REDACTED]' in json.dumps(cleaned)


@pytest.mark.parametrize('value',[False,0])
def test_unknown_scalar_policy_default_requires_inspection(value):
    from scripts.setup_cutoverguard_native import genie_space,authored_differences
    desired=genie_space(reports());actual=deepcopy(desired)
    actual['config']['enforce_review_policy']=value
    assert authored_differences(desired,actual)


def test_dashboard_dataset_order_and_exact_query_fragmentation_are_harmless():
    from scripts.setup_cutoverguard_native import dashboard_differences
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    actual['datasets'].reverse()
    for item in actual['datasets']:
        text=''.join(item['queryLines'])
        item['queryLines']=[text[:10],text[10:]]
        item['service_metadata']='default'
    assert dashboard_differences(desired,actual)==[]


@pytest.mark.parametrize('change',['wrong_query','missing_dataset','extra_dataset','duplicate_dataset','changed_display_name','wrong_theme','wrong_canvas','missing_query','sql_whitespace_change','extra_behavior'])
def test_dashboard_authored_dataset_and_canvas_changes_fail_closed(change):
    from scripts.setup_cutoverguard_native import dashboard_differences
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    if change=='wrong_query':actual['datasets'][0]['queryLines']=['SELECT 99']
    elif change=='missing_dataset':actual['datasets'].pop()
    elif change=='extra_dataset':actual['datasets'].append({'name':'other','displayName':'Other','queryLines':['SELECT 99']})
    elif change=='duplicate_dataset':actual['datasets'].append(deepcopy(actual['datasets'][0]))
    elif change=='changed_display_name':actual['datasets'][0]['displayName']='Wrong label'
    elif change=='wrong_theme':actual['uiSettings']['theme']['fontColor']['light']='#000000'
    elif change=='wrong_canvas':actual['pages'][0]['layout'].pop()
    elif change=='missing_query':actual['datasets'][0].pop('queryLines')
    elif change=='sql_whitespace_change':actual['datasets'][0]['queryLines'][0]+=' '
    elif change=='extra_behavior':actual['datasets'][0]['parameters']=[{'name':'unsafe','default':'wrong'}]
    assert dashboard_differences(desired,actual)


def test_dashboard_inspection_is_only_get_and_always_saves_config_diff(tmp_path):
    from scripts.setup_cutoverguard_native import inspect_dashboard
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    actual['datasets'][0]['queryLines']=['SELECT 99']
    class Client:
        calls=[]
        def request(self,method,path,payload=None):
            self.calls.append((method,path,payload))
            return {'serialized_dashboard':json.dumps(actual),'credential':'NO_ENVELOPE'}
    client=Client()
    diff=inspect_dashboard(reports(),client,'b'*32,'a'*32,tmp_path)
    assert diff and len(client.calls)==1 and client.calls[0][0]=='get'
    assert (tmp_path/'dashboard-desired.json').exists()
    assert (tmp_path/'dashboard-differences.json').exists()
    assert 'NO_ENVELOPE' not in (tmp_path/'dashboard-readback.json').read_text()


def test_dashboard_unknown_root_behavior_cannot_bypass_dataset_checks():
    from scripts.setup_cutoverguard_native import dashboard_differences
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    actual['queryOverrides']=[{'query':'SELECT 99'}]
    assert dashboard_differences(desired,actual)
    actual.pop('queryOverrides');actual['service_metadata']='default'
    assert dashboard_differences(desired,actual)==[]


def test_dashboard_materialized_create_context_matches_exact_defaults():
    from scripts.setup_cutoverguard_native import dashboard_differences
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    for item in actual['datasets']:
        item.update(catalog='workspace',schema='default')
    assert dashboard_differences(desired,actual)==[]
    assert 'catalog' not in desired['datasets'][0]


@pytest.mark.parametrize('key,value',[('catalog','other'),('schema','other'),('catalog',None),('schema',False)])
def test_dashboard_wrong_materialized_sql_context_is_rejected(key,value):
    from scripts.setup_cutoverguard_native import dashboard_differences
    desired=dashboard(reports(),'a'*32);actual=deepcopy(desired)
    actual['datasets'][0][key]=value
    assert dashboard_differences(desired,actual)==[{'path':'/datasets/review_summary/'+key,'kind':'content'}]
