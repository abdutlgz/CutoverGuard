"""Create a populated draft Lakeview dashboard and governed Genie space via existing CLI."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_databricks_demo import load_report,datasets
from cutoverguard.databricks import CloudError,StatementRunner
from cutoverguard.engine import reconcile
from cutoverguard.fixtures import fixture

WAREHOUSE='515dff82a7a2c282'
SOURCES={
 'dashboard_api':'https://docs.databricks.com/api/lakeview/v1/create-dashboard',
 'widget_schema':'https://github.com/databricks/databricks-agent-skills/blob/main/plugins/databricks/copilot/skills/databricks-aibi-dashboards/references/1-widget-specifications.md',
 'dashboard_example':'https://github.com/databricks/databricks-agent-skills/blob/main/plugins/databricks/copilot/skills/databricks-aibi-dashboards/references/4-examples.md',
 'genie_api':'https://docs.databricks.com/api/genie/v1/create-space',
 'genie_validation':'https://docs.databricks.com/aws/en/genie-agents/conversation-api'}


def reports():
    result={'defective':load_report(ROOT/'cutover_results'/'defective-cloud-v2.json'),
            'fixed':load_report(ROOT/'cutover_results'/'fixed-cloud.json')}
    for name,report in result.items():
        if report['run_id']!=reconcile(fixture(name))['run_id']:
            raise CloudError('Native story is scoped to the exact tiny USD defective/fixed fixtures; input fingerprint changed.')
    return result


def native_queries(r,dollars=True):
    base=datasets(r)
    output={k:base[k].rstrip(';') for k in ('review_summary','ownership_findings','account_effects','zero_net_chart')}
    t=r['defective']['execution']['tables']
    money=','.join(f'CAST({field}/100 AS DECIMAL(38,2)) {field.replace("_minor","_usd")}' for field in ('net_delta_minor','absolute_account_delta_minor','affected_notional_minor')) if dollars else 'net_delta_minor,absolute_account_delta_minor,affected_notional_minor'
    output['defective_kpis']=f"SELECT (SELECT count(*) FROM {t['issues']}) primary_findings,{money} FROM {t['totals']} WHERE currency='USD'"
    return output


def widget(name,dataset,fields,kind,title,x,y,w,h,encoding=None):
    enc=encoding or {'columns':[{'fieldName':f,'displayName':f.replace('_',' ').title()} for f in fields]}
    return {'widget':{'name':name,'queries':[{'name':'main_query','query':{'datasetName':dataset,
            'fields':[{'name':f,'expression':'`'+f+'`'} for f in fields],'disaggregated':True}}],
            'spec':{'version':3 if kind=='bar' else 2,'widgetType':kind,'encodings':enc,'frame':{'showTitle':True,'title':title}}},
            'position':{'x':x,'y':y,'width':w,'height':h}}


def dashboard(r,genie_id=None,dollars=True):
    q=native_queries(r,dollars=dollars)
    layout=[{'widget':{'name':'story','multilineTextboxSpec':{'lines':['# CutoverGuard\n','\n','**Same total. Different owner.** Synthetic migration snapshots: two ownership breaks, opposite account effects, zero firm net. Corrected replay restores parity. Differences and affected notional are not losses.']}},'position':{'x':0,'y':0,'width':12,'height':3}}]
    kpis=[('primary_findings','Ownership breaks'),('net_delta_usd','Firm net change (USD)'),('absolute_account_delta_usd','Absolute allocation difference (USD)'),('affected_notional_usd','Changed transaction notional (USD)')] if dollars else [('primary_findings','Ownership breaks'),('net_delta_minor','Firm net change (USD cents)'),('absolute_account_delta_minor','Absolute allocation difference (USD cents)'),('affected_notional_minor','Changed transaction notional (USD cents)')]
    for index,(field,title) in enumerate(kpis):
        layout.append(widget('kpi_'+field,'defective_kpis',[field],'counter',title,index*3,3,3,3,{'value':{'fieldName':field,'displayName':title}}))
    bar=widget('allocation_bars','zero_net_chart',['label','delta_major','scenario'],'bar','Account allocation vs firm net — USD',0,6,7,6,
               {'x':{'fieldName':'label','scale':{'type':'categorical'}},'y':{'fieldName':'delta_major','scale':{'type':'quantitative'},'displayName':'Candidate − source (USD)'},'color':{'fieldName':'scenario','scale':{'type':'categorical','mappings':[{'value':'defective','color':'#C75C3C'},{'value':'fixed','color':'#247B75'}]}}})
    bar['widget']['spec']['mark']={'layout':'group'}
    layout.append(bar)
    layout.append(widget('run_comparison','review_summary',['scenario','currency','primary_findings','source_minor','candidate_minor','net_delta_minor'],'table','Defective → corrected SQL runs',7,6,5,6))
    layout.append(widget('ownership_evidence','ownership_findings',['scenario','key','type','source_value','candidate_value'],'table','Exact event ownership evidence',0,12,12,5))
    layout.append(widget('account_effects','account_effects',['scenario','account_id','currency','source_minor','candidate_minor','delta_minor'],'table','Exact account effects — integer minor units',0,17,12,5))
    settings={'theme':{'canvasBackgroundColor':{'light':'#F6F5F1','dark':'#14202C'},'widgetBackgroundColor':{'light':'#FFFFFF','dark':'#1B2938'},'fontColor':{'light':'#142B43','dark':'#F1F3F5'},'selectionColor':{'light':'#247B75','dark':'#54B7A9'},'visualizationColors':['#C75C3C','#247B75','#416D9D','#C9A64C','#8463A6'],'widgetHeaderAlignment':'LEFT'}}
    if genie_id:
        settings['genieSpace']={'isEnabled':True,'overrideId':genie_id,'enablementMode':'ENABLED'}
    return {'datasets':[{'name':name,'displayName':name.replace('_',' ').title(),'queryLines':[sql+'\n']} for name,sql in q.items()],
            'pages':[{'name':'review','displayName':'Migration review','pageType':'PAGE_TYPE_CANVAS','layout':layout}], 'uiSettings':settings}


def genie_space(r):
    q=native_queries(r)
    examples=[('Why does the defective migration fail despite an unchanged total?',q['review_summary']),
              ('Which transaction account assignments changed?',q['ownership_findings']),
              ('Which accounts changed and by how much?',q['account_effects']),
              ('Did the corrected migration restore account parity?',q['zero_net_chart'])]
    uid=lambda value:hashlib.sha256(value.encode()).hexdigest()[:32]
    instructions=['All records are synthetic, designed examples; never present observed client failures or savings.',
                  'Use exact SQL results from the six listed Gold tables. Keep source versus candidate and defective versus fixed runs distinct. Always name the scenario and currency.',
                  'Amounts ending _minor are signed integer cents for USD/EUR (scale2). Derive display units in SQL with DECIMAL, never binary floating-point. Never combine currencies.',
                  'A zero firm-level net difference does not establish account ownership parity. USD2000 absolute allocation discrepancy is a double-sided discrepancy, not money lost. USD3000 affected notional is not a loss estimate.',
                  'Explain the two event-account mismatches with their original/candidate IDs and exact account effects. Preserve leading-zero account IDs as strings.',
                  'Genie explains SQL evidence; it cannot authorize a cutover or establish regulatory compliance. Metadata completeness remains supplied declarations. If evidence is missing or a query fails, state uncertainty rather than inventing an answer.',
                  'Only SELECT queries over the registered tables belong in this space. Do not suggest executing remediation, writes, schema changes or privilege changes. A human validates generated SQL and explanations.']
    space={'version':2,'config':{'sample_questions':[{'id':uid('sample'+question),'question':[question]} for question,_ in examples]},
            'data_sources':{'tables':[{'identifier':r[scenario]['execution']['tables'][name].replace('`',''),'description':[f'{scenario} synthetic {name}; exact results from verified SQL snapshot.']} for scenario in ('defective','fixed') for name in ('issues','impacts','totals')]},
            'instructions':{'text_instructions':[{'id':uid('cutoverguard-policy'),'content':[text+'\n' for text in instructions]}],
                            'example_question_sqls':[{'id':uid('example'+question),'question':[question],'sql':[sql]} for question,sql in examples]},
            'benchmarks':{'questions':[{'id':uid('benchmark'+question),'question':[question],'answer':[{'format':'SQL','content':[sql]}]} for question,sql in examples]}}
    for collection in (space['config']['sample_questions'],space['instructions']['text_instructions'],space['instructions']['example_question_sqls'],space['benchmarks']['questions']):
        collection.sort(key=lambda item:item['id'])
    space['data_sources']['tables'].sort(key=lambda item:item['identifier'])
    return space


class NativeClient:
    def __init__(self,profile=None):
        self.profile=profile
    def request(self,method,path,payload=None):
        allowed=(path.startswith('/api/2.0/sql/statements') or
                 (method=='post' and path=='/api/2.0/genie/spaces') or
                 (method=='get' and re.fullmatch(r'/api/2.0/genie/spaces/[a-f0-9-]+\?include_serialized_space=true',path)) or
                 (method=='post' and path=='/api/2.0/lakeview/dashboards?dataset_catalog=workspace&dataset_schema=default') or
                 (method=='patch' and re.fullmatch(r'/api/2.0/lakeview/dashboards/[a-f0-9-]+\?dataset_catalog=workspace&dataset_schema=default',path)) or
                 (method=='get' and re.fullmatch(r'/api/2.0/lakeview/dashboards/[a-f0-9-]+',path)))
        if not allowed:
            raise CloudError('Native setup endpoint is not authorized by this runner.')
        command=['databricks','api',method,path,'--output','json']
        if payload is not None:
            command+=['--json',json.dumps(payload)]
        if self.profile:
            command+=['--profile',self.profile]
        try:
            response=subprocess.run(command,text=True,capture_output=True,timeout=40)
        except (FileNotFoundError,subprocess.TimeoutExpired) as error:
            raise CloudError('CLI unavailable/timed out; inspect your workspace before retrying resource creation.') from error
        if response.returncode:
            code=re.search(r'[\"\']error_code[\"\']\s*:\s*[\"\']([A-Z_]{3,60})[\"\']',response.stderr)
            suffix=' Error code: '+code.group(1) if code else ''
            raise CloudError('CLI API failed; verify login, entitlement, permissions and workspace details in your terminal.'+suffix+' No raw credentials/service response printed.')
        try:
            result=json.loads(response.stdout)
        except json.JSONDecodeError as error:
            raise CloudError('CLI response was not valid JSON; creation may have succeeded. Inspect workspace before retrying.') from error
        if not isinstance(result,dict):
            raise CloudError('Unexpected API response')
        return result


NUMERIC_COLUMNS={
    'review_summary':set(range(2,8)),
    'ownership_findings':set(),
    'account_effects':{3,4,5,6},
    'zero_net_chart':{3,4},
    'defective_kpis':{0,1,2,3},
}


def normalized(rows,numeric_columns=()):
    """Compare typed money/count columns exactly; IDs are never inferred numeric.

    Decimal equality ignores display scale (0.00 == 0) without normalization's
    context-dependent rounding. The SQL API returns DECIMAL values as strings.
    """
    result=[]
    for row in rows:
        values=[]
        for index,value in enumerate(row):
            if index in numeric_columns:
                if value is None:
                    values.append(None)
                elif type(value) is int or isinstance(value,Decimal) or (isinstance(value,str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value)):
                    number=Decimal(str(value))
                    if not number.is_finite():
                        raise CloudError('Nonfinite numeric SQL result')
                    values.append(number)
                else:
                    raise CloudError('Unexpected nonexact numeric SQL result')
            else:
                values.append(None if value is None else str(value))
        result.append(tuple(values))
    return Counter(result)


def expected_rows(r,dollars=True):
    expected={name:[] for name in native_queries(r)}
    for scenario,report in r.items():
        execution=report['execution']
        count=len(execution['sql_issue_rows'])
        expected['review_summary'] += [[scenario,*row,count] for row in execution['sql_total_rows']]
        expected['ownership_findings'] += [[scenario,*row] for row in execution['sql_issue_rows']]
        for row in execution['sql_impact_rows']:
            expected['account_effects'].append([scenario,*row,str(Decimal(row[4])/100)])
            expected['zero_net_chart'].append([scenario,row[0],row[1],row[4],str(Decimal(row[4])/100),'Account allocation difference'])
        for row in execution['sql_total_rows']:
            expected['zero_net_chart'].append([scenario,'Firm net',row[0],row[3],str(Decimal(row[3])/100),'Firm net difference'])
            if scenario=='defective' and row[0]=='USD':
                expected['defective_kpis'].append([count,*[str(Decimal(value)/100) if dollars else value for value in row[3:6]]])
    return expected


def validate_sql(r,runner,diagnostics_dir=None):
    expected=expected_rows(r)
    for name,sql in native_queries(r).items():
        rows=runner.execute(sql)
        if normalized(rows,NUMERIC_COLUMNS[name])!=normalized(expected[name],NUMERIC_COLUMNS[name]):
            if diagnostics_dir is not None:
                # This creator is pinned to the two tiny synthetic fixtures.
                # Save query values only, never CLI responses or authentication.
                (Path(diagnostics_dir)/'dataset-mismatch.json').write_text(json.dumps({
                    'data_kind':'synthetic','dataset':name,
                    'statement_id':runner.statement_ids[-1] if runner.statement_ids else None,
                    'numeric_columns':sorted(NUMERIC_COLUMNS[name]),
                    'expected_rows':expected[name],'actual_rows':rows,
                },indent=2,default=str)+'\n')
            raise CloudError('Live dataset differs from captured oracle: '+name)
    return {'datasets_verified':len(expected),'statement_ids':list(runner.statement_ids)}


def identifier(result,key):
    value=result.get(key)
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9-]{16,40}',value):
        raise CloudError('Resource response omitted a valid '+key+'; inspect workspace before retrying.')
    return value


def host_url(value):
    parsed=urlsplit(value or '')
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Host must be an HTTPS workspace origin without credentials or path')
    return value.rstrip('/')


def configured_host(profile):
    if os.environ.get('DATABRICKS_HOST'):
        return host_url(os.environ['DATABRICKS_HOST'])
    config=configparser.ConfigParser();config.read(Path.home()/'.databrickscfg')
    value=config.get(profile or 'DEFAULT','host',fallback=None)
    return host_url(value) if value else ''


def authored_differences(desired,received,path=''):
    """Exact authored content, unordered ID collections and exact text concatenation.

    Additional object metadata/defaults are ignored; collection membership and
    authored fields are exact. Extra instruction/source behavior is rejected.
    """
    differences=[]
    def add(kind):
        differences.append({'path':path or '/', 'kind':kind})
    if isinstance(desired,dict):
        if not isinstance(received,dict):
            add('type');return differences
        for key,value in desired.items():
            if key not in received:
                differences.append({'path':path+'/'+key,'kind':'missing_field'})
            else:
                differences.extend(authored_differences(value,received[key],path+'/'+key))
        metadata_keys={'id','etag','create_time','update_time','created_at','updated_at','service_metadata'}
        def empty_default(value):
            if value is None or value=='':
                return True
            if isinstance(value,(list,dict)):
                return all(empty_default(item) for item in (value.values() if isinstance(value,dict) else value))
            return False
        for key,value in received.items():
            if key not in desired and key not in metadata_keys and not empty_default(value):
                differences.append({'path':path+'/'+key,'kind':'extra_behavior'})
        return differences
    if isinstance(desired,list):
        if not isinstance(received,list):
            add('type');return differences
        if all(isinstance(item,str) for item in desired):
            if not all(isinstance(item,str) for item in received) or ''.join(desired)!=''.join(received):
                add('content')
            return differences
        identity=next((key for key in ('id','identifier') if desired and all(isinstance(item,dict) and key in item for item in desired)),None)
        if identity=='id':
            if len(desired)!=len(received) or not all(isinstance(item,dict) for item in received):
                add('membership');return differences
            # Server IDs are metadata. Match complete semantic records, preserving
            # question-to-SQL associations and duplicate multiplicity.
            remaining=list(received)
            for index,item in enumerate(desired):
                authored={key:value for key,value in item.items() if key!='id'}
                matches=[i for i,candidate in enumerate(remaining) if not authored_differences(authored,candidate,path+'/'+str(index))]
                if not matches:
                    if remaining:
                        candidates=[authored_differences(authored,candidate,path+'/'+str(index)) for candidate in remaining]
                        differences.extend(min(candidates,key=len))
                    else:
                        add('membership')
                    return differences
                remaining.pop(matches[0])
            return differences
        if identity:
            if not all(isinstance(item,dict) and isinstance(item.get(identity),str) for item in received):
                add('invalid_collection');return differences
            actual={item[identity]:item for item in received}
            expected={item[identity]:item for item in desired}
            if len(actual)!=len(received) or actual.keys()!=expected.keys():
                add('membership');return differences
            for key,item in expected.items():
                differences.extend(authored_differences(item,actual[key],path+'/'+key))
            return differences
        if len(desired)!=len(received):
            add('membership');return differences
        for index,(a,b) in enumerate(zip(desired,received)):
            differences.extend(authored_differences(a,b,path+'/'+str(index)))
        return differences
    if type(desired) is not type(received) or desired!=received:
        add('content')
    return differences


def sanitize_configuration(value):
    """Persist only serialized configuration, with common secret patterns removed."""
    if isinstance(value,dict):
        return {key:('[REDACTED]' if re.search(r'(?i)(token|secret|password|credential|api[_-]?key|authorization)',key) else sanitize_configuration(item)) for key,item in value.items()}
    if isinstance(value,list):
        return [sanitize_configuration(item) for item in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+',r'\1=[REDACTED]',value)
        value=re.sub(r'\bdapi[A-Za-z0-9]{16,}\b','[REDACTED]',value)
        value=re.sub(r'(?i)bearer\s+[A-Za-z0-9._-]+','Bearer [REDACTED]',value)
        def redact_url(match):
            url=match.group(0)
            parsed=urlsplit(url)
            if re.search(r'(?i)(sig|credential|token|api[_-]?key|x-amz-|x-goog-)',parsed.query):
                return parsed.scheme+'://'+parsed.netloc+parsed.path+'?[REDACTED]'
            return url
        value=re.sub(r"https?://[^\s\"'<>]+",redact_url,value)
    return value


def inspect_genie(r,client,space_id,output):
    identifier({'space_id':space_id},'space_id')
    response=client.request('get',f'/api/2.0/genie/spaces/{space_id}?include_serialized_space=true')
    value=response.get('serialized_space')
    if not isinstance(value,str):
        raise CloudError('Genie GET omitted serialized_space; inspect permissions/include_serialized_space support.')
    try:
        received=json.loads(value)
    except json.JSONDecodeError as error:
        raise CloudError('Genie GET returned invalid serialized configuration') from error
    desired=genie_space(r)
    differences=authored_differences(desired,received)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    # Never persist the raw API envelope, authentication data or signed links.
    projection={key:received.get(key) for key in ('version','config','data_sources','instructions','benchmarks')} if isinstance(received,dict) else received
    for name,body in [('genie-desired.json',desired),('genie-readback.json',projection),('genie-differences.json',differences)]:
        (output/name).write_text(json.dumps(sanitize_configuration(body),indent=2)+'\n')
    return differences


def dashboard_differences(desired,actual):
    """Keep canvas/theme exact; match complete authored datasets by unique name."""
    differences=[]
    if not isinstance(actual,dict):
        return [{'path':'/','kind':'type'}]
    differences.extend(authored_differences({}, {key:value for key,value in actual.items() if key not in desired}))
    for key in ('pages','uiSettings'):
        if actual.get(key)!=desired[key]:
            differences.append({'path':'/'+key,'kind':'content'})
    datasets=actual.get('datasets')
    if not isinstance(datasets,list) or not all(isinstance(item,dict) and isinstance(item.get('name'),str) for item in datasets):
        return differences+[{'path':'/datasets','kind':'invalid_collection'}]
    observed={item['name']:item for item in datasets}
    expected={item['name']:item for item in desired['datasets']}
    if len(observed)!=len(datasets) or observed.keys()!=expected.keys():
        return differences+[{'path':'/datasets','kind':'membership'}]
    for name,dataset in expected.items():
        expected_dataset=deepcopy(dataset)
        # Lakeview materializes the defaults supplied on our create request.
        # Validate their exact values when returned; do not ignore SQL context.
        for key,value in (('catalog','workspace'),('schema','default')):
            if key in observed[name] and key not in expected_dataset:
                expected_dataset[key]=value
        differences.extend(authored_differences(expected_dataset,observed[name],'/datasets/'+name))
    return differences


def inspect_dashboard(r,client,dashboard_id,space_id,output):
    identifier({'dashboard_id':dashboard_id},'dashboard_id')
    identifier({'space_id':space_id},'space_id')
    response=client.request('get','/api/2.0/lakeview/dashboards/'+dashboard_id)
    value=response.get('serialized_dashboard')
    if not isinstance(value,str):
        raise CloudError('Dashboard GET omitted serialized_dashboard')
    try:
        actual=json.loads(value)
    except json.JSONDecodeError as error:
        raise CloudError('Dashboard GET returned invalid serialized configuration') from error
    desired=dashboard(r,space_id)
    differences=dashboard_differences(desired,actual)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    projection=actual  # Serialized configuration only; sanitizer covers extra fields too.
    for name,body in [('dashboard-desired.json',desired),('dashboard-readback.json',projection),('dashboard-differences.json',differences)]:
        (output/name).write_text(json.dumps(sanitize_configuration(body),indent=2)+'\n')
    return differences


def setup(r,client,runner,output,parent=None,host='',resume=False,existing_space_id=None,existing_dashboard_id=None):
    output=Path(output);state_path=output/'state.json'
    if resume:
        state=json.loads(state_path.read_text())
        if state.get('host')!=host or state.get('warehouse_id')!=runner.warehouse_id:
            raise CloudError('Resume workspace host/warehouse differs from saved run')
        if state['source_run_ids']!={name:report['run_id'] for name,report in r.items()}:
            raise CloudError('Resume input fingerprints changed')
    else:
        if output.exists():
            raise CloudError('Output folder exists; use --resume or choose a fresh --output')
        output.mkdir(parents=True)
        state={'status':'prepared','source_run_ids':{name:report['run_id'] for name,report in r.items()},'resource_suffix':uuid.uuid4().hex[:8],
               'created_at':datetime.now(timezone.utc).isoformat(),'warehouse_id':runner.warehouse_id,'published':False,'sources':SOURCES}
    for key,value in [('space_id',existing_space_id),('dashboard_id',existing_dashboard_id)]:
        if value:
            if not resume:
                raise CloudError('Existing resource IDs require --resume after inspecting saved creation state')
            identifier({key:value},key)
            if state.get(key) and state[key]!=value:
                raise CloudError('Recovered resource ID conflicts with saved state')
            state[key]=value
    state.update(parent_path=parent,host=host,ui_visual_verification='pending',genie_answer_evaluation='pending')
    try:
        state['sql_verification']=validate_sql(r,runner,output)
        if not state.get('space_id'):
            if state.get('genie_creation_attempted'):
                raise CloudError('Genie creation outcome is unknown. Inspect workspace and recover its ID with --existing-space-id before resume.')
            payload={'warehouse_id':runner.warehouse_id,'title':'CutoverGuard explanations '+state['resource_suffix'],
                     'description':'Synthetic financial migration evidence; generated answers require review.','serialized_space':json.dumps(genie_space(r))}
            if parent:
                payload['parent_path']=parent
            (output/'genie-create.json').write_text(json.dumps(payload,indent=2)+'\n')
            state['genie_creation_attempted']=True
            state_path.write_text(json.dumps(state,indent=2)+'\n')
            result=client.request('post','/api/2.0/genie/spaces',payload)
            state['space_id']=identifier(result,'space_id')
            state['genie_url']=host+'/genie/rooms/'+state['space_id']
            state_path.write_text(json.dumps(state,indent=2)+'\n')
        differences=inspect_genie(r,client,state['space_id'],output/'diagnostics')
        if differences:
            raise CloudError('Genie authored configuration differs at '+differences[0]['path']+' ('+differences[0]['kind']+'); see diagnostics/genie-differences.json.')
        state['genie_configuration_verified']=True
        serialized=dashboard(r,state['space_id'])
        if not state.get('dashboard_id'):
            if state.get('dashboard_creation_attempted'):
                raise CloudError('Dashboard creation outcome is unknown. Inspect workspace and recover its ID with --existing-dashboard-id before resume.')
            payload={'display_name':'CutoverGuard migration review '+state['resource_suffix'],'warehouse_id':runner.warehouse_id,'serialized_dashboard':json.dumps(serialized)}
            if parent:
                payload['parent_path']=parent
            (output/'dashboard-create.json').write_text(json.dumps(payload,indent=2)+'\n')
            state['dashboard_creation_attempted']=True
            state_path.write_text(json.dumps(state,indent=2)+'\n')
            created=client.request('post','/api/2.0/lakeview/dashboards?dataset_catalog=workspace&dataset_schema=default',payload)
            state['dashboard_id']=identifier(created,'dashboard_id')
            state['dashboard_url']=host+'/sql/dashboardsv3/'+state['dashboard_id']
            state_path.write_text(json.dumps(state,indent=2)+'\n')
        differences=inspect_dashboard(r,client,state['dashboard_id'],state['space_id'],output/'diagnostics')
        if differences:
            raise CloudError('Dashboard authored configuration differs at '+differences[0]['path']+' ('+differences[0]['kind']+'); see diagnostics/dashboard-differences.json.')
        state.update(status='created_configuration_verified',dashboard_url=host+'/sql/dashboardsv3/'+state['dashboard_id'],genie_url=host+'/genie/rooms/'+state['space_id'])
        return state
    except BaseException:
        state['status']='partial_or_unverified'
        raise
    finally:
        state['statement_ids']=list(runner.statement_ids)
        state_path.write_text(json.dumps(state,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-dashboard',action='store_true',help='GET existing draft dashboard and save sanitized differences; no SQL/create')
    parser.add_argument('--inspect',action='store_true',help='GET existing Genie configuration and save sanitized differences; no SQL/create')
    parser.add_argument('--create',action='store_true',help='Validate live SQL then create new Genie space and draft dashboard')
    parser.add_argument('--profile')
    parser.add_argument('--warehouse-id',default=WAREHOUSE)
    parser.add_argument('--parent-path',help='Existing workspace folder; default user home')
    parser.add_argument('--host',help='Optional HTTPS workspace origin, used only to format links')
    parser.add_argument('--output',type=Path,default=ROOT/'native_databricks'/'run-001')
    parser.add_argument('--existing-space-id',help='Recover a created Genie ID after inspecting an unknown creation outcome; requires --resume')
    parser.add_argument('--existing-dashboard-id',help='Recover a created dashboard ID after inspecting unknown creation outcome; requires --resume')
    parser.add_argument('--resume',action='store_true',help='Reuse recorded IDs from a partially completed run')
    parser.add_argument('--prepare-only',action='store_true',help='Generate reviewable JSON without making remote requests')
    args=parser.parse_args()
    try:
        r=reports()
        if args.inspect_dashboard:
            saved=json.loads((args.output/'state.json').read_text())
            dashboard_id=args.existing_dashboard_id or saved.get('dashboard_id')
            space_id=args.existing_space_id or saved.get('space_id')
            if not dashboard_id or not space_id:
                raise CloudError('Existing dashboard and Genie IDs are required for read-only dashboard inspection')
            differences=inspect_dashboard(r,NativeClient(args.profile),dashboard_id,space_id,args.output/'diagnostics')
            print('Read-only dashboard inspection saved:',args.output/'diagnostics')
            print('Authored-content differences:',len(differences))
            for difference in differences[:10]:
                print(difference['path'],difference['kind'])
            return 1 if differences else 0
        if args.inspect:
            saved=json.loads((args.output/'state.json').read_text())
            space_id=args.existing_space_id or saved.get('space_id')
            if not space_id:
                raise CloudError('No existing space ID available for read-only inspection')
            differences=inspect_genie(r,NativeClient(args.profile),space_id,args.output/'diagnostics')
            print('Read-only Genie inspection saved:',args.output/'diagnostics')
            print('Authored-content differences:',len(differences))
            for difference in differences[:10]:
                print(difference['path'],difference['kind'])
            return 1 if differences else 0
        if args.prepare_only or not args.create:
            args.output.mkdir(parents=True,exist_ok=False)
            (args.output/'dashboard.lvdash.json').write_text(json.dumps(dashboard(r),indent=2)+'\n')
            (args.output/'genie-space.json').write_text(json.dumps(genie_space(r),indent=2)+'\n')
            (args.output/'sources.json').write_text(json.dumps(SOURCES,indent=2)+'\n')
            print('Local payloads prepared; native creation and UI rendering remain unverified:',args.output)
            return 0
        client=NativeClient(args.profile)
        runner=StatementRunner(client,args.warehouse_id)
        host=host_url(args.host) if args.host else configured_host(args.profile)
        state=setup(r,client,runner,args.output,args.parent_path,host,args.resume,args.existing_space_id,args.existing_dashboard_id)
        print('Draft dashboard:',state['dashboard_url'])
        print('Genie space:',state['genie_url'])
        print('Live SQL/configuration verified. Open both links to verify rendering and answers. Not published.')
        return 0
    except (CloudError,ValueError,OSError,KeyError) as error:
        print('Native setup UNVERIFIED:',error)
        print('Saved state (if execution began):',args.output/'state.json')
        print('Inspect any saved state/resource IDs before retrying creation; --resume reuses known IDs.')
        return 1


if __name__=='__main__':
    sys.exit(main())
