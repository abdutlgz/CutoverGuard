"""CutoverGuard local dashboard. Run .venv/bin/python cutover_app.py."""
import argparse
import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
MAX_BODY = 4_000_000


def validate_report(report):
    """Recompute imported claims locally; supplied cloud provenance is not authenticated."""
    from cutoverguard.engine import reconcile, ReconcileError
    if not isinstance(report, dict) or not isinstance(report.get('input_bundle'), dict):
        raise ReconcileError('Import a complete report containing its original input_bundle.')
    expected = reconcile(report['input_bundle'])
    keys = ['schema_version', 'engine_version', 'run_id', 'data_kind', 'status', 'summary',
            'checks', 'issues', 'account_impacts', 'currency_totals', 'quarantine', 'coverage',
            'dependency_graph']
    for key in keys:
        if key not in report or report[key] != expected[key]:
            raise ReconcileError(f'Imported report does not reconcile with its input: {key}. Rerun with the current engine.')
    execution = report.get('execution', {})
    if not isinstance(execution, dict) or execution.get('mode') not in {'local', 'databricks'}:
        raise ReconcileError('Imported report needs a supported execution mode.')
    if execution['mode'] == 'databricks':
        from cutoverguard.databricks import supported, verify_remote, CloudError
        required = ('statement_ids', 'tables', 'sql_issue_rows', 'sql_impact_rows', 'sql_total_rows')
        if execution.get('status') != 'verified' or any(key not in execution for key in required):
            raise ReconcileError('Saved Databricks reports require verified execution evidence and SQL result rows.')
        statements, tables = execution['statement_ids'], execution['tables']
        if (not isinstance(statements, list) or not statements
                or not all(isinstance(item, str) and item for item in statements)
                or not isinstance(tables, dict) or not tables
                or not all(isinstance(item, str) and item for item in tables.values())
                or execution.get('run_id') != expected['run_id']):
            raise ReconcileError('Saved Databricks evidence needs statement IDs, table names and the matching run ID.')
        try:
            if execution.get('transport') == 'authenticated_cli_volume_csv':
                from cutoverguard.file_ingestion import validate_bundle, file_pipeline
                validate_bundle(report['input_bundle'])
                if (execution.get('imported_rows_equal') is not True
                        or execution.get('validation_kind') != 'historical_source_controlled_candidate'
                        or any(type(execution.get(side+'_rows')) is not int
                               or execution[side+'_rows'] != len(report['input_bundle'][side]['transactions'])
                               for side in ('source','candidate'))):
                    raise ReconcileError('File-ingestion report requires complete imported-row agreement and matching row counts.')
                # Validate isolated table/path consistency; this generates SQL only.
                list(file_pipeline(report['input_bundle'], tables, expected['run_id'], execution.get('remote_directory','')))
            else:
                supported(report['input_bundle'], expected)
            comparison = verify_remote(expected, execution['sql_issue_rows'], execution['sql_impact_rows'], execution['sql_total_rows'])
            if execution.get('oracle_comparison') != comparison:
                raise ReconcileError('Saved Databricks comparison evidence is inconsistent.')
        except (CloudError, ValueError, TypeError, KeyError) as error:
            raise ReconcileError('Saved Databricks SQL evidence does not match this input and report.') from error
    result = deepcopy(expected)
    result['execution'] = deepcopy(execution)
    result['execution']['imported'] = True
    result['execution']['provenance_attestation'] = 'self_reported_not_authenticated'
    result['execution']['local_recalculation_verified'] = True
    result['limitations'].append('Imported calculations were recomputed locally. Execution provenance is supplied by the report and has not been independently authenticated.')
    return result


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value, mime='application/json'):
        if isinstance(value, bytes):
            payload = value
        elif mime == 'application/json':
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        else:
            payload = value.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def trusted_host(self):
        host = self.headers.get('Host', '').split(':')[0].lower()
        return host in {'localhost', '127.0.0.1'}

    def do_GET(self):
        from cutoverguard.engine import ReconcileError
        from cutoverguard.fixtures import available_fixtures, fixture
        if not self.trusted_host():
            self.reply(403, {'error': 'Use the local loopback address.'})
            return
        url = urlparse(self.path)
        if url.path == '/api/examples':
            self.reply(200, {'examples': available_fixtures()})
            return
        if url.path == '/api/replay':
            from cutoverguard.replay import build_replay
            scenario = parse_qs(url.query).get('scenario', ['defective'])[0]
            try:
                self.reply(200, build_replay(scenario))
            except (ValueError, KeyError, ReconcileError):
                self.reply(400, {'error': 'Choose defective or fixed for the controlled synthetic replay.'})
            return
        if url.path == '/api/fixture':
            kind = parse_qs(url.query).get('kind', ['defective'])[0]
            try:
                self.reply(200, fixture(kind))
            except (ValueError, KeyError, ReconcileError):
                self.reply(400, {'error': 'Unknown controlled example.'})
            return
        files = {'/': ('index.html', 'text/html'), '/style.css': ('style.css', 'text/css'), '/app.js': ('app.js', 'text/javascript')}
        if url.path not in files:
            self.reply(404, {'error': 'Not found.'})
            return
        name, mime = files[url.path]
        self.reply(200, (ROOT / 'cutover_static' / name).read_bytes(), mime)

    def do_POST(self):
        from cutoverguard.engine import reconcile, ReconcileError
        if not self.trusted_host():
            self.reply(403, {'error': 'Use the local loopback address.'})
            return
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            self.reply(403, {'error': 'Cross-origin requests are not supported.'})
            return
        if self.path not in {'/api/reconcile', '/api/validate-report'}:
            self.reply(404, {'error': 'Not found.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BODY:
                raise ReconcileError('Choose an input under 4 MB.')
            raw = self.rfile.read(length).decode('utf-8-sig')
            def reject_nonfinite(value):
                raise ReconcileError('JSON must not contain NaN or Infinity.')
            value = json.loads(raw, parse_constant=reject_nonfinite)
            result = reconcile(value) if self.path == '/api/reconcile' else validate_report(value)
            self.reply(200, result)
        except (ReconcileError, ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError) as error:
            self.reply(400, {'error': str(error) if isinstance(error, ReconcileError) else 'Invalid JSON input. Check the bundle format and fields.'})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8502)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'CutoverGuard at http://127.0.0.1:{args.port} — Ctrl+C to stop', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
