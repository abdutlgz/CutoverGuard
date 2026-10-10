from copy import deepcopy
import csv
import json
from pathlib import Path
from scripts.validate_public_retail import build_bundle,csv_rows,controlled_cases
import pytest
from cutoverguard.databricks import CloudError
from cutoverguard.file_ingestion import prepare,execute,rows_for,file_pipeline,validate_bundle,volume_path,micros

@pytest.fixture
def bundle(tmp_path):
    # Invented invoice lines exercise the adapter. These are NOT observed UCI data.
    path=tmp_path/'invented-retail.csv'
    with path.open('w',newline='') as file:
        writer=csv.writer(file);writer.writerow(['InvoiceNo','Quantity','InvoiceDate','UnitPrice','CustomerID'])
        for index in range(1467):
            writer.writerow([f'INVENTED-{index}',1,'2011-01-04T10:00:00',f'{index%29+1}.25',f'{index%37:05d}'])
    baseline,_=build_bundle(csv_rows(path),source_sha256='a'*64,captured_at='2026-10-10T05:00:00Z')
    return next(case for label,case,_ in controlled_cases(baseline) if label=='ownership_swaps')

def pack(bundle,tmp_path):
    output=tmp_path/'run'
    state=prepare(bundle,output,'workspace.default.existing_volume','warehouse-1')
    return output,state

class Runner:
    warehouse_id='warehouse-1'
    def __init__(self,bundle,oracle,bad_import=False,bad_money=False,fail_create=False):
        self.rows=rows_for(bundle,oracle['run_id']); self.oracle=oracle
        self.statement_ids=[]; self.sql=[]
        self.bad_import=bad_import; self.bad_money=bad_money; self.fail_create=fail_create
    def execute(self,sql):
        self.sql.append(sql); self.statement_ids.append('statement-'+str(len(self.sql)))
        if sql.startswith('CREATE'):
            if self.fail_create: raise CloudError('unknown mutation')
            return []
        if 'check_id,type,key' in sql:
            return [[i[k] for k in ['check_id','type','key','field','source_value','candidate_value']] for i in self.oracle['issues'] if i['check_id']!='business']
        if 'source_minor' in sql:
            if 'account_id,currency' in sql: return [[r[k] for k in ['account_id','currency','source_minor','candidate_minor','delta_minor']] for r in self.oracle['account_impacts']]
            rows=[[r[k] for k in ['currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor']] for r in self.oracle['currency_totals']]
            if self.bad_money: rows[0][3]='999'
            return rows
        for name in self.rows:
            if sql.endswith('_'+name+'`'):
                rows=deepcopy(self.rows[name])
                if name=='transactions':
                    for row in rows:
                        for index in (5,6,7): row[index]=micros(row[index])
                if self.bad_import and name=='transactions': rows[0][2]='tampered'
                return rows
        raise AssertionError(sql)

def test_prepare_roundtrip_and_platform_queries(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    with (output/'transactions.csv').open(newline='') as file:
        rows=list(csv.DictReader(file))
    assert len(rows)==2934
    assert rows[0]['account_id']==bundle['source']['transactions'][0]['account_id']
    sql=(output/'pipeline.sql').read_text()
    assert 'FROM VALUES' not in sql and 'read_files(' in sql
    assert 'CAST(event_time AS TIMESTAMP) AS event_time' in sql
    assert 'FULL OUTER JOIN' in sql and 'sum(amount_minor)' in sql
    assert 'DROP ' not in sql and 'REPLACE ' not in sql
    assert state['status']=='prepared_unverified'
    assert not (output/'report.json').exists()

def test_actual_sql_full_oracle_and_resume(bundle,tmp_path):
    output,state=pack(bundle,tmp_path); oracle=validate_bundle(bundle)
    uploads=[]; runner=Runner(bundle,oracle)
    report=execute(output,runner,lambda *args:uploads.append(args))
    assert len(uploads)==3
    assert sum(sql.startswith('CREATE') for sql in runner.sql)==6
    assert report['execution']['imported_rows_equal'] is True
    assert report['execution']['status']=='verified'
    assert len(report['execution']['statement_ids'])==12
    assert report['data_kind']=='historical'
    again=Runner(bundle,oracle)
    execute(output,again,lambda *args:pytest.fail('Repeated upload'))
    assert not any(sql.startswith('CREATE') for sql in again.sql)
    assert len(again.sql)==6

@pytest.mark.parametrize('failure',['bad_import','bad_money'])
def test_differences_not_verified(bundle,tmp_path,failure):
    output,state=pack(bundle,tmp_path)
    with pytest.raises(CloudError): execute(output,Runner(bundle,validate_bundle(bundle),**{failure:True}),lambda *args:None)
    assert not (output/'report.json').exists()
    assert json.loads((output/'state.json').read_text())['status']!='verified'

def test_ambiguous_creation_never_repeated(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    with pytest.raises(CloudError): execute(output,Runner(bundle,validate_bundle(bundle),fail_create=True),lambda *args:None)
    saved=json.loads((output/'state.json').read_text())
    assert saved['attempted']=='table_manifest'
    retry=Runner(bundle,validate_bundle(bundle))
    with pytest.raises(CloudError,match='ambiguous'): execute(output,retry,lambda *args:pytest.fail('Repeated mutation'))
    assert retry.sql==[]

def test_upload_failure_blocks_resume(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    def failed(*args): raise CloudError('unknown upload')
    with pytest.raises(CloudError): execute(output,Runner(bundle,validate_bundle(bundle)),failed)
    assert json.loads((output/'state.json').read_text())['attempted']=='upload_manifest'

def test_changed_csv_refused_before_sql(bundle,tmp_path):
    output,state=pack(bundle,tmp_path); (output/'profiles.csv').write_text('tampered')
    runner=Runner(bundle,validate_bundle(bundle))
    with pytest.raises(CloudError,match='changed'): execute(output,runner,lambda *args:pytest.fail('upload'))
    assert runner.sql==[]

def test_invalid_scope_or_volume(bundle,tmp_path):
    for value in ['dbfs:/tmp/x','a.b.c/../d','a.b.c;DROP TABLE x']:
        with pytest.raises(CloudError): volume_path(value,'cutoverguard_'+'a'*16)
    bad=deepcopy(bundle); bad['data_kind']='synthetic'
    with pytest.raises(CloudError): validate_bundle(bad)
    output,state=pack(bundle,tmp_path)
    with pytest.raises(CloudError,match='already exists'): prepare(bundle,output,'workspace.default.existing_volume','warehouse-1')


def test_failed_revalidation_changes_checkpoint_to_unverified(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    execute(output,Runner(bundle,validate_bundle(bundle)),lambda *args:None)
    with pytest.raises(CloudError): execute(output,Runner(bundle,validate_bundle(bundle),bad_money=True),lambda *args:pytest.fail('upload'))
    assert json.loads((output/'state.json').read_text())['status']=='unverified'


def test_microseconds_exact_with_offsets_and_pre_epoch():
    assert micros('1970-01-01T00:00:00.000001+00:00')=='1'
    assert micros('1969-12-31T23:59:59.999999+00:00')=='-1'
    assert micros('1970-01-01T01:00:00+01:00')=='0'


def test_corrected_baseline_full_sql_oracle_and_import(bundle,tmp_path):
    from cutover_app import validate_report
    bundle['candidate']=deepcopy(bundle['source'])
    bundle['candidate']['snapshot_id']+='-controlled-baseline'
    bundle['dataset_info']['controlled_case']='baseline'
    output,state=pack(bundle,tmp_path)
    oracle=validate_bundle(bundle)
    report=execute(output,Runner(bundle,oracle),lambda *args:None)
    assert validate_report(report)['status']=='ready_for_review'
    assert report['summary']['primary_issue_count']==0
    assert all(r['delta_minor']=='0' for r in report['account_impacts'])
    assert all(r['net_delta_minor']=='0' and r['absolute_account_delta_minor']=='0' and r['affected_notional_minor']=='0' for r in report['currency_totals'])


def test_baseline_label_cannot_hide_defects(bundle):
    bundle['dataset_info']['controlled_case']='baseline'
    with pytest.raises(CloudError,match='identical'): validate_bundle(bundle)


def test_baseline_requires_exact_records_even_if_normalized_equal(bundle):
    bundle['candidate']=deepcopy(bundle['source'])
    bundle['dataset_info']['controlled_case']='baseline'
    bundle['candidate']['transactions'].reverse()
    with pytest.raises(CloudError,match='identical'): validate_bundle(bundle)


def test_read_files_path_rejects_sql_injection(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    with pytest.raises(CloudError,match='directory'):
        list(file_pipeline(bundle,state['tables'],state['run_id'],"/Volumes/a/b/c/x'; DROP TABLE something"))


def test_timestamp_changes_rejected_before_upload(bundle):
    for side in ('source','candidate'):
        bundle[side]['window_end']='2011-01-04T11:00:00+00:00'
        bundle[side]['complete_through']='2011-01-04T11:00:00+00:00'
    bundle['candidate']['transactions'][0]['event_time']='2011-01-04T10:00:01+00:00'
    with pytest.raises(CloudError,match='Timestamp-mismatch'): validate_bundle(bundle)


def test_tampered_checkpoint_target_rejected_before_mutation(bundle,tmp_path):
    output,state=pack(bundle,tmp_path)
    state['tables']['issues']='existing_table; DROP TABLE sensitive'
    (output/'state.json').write_text(json.dumps(state))
    runner=Runner(bundle,validate_bundle(bundle))
    with pytest.raises(CloudError,match='table names'):
        execute(output,runner,lambda *args:pytest.fail('upload'))
    assert runner.sql==[]


def test_upload_mkdir_then_list_then_copy_without_overwrite(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from cutoverguard import file_ingestion as module
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0,stdout='[]' if cmd[2]=='ls' else '',stderr='')
    monkeypatch.setattr(module.subprocess,'run',run)
    remote='/Volumes/workspace/default/files/cutoverguard_'+'a'*16+'/manifest.csv'
    module.upload_file(tmp_path/'manifest.csv',remote,'named-profile')
    assert [cmd[2] for cmd in calls]==['mkdir','ls','cp']
    assert calls[0][3]=='dbfs:'+remote.rsplit('/',1)[0]
    assert all(cmd[-2:]==['--profile','named-profile'] for cmd in calls)
    assert '--overwrite' not in calls[-1]


def test_failed_mkdir_never_copies_or_leaks_stderr(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from cutoverguard import file_ingestion as module
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1,stdout='',stderr='PERMISSION_DENIED token='+'dapi'+'SuperSecretSensitiveValue')
    monkeypatch.setattr(module.subprocess,'run',run)
    remote='/Volumes/workspace/default/files/cutoverguard_'+'a'*16+'/manifest.csv'
    with pytest.raises(CloudError,match='category: permission_denied') as error:
        module.upload_file(tmp_path/'manifest.csv',remote)
    assert len(calls)==1 and calls[0][2]=='mkdir'
    assert 'SuperSecret' not in str(error.value)


@pytest.mark.parametrize('listing',[[{'name':'manifest.csv','type':'file'}],[{'path':'/Volumes/a/b/c/manifest.csv','is_dir':False}]])
def test_existing_file_refused_without_copy(tmp_path,monkeypatch,listing):
    from types import SimpleNamespace
    from cutoverguard import file_ingestion as module
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0,stdout=json.dumps(listing) if cmd[2]=='ls' else '',stderr='')
    monkeypatch.setattr(module.subprocess,'run',run)
    remote='/Volumes/workspace/default/files/cutoverguard_'+'a'*16+'/manifest.csv'
    with pytest.raises(CloudError,match='existing_file'): module.upload_file(tmp_path/'manifest.csv',remote)
    assert [cmd[2] for cmd in calls]==['mkdir','ls']


@pytest.mark.parametrize('stderr,expected',[
    ('UNAUTHENTICATED credential=secret','authentication'),
    ('PERMISSION_DENIED','permission_denied'),
    ('RESOURCE_DOES_NOT_EXIST','directory_or_path'),
    ('DIRECTORY_NOT_FOUND','directory_or_path'),
    ('PATH_NOT_FOUND','directory_or_path'),
    ('AUTHENTICATION_FAILED','authentication'),
    ('HTTP status code: 403','permission_denied'),
    ('id=abc403secret','unknown'),
    ('no such file or directory','directory_or_path'),
    ('dial tcp: connection refused','network'),
    ('FILE_ALREADY_EXISTS','existing_file'),
    ('Unrecognized failure with sensitive private value','unknown'),
])
def test_cli_error_categories_only(stderr,expected):
    from cutoverguard.file_ingestion import classify_cli_error
    assert classify_cli_error(stderr)==expected


def test_unsafe_upload_path_rejected_before_cli(tmp_path,monkeypatch):
    from cutoverguard import file_ingestion as module
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:pytest.fail('CLI called'))
    with pytest.raises(CloudError,match='path'):
        module.upload_file(tmp_path/'manifest.csv','/Volumes/a/b/c/../manifest.csv')
