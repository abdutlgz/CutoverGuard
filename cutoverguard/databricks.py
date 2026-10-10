"""Credential-free CLI transport and actual SQL reconciliation execution."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from .sql import build_pipeline, table_names, utc


class CloudError(RuntimeError):
    pass


class CLIClient:
    def __init__(self, profile=None):
        self.profile = profile

    def request(self, method, path, payload=None):
        if not path.startswith('/api/2.0/sql/statements') and path != '/api/2.0/sql/warehouses':
            raise CloudError('Unsupported API endpoint')
        cmd = ['databricks', 'api', method.lower(), path, '--output', 'json']
        if payload is not None:
            cmd += ['--json', json.dumps(payload)]
        if self.profile:
            cmd += ['--profile', self.profile]
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise CloudError('CLI unavailable or request timed out; verify Databricks login in your terminal.') from exc
        if result.returncode:
            # Raw stderr can contain sensitive service details. Query history has diagnostics.
            raise CloudError('Databricks CLI request failed; check authentication, network and query history in your terminal.')
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CloudError('CLI returned invalid JSON') from exc
        if not isinstance(value, dict):
            raise CloudError('Unexpected API response')
        return value


class StatementRunner:
    def __init__(self, client, warehouse_id, timeout=180, poll_interval=2):
        if not re.fullmatch(r'[A-Za-z0-9-]+', warehouse_id):
            raise CloudError('Invalid warehouse ID')
        self.client, self.warehouse_id = client, warehouse_id
        self.timeout, self.poll_interval = timeout, poll_interval
        self.statement_ids = []

    def cancel(self, statement_id):
        try:
            self.client.request('post', f'/api/2.0/sql/statements/{statement_id}/cancel')
            return True
        except CloudError:
            return False

    def execute(self, sql):
        response = self.client.request('post', '/api/2.0/sql/statements', {
            'warehouse_id': self.warehouse_id, 'statement': sql,
            'wait_timeout': '0s', 'disposition': 'INLINE', 'format': 'JSON_ARRAY'})
        sid = response.get('statement_id')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9-]+', sid):
            raise CloudError('Missing valid statement ID')
        self.statement_ids.append(sid)
        deadline = time.monotonic() + self.timeout
        try:
            while response.get('status', {}).get('state') != 'SUCCEEDED':
                state = response.get('status', {}).get('state')
                if state not in ('PENDING', 'RUNNING'):
                    raise CloudError(f'Statement {sid} did not succeed ({state}); inspect query history.')
                if time.monotonic() >= deadline:
                    cancelled = self.cancel(sid)
                    raise CloudError(f'Statement {sid} timed out; cancellation ' + ('requested.' if cancelled else 'unverified.'))
                time.sleep(self.poll_interval)
                response = self.client.request('get', f'/api/2.0/sql/statements/{sid}')
        except (KeyboardInterrupt, CloudError):
            self.cancel(sid)
            raise
        manifest = response.get('manifest', {})
        if manifest.get('truncated') or response.get('result', {}).get('truncated'):
            raise CloudError('Truncated SQL result; refusing to verify partial evidence.')
        result = response.get('result', {})
        rows = list(result.get('data_array', []))
        next_index = result.get('next_chunk_index')
        seen = set()
        while next_index is not None:
            if time.monotonic() >= deadline:
                raise CloudError('Timed out fetching SQL result chunks; partial evidence cannot be verified.')
            if type(next_index) is not int or next_index < 0 or next_index in seen:
                raise CloudError('Invalid SQL pagination')
            seen.add(next_index)
            chunk = self.client.request('get', f'/api/2.0/sql/statements/{sid}/result/chunks/{next_index}')
            if chunk.get('external_links') or chunk.get('truncated'):
                raise CloudError('External or truncated results are unsupported; use INLINE results.')
            rows.extend(chunk.get('data_array', []))
            next_index = chunk.get('next_chunk_index')
        expected = manifest.get('total_row_count')
        if expected is not None and int(expected) != len(rows):
            raise CloudError('SQL result row count does not match manifest')
        return rows


def supported(bundle, oracle):
    if bundle.get('data_kind') != 'synthetic':
        raise CloudError('Cloud runner accepts synthetic fixtures only; client/historical data is outside this MVP.')
    if oracle.get('quarantine'):
        raise CloudError('Cloud runner does not yet support quarantined records; inspect local results.')
    unsupported = {'duplicate_profile_key','duplicate_event_key','invalid_account_link'}
    if any(i['type'] in unsupported for i in oracle['issues']):
        raise CloudError('Ambiguous keys/account links are unsupported remotely; no joins were executed.')
    if any(i['type'] == 'metadata_incomplete' for i in oracle['issues']) or any(check['status'] == 'unknown' for check in oracle['checks']):
        raise CloudError('Incomplete metadata/coverage is unsupported remotely; no results will be labeled verified.')
    for side in ('source','candidate'):
        for row in bundle[side]['transactions']:
            if abs(int(row['amount_minor'])) >= 10**30:
                raise CloudError('Remote DECIMAL safety limit is 30 amount digits; local integer arithmetic remains available.')


def _money_rows(rows, columns):
    output = []
    for row in rows:
        if len(row) != len(columns) or any(value is None for value in row):
            raise CloudError('Incomplete financial SQL results')
        item = dict(zip(columns, row))
        for name in columns:
            if name.endswith('minor'):
                value = str(item[name])
                if not re.fullmatch(r'-?\d+', value):
                    raise CloudError('Noninteger financial SQL result')
                item[name] = str(int(value))
            else:
                item[name] = str(item[name])
        output.append(item)
    return sorted(output, key=lambda item: (item.get('account_id',''), item['currency']))


def verify_remote(oracle, issue_rows, impact_rows, total_rows):
    def normalized(check, typ, key, field, source, candidate):
        def value(raw):
            if raw is None:
                return None
            if field == 'amount_minor':
                return str(int(raw))
            if field == 'event_time':
                return utc(raw)
            return str(raw)
        return check, typ, key, field, value(source), value(candidate)
    expected = Counter(normalized(i['check_id'], i['type'], i['key'], i['field'], i['source_value'], i['candidate_value']) for i in oracle['issues'] if i['check_id'] != 'business')
    if any(len(row) != 6 for row in issue_rows):
        raise CloudError('Invalid SQL finding columns')
    observed = Counter(normalized(*row) for row in issue_rows)
    if any(len(row) != 6 for row in issue_rows) or observed != expected:
        raise CloudError('SQL primary findings disagree with local oracle; cloud verification failed.')
    impacts = _money_rows(impact_rows, ['account_id','currency','source_minor','candidate_minor','delta_minor'])
    totals = _money_rows(total_rows, ['currency','source_minor','candidate_minor','net_delta_minor','absolute_account_delta_minor','affected_notional_minor'])
    expected_impacts = sorted(oracle['account_impacts'], key=lambda item:(item['account_id'],item['currency']))
    expected_totals = sorted(oracle['currency_totals'],key=lambda item:item['currency'])
    if impacts != expected_impacts or totals != expected_totals:
        raise CloudError('SQL monetary results disagree with local oracle; cloud verification failed.')
    return {'primary_findings_equal': True, 'account_impacts_equal': True, 'currency_totals_equal': True}


def run_cloud(bundle, runner, catalog='workspace', schema='default', evidence_path=None, progress=None):
    from .engine import reconcile
    oracle = reconcile(bundle)
    supported(bundle, oracle)
    prefix = 'cutoverguard_' + uuid.uuid4().hex[:16]
    tables = table_names(catalog, schema, prefix)
    evidence = {'status':'pending','run_id':oracle['run_id'],'tables':tables,
                'warehouse_id':runner.warehouse_id,'data_kind':'synthetic',
                'started_at':datetime.now(timezone.utc).isoformat(),'statement_ids':[]}
    try:
        for name, sql in build_pipeline(bundle,tables,oracle['run_id']):
            if progress:
                progress(f'Creating isolated {name} Delta table...')
            runner.execute(sql)
        issue_rows = runner.execute(f"SELECT check_id,type,key,field,source_value,candidate_value FROM {tables['issues']} ORDER BY check_id,type,key,field")
        impact_rows = runner.execute(f"SELECT account_id,currency,CAST(source_minor AS STRING),CAST(candidate_minor AS STRING),CAST(delta_minor AS STRING) FROM {tables['impacts']} ORDER BY account_id,currency")
        total_rows = runner.execute(f"SELECT currency,CAST(source_minor AS STRING),CAST(candidate_minor AS STRING),CAST(net_delta_minor AS STRING),CAST(absolute_account_delta_minor AS STRING),CAST(affected_notional_minor AS STRING) FROM {tables['totals']} ORDER BY currency")
        comparison = verify_remote(oracle, issue_rows, impact_rows, total_rows)
        evidence.update(status='verified',oracle_comparison=comparison,completed_at=datetime.now(timezone.utc).isoformat(),sql_issue_rows=issue_rows,sql_impact_rows=impact_rows,sql_total_rows=total_rows,statement_ids=list(runner.statement_ids))
        report = deepcopy(oracle)
        # Local validation/report text packaging, after actual platform computation agrees.
        report['execution'] = {'mode':'databricks','transport':'authenticated_cli','result_kind':'exported_snapshot',**evidence}
        return report
    except BaseException:
        evidence['status'] = 'unverified'
        raise
    finally:
        evidence['statement_ids'] = list(runner.statement_ids)
        # Each run gets an explicit evidence log, including failure tables for inspection.
        if evidence_path:
            Path(evidence_path).write_text(json.dumps(evidence,indent=2)+'\n')
