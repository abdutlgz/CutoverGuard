import json
import pytest
from scripts.build_retail_genie import prepare
from tests.test_historical_cloud_import import example_report


def test_retail_genie_scopes_tables_units_and_correction_boundary(tmp_path):
    report=example_report();report['execution']['warehouse_id']='test-warehouse'
    source=tmp_path/'report.json';source.write_text(json.dumps(report))
    output=tmp_path/'genie'
    space=prepare(source,output)
    for rows in (space['config']['sample_questions'],space['instructions']['example_question_sqls']):
        assert [r['id'] for r in rows]==sorted(r['id'] for r in rows)
    assert len(space['data_sources']['tables'])==3
    assert {r['identifier'] for r in space['data_sources']['tables']}=={report['execution']['tables'][name].replace('`','') for name in ('issues','impacts','totals')}
    text=(output/'instructions.txt').read_text()
    assert 'No corrected run is registered' in text and 'not independently authenticated' in text
    assert 'Every standalone answer' in text and 'not necessarily the amount' in text
    queries='\n'.join(''.join(r['sql']) for r in space['instructions']['example_question_sqls'])
    assert 'DECIMAL(38,2)' in queries and 'delta_minor' in queries
    readme=(output/'README.md').read_text()
    assert 'currency USD' in readme and '2000.00' in readme and '3000.00' in readme
    assert 'test-warehouse' in readme and '77.56' not in readme
    with pytest.raises(ValueError,match='fresh'): prepare(source,output)


def test_bad_report_cannot_prepare_genie(tmp_path):
    report=example_report();report['execution']['sql_total_rows'][0][-1]='999'
    source=tmp_path/'bad.json';source.write_text(json.dumps(report))
    with pytest.raises(ValueError):prepare(source,tmp_path/'genie')
    assert not (tmp_path/'genie').exists()


def test_paired_genie_registers_both_runs_without_stale_absence_claim(tmp_path):
    from tests.test_retail_dashboard import paired_reports
    d,f=paired_reports();source=tmp_path/'d.json';fixed=tmp_path/'f.json'
    source.write_text(json.dumps(d));fixed.write_text(json.dumps(f))
    output=tmp_path/'paired';space=prepare(source,output,fixed)
    assert len(space['data_sources']['tables'])==6
    text=(output/'instructions.txt').read_text()
    assert 'No corrected run is registered' not in text
    assert f['run_id'] in text and d['run_id'] in text
    assert 'ready for human review' in text
    assert 'six' in (output/'README.md').read_text()
    assert 'UNION ALL' in (output/'examples.sql').read_text()
