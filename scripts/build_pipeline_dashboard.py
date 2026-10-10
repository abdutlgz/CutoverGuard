"""Build native dashboard from persisted crosswalk-pipeline gold evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_tpch_dashboard import build, validate_structure
from scripts.setup_cutoverguard_native import widget

def queries(bad='cutoverguard_migration_bad',fixed='cutoverguard_migration_fixed'):
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',x) for x in (bad,fixed)):
        raise ValueError('Use simple evidence schema identifiers')
    b=f'`workspace`.`{bad}`';f=f'`workspace`.`{fixed}`'
    checks=f"""WITH d AS (SELECT * FROM {b}.gold_cutover_decision), raw AS (
SELECT 1 ordinal,'Row count' check_name,format_number(source_rows,0) source_value,format_number(candidate_rows,0) candidate_value,source_rows=candidate_rows AND source_rows=legacy_rows passed FROM d
UNION ALL SELECT 2,'Total price units',format_number(CAST(source_total/100 AS DECIMAL(38,2)),2),format_number(CAST(candidate_total/100 AS DECIMAL(38,2)),2),source_total<=>candidate_total FROM d
UNION ALL SELECT 3,'Quarantine','0 rows',concat(CAST(quarantined_rows AS STRING),' rows'),quarantined_rows=0 FROM d
UNION ALL SELECT 4,'Crosswalk mapping','Expected customer',concat(CAST(crosswalk_findings AS STRING),' wrong mappings'),crosswalk_findings=0 FROM d
UNION ALL SELECT 5,'Order ownership','Original owner',concat(CAST(order_findings AS STRING),' wrong-owner orders'),order_findings=0 FROM d
) SELECT ordinal,check_name,source_value,candidate_value,CASE WHEN passed THEN '🟢 PASS' ELSE '🔴 BLOCKED' END result_label FROM raw ORDER BY ordinal"""
    return {
      'scale_kpis':f'SELECT source_rows orders,candidate_total-source_total total_difference_scaled,crosswalk_findings,order_findings wrong_owners,quarantined_rows FROM {b}.gold_cutover_decision',
      'scale_checks':checks,
      'scale_effects':f"SELECT account_id customer,CAST(delta_scaled_units/100 AS DECIMAL(38,2)) signed_price_units,CASE WHEN delta_scaled_units>0 THEN 'Increase' ELSE 'Decrease' END direction FROM {b}.gold_customer_effects WHERE delta_scaled_units<>0 ORDER BY delta_scaled_units DESC,account_id",
      'scale_drilldown':f'SELECT order_id order_key,original_owner,migrated_owner,CAST(original_amount/100 AS DECIMAL(38,2)) price_units,original_utc FROM {b}.gold_order_issues ORDER BY original_amount DESC,order_id',
      'pipeline_replay':f"SELECT 'defective' scenario,source_rows,candidate_rows,order_findings,crosswalk_findings,CAST((candidate_total-source_total)/100 AS DECIMAL(38,2)) net_price_difference,status FROM {b}.gold_cutover_decision UNION ALL SELECT 'corrected',source_rows,candidate_rows,order_findings,crosswalk_findings,CAST((candidate_total-source_total)/100 AS DECIMAL(38,2)),status FROM {f}.gold_cutover_decision",
    }

def build_pipeline(report,retail,corrected,report_sha,bad,fixed):
    body=build(report,retail,corrected,report_sha)
    q=queries(bad,fixed)
    body['datasets']=[x for x in body['datasets'] if not x['name'].startswith('scale_')]
    body['datasets'] += [{'name':k,'displayName':k.replace('_',' ').title(),'queryLines':[sql+'\n']} for k,sql in q.items()]
    page=body['pages'][0];page['displayName']='Migration pipeline · 7.5M orders'
    page['layout'][0]['widget']['multilineTextboxSpec']['lines']=['# Same total. Wrong customers.\n','A swapped customer crosswalk reallocates whole order histories. Persisted Lakeflow gold results show exactly which orders and customers changed.']
    layout=[]
    for x in page['layout']:
        w=x['widget'];name=w['name']
        if name in ('scale_checksum_mismatches','scale_recorded_query_seconds'): continue
        if name=='scale_diverging': w['spec']['frame']['title']='Signed customer effects · price units · net zero'
        if name=='scale_order_drilldown':
            for query in w['queries']:
                for field in query['query']['fields']:
                    if field.get('name')=='order_date':field.update(name='original_utc',expression='`original_utc`')
            w['spec']['encodings']['columns']=[x for x in w['spec']['encodings']['columns'] if x.get('fieldName')!='order_date']
        if name=='scale_orders':
            w['spec']['encodings']['value']['format']={'type':'number-plain','abbreviation':'none','decimalPlaces':{'type':'max','places':0}}
        if name=='scale_total_difference_scaled':
            w['spec']['frame']['title']='Firm total difference'
            w['spec']['encodings']['value']['displayName']='Firm total difference'
        if name=='scale_checklist':w['spec']['frame']['title']='Aggregate parity versus crosswalk and ownership'
        layout.append(x)
    layout += [widget('pipeline_crosswalk_counter','scale_kpis',['crosswalk_findings'],'counter','Wrong customer mappings',6,2,3,3,{'value':{'fieldName':'crosswalk_findings','displayName':'Wrong customer mappings'}})]
    # Four headline counters across one row; quarantine stays in the checklist.
    counter_layout={'scale_orders':(0,4),'scale_wrong_owners':(4,3),'pipeline_crosswalk_counter':(7,2),'scale_total_difference_scaled':(9,3)}
    for x in layout:
        name=x['widget']['name']
        if name in counter_layout:
            left,width=counter_layout[name]
            x['position'].update(x=left,y=2,width=width,height=3)
    layout.append(widget('pipeline_corrected_replay','pipeline_replay',['scenario','source_rows','candidate_rows','order_findings','crosswalk_findings','net_price_difference','status'],'table','Defective → corrected · persisted gold decisions',0,27,12,5))
    page['layout']=layout
    body['pages'][2]['layout'][0]['widget']['multilineTextboxSpec']['lines']=['# Evidence and limitations\n',f'Live dashboard reads persisted gold tables in workspace.{bad} and workspace.{fixed}.\n','Independent count from the frozen source and the 50 authored swapped customer IDs: 811 affected orders.\n','Synthetic TPC-H benchmark; arbitrary price units, no currency. This crosswalk experiment differs from the earlier 50-order swap experiment.\n','Source dates are interpreted as explicit UTC-midnight instants. Deliberate mapping defects, not observed bank migration failures.\n','Promotion tests and update IDs are recorded separately. A ready review verdict is not production authorization.\n','Public retail tab uses historical UCI invoices with controlled candidate defects; DOI 10.24432/C5BW33, CC BY 4.0.\n','Keyed row-diff tools can detect ownership changes too; CutoverGuard adds signed effects, review decisions and a pipeline promotion gate.']
    validate_structure(body)
    return body

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bad-schema', default='cutoverguard_migration_bad')
    p.add_argument('--fixed-schema', default='cutoverguard_migration_fixed')
    p.add_argument('--report', type=Path, default=ROOT/'tpch_results/run-001/report.json')
    p.add_argument('--retail', type=Path, default=ROOT/'databricks_files/retail-run-002/report.json')
    p.add_argument('--corrected', type=Path, default=ROOT/'databricks_files/retail-fixed-001/report.json')
    p.add_argument('--output', type=Path, default=ROOT/'native_databricks/final/CutoverGuard-pipeline.lvdash.json')
    a = p.parse_args()
    raw = a.report.read_bytes()
    retail = json.loads(a.retail.read_text())
    corrected = json.loads(a.corrected.read_text())
    from scripts.build_retail_dashboard import comparison
    comparison(retail, corrected)
    body = build_pipeline(json.loads(raw), retail, corrected,
                          hashlib.sha256(raw).hexdigest(), a.bad_schema, a.fixed_schema)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(body, indent=2)+'\n')
    print(a.output)

if __name__ == '__main__':
    main()
