import json
import sqlite3
from pathlib import Path
import pytest
from scripts.build_databricks_demo import load_report,datasets,notebook,pipeline_sql,generate
from cutoverguard.sql import build_pipeline

ROOT=Path(__file__).resolve().parents[1]


def reports():
    return {name:load_report(ROOT/'cutover_results'/file) for name,file in [('defective','defective-cloud-v2.json'),('fixed','fixed-cloud.json')]}


def test_notebook_is_read_only_and_references_actual_twelve_tables():
    r=reports();source=notebook(r,datasets(r))
    assert source.startswith('-- Databricks notebook source')
    assert '-- MAGIC %md' in source and '-- COMMAND ----------' in source
    assert 'CREATE TABLE' not in source and 'DROP ' not in source
    for report in r.values():
        for table in report['execution']['tables'].values():
            assert table in source
    assert 'loss' in source


def test_pipeline_reuses_comparison_queries_not_existing_gold_outputs():
    r=reports();prefix='cg_demo_0123456789ab';sql=pipeline_sql(r,prefix)
    assert sql.count('CREATE OR REFRESH MATERIALIZED VIEW')==18
    assert 'ON VIOLATION DROP ROW' not in sql
    assert 'ON VIOLATION FAIL UPDATE' in sql
    for scenario,report in r.items():
        table=lambda suffix:'`'+prefix+'_'+scenario+'_'+suffix+'`'
        logical={'profiles':table('silver_profiles'),'transactions':table('silver_in_window'),'manifest':table('bronze_manifest'),
                 'issues':table('gold_issues'),'impacts':table('gold_impacts'),'totals':table('gold_totals')}
        for name,statement in build_pipeline(report['input_bundle'],logical,report['run_id']):
            if name in ('issues','impacts','totals'):
                assert statement.split(' USING DELTA AS ',1)[1] in sql
                assert report['execution']['tables'][name] not in sql
        for name in ('manifest','profiles','transactions'):
            assert report['execution']['tables'][name] in sql


def test_generation_deterministic_with_explicit_prefix_and_refuses_overwrite(tmp_path):
    defective=ROOT/'cutover_results'/'defective-cloud-v2.json';fixed=ROOT/'cutover_results'/'fixed-cloud.json'
    a=generate(defective,fixed,tmp_path/'a','cg_demo_0123456789ab')
    b=generate(defective,fixed,tmp_path/'b','cg_demo_0123456789ab')
    assert a==b
    with pytest.raises(ValueError,match='exists'):
        generate(defective,fixed,tmp_path/'a','cg_demo_0123456789ab')
    assert a['pipeline_status']=='prepared_not_executed'


def test_changed_or_local_report_refused(tmp_path):
    r=reports()['defective']
    r['execution']['mode']='local';path=tmp_path/'report.json';path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='verified Databricks'):
        load_report(path)
    r=reports()['defective'];r['account_impacts'][0]['delta_minor']='0';path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='reproducible'):
        load_report(path)


def test_dashboard_aggregate_and_zero_net_chart_values():
    """Adapt only identifier/storage dialect; verify chart never masks opposite bars."""
    r=reports();queries=datasets(r);db=sqlite3.connect(':memory:')
    mapping={}
    for scenario,report in r.items():
        for name in ('issues','impacts','totals'):
            mapping[report['execution']['tables'][name]]=scenario+'_'+name
        issue_name=scenario+'_issues';impact_name=scenario+'_impacts';total_name=scenario+'_totals'
        db.execute(f'CREATE TABLE {issue_name}(check_id TEXT,type TEXT,key TEXT,field TEXT,source_value TEXT,candidate_value TEXT)')
        db.execute(f'CREATE TABLE {impact_name}(account_id TEXT,currency TEXT,source_minor INTEGER,candidate_minor INTEGER,delta_minor INTEGER)')
        db.execute(f'CREATE TABLE {total_name}(currency TEXT,source_minor INTEGER,candidate_minor INTEGER,net_delta_minor INTEGER,absolute_account_delta_minor INTEGER,affected_notional_minor INTEGER)')
        db.executemany(f'INSERT INTO {issue_name} VALUES(?,?,?,?,?,?)',report['execution']['sql_issue_rows'])
        db.executemany(f'INSERT INTO {impact_name} VALUES(?,?,?,?,?)',report['execution']['sql_impact_rows'])
        db.executemany(f'INSERT INTO {total_name} VALUES(?,?,?,?,?,?)',report['execution']['sql_total_rows'])
    def query(name):
        sql=queries[name]
        for original,replacement in mapping.items():
            sql=sql.replace(original,replacement)
        return db.execute(sql).fetchall()
    summary=query('review_summary')
    assert summary==[('defective','USD',300000,300000,0,200000,300000,2),('fixed','USD',300000,300000,0,0,0,0)]
    chart=query('zero_net_chart')
    assert [(row[0],row[1],row[4]) for row in chart]==[('defective','0001',1000),('defective','0002',-1000),('defective','Firm net',0),('fixed','0001',0),('fixed','0002',0),('fixed','Firm net',0)]
    assert len(query('ownership_findings'))==2


def test_pipeline_manifest_gate_is_consumed_and_windows_bound_to_verified_inputs():
    sql=pipeline_sql(reports(),'cg_demo_0123456789ab')
    assert sql.count('CONSTRAINT expected_snapshot')==2
    assert sql.count('CROSS JOIN `cg_demo_0123456789ab_')==4
    assert sql.count('window_start_utc=CAST(')==2
    assert sql.count('window_end_utc=CAST(')==2
    assert '_manifest_count=1' in sql


def test_report_raw_sql_evidence_must_agree(tmp_path):
    r=reports()['defective'];r['execution']['sql_issue_rows'][0][4]='WRONG'
    path=tmp_path/'report.json';path.write_text(json.dumps(r))
    with pytest.raises(RuntimeError,match='findings'):
        load_report(path)


def test_empty_manifest_still_emits_single_failure_sentinel():
    sql=pipeline_sql(reports(),'cg_demo_0123456789ab')
    assert sql.count('SELECT min(run_id) run_id,min(input_json) input_json,count(*) _manifest_count')==2
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE manifest(run_id TEXT,input_json TEXT)')
    rows=db.execute('SELECT min(run_id),min(input_json),count(*) FROM manifest').fetchall()
    assert rows==[(None,None,0)]
    assert rows[0][2]!=1  # The expectation sees a row, rather than an empty source.
    db.executemany('INSERT INTO manifest VALUES(?,?)',[('a','{}'),('b','{}')])
    assert db.execute('SELECT count(*) FROM manifest').fetchone()[0]!=1


def test_generator_cli_failure_is_actionable(monkeypatch,tmp_path,capsys):
    from scripts.build_databricks_demo import main
    monkeypatch.setattr('sys.argv',['build_databricks_demo.py','--prefix','not-a-safe-prefix','--output',str(tmp_path/'new')])
    assert main()==1
    assert 'Generation failed:' in capsys.readouterr().out
    assert not (tmp_path/'new').exists()
