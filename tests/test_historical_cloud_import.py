from copy import deepcopy
import pytest
from cutoverguard.engine import reconcile, ReconcileError
from cutoverguard.fixtures import fixture
from cutoverguard.sql import table_names
from cutover_app import validate_report


def example_report():
    # Invented test rows exercise historical-mode import, not actual UCI observations.
    bundle=fixture()
    bundle['data_kind']='historical'
    bundle['dataset_info']={'name':'UCI Online Retail','validation_kind':'historical_source_controlled_candidate',
                            'controlled_case':'ownership_swaps','test_rows':'invented'}
    report=reconcile(bundle)
    report['execution']={
        'mode':'databricks','status':'verified','run_id':report['run_id'],
        'transport':'authenticated_cli_volume_csv','imported_rows_equal':True,
        'validation_kind':'historical_source_controlled_candidate','source_rows':2,'candidate_rows':2,
        'remote_directory':'/Volumes/workspace/default/workshop/cutoverguard_'+'a'*16,
        'tables':table_names('workspace','default','cutoverguard_'+'a'*16),
        'statement_ids':['invented-test-statement'],
        'sql_issue_rows':[[i[k] for k in ('check_id','type','key','field','source_value','candidate_value')] for i in report['issues'] if i['check_id']!='business'],
        'sql_impact_rows':[[i[k] for k in ('account_id','currency','source_minor','candidate_minor','delta_minor')] for i in report['account_impacts']],
        'sql_total_rows':[[i[k] for k in ('currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor')] for i in report['currency_totals']],
        'oracle_comparison':{'primary_findings_equal':True,'account_impacts_equal':True,'currency_totals_equal':True},
    }
    return report


def test_historical_file_report_recalculates_and_keeps_provenance_boundary():
    result=validate_report(example_report())
    assert result['status']=='blocked' and result['data_kind']=='historical'
    assert result['execution']['local_recalculation_verified'] is True
    assert result['execution']['provenance_attestation']=='self_reported_not_authenticated'


@pytest.mark.parametrize('change',['transport','imported_rows','row_count','row_count_bool','path','tables','money','case'])
def test_historical_import_rejects_unsupported_or_changed_evidence(change):
    report=deepcopy(example_report());e=report['execution']
    if change=='transport':e['transport']='unknown'
    elif change=='imported_rows':e['imported_rows_equal']=False
    elif change=='row_count':e['source_rows']=3
    elif change=='row_count_bool':e['source_rows']=True
    elif change=='path':e['remote_directory']='/Volumes/workspace/default/workshop/other'
    elif change=='tables':e['tables']['issues']='existing.unrelated.table'
    elif change=='money':e['sql_impact_rows'][0][-1]='0'
    elif change=='case':report['input_bundle']['dataset_info']['controlled_case']='other'
    with pytest.raises(ReconcileError):validate_report(report)


@pytest.mark.parametrize('metadata',[[],None,'invalid'])
def test_malformed_metadata_is_a_validation_error_not_handler_crash(metadata):
    original=example_report()
    bundle=deepcopy(original['input_bundle']);bundle['dataset_info']=metadata
    report=reconcile(bundle);report['execution']=deepcopy(original['execution'])
    report['execution']['run_id']=report['run_id']
    with pytest.raises(ReconcileError):validate_report(report)
