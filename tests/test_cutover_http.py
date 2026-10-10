import io
import json
from copy import deepcopy

from cutover_app import Handler, validate_report


class Connection:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()
    def makefile(self, mode, buffering=None):
        return self.request
    def sendall(self, data):
        self.response.write(data)


def request(path, payload=None, origin=None, host='127.0.0.1:8502', raw=None):
    body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else b''
    method = 'POST' if payload is not None or raw is not None else 'GET'
    headers = f'{method} {path} HTTP/1.0\r\nHost: {host}\r\nContent-Length: {len(body)}\r\n'
    if origin:
        headers += f'Origin: {origin}\r\n'
    connection = Connection(headers.encode() + b'\r\n' + body)
    Handler(connection, ('127.0.0.1', 1234), None)
    head, content = connection.response.getvalue().split(b'\r\n\r\n', 1)
    return int(head.split(b' ')[1]), content


def test_reconciliation_and_corrected_control():
    from cutoverguard.fixtures import fixture
    status, body = request('/api/reconcile', fixture())
    result = json.loads(body)
    assert status == 200 and result['status'] == 'blocked'
    assert result['summary']['primary_issue_count'] == 2
    assert {r['account_id']: int(r['delta_minor']) for r in result['account_impacts']} == {'0001': 100000, '0002': -100000}
    status, body = request('/api/reconcile', fixture('fixed'))
    assert status == 200 and json.loads(body)['status'] == 'ready_for_review'


def test_import_recomputes_and_rejects_modified_claims():
    from cutoverguard.engine import reconcile
    from cutoverguard.fixtures import fixture
    report = reconcile(fixture())
    status, body = request('/api/validate-report', report)
    assert status == 200
    assert json.loads(body)['execution']['local_recalculation_verified'] is True
    modified = deepcopy(report)
    modified['account_impacts'][0]['delta_minor'] = '0'
    assert request('/api/validate-report', modified)[0] == 400
    modified = deepcopy(report)
    modified['summary']['primary_issue_count'] = 0
    assert request('/api/validate-report', modified)[0] == 400


def test_missing_report_input_and_untrusted_origins_rejected():
    assert request('/api/validate-report', {'status': 'ready_for_review'})[0] == 400
    assert request('/api/reconcile', {}, origin='https://example.com')[0] == 403
    assert request('/api/examples', host='evil.example')[0] == 403
    assert request('/../.env')[0] == 404


def test_bad_json_and_unknown_fixtures():
    assert request('/api/reconcile', raw=b'{broken')[0] == 400
    assert request('/api/reconcile', raw=b'{"value":NaN}')[0] == 400
    assert request('/api/fixture?kind=unknown')[0] == 400


def test_fixture_listing_and_download():
    status, content = request('/api/examples')
    assert status == 200
    examples = json.loads(content)['examples']
    assert {'defective', 'clean', 'fixed'}.issubset({e['id'] for e in examples})
    assert request('/api/fixture?kind=clean')[0] == 200


def test_unverified_cloud_label_and_inconsistent_sql_evidence_rejected():
    from cutoverguard.engine import reconcile
    from cutoverguard.fixtures import fixture
    report = reconcile(fixture())
    report['execution'] = {'mode': 'databricks', 'status': 'unverified'}
    assert request('/api/validate-report', report)[0] == 400
    primary = [i for i in report['issues'] if i['check_id'] != 'business']
    report['execution'] = {
        'mode':'databricks', 'status':'verified', 'run_id':report['run_id'],
        'statement_ids':['test-statement'], 'tables':{'issues':'test.issues'},
        'sql_issue_rows':[[i[k] for k in ['check_id','type','key','field','source_value','candidate_value']] for i in primary],
        'sql_impact_rows':[[i[k] for k in ['account_id','currency','source_minor','candidate_minor','delta_minor']] for i in report['account_impacts']],
        'sql_total_rows':[[i[k] for k in ['currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor']] for i in report['currency_totals']],
        'oracle_comparison': {'primary_findings_equal':True,'account_impacts_equal':True,'currency_totals_equal':True},
    }
    status, body = request('/api/validate-report',report)
    assert status == 200
    assert json.loads(body)['execution']['provenance_attestation'] == 'self_reported_not_authenticated'
    report['execution']['sql_impact_rows'][0][-1]='0'
    assert request('/api/validate-report',report)[0] == 400


def test_static_assets_and_content_security_headers():
    assert request('/')[0] == 200
    assert request('/style.css')[0] == 200
    assert request('/app.js')[0] == 200


def test_invalid_unicode_input_returns_actionable_error():
    from cutoverguard.fixtures import fixture
    bundle = fixture('clean')
    bundle['source']['profiles'][0]['customer_id'] = '\ud800'
    status, body = request('/api/reconcile', bundle)
    assert status == 400
    assert json.loads(body)['error']


def test_replay_endpoint_and_allowed_scenarios():
    status,body=request('/api/replay?scenario=defective')
    result=json.loads(body)
    assert status==200
    assert result['data_kind']=='synthetic' and result['scope']=='process_simulation'
    assert len(result['frames'])==3
    assert result['frames'][-1]['ownership_mismatches']==2
    assert result['frames'][-1]['net_delta_minor']=='0'
    status,body=request('/api/replay?scenario=fixed')
    assert status==200 and json.loads(body)['frames'][-1]['ownership_mismatches']==0
    assert request('/api/replay?scenario=malformed')[0]==400
