import json
import sqlite3
import re
import pytest
from cutoverguard.databricks import CloudError, StatementRunner, verify_remote, supported
from cutoverguard.sql import table_names, literal, build_pipeline, utc


class FakeClient:
    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]
    def request(self,*args):
        self.calls.append(args)
        value=self.responses.pop(0)
        if isinstance(value,Exception):
            raise value
        return value


def test_poll_and_paginated_inline_results():
    client=FakeClient([
        {'statement_id':'abc-1','status':{'state':'RUNNING'}},
        {'status':{'state':'SUCCEEDED'},'manifest':{'total_row_count':2},'result':{'data_array':[['0001']], 'next_chunk_index':1}},
        {'data_array':[['0002']]},
    ])
    runner=StatementRunner(client,'warehouse-1',poll_interval=0)
    assert runner.execute('SELECT 1')==[['0001'],['0002']]
    assert runner.statement_ids==['abc-1']
    assert client.calls[-1][1].endswith('/result/chunks/1')


def test_timeout_requests_cancellation():
    client=FakeClient([{'statement_id':'abc','status':{'state':'RUNNING'}},{},{}])
    with pytest.raises(CloudError,match='timed out'):
        StatementRunner(client,'warehouse',timeout=-1).execute('SELECT 1')
    assert any(call[1].endswith('/cancel') for call in client.calls)


def test_poll_transport_failure_requests_cancellation():
    client=FakeClient([{'statement_id':'abc','status':{'state':'PENDING'}},CloudError('offline'),{}])
    with pytest.raises(CloudError,match='offline'):
        StatementRunner(client,'warehouse',poll_interval=0).execute('SELECT 1')
    assert client.calls[-1][1].endswith('/cancel')


def test_truncated_results_fail_closed():
    client=FakeClient([{'statement_id':'abc','status':{'state':'SUCCEEDED'},'manifest':{'truncated':True},'result':{}}])
    with pytest.raises(CloudError,match='Truncated'):
        StatementRunner(client,'warehouse').execute('SELECT 1')


def test_partial_results_fail_closed():
    client=FakeClient([{'statement_id':'abc','status':{'state':'SUCCEEDED'},'manifest':{'total_row_count':2},'result':{'data_array':[[1]]}}])
    with pytest.raises(CloudError,match='row count'):
        StatementRunner(client,'warehouse').execute('SELECT 1')


def test_literals_identifiers_are_safe():
    hostile="0001'); DROP TABLE x; --\\\n"
    value=literal(hostile)
    assert hostile not in value
    assert hostile.encode().hex() in value
    with pytest.raises(ValueError):
        table_names('workspace','default','existing_table')


def test_oracle_comparison_rejects_unequal_values():
    oracle={'issues':[],'account_impacts':[],'currency_totals':[]}
    with pytest.raises(CloudError,match='monetary'):
        verify_remote(oracle,[],[['0001','USD','1','2','1']],[])
    with pytest.raises(CloudError,match='findings'):
        verify_remote(oracle,[['transactions','account_mismatch','E1','account_id','a','b']],[],[])


@pytest.mark.parametrize('kind',['defective','clean','fixed','missing_extra','compensating_amounts','mixed_currency','timezone_equivalent'])
def test_sql_comparison_relational_semantics(kind):
    """SQLite checks SQL joins/aggregate logic, NOT Databricks dialect execution.

    Seed typed tables directly; adapt only storage/CAST dialect syntax. Live dialect
    acceptance remains a required external gate, never claimed by this test.
    """
    from cutoverguard.fixtures import fixture
    from cutoverguard.engine import reconcile
    bundle=fixture(kind)
    oracle=reconcile(bundle)
    supported(bundle,oracle)
    tables={name:name for name in ('manifest','profiles','transactions','issues','impacts','totals')}
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE profiles(side TEXT,account_id TEXT,customer_id TEXT)')
    db.execute('CREATE TABLE transactions(side TEXT,event_id TEXT,account_id TEXT,amount_minor INTEGER,currency TEXT,event_time TEXT,window_start TEXT,window_end TEXT)')
    for side in ('source','candidate'):
        snapshot=bundle[side]
        db.executemany('INSERT INTO profiles VALUES(?,?,?)',[(side,row['account_id'],row['customer_id']) for row in snapshot['profiles']])
        db.executemany('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)',[(side,row['event_id'],row['account_id'],int(row['amount_minor']),row['currency'],utc(row['event_time']),utc(snapshot['window_start']),utc(snapshot['window_end'])) for row in snapshot['transactions']])
    for name,sql in build_pipeline(bundle,tables,oracle['run_id']):
        if name in ('issues','impacts','totals'):
            sql=sql.replace(' USING DELTA','').replace(' AS STRING',' AS TEXT').replace(' AS TIMESTAMP',' AS TEXT').replace('DECIMAL(38,0)','INTEGER')
            db.execute(sql)
    rows=db.execute('SELECT check_id,type,key,field,source_value,candidate_value FROM issues').fetchall()
    impacts=db.execute('SELECT * FROM impacts').fetchall()
    totals=db.execute('SELECT * FROM totals').fetchall()
    assert verify_remote(oracle,rows,impacts,totals)['account_impacts_equal']


def test_full_run_packages_real_sql_query_evidence(tmp_path):
    from cutoverguard.databricks import run_cloud
    from cutoverguard.fixtures import fixture
    from cutoverguard.engine import reconcile
    bundle=fixture('defective')
    oracle=reconcile(bundle)
    class Runner:
        warehouse_id='warehouse'
        statement_ids=[]
        def execute(self,sql):
            self.statement_ids.append('stmt-'+str(len(self.statement_ids)))
            if sql.startswith('SELECT check_id'):
                return [[i[k] for k in ('check_id','type','key','field','source_value','candidate_value')] for i in oracle['issues'] if i['check_id']!='business']
            if sql.startswith('SELECT account_id'):
                return [[i[k] for k in ('account_id','currency','source_minor','candidate_minor','delta_minor')] for i in oracle['account_impacts']]
            if sql.startswith('SELECT currency'):
                return [[i[k] for k in ('currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor')] for i in oracle['currency_totals']]
            assert sql.startswith('CREATE TABLE')
            assert ' OR REPLACE ' not in sql and 'DROP TABLE' not in sql
            return []
    output=tmp_path/'execution.json'
    report=run_cloud(bundle,Runner(),evidence_path=output)
    assert report['execution']['mode']=='databricks'
    assert len(report['execution']['statement_ids'])==9
    assert report['execution']['oracle_comparison']['primary_findings_equal']
    assert json.loads(output.read_text())['status']=='verified'


def test_remote_failure_leaves_unverified_evidence(tmp_path):
    from cutoverguard.databricks import run_cloud
    from cutoverguard.fixtures import fixture
    class Runner:
        warehouse_id='warehouse'
        statement_ids=['failed-stmt']
        def execute(self,sql):
            raise CloudError('failure')
    output=tmp_path/'execution.json'
    with pytest.raises(CloudError):
        run_cloud(fixture('defective'),Runner(),evidence_path=output)
    assert json.loads(output.read_text())['status']=='unverified'


@pytest.mark.parametrize('kind',['duplicate_keys','malformed','missing_metadata','lost_identifier'])
def test_unsupported_inputs_stop_before_sql(kind):
    from cutoverguard.databricks import run_cloud
    from cutoverguard.fixtures import fixture
    class Runner:
        warehouse_id='warehouse'
        statement_ids=[]
        def execute(self,sql):
            pytest.fail('Unsupported input must not execute SQL')
    with pytest.raises(CloudError):
        run_cloud(fixture(kind),Runner())


def test_wrong_sql_evidence_values_rejected():
    from cutoverguard.engine import reconcile
    from cutoverguard.fixtures import fixture
    oracle=reconcile(fixture('defective'))
    rows=[[i[k] for k in ('check_id','type','key','field','source_value','candidate_value')] for i in oracle['issues'] if i['check_id']!='business']
    rows[0][4]='WRONG-ACCOUNT'
    with pytest.raises(CloudError,match='findings'):
        verify_remote(oracle,rows,[],[])


def test_metadata_warning_with_critical_breaks_stops_before_sql():
    from cutoverguard.databricks import run_cloud
    from cutoverguard.fixtures import fixture
    bundle=fixture('defective')
    bundle['candidate']['complete_through']=None
    class Runner:
        warehouse_id='warehouse'
        statement_ids=[]
        def execute(self,sql):
            pytest.fail('Metadata warning cannot be concealed by fail status')
    with pytest.raises(CloudError,match='Incomplete metadata'):
        run_cloud(bundle,Runner())


def test_ingestion_values_are_constants_and_decode_round_trips():
    """Reproduce the live inline-table restriction, then execute the projection.

    SQLite supports the projection functions through registered equivalents; its
    VALUES alias syntax differs. This checks data fidelity, not live SQL acceptance.
    """
    from cutoverguard.sql import _rows_select
    rows = [['0001', "O'Brien\\folder\n客户", '-123'], ['0002', None, '0']]
    columns = [('account_id','STRING'), ('customer_id','STRING'), ('amount_minor','DECIMAL(38,0)')]
    sql = _rows_select(rows, columns)
    projection, values_alias = sql.split(' FROM VALUES ', 1)
    values, alias = values_alias.split(' AS v(', 1)
    assert 'decode' not in values and 'unhex' not in values
    assert re.fullmatch(r"[\s(),'0-9a-fNULL]+", values)
    db = sqlite3.connect(':memory:')
    db.create_function('unhex', 1, lambda value: None if value is None else bytes.fromhex(value))
    db.create_function('decode', 2, lambda value, encoding: None if value is None else value.decode(encoding))
    column_names = alias[:-1]
    adapted = projection.replace(' AS STRING', ' AS TEXT').replace('DECIMAL(38,0)', 'INTEGER')
    query = f'WITH v({column_names}) AS (VALUES {values}) {adapted} FROM v'
    assert db.execute(query).fetchall() == [('0001', rows[0][1], -123), ('0002', None, 0)]
