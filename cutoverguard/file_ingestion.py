"""Bounded public-retail file ingestion; actual SQL outputs must match the full oracle."""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid
from datetime import datetime, timezone
from .databricks import CloudError, supported, verify_remote
from .engine import reconcile
from .sql import build_pipeline, table_names, utc

COLUMNS = {
    'manifest': ['run_id', 'input_json'],
    'profiles': ['side', 'account_id', 'customer_id'],
    'transactions': ['side','event_id','account_id','amount_minor','currency','event_time','window_start','window_end'],
}

def validate_bundle(bundle):
    oracle = reconcile(bundle)
    info = bundle.get('dataset_info', {})
    if not isinstance(info,dict):
        raise CloudError('Public-retail dataset_info must be a metadata object.')
    case=info.get('controlled_case')
    if bundle.get('data_kind') != 'historical' or info.get('name') != 'UCI Online Retail' or info.get('validation_kind') != 'historical_source_controlled_candidate' or case not in ('ownership_swaps','baseline'):
        raise CloudError('This ingestion path supports only disclosed public-retail ownership_swaps and exact baseline cases.')
    if case=='baseline' and (oracle['status']!='ready_for_review' or any(bundle['source'][name]!=bundle['candidate'][name] for name in ('profiles','transactions'))):
        raise CloudError('The baseline requires identical source/candidate records and all supported checks passing.')
    # Reuse all numerical/ambiguity checks without relaxing the synthetic-only runner.
    gate = deepcopy(bundle)
    gate['data_kind'] = 'synthetic'
    supported(gate, oracle)
    if any(issue['type']=='timestamp_mismatch' for issue in oracle['issues']):
        raise CloudError('Timestamp-mismatch findings are outside this file demonstration; typed SQL rendering is not used as timestamp evidence.')
    return oracle


def rows_for(bundle, run_id):
    rows = {'manifest': [[run_id,json.dumps(bundle,sort_keys=True,separators=(',',':'))]], 'profiles':[], 'transactions':[]}
    for side in ('source','candidate'):
        snap = bundle[side]
        for row in snap['profiles']:
            rows['profiles'].append([side,row['account_id'],row['customer_id']])
        for row in snap['transactions']:
            rows['transactions'].append([side,row['event_id'],row['account_id'],row['amount_minor'],row['currency'],utc(row['event_time']),utc(snap['window_start']),utc(snap['window_end'])])
    return rows


def volume_path(volume, prefix):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', volume):
        raise CloudError('--volume must name an existing catalog.schema.volume (simple identifiers).')
    if not re.fullmatch(r'cutoverguard_[a-f0-9]{16}', prefix):
        raise CloudError('Invalid ingestion prefix')
    return '/Volumes/' + volume.replace('.','/') + '/' + prefix


def file_pipeline(bundle, tables, run_id, remote):
    if not re.fullmatch(r'/Volumes/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/cutoverguard_[a-f0-9]{16}', remote):
        raise CloudError('Invalid generated Volume file directory')
    target=re.fullmatch(r'`((?:[^`]|``)+)`\.`((?:[^`]|``)+)`\.`(cutoverguard_[a-f0-9]{16})_manifest`', tables.get('manifest',''))
    if not target or target.group(3)!=remote.rsplit('/',1)[-1] or tables!=table_names(target.group(1).replace('``','`'),target.group(2).replace('``','`'),target.group(3)):
        raise CloudError('Invalid or inconsistent generated Delta table names')
    # Never submit VALUES or embed the input JSON in SQL for this path.
    for name in COLUMNS:
        schema = ', '.join(col+' STRING' for col in COLUMNS[name])
        read = f"read_files('{remote}/{name}.csv', format => 'csv', header => true, schema => '{schema}', mode => 'FAILFAST', multiLine => true, escape => '\"')"
        projection = ','.join((f'CAST({col} AS TIMESTAMP) AS {col}' if col in ('event_time','window_start','window_end') else 'CAST(amount_minor AS DECIMAL(38,0)) AS amount_minor' if col=='amount_minor' else col) for col in COLUMNS[name])
        yield name, f'CREATE TABLE {tables[name]} USING DELTA AS SELECT {projection} FROM {read}'
    # Existing generator's first three statements are inline fixtures; skip them.
    # Its Gold statements independently compute joins/ledger effects on imported tables.
    for name, sql in build_pipeline(bundle, tables, run_id):
        if name in ('issues','impacts','totals'):
            yield name, sql


def prepare(bundle, output, volume, warehouse_id, catalog='workspace', schema='default', profile=None):
    oracle = validate_bundle(bundle)
    output = Path(output)
    if output.exists():
        raise CloudError('Output directory already exists; use --resume or choose a fresh directory.')
    prefix = 'cutoverguard_' + uuid.uuid4().hex[:16]
    remote = volume_path(volume,prefix)
    tables = table_names(catalog,schema,prefix)
    output.mkdir(parents=True)
    rows = rows_for(bundle,oracle['run_id'])
    hashes = {}
    for name, records in rows.items():
        path = output/(name+'.csv')
        with path.open('w',newline='',encoding='utf-8') as file:
            writer=csv.writer(file); writer.writerow(COLUMNS[name]); writer.writerows(records)
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (output/'input.json').write_text(json.dumps(bundle,indent=2)+'\n')
    (output/'pipeline.sql').write_text('\n\n'.join(sql+';' for _,sql in file_pipeline(bundle,tables,oracle['run_id'],remote))+'\n')
    state={'version':1,'status':'prepared_unverified','run_id':oracle['run_id'],'tables':tables,'remote_directory':remote,'warehouse_id':warehouse_id,'profile':profile,'file_sha256':hashes,'completed':[],'attempted':None,'statement_ids':[],'data_kind':'historical','validation_kind':'historical_source_controlled_candidate','source_rows':len(bundle['source']['transactions']),'candidate_rows':len(bundle['candidate']['transactions'])}
    save_state(output,state)
    return state


def micros(value):
    delta=datetime.fromisoformat(value).astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return str((delta.days*86400+delta.seconds)*1000000+delta.microseconds)


def save_state(output,state):
    path=Path(output)/'state.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(state,indent=2)+'\n'); temp.replace(path)


def classify_cli_error(stderr):
    """Return a fixed category only; never return supplied service/error text."""
    value=str(stderr).lower()
    categories=[
        ('authentication', ('unauthenticated','invalid_access_token','unauthorized','cannot configure default credentials','refresh token','authentication_failed','authentication failed','authfailed')),
        ('permission_denied', ('permission_denied','permission denied','insufficient_permissions','access denied')),
        ('directory_or_path', ('resource_does_not_exist','directory_not_found','path_not_found','file_not_found','not_found','no such file or directory','not found','does not exist','not a directory')),
        ('existing_file', ('file_already_exists','already_exists','already exists','file exists')),
        ('network', ('connection refused','connection reset','network is unreachable','no such host','dial tcp','tls handshake','context deadline exceeded','timeout','timed out')),
    ]
    for category,patterns in categories:
        if any(re.search(r'(?<![a-z0-9_])'+re.escape(pattern)+r'(?![a-z0-9_])', value) for pattern in patterns): return category
    if re.search(r'(?:status(?: code)?|http)\s*[:=]?\s*401\b',value): return 'authentication'
    if re.search(r'(?:status(?: code)?|http)\s*[:=]?\s*403\b',value): return 'permission_denied'
    return 'unknown'


def _fs_call(arguments,profile=None):
    cmd=['databricks','fs',*arguments]
    if profile: cmd += ['--profile',profile]
    try: result=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise CloudError('Volume operation outcome unknown (category: network); inspect the unique destination. No automatic retry.') from exc
    except FileNotFoundError as exc:
        raise CloudError('Volume operation failed (category: cli_unavailable); verify CLI installation.') from exc
    if result.returncode:
        raise CloudError('Volume operation failed or outcome unknown (category: '+classify_cli_error(result.stderr)+'); inspect permissions, login and unique destination in Terminal. No automatic retry.')
    return result.stdout


def upload_file(local,remote,profile=None):
    if not re.fullmatch(r'/Volumes/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/cutoverguard_[a-f0-9]{16}/(?:manifest|profiles|transactions)\.csv', remote):
        raise CloudError('Invalid generated upload file path')
    parent,filename=remote.rsplit('/',1)
    # UC file creation requires its parent directory to exist. mkdir changes only
    # this strictly validated generated run directory, not the Volume definition.
    _fs_call(['mkdir','dbfs:'+parent],profile)
    raw=_fs_call(['ls','dbfs:'+parent,'--output','json'],profile)
    try: entries=json.loads(raw)
    except (ValueError,TypeError) as exc:
        raise CloudError('Unexpected directory listing; no file copied.') from exc
    if not isinstance(entries,list): raise CloudError('Unexpected directory listing; no file copied.')
    names=[]
    for entry in entries:
        if not isinstance(entry,dict): raise CloudError('Unexpected directory entry; no file copied.')
        value=entry.get('name',entry.get('path'))
        if not isinstance(value,str): raise CloudError('Unexpected directory entry; no file copied.')
        names.append(value.rstrip('/').rsplit('/',1)[-1])
    if filename in names:
        raise CloudError('Volume file already exists (category: existing_file); no overwrite or automatic reuse.')
    # No --overwrite. A concurrent destination creation is also protected by CLI.
    _fs_call(['cp',str(local),'dbfs:'+remote],profile)


def execute(output,runner,uploader=upload_file):
    output=Path(output); state=json.loads((output/'state.json').read_text())
    bundle=json.loads((output/'input.json').read_text()); oracle=validate_bundle(bundle)
    if oracle['run_id'] != state['run_id'] or runner.warehouse_id != state['warehouse_id']:
        raise CloudError('Resume input fingerprint or warehouse differs.')
    for name,digest in state['file_sha256'].items():
        if hashlib.sha256((output/(name+'.csv')).read_bytes()).hexdigest()!=digest:
            raise CloudError('Prepared file changed; refusing upload/resume.')
    pipeline=list(file_pipeline(bundle,state['tables'],state['run_id'],state['remote_directory']))
    if state['attempted']:
        raise CloudError('A previous mutation has an ambiguous outcome: '+state['attempted']+'. Inspect remote objects/query history; start a fresh output directory rather than retrying writes.')
    state['status']='execution_pending'; save_state(output,state)
    def mutation(name,action):
        if name in state['completed']: return
        state['attempted']=name; save_state(output,state)
        try: action()
        except BaseException:
            state['status']='unverified'
            raise
        finally:
            state['statement_ids'] += [sid for sid in runner.statement_ids if sid not in state['statement_ids']]
            save_state(output,state)
        state['completed'].append(name); state['attempted']=None; save_state(output,state)
    for name in COLUMNS:
        mutation('upload_'+name,lambda name=name:uploader(output/(name+'.csv'),state['remote_directory']+'/'+name+'.csv',state['profile']))
    for name,sql in pipeline:
        mutation('table_'+name,lambda sql=sql:runner.execute(sql))
    try:
        # Check every imported row, including excluded-window rows and identifier strings.
        expected=rows_for(bundle,oracle['run_id'])
        for name,cols in COLUMNS.items():
            projection=','.join(f'CAST(unix_micros({c}) AS STRING)' if c in ('event_time','window_start','window_end') else 'CAST(amount_minor AS STRING)' if c=='amount_minor' else c for c in cols)
            expected_rows=[[micros(value) if cols[index] in ('event_time','window_start','window_end') else value for index,value in enumerate(row)] for row in expected[name]]
            observed=runner.execute('SELECT '+projection+' FROM '+state['tables'][name])
            if any(len(row)!=len(cols) for row in observed) or Counter(tuple(row) for row in observed)!=Counter(tuple(row) for row in expected_rows):
                raise CloudError('Imported '+name+' rows differ from the full prepared input.')
        tables=state['tables']
        issues=runner.execute(f"SELECT check_id,type,key,field,source_value,candidate_value FROM {tables['issues']}")
        impacts=runner.execute(f"SELECT account_id,currency,CAST(source_minor AS STRING),CAST(candidate_minor AS STRING),CAST(delta_minor AS STRING) FROM {tables['impacts']}")
        totals=runner.execute(f"SELECT currency,CAST(source_minor AS STRING),CAST(candidate_minor AS STRING),CAST(net_delta_minor AS STRING),CAST(absolute_account_delta_minor AS STRING),CAST(affected_notional_minor AS STRING) FROM {tables['totals']}")
        comparison=verify_remote(oracle,issues,impacts,totals)
        state['statement_ids'] += [sid for sid in runner.statement_ids if sid not in state['statement_ids']]
        state.update(status='verified',completed_at=datetime.now(timezone.utc).isoformat(),oracle_comparison=comparison,imported_rows_equal=True,sql_issue_rows=issues,sql_impact_rows=impacts,sql_total_rows=totals)
        report=deepcopy(oracle)
        report['execution']={'mode':'databricks','transport':'authenticated_cli_volume_csv','result_kind':'exported_snapshot',**state}
        report['limitations'].append('File-ingestion demonstration is bounded to the supplied public retail extract and controlled candidate; no large-scale or real migration performance claim.')
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
    except BaseException:
        state['status']='unverified'
        raise
    finally:
        state['statement_ids'] += [sid for sid in runner.statement_ids if sid not in state['statement_ids']]
        save_state(output,state)
