"""Generate original CutoverGuard notebook, dashboard queries and optional Lakeflow code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.sql import build_pipeline,literal,utc
from cutoverguard.databricks import supported,verify_remote,CloudError
from cutoverguard.engine import reconcile


def load_report(path):
    report=json.loads(Path(path).read_text())
    execution=report.get('execution',{})
    if execution.get('mode')!='databricks' or execution.get('status')!='verified':
        raise ValueError('Need a verified Databricks report, not a local or failed result')
    expected=reconcile(report['input_bundle'])
    supported(report['input_bundle'],expected)
    for field in ('run_id','issues','account_impacts','currency_totals','status'):
        if report[field]!=expected[field]:
            raise ValueError('Report differs from reproducible input calculation: '+field)
    for name in ('manifest','profiles','transactions','issues','impacts','totals'):
        table=execution.get('tables',{}).get(name,'')
        if not re.fullmatch(r'`[A-Za-z0-9_]+`\.`[A-Za-z0-9_]+`\.`cutoverguard_[a-f0-9]{16}_'+name+r'`',table):
            raise ValueError('Unexpected actual table identifier: '+name)
    if not execution.get('statement_ids') or not all(execution.get('oracle_comparison',{}).get(k) is True for k in ('primary_findings_equal','account_impacts_equal','currency_totals_equal')):
        raise ValueError('Missing successful execution/oracle evidence')
    verify_remote(expected,execution.get('sql_issue_rows',[]),execution.get('sql_impact_rows',[]),execution.get('sql_total_rows',[]))
    return report


def datasets(reports):
    parts={name:[] for name in ('review_summary','ownership_findings','account_effects','zero_net_chart','snapshot_provenance','snapshot_profiles','snapshot_transactions')}
    for scenario,report in reports.items():
        t=report['execution']['tables']; label="'"+scenario+"'"
        parts['review_summary'].append(f"SELECT {label} scenario, currency, source_minor, candidate_minor, net_delta_minor, absolute_account_delta_minor, affected_notional_minor, (SELECT count(*) FROM {t['issues']}) primary_findings FROM {t['totals']}")
        parts['ownership_findings'].append(f"SELECT {label} scenario, check_id,type,key,field,source_value,candidate_value FROM {t['issues']}")
        parts['account_effects'].append(f"SELECT {label} scenario, account_id,currency,source_minor,candidate_minor,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM {t['impacts']}")
        parts['zero_net_chart'].append(f"SELECT {label} scenario, account_id label,currency,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major,'Account allocation difference' measure FROM {t['impacts']} UNION ALL SELECT {label} scenario,'Firm net' label,currency,net_delta_minor delta_minor,CAST(net_delta_minor/100 AS DECIMAL(38,2)) delta_major,'Firm net difference' measure FROM {t['totals']}")
        parts['snapshot_provenance'].append(f"SELECT {label} scenario,run_id,input_json FROM {t['manifest']}")
        parts['snapshot_profiles'].append(f"SELECT {label} scenario,side,account_id,customer_id FROM {t['profiles']}")
        parts['snapshot_transactions'].append(f"SELECT {label} scenario,side,event_id,account_id,amount_minor,currency,event_time,window_start,window_end FROM {t['transactions']}")
    return {name:'\nUNION ALL\n'.join(queries)+';' for name,queries in parts.items()}


def md(text):
    return '-- MAGIC %md\n'+'\n'.join('-- MAGIC '+line for line in text.splitlines())


def notebook(reports,queries):
    cells=[md('# CutoverGuard — Same total, different owner\nOriginal Knight Hacks IX project for financial migration review. All records are synthetic.\nThis notebook only reads the two actual successful SQL runs; it creates no objects.\nSelect SQL warehouse 515dff82a7a2c282. Zero net difference does not establish correct ownership.')]
    titles={'review_summary':'1. Two runs: defective → corrected','ownership_findings':'2. The exact transaction ownership breaks','account_effects':'3. Account effects; differences are not losses','zero_net_chart':'4. Plot allocation effects beside zero firm net','snapshot_provenance':'5. Original snapshots and fingerprints','snapshot_profiles':'6. Preserved leading-zero account IDs','snapshot_transactions':'7. The inputs used by SQL'}
    for name,query in queries.items():
        text=titles[name]
        if name=='zero_net_chart':
            text+='\nAdd a bar visualization: x=label, y=delta_major, series=scenario; filter currency=USD. Preserve sign and zero baseline. Do not sum account absolute differences into loss. Fixed bars are zero.'
        cells.extend([md(text),query])
    cells.append(md('## Evidence boundary\nThe existing Delta reconciliation runs succeeded and were compared to the exact Python oracle.\nThe optional Lakeflow extension in this pack has not run yet.\nCompleteness and dependency metadata are supplied declarations; ready for review is human review, not an automatic production cutover.\nNo BNY API, live mainframe ingestion, savings measurement or GPU workload is claimed.'))
    return '-- Databricks notebook source\n'+'\n\n-- COMMAND ----------\n\n'.join(cells)+'\n'


def pipeline_sql(reports,prefix):
    if not re.fullmatch(r'cg_demo_[a-f0-9]{12}',prefix):
        raise ValueError('Prefix must be cg_demo_ plus12 lowercase hexadecimal characters')
    blocks=['-- Optional Lakeflow materialized-view extension. Not yet executed.\n-- New pipeline; default catalog/schema workspace.default; do NOT execute as notebook SQL.\n-- Uses verified existing synthetic snapshot tables. No source mutations or mirrored gold findings.\n-- Expectation failures fail an update, never silently drop rows.']
    for scenario,report in reports.items():
        actual=report['execution']['tables']
        target=lambda suffix:'`'+prefix+'_'+scenario+'_'+suffix+'`'
        bmanifest,bprofiles,btransactions=(target('bronze_'+s) for s in ('manifest','profiles','transactions'))
        sp,st=target('silver_profiles'),target('silver_transactions')
        expected_json=json.dumps(report['input_bundle'],sort_keys=True,separators=(',',':'))
        blocks.append(f'''CREATE OR REFRESH MATERIALIZED VIEW {bmanifest} (
CONSTRAINT expected_snapshot EXPECT (run_id={literal(report['run_id'])} AND input_json={literal(expected_json)} AND _manifest_count=1) ON VIOLATION FAIL UPDATE
) AS SELECT min(run_id) run_id,min(input_json) input_json,count(*) _manifest_count FROM {actual['manifest']};''')
        for name,dest in [('profiles',bprofiles),('transactions',btransactions)]:
            blocks.append(f'CREATE OR REFRESH MATERIALIZED VIEW {dest}\nAS SELECT * FROM {actual[name]};')
        start=literal(utc(report['input_bundle']['source']['window_start']))
        end=literal(utc(report['input_bundle']['source']['window_end']))
        blocks.append(f'''CREATE OR REFRESH MATERIALIZED VIEW {sp} (
CONSTRAINT valid_profile EXPECT (side IN ('source','candidate') AND account_id IS NOT NULL AND length(trim(account_id))>0 AND customer_id IS NOT NULL AND length(trim(customer_id))>0) ON VIOLATION FAIL UPDATE,
CONSTRAINT unique_profile EXPECT (_key_count=1) ON VIOLATION FAIL UPDATE
) AS SELECT p.side,CAST(p.account_id AS STRING) account_id,CAST(p.customer_id AS STRING) customer_id,
count(*) OVER (PARTITION BY p.side,p.account_id) _key_count FROM {bprofiles} p CROSS JOIN {bmanifest} m;''')
        blocks.append(f'''CREATE OR REFRESH MATERIALIZED VIEW {st} (
CONSTRAINT unique_event EXPECT (_key_count=1) ON VIOLATION FAIL UPDATE,
CONSTRAINT valid_event EXPECT (side IN ('source','candidate') AND event_id IS NOT NULL AND length(trim(event_id))>0 AND amount_minor IS NOT NULL AND abs(amount_minor)<CAST('1000000000000000000000000000000' AS DECIMAL(38,0)) AND currency IN ('USD','EUR') AND event_time_utc IS NOT NULL AND window_start_utc IS NOT NULL AND window_end_utc IS NOT NULL AND window_start_utc<window_end_utc AND window_start_utc=CAST({start} AS TIMESTAMP) AND window_end_utc=CAST({end} AS TIMESTAMP) AND _valid_link) ON VIOLATION FAIL UPDATE
) AS SELECT t.*,CAST(event_time AS TIMESTAMP) event_time_utc,CAST(window_start AS TIMESTAMP) window_start_utc,
CAST(window_end AS TIMESTAMP) window_end_utc,count(*) OVER (PARTITION BY t.side,t.event_id) _key_count,
p.account_id IS NOT NULL _valid_link FROM {btransactions} t LEFT JOIN {sp} p ON t.side=p.side AND t.account_id=p.account_id CROSS JOIN {bmanifest} m;''')
        blocks.append(f'CREATE OR REFRESH MATERIALIZED VIEW {target("silver_in_window")}\nAS SELECT * FROM {st} WHERE event_time_utc>=window_start_utc AND event_time_utc<window_end_utc;')
        logical={'profiles':sp,'transactions':target('silver_in_window'),'manifest':bmanifest,
                 'issues':target('gold_issues'),'impacts':target('gold_impacts'),'totals':target('gold_totals')}
        for name,statement in build_pipeline(report['input_bundle'],logical,report['run_id']):
            if name in ('issues','impacts','totals'):
                query=statement.split(' USING DELTA AS ',1)[1]
                blocks.append(f'CREATE OR REFRESH MATERIALIZED VIEW {logical[name]}\nAS {query};')
    return '\n\n'.join(blocks)+'\n'


def generate(defective,fixed,destination,prefix):
    reports={'defective':load_report(defective),'fixed':load_report(fixed)}
    if reports['defective']['status']!='blocked' or reports['fixed']['status']!='ready_for_review':
        raise ValueError('Need blocked defective and ready-for-review fixed reports')
    queries=datasets(reports)
    pipeline=pipeline_sql(reports,prefix)
    destination=Path(destination)
    if destination.exists():
        raise ValueError('Destination already exists; choose a fresh output folder')
    destination.mkdir(parents=True)
    (destination/'CutoverGuard.sql').write_text(notebook(reports,queries))
    (destination/'lakeflow_pipeline.sql').write_text(pipeline)
    querydir=destination/'dashboard_queries';querydir.mkdir()
    for name,query in queries.items():
        (querydir/(name+'.sql')).write_text(query+'\n')
    manifest={'prefix':prefix,'pipeline_status':'prepared_not_executed','default_catalog':'workspace','default_schema':'default',
              'source_reports':{name:{'run_id':r['run_id'],'tables':r['execution']['tables'],'statement_ids':r['execution']['statement_ids']} for name,r in reports.items()},
              'notebook_sha256':hashlib.sha256((destination/'CutoverGuard.sql').read_bytes()).hexdigest(),
              'pipeline_sha256':hashlib.sha256(pipeline.encode()).hexdigest()}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defective',type=Path,default=ROOT/'cutover_results'/'defective-cloud-v2.json')
    parser.add_argument('--fixed',type=Path,default=ROOT/'cutover_results'/'fixed-cloud.json')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--prefix',default=None)
    args=parser.parse_args()
    prefix=args.prefix or 'cg_demo_'+uuid.uuid4().hex[:12]
    output=args.output or ROOT/'databricks_demo'/prefix
    try:
        generate(args.defective,args.fixed,output,prefix)
        print('Prepared local notebook, dashboard datasets and optional pipeline:',output)
        print('No remote objects created. Pipeline remains unexecuted. New dataset prefix:',prefix)
        return 0
    except (ValueError,OSError,KeyError,CloudError) as error:
        print('Generation failed:',error)
        return 1


if __name__=='__main__':
    sys.exit(main())
