"""Build the final TPC-H-first native dashboard; optional read-only live SQL checks."""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_tpch_scale import verify,names,integer
from scripts.setup_cutoverguard_native import widget
from scripts.build_retail_dashboard import build as retail_dashboard
from cutoverguard.databricks import CLIClient,StatementRunner,CloudError


def validated(report):
    if report.get('status')!='verified_narrow_sql_experiment' or report.get('attempted'):
        raise ValueError('Require a completed verified TPC-H run.')
    if report.get('data_kind')!='synthetic_benchmark' or report.get('completed')!=['source','mapping','candidate','issues','impacts','metrics']:
        raise ValueError('Require all six completed benchmark tables.')
    if report['tables']!=names('workspace','default',report['prefix']):
        raise ValueError('Unexpected table identifiers; only isolated workspace.default scale tables supported.')
    evidence=verify(report['swaps'],report['discovery'],report['issue_rows'],report['account_effect_rows'],report['metric_rows'])
    if report.get('verification')!=evidence:raise ValueError('Saved verification differs from recomputed evidence.')
    if not report.get('warehouse_id'):raise ValueError('Missing execution warehouse.')
    return evidence


def queries(report):
    validated(report)
    t=report['tables'];s,c,i,a,m=(t[k] for k in ('source','candidate','issues','impacts','metrics'))
    seconds=sum(Decimal(str(x['elapsed_seconds'])) for x in report['timings'])
    pairs=f"WITH paired AS (SELECT s.row_count source_count,c.row_count candidate_count,s.total_scaled_units source_total,c.total_scaled_units candidate_total,s.owner_checksum_unsigned64 source_owner,c.owner_checksum_unsigned64 candidate_owner,s.order_checksum_unsigned64 source_order,c.order_checksum_unsigned64 candidate_order FROM {m} s CROSS JOIN {m} c WHERE s.side='source' AND c.side='candidate')"
    checks=pairs+f""", daily AS (SELECT order_date,sum(amount_scaled_units) amount FROM {s} GROUP BY order_date), candidate_daily AS (SELECT order_date,sum(amount_scaled_units) amount FROM {c} GROUP BY order_date), daily_diff AS (SELECT count(*) n FROM daily s FULL OUTER JOIN candidate_daily c ON s.order_date=c.order_date WHERE s.order_date IS NULL OR c.order_date IS NULL OR NOT(s.amount<=>c.amount)), raw AS (
SELECT 1 ordinal,'Row count' check_name,source_count source_value,candidate_count candidate_value,source_count=candidate_count passed FROM paired
UNION ALL SELECT 2,'Total price units',CAST(CAST(CAST(source_total AS DECIMAL(38,0))/100 AS DECIMAL(38,2)) AS STRING),CAST(CAST(CAST(candidate_total AS DECIMAL(38,0))/100 AS DECIMAL(38,2)) AS STRING),source_total=candidate_total FROM paired
UNION ALL SELECT 3,'Owner-ID checksum',source_owner,candidate_owner,source_owner=candidate_owner FROM paired
UNION ALL SELECT 4,'Order-key checksum',source_order,candidate_order,source_order=candidate_order FROM paired
UNION ALL SELECT 5,'Daily price totals','0 mismatched days',concat(CAST(n AS STRING),' mismatched days'),n=0 FROM daily_diff
UNION ALL SELECT 6,'CutoverGuard ownership','—',concat(CAST(count(*) AS STRING),' wrong-owner rows'),count(*)=0 FROM {i})
SELECT ordinal,check_name,source_value,candidate_value,CASE WHEN passed THEN '🟢 PASS' WHEN ordinal=6 THEN '🔴 BLOCKED' ELSE '🔴 FAIL' END result_label FROM raw ORDER BY ordinal"""
    return {
      'scale_kpis':pairs+f" SELECT CAST(source_count AS BIGINT) orders,CAST(candidate_total AS DECIMAL(38,0))-CAST(source_total AS DECIMAL(38,0)) total_difference_scaled,CAST(source_owner<>candidate_owner AS INT)+CAST(source_order<>candidate_order AS INT) checksum_mismatches,(SELECT count(*) FROM {i}) wrong_owners,CAST({seconds} AS DOUBLE) recorded_query_seconds FROM paired",
      'scale_checks':checks,
      'scale_effects':f"SELECT account_id customer,CAST(delta_units/100 AS DECIMAL(38,2)) signed_price_units,CASE WHEN delta_units>0 THEN 'Increase' ELSE 'Decrease' END direction FROM {a} WHERE delta_units<>0 ORDER BY CAST(delta_units AS DECIMAL(38,0)) DESC,account_id",
      'scale_drilldown':f"SELECT order_id order_key,source_account_id original_owner,candidate_account_id migrated_owner,CAST(CAST(source_amount AS DECIMAL(38,0))/100 AS DECIMAL(38,2)) price_units,source_date order_date FROM {i} ORDER BY CAST(source_amount AS DECIMAL(38,0)) DESC,order_id",
    }


def text(name,lines,x,y,w,h):
    return {'widget':{'name':name,'multilineTextboxSpec':{'lines':lines}},'position':{'x':x,'y':y,'width':w,'height':h}}


def build(report,retail,corrected,report_sha):
    v=validated(report);q=queries(report)
    layout=[text('scale_story',['# Same total. Different owner.\n','Five aggregate checks pass. Fifty order assignments have the wrong customer.'],0,0,12,2)]
    counters=[('orders','Orders compared',0,3),('total_difference_scaled','Total difference (scaled price units)',3,3),('checksum_mismatches','Checksum mismatches',6,2),('wrong_owners','Wrong owners — BLOCKED',8,2),('recorded_query_seconds','Recorded query intervals (seconds)',10,2)]
    for field,title,x,w in counters:
        layout.append(widget('scale_'+field,'scale_kpis',[field],'counter',title,x,2,w,3,{'value':{'fieldName':field,'displayName':title}}))
    layout.extend([
      widget('scale_checklist','scale_checks',['check_name','source_value','candidate_value','result_label'],'table','Same snapshots: five PASS checks, ownership BLOCKED',0,5,12,5),
      widget('scale_diverging','scale_effects',['customer','signed_price_units','direction'],'bar','50 customer effects • signed price units • net zero',0,10,12,7,{'x':{'fieldName':'customer','scale':{'type':'categorical'},'displayName':'Customer'},'y':{'fieldName':'signed_price_units','scale':{'type':'quantitative'},'displayName':'Candidate − source (price units)'},'color':{'fieldName':'direction','scale':{'type':'categorical','mappings':[{'value':'Increase','color':'#16803D'},{'value':'Decrease','color':'#DC2626'}]}}}),
      widget('scale_orders','scale_drilldown',['order_key','original_owner','migrated_owner','price_units','order_date'],'table','Wrong-owner orders • largest price first',0,17,12,7),
    ])
    retail_part=retail_dashboard(retail)
    retail_layout=retail_part['pages'][0]['layout']
    retail_layout[0]=text('retail_story',['# Real public records: UCI Online Retail\n','1,467 historical invoice lines per side. Deliberately controlled owner defects; corrected replay restores parity.'],0,0,12,2)
    for item in retail_layout[1:]:item['position']['y']-=1
    # Keep the real-record demonstration; omit the trivial corrected-zero bar.
    rt=retail['execution']['tables']['totals'];ft=corrected['execution']['tables']['totals']
    q['retail_replay']=f"SELECT 'defective' scenario,CAST(absolute_account_delta_minor/100 AS DECIMAL(38,2)) absolute_difference_gbp,(SELECT count(*) FROM {retail['execution']['tables']['issues']}) findings FROM {rt} WHERE currency='GBP' UNION ALL SELECT 'corrected',CAST(absolute_account_delta_minor/100 AS DECIMAL(38,2)),(SELECT count(*) FROM {corrected['execution']['tables']['issues']}) FROM {ft} WHERE currency='GBP'"
    retail_layout.append(widget('retail_replay','retail_replay',['scenario','findings','absolute_difference_gbp'],'table','Corrected replay — GBP',0,22,12,4))
    evidence_lines=['# Evidence and limitations\n',f'TPC-H source/candidate: {report["discovery"]["row_count"]:,} orders each; {v["changed_owner_rows"]} deliberately changed owners. Completed: {report["completed_at"]}.\n',f'Run evidence SHA256: `{report_sha}`. Isolated run: `{report["prefix"]}`.\n','Synthetic wholesale benchmark; arbitrary price units, no currency. Narrow SQL reconciliation, not full local-engine equivalence, bank data or loss prevention.\n','Timing is the sum of recorded SQL submit/poll/fetch intervals; includes possible startup and transfers. Discovery is separate. It is not query-engine runtime, GPU timing, or a performance comparison.\n','Owner and order checksums are permutation-invariant. Keyed row-diff tools also catch ownership swaps; CutoverGuard adds signed allocation effects and a review verdict.\n',f'Retail defective fingerprint: `{retail["run_id"]}`; corrected: `{corrected["run_id"]}`.\n','UCI Online Retail DOI 10.24432/C5BW33, CC BY 4.0. Historical customer-derived invoice buckets are not bank accounts or cash settlements. Controlled defects, not observed migration errors.\n','Ready for human review is not production approval. Lakeflow gate execution is separate and must be independently verified.']
    body={'datasets':[{'name':k,'displayName':k.replace('_',' ').title(),'queryLines':[sql+'\n']} for k,sql in q.items()]+retail_part['datasets'], 'pages':[
      {'name':'scale_main','displayName':'7.5 million orders' if report['discovery']['row_count']==7500000 else f"{report['discovery']['row_count']:,} orders",'pageType':'PAGE_TYPE_CANVAS','layout':layout},
      {'name':'public_retail','displayName':'Real public data','pageType':'PAGE_TYPE_CANVAS','layout':retail_layout},
      {'name':'evidence','displayName':'Evidence','pageType':'PAGE_TYPE_CANVAS','layout':[text('evidence_text',evidence_lines,0,0,12,10)]}]}
    return body


def verify_queries(report,runner):
    q=queries(report);results={name:runner.execute(sql) for name,sql in q.items()}
    rows=results['scale_kpis']
    if len(rows)!=1 or len(rows[0])!=5 or [integer(x) for x in rows[0][:4]]!=[report['discovery']['row_count'],0,0,50]:raise CloudError('Live counters differ from saved evidence.')
    if abs(Decimal(str(rows[0][4]))-sum(Decimal(str(x['elapsed_seconds'])) for x in report['timings']))>Decimal('0.000001'):raise CloudError('Timing constant differs.')
    checks=results['scale_checks']
    if len(checks)!=6 or [integer(x[0]) for x in checks]!=list(range(1,7)) or [x[4] for x in checks]!=['🟢 PASS']*5+['🔴 BLOCKED']:raise CloudError('Live five-check baseline or ownership result differs.')
    wanted={str(x[0]):Decimal(x[3])/100 for x in report['account_effect_rows']}
    observed={str(x[0]):Decimal(str(x[1])) for x in results['scale_effects']}
    if len(results['scale_effects'])!=len(wanted) or observed!=wanted or sum(observed.values())!=0:raise CloudError('Live signed customer effects differ.')
    wanted_orders={(x['order_id'],x['source_account_id'],x['candidate_account_id'],Decimal(x['amount_scaled_units'])/100,x['order_date']) for x in report['swaps']}
    observed_orders={(str(x[0]),str(x[1]),str(x[2]),Decimal(str(x[3])),str(x[4])) for x in results['scale_drilldown']}
    if len(results['scale_drilldown'])!=50 or observed_orders!=wanted_orders:raise CloudError('Live order drilldown differs.')
    return {'status':'verified_read_only_scale_dashboard_queries','statement_ids':runner.statement_ids,'rows':results}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,default=ROOT/'tpch_results/run-001/report.json')
    p.add_argument('--retail',type=Path,default=ROOT/'databricks_files/retail-run-002/report.json')
    p.add_argument('--corrected',type=Path,default=ROOT/'databricks_files/retail-fixed-001/report.json')
    p.add_argument('--output',type=Path,default=ROOT/'native_databricks/final')
    p.add_argument('--verify-sql',action='store_true');p.add_argument('--profile')
    args=p.parse_args()
    try:
        raw=args.report.read_bytes();r=json.loads(raw)
        from scripts.build_retail_dashboard import comparison
        retail=json.loads(args.retail.read_text());fixed=json.loads(args.corrected.read_text())
        comparison(retail,fixed) # Validate paired sources and warehouse before authoring.
        if retail['execution']['warehouse_id']!=r['warehouse_id']:raise ValueError('Scale and retail warehouses differ.')
        body=build(r,retail,fixed,hashlib.sha256(raw).hexdigest())
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'CutoverGuard.lvdash.json').write_text(json.dumps(body,indent=2)+'\n')
        if args.verify_sql:
            (args.output/'scale-query-verification.json').write_text(json.dumps({'status':'unverified','reason':'Read-only live validation started; not completed.'})+'\n')
            result=verify_queries(r,StatementRunner(CLIClient(args.profile),r['warehouse_id']))
            (args.output/'scale-query-verification.json').write_text(json.dumps(result,indent=2)+'\n')
            print('Scale dashboard SQL verified read-only. Rendering remains pending.')
        print('Import:',args.output/'CutoverGuard.lvdash.json')
        return 0
    except (ValueError,KeyError,OSError,CloudError) as e:
        print('Dashboard preparation UNVERIFIED:',str(e));return 1
if __name__=='__main__':sys.exit(main())
