from copy import deepcopy
import json
import pytest
from test_historical_cloud_import import example_report
from scripts.build_retail_dashboard import build,generate


def paired_reports(nonpermutation=False):
    from cutoverguard.engine import reconcile
    from cutoverguard.sql import table_names
    d=example_report();d['input_bundle']['dataset_info']['source_file_sha256']='a'*64
    if nonpermutation:
        d['input_bundle']['candidate']['transactions'][1]['account_id']=d['input_bundle']['source']['transactions'][1]['account_id']
    # Metadata is fingerprinted, so rebuild the report and its supplied evidence.
    def pack(bundle,old,prefix):
        r=reconcile(bundle);e=deepcopy(old['execution']);e['run_id']=r['run_id'];e['warehouse_id']='test-warehouse'
        e['tables']=table_names('workspace','default','cutoverguard_'+prefix*16)
        for name,rows,fields in [('issue', [i for i in r['issues'] if i['check_id']!='business'],('check_id','type','key','field','source_value','candidate_value')),
                                 ('impact',r['account_impacts'],('account_id','currency','source_minor','candidate_minor','delta_minor')),
                                 ('total',r['currency_totals'],('currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor'))]:
            e['sql_'+name+'_rows']=[[row[k] for k in fields] for row in rows]
        e['remote_directory']='/Volumes/workspace/default/workshop/cutoverguard_'+prefix*16
        r['execution']=e;return r
    d=pack(d['input_bundle'],d,'a')
    b=deepcopy(d['input_bundle']);b['candidate']=deepcopy(b['source']);b['candidate']['snapshot_id']+='-baseline';b['dataset_info']['controlled_case']='baseline'
    return d,pack(b,d,'b')


def test_pair_uses_both_distinct_runs_and_preserves_labels():
    from scripts.build_retail_dashboard import comparison
    d,f=paired_reports();body=comparison(d,f)
    assert len(body['pages'])==4
    sql=''.join(next(d for d in body['datasets'] if d['name']=='retail_comparison')['queryLines'])
    assert "'defective' scenario" in sql and "'corrected' scenario" in sql
    assert d['execution']['tables']['totals'] in sql and f['execution']['tables']['totals'] in sql
    names={row['name'] for row in body['datasets']}
    for page in body['pages']:
        for item in page['layout']:
            for q in item['widget'].get('queries',[]):assert q['query']['datasetName'] in names


def test_proof_copy_follows_actual_checksum_and_findings():
    from scripts.build_retail_dashboard import comparison
    d,f=paired_reports(nonpermutation=True);body=comparison(d,f)
    text=''.join(body['pages'][0]['layout'][0]['widget']['multilineTextboxSpec']['lines'])
    assert '1 invoice assignments' in text and 'checksum fails' in text
    assert 'Every aggregate check shown passes' not in text


@pytest.mark.parametrize('change',['source','warehouse','tables'])
def test_pair_rejects_unrelated_or_reused_evidence(change):
    from scripts.build_retail_dashboard import comparison
    d,f=paired_reports()
    if change=='source':
        # Change source snapshot metadata on a fully reconstructed valid report.
        f['input_bundle']['source']['captured_at']='2026-10-10T12:00:00Z'
        from cutoverguard.engine import reconcile
        from cutover_app import validate_report
        f['run_id']=reconcile(f['input_bundle'])['run_id']
        f['execution']['run_id']=f['run_id']
        validate_report(f)
    elif change=='warehouse':f['execution']['warehouse_id']='other'
    elif change=='tables':
        f['execution']['tables']=deepcopy(d['execution']['tables']);f['execution']['remote_directory']=d['execution']['remote_directory']
    with pytest.raises(ValueError):comparison(d,f)


def test_ret_dashboard_is_definition_with_bound_signed_fields_and_units():
    report=example_report();body=build(report)
    assert 'input_bundle' not in body and 'datasets' in body and 'pages' in body
    datasets={item['name']:''.join(item['queryLines']) for item in body['datasets']}
    assert 'delta_minor<>0' in datasets['retail_chart']
    assert 'CAST(delta_minor/100 AS DECIMAL(38,2))' in datasets['retail_chart']
    assert 'source_major' in datasets['retail_kpis']
    text=''.join(body['pages'][0]['layout'][0]['widget']['multilineTextboxSpec']['lines'])
    assert 'controlled' in text and 'not bank accounts' in text and 'CC BY 4.0' in text
    assert 'genieSpace' not in body.get('uiSettings',{})
    for item in body['pages'][0]['layout'][1:]:
        w=item['widget'];q=w['queries'][0]['query']
        assert q['datasetName'] in datasets
        fields={f['name'] for f in q['fields']}
        for encoding in w['spec']['encodings'].values():
            for entry in encoding if isinstance(encoding,list) else [encoding]:
                assert entry['fieldName'] in fields


def test_evidence_tampering_cannot_generate_native_definition():
    report=deepcopy(example_report());report['account_impacts'][0]['delta_minor']='0'
    with pytest.raises(ValueError):build(report)


def test_generated_expectations_follow_input_and_preserve_provenance_boundary(tmp_path):
    report=example_report();report['execution']['warehouse_id']='invented-test-warehouse'
    source=tmp_path/'report.json';source.write_text(json.dumps(report))
    output=tmp_path/'generated';generate(source,output)
    notes=(output/'README.md').read_text()
    assert 'currency USD' in notes and '2000.00' in notes
    assert '69.38' not in notes and 'GBP' not in notes
    metadata=json.loads((output/'generation.json').read_text())
    assert metadata['local_calculation_verified'] is True
    assert metadata['provenance_attestation']=='self_reported_not_authenticated'
    assert 'source_execution_verified' not in metadata
