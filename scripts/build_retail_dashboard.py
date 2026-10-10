"""Generate a genuine Lakeview dashboard definition from verified retail evidence."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutover_app import validate_report
from scripts.setup_cutoverguard_native import widget
from cutoverguard.aggregate_baseline import compute as aggregate_compute,query as aggregate_query


def build(report):
    verified=validate_report(report)
    if verified['execution'].get('transport')!='authenticated_cli_volume_csv':
        raise ValueError('Use a verified historical retail file-ingestion report.')
    if len(verified['currency_totals'])!=1:
        raise ValueError('This retail dashboard requires one separately displayed currency.')
    currency=verified['currency_totals'][0]['currency']
    t=verified['execution']['tables']
    queries={
        'retail_kpis':f"SELECT (SELECT count(*) FROM {t['issues']}) primary_findings,currency,CAST(source_minor/100 AS DECIMAL(38,2)) source_major,CAST(candidate_minor/100 AS DECIMAL(38,2)) candidate_major,CAST(net_delta_minor/100 AS DECIMAL(38,2)) net_delta_major,CAST(absolute_account_delta_minor/100 AS DECIMAL(38,2)) absolute_delta_major,CAST(affected_notional_minor/100 AS DECIMAL(38,2)) affected_major FROM {t['totals']} WHERE currency='{currency}'",
        'retail_findings':f'SELECT check_id,type,key,field,source_value,candidate_value FROM {t["issues"]}',
        'retail_impacts':f"SELECT account_id,currency,CAST(source_minor/100 AS DECIMAL(38,2)) source_major,CAST(candidate_minor/100 AS DECIMAL(38,2)) candidate_major,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM {t['impacts']} WHERE currency='{currency}'",
        'retail_chart':f"SELECT account_id label,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM {t['impacts']} WHERE currency='{currency}' AND delta_minor<>0 UNION ALL SELECT 'Extract net' label,CAST(net_delta_minor/100 AS DECIMAL(38,2)) delta_major FROM {t['totals']} WHERE currency='{currency}'",
    }
    count=len(verified['input_bundle']['source']['transactions'])
    candidate_count=len(verified['input_bundle']['candidate']['transactions'])
    layout=[{'widget':{'name':'retail_story','multilineTextboxSpec':{'lines':[
        '# CutoverGuard: historical invoice review\n',
        f'UCI Online Retail: {count:,} source and {candidate_count:,} candidate historical invoice lines; controlled case: {verified["input_bundle"]["dataset_info"]["controlled_case"]}. Customer-derived receivables buckets are not bank accounts; invoice values are not cash settlements.\n',
        f'**Review status: {verified["status"]}.** Saved SQL results match the local oracle; execution provenance is supplied by the report. Matching extract totals can hide ownership differences. Absolute differences and changed transaction notional are not losses.\n',
        f'Currency: {currency}. Run fingerprint: `{verified["run_id"]}`. Source: UCI Online Retail, DOI 10.24432/C5BW33, CC BY 4.0.'
    ]}},'position':{'x':0,'y':0,'width':12,'height':3}}]
    for i,(field,title) in enumerate([('primary_findings','Ownership findings'),('net_delta_major',f'Extract net change ({currency})'),('absolute_delta_major',f'Absolute account difference ({currency})'),('affected_major',f'Changed transaction notional ({currency})')]):
        layout.append(widget('retail_'+field,'retail_kpis',[field],'counter',title,i*3,3,3,3,
                             {'value':{'fieldName':field,'displayName':title}}))
    layout.append(widget('retail_signed_chart','retail_chart',['label','delta_major'],'bar',f'Changed bucket allocation vs extract net — {currency}',0,6,7,6,
                         {'x':{'fieldName':'label','scale':{'type':'categorical'}},'y':{'fieldName':'delta_major','scale':{'type':'quantitative'},'displayName':f'Candidate minus source ({currency})'}}))
    layout.append(widget('retail_totals','retail_kpis',['currency','source_major','candidate_major','net_delta_major'],'table',f'Equal extract totals — {currency}',7,6,5,6))
    layout.append(widget('retail_ownership','retail_findings',['key','type','field','source_value','candidate_value'],'table','Controlled ownership evidence',0,12,12,5))
    layout.append(widget('retail_changed_accounts','retail_impacts',['account_id','currency','source_major','candidate_major','delta_major'],'table',f'Receivables bucket comparison — {currency}',0,17,12,6))
    body={'datasets':[{'name':name,'displayName':name.replace('_',' ').title(),'queryLines':[query+'\n']} for name,query in queries.items()],
          'pages':[{'name':'retail_review','displayName':'Historical retail review','pageType':'PAGE_TYPE_CANVAS','layout':layout}]}
    return body


def comparison(defective,corrected):
    d=validate_report(defective);f=validate_report(corrected)
    if d['input_bundle']['dataset_info']['controlled_case']!='ownership_swaps' or f['input_bundle']['dataset_info']['controlled_case']!='baseline':
        raise ValueError('Comparison requires controlled ownership_swaps and exact baseline reports.')
    if d['input_bundle']['source']!=f['input_bundle']['source'] or d['input_bundle']['config']!=f['input_bundle']['config'] or d['input_bundle']['dataset_info']['source_file_sha256']!=f['input_bundle']['dataset_info']['source_file_sha256']:
        raise ValueError('Comparison source, configuration or historical file hash differs.')
    if d['status']!='blocked' or f['status']!='ready_for_review':
        raise ValueError('Comparison requires blocked defective and ready_for_review baseline.')
    if not d['input_bundle']['dataset_info'].get('source_file_sha256'):
        raise ValueError('Comparison requires the historical source file hash.')
    if not d['execution'].get('warehouse_id') or d['execution']['warehouse_id']!=f['execution'].get('warehouse_id'):
        raise ValueError('Comparison warehouse differs or is missing.')
    if set(d['execution']['tables'].values()) & set(f['execution']['tables'].values()):
        raise ValueError('Comparison requires distinct isolated table sets.')
    body={'datasets':[],'pages':[]}
    for label,r in [('defective',d),('corrected',f)]:
        part=deepcopy(build(r))
        for dataset in part['datasets']:
            dataset['name']=label+'_'+dataset['name']
        page=part['pages'][0];page['name']=label+'_review';page['displayName']=label.title()+' evidence'
        for item in page['layout']:
            item['widget']['name']=label+'_'+item['widget']['name']
            for query in item['widget'].get('queries',[]):
                query['query']['datasetName']=label+'_'+query['query']['datasetName']
        body['datasets'].extend(part['datasets']);body['pages'].append(page)
    queries={x['name']:''.join(x['queryLines']).strip() for x in body['datasets']}
    summary=' UNION ALL '.join(f"SELECT '{label}' scenario, q.* FROM ({queries[label+'_retail_kpis']}) q" for label in ('defective','corrected'))
    body['datasets'].append({'name':'retail_comparison','displayName':'Before and after','queryLines':[summary+'\n']})
    currency=d['currency_totals'][0]['currency']
    layout=[{'widget':{'name':'comparison_story','multilineTextboxSpec':{'lines':[
        '# Same total. Corrected ownership.\n',
        'Historical UCI invoices with controlled defects and an exact corrected baseline. The same source is preserved in both runs. Defective: **blocked**. Corrected: **ready for human review**, not automatic production approval.\n',
        'Both saved file-ingestion SQL runs match their local oracle. Execution provenance is supplied; differences and affected notional are not losses. See the two evidence tabs for run fingerprints, findings and source attribution.'
    ]}},'position':{'x':0,'y':0,'width':12,'height':3}},
    widget('comparison_table','retail_comparison',['scenario','currency','primary_findings','source_major','candidate_major','net_delta_major','absolute_delta_major','affected_major'],'table','Defective → corrected: exact SQL results',0,3,12,5),
    widget('comparison_chart','retail_comparison',['scenario','absolute_delta_major'],'bar',f'Absolute bucket differences — {currency}',0,8,12,6,
           {'x':{'fieldName':'scenario','scale':{'type':'categorical'}},'y':{'fieldName':'absolute_delta_major','scale':{'type':'quantitative'},'displayName':f'Absolute differences ({currency})'}})]
    body['pages'].insert(0,{'name':'comparison_review','displayName':'Before and after','pageType':'PAGE_TYPE_CANVAS','layout':layout})
    baseline=aggregate_compute(d)
    all_green=all(row['pass'] for row in baseline['checks'])
    ownership_count=baseline['ownership_findings']
    checksum_pass=next(row['pass'] for row in baseline['checks'] if row['id']=='account_checksum')
    body['datasets'].append({'name':'aggregate_baseline','displayName':'Totals-only baseline','queryLines':[aggregate_query(d['execution']['tables']['transactions'],d['execution']['tables']['issues'])+'\n']})
    body['pages'].insert(0,{'name':'baseline_review','displayName':'What totals miss','pageType':'PAGE_TYPE_CANVAS','layout':[
        {'widget':{'name':'baseline_story','multilineTextboxSpec':{'lines':[
            f'# {"Totals match." if all_green else "Aggregate checks disagree."} {ownership_count} invoice assignments have the wrong owner.\n',
            ('Every aggregate check shown passes.' if all_green else 'At least one aggregate check shown fails.')+' CutoverGuard blocks the controlled candidate on its verified findings.\n',
            ('The unweighted owner-ID checksum also passes: swapping owners preserves the same owner multiset.' if checksum_pass else 'The owner-ID checksum fails: these candidates have different owner-ID checksums.')
        ]}},'position':{'x':0,'y':0,'width':12,'height':2}},
        widget('baseline_checks','aggregate_baseline',['check_name','result_label','source_value','candidate_value'],'table','Same snapshots: aggregate checks versus ownership',0,2,12,5),
        widget('baseline_status','aggregate_baseline',['check_name','result','status_marker'],'bar','Green aggregates, blocked ownership',0,7,12,4,
               {'x':{'fieldName':'check_name','scale':{'type':'categorical'}},'y':{'fieldName':'status_marker','scale':{'type':'quantitative'},'displayName':'Check result'},
                'color':{'fieldName':'result','scale':{'type':'categorical','mappings':[{'value':'PASS','color':'#16803D'},{'value':'BLOCKED','color':'#DC2626'},{'value':'FAIL','color':'#DC2626'},{'value':'NO OWNERSHIP FINDINGS','color':'#16803D'}]}}}),
        {'widget':{'name':'baseline_footnote','multilineTextboxSpec':{'lines':[
            'Historical UCI invoices; deliberately injected defects. Source: DOI 10.24432/C5BW33, CC BY 4.0.\n',
            'Illustrative checklist, not production sign-off. Owner checksum: SHA256(raw UTF-8 ID), first 64 bits, summed modulo 2^64. It omits the event-to-owner relationship. Row-diff and owner-aware hash tools can catch swaps too; CutoverGuard adds bucket effects, review state and evidence.'
        ]}},'position':{'x':0,'y':11,'width':12,'height':2}}]})
    return body


def generate(source,output,corrected=None):
    source=Path(source);output=Path(output)
    if output.exists():
        raise ValueError('Output folder already exists; choose a fresh output.')
    report=json.loads(source.read_text());body=build(report);verified=validate_report(report)
    if corrected:
        body=comparison(report,json.loads(Path(corrected).read_text()))
    totals=verified['currency_totals'][0];currency=totals['currency']
    def major(value):
        amount=int(value);whole,cents=divmod(abs(amount),100)
        return ('-' if amount<0 else '')+str(whole)+'.'+str(cents).zfill(2)
    output.mkdir(parents=True)
    target=output/'CutoverGuard-Retail.lvdash.json'
    target.write_text(json.dumps(body,indent=2)+'\n')
    (output/'README.md').write_text('''# Import the historical retail dashboard

This .lvdash.json contains dataset queries and widget/page definitions, not a
reconciliation evidence report. The report remains report.json in the run folder.

In Databricks, go to the Dashboards listing → Create dashboard dropdown → Import
dashboard from file. Select CutoverGuard-Retail.lvdash.json and import it as a new
dashboard. Select the existing SQL warehouse, then refresh. This preserves the
separate synthetic dashboard and its synthetic-only Genie space. Rendering and
the newly generated dataset queries must be checked in the workspace.

'''+f'''Expected saved run: {verified['summary']['primary_issue_count']} controlled ownership findings;
currency {currency}; extract net {major(totals['net_delta_minor'])}; absolute account
differences {major(totals['absolute_account_delta_minor'])}; affected transaction
notional {major(totals['affected_notional_minor'])}; source total
{major(totals['source_minor'])}; candidate total {major(totals['candidate_minor'])}.
These are historical invoice values, not cash/loss. Execution provenance remains
supplied, not authenticated, although financial claims are locally recalculated.
''')
    if corrected:
        with (output/'README.md').open('a') as file:
            file.write('\nThis version includes What totals miss, Before and after, Defective evidence and Corrected evidence tabs. Corrected expectations: zero findings and discrepancy metrics; ready_for_review. Refresh and inspect both evidence tabs. The new totals-only baseline SQL is prepared, not live-verified.\n')
        (output/'aggregate-baseline.expected.json').write_text(json.dumps(aggregate_compute(verified),indent=2)+'\n')
    (output/'generation.json').write_text(json.dumps({'status':'prepared_not_imported','source_report_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'run_id':report['run_id'],'warehouse_id':report['execution']['warehouse_id'],
        'corrected_report_sha256':hashlib.sha256(Path(corrected).read_bytes()).hexdigest() if corrected else None,
        'corrected_run_id':validate_report(json.loads(Path(corrected).read_text()))['run_id'] if corrected else None,
        'dashboard_filename':target.name,'supplied_execution_status':report['execution']['status'],
        'local_calculation_verified':True,'provenance_attestation':verified['execution']['provenance_attestation'],
        'native_rendering_verified':False},indent=2)+'\n')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=ROOT/'databricks_files/retail-run-002/report.json')
    parser.add_argument('--output',type=Path,default=ROOT/'native_databricks/retail-001')
    parser.add_argument('--corrected',type=Path,help='Verified exact baseline report for paired dashboard')
    args=parser.parse_args()
    try:
        print('Prepared actual dashboard definition:',generate(args.report,args.output,args.corrected))
        print('Import/rendering remain pending. No remote resources changed.')
        return 0
    except (ValueError,OSError,KeyError) as error:
        print('Retail dashboard preparation failed:',error)
        return 1


if __name__=='__main__':sys.exit(main())
