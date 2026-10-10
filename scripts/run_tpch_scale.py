"""Separate TPC-H ownership-swap SQL experiment; not a banking migration or engine run."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cutoverguard.databricks import CLIClient,StatementRunner,CloudError
from cutoverguard.sql import ident,literal
MODULUS=2**64
SOURCE='`samples`.`tpch`.`orders`'

def integer(value):
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number!=number.to_integral_value(): raise ValueError()
        return int(number)
    except (ValueError,ArithmeticError): raise CloudError('Expected an exact integer SQL result.')

def owner_checksum(owners):
    return str(sum(int(hashlib.sha256(str(owner).encode()).hexdigest()[:16],16) for owner in owners)%MODULUS)

def checksum_sql(column):
    return f"CAST(pmod(sum(CAST(conv(substring(sha2({column},256),1,16),16,10) AS DECIMAL(38,0))),CAST({MODULUS} AS DECIMAL(38,0))) AS STRING)"

def validate_schema(rows):
    fields={str(row[0]).lower():str(row[1]).lower().replace(' ','') for row in rows if len(row)>=2 and row[0] and not str(row[0]).startswith('#')}
    for key in ('o_orderkey','o_custkey'):
        if fields.get(key) not in ('bigint','int','integer','long','smallint'):
            raise CloudError('TPC-H '+key+' must have an integer SQL type.')
    match=re.fullmatch(r'decimal\((\d+),2\)',fields.get('o_totalprice',''))
    if not match or int(match[1])>28: raise CloudError('TPC-H o_totalprice must be DECIMAL with scale 2 and precision <=28, never floating point.')
    if fields.get('o_orderdate')!='date': raise CloudError('TPC-H o_orderdate must be DATE.')
    return {key:fields[key] for key in ('o_orderkey','o_custkey','o_totalprice','o_orderdate')}

def discover(runner):
    started=time.monotonic()
    schema=validate_schema(runner.execute('DESCRIBE TABLE '+SOURCE))
    rows=runner.execute(f'''SELECT CAST(count(*) AS STRING),CAST(count(DISTINCT o_orderkey) AS STRING),
CAST(count_if(o_orderkey IS NULL OR o_custkey IS NULL OR o_totalprice IS NULL OR o_orderdate IS NULL) AS STRING),
CAST(sum(CAST(o_totalprice*100 AS DECIMAL(38,0))) AS STRING),{checksum_sql('CAST(o_custkey AS STRING)')},{checksum_sql('CAST(o_orderkey AS STRING)')} FROM {SOURCE}''')
    if len(rows)!=1 or len(rows[0])!=6: raise CloudError('Unexpected discovery result.')
    count,unique,bad,total,owners,keys=map(integer,rows[0])
    if count<50 or count!=unique or bad or count>=10**10: raise CloudError('Sample source has insufficient rows, duplicate order keys, null fields or unsupported size.')
    return {'source':SOURCE,'schema':schema,'row_count':count,'total_scaled_units':str(total),'owner_checksum_unsigned64':str(owners),'order_checksum_unsigned64':str(keys),'observed_at':datetime.now(timezone.utc).isoformat(),'data_kind':'synthetic_benchmark','amount_units':'arbitrary TPC-H price units scaled by 100; no currency asserted','discovery_elapsed_seconds':time.monotonic()-started,'timing_boundary':'DESCRIBE plus full source aggregate; includes CLI submit, warehouse startup if needed, polling and fetch'}

def names(catalog,schema,prefix):
    if not re.fullmatch(r'tpch_swap_[a-f0-9]{16}',prefix): raise CloudError('Invalid experiment prefix.')
    for value in (catalog,schema):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value): raise CloudError('Catalog/schema require simple identifiers.')
    return {key:'.'.join(ident(v) for v in (catalog,schema,prefix+'_'+key)) for key in ('source','mapping','candidate','issues','impacts','metrics')}

def injection(rows):
    if len(rows)!=50 or any(len(row)!=4 for row in rows): raise CloudError('Need exactly 50 selected orders from distinct customers.')
    clean=[[str(key),str(owner),str(integer(amount)),str(date)] for key,owner,amount,date in rows]
    if len({r[0] for r in clean})!=50 or len({r[1] for r in clean})!=50: raise CloudError('Selected order/customer keys must be unique.')
    out=[]
    for offset in range(0,50,2):
        a,b=clean[offset:offset+2]
        for source,target in ((a,b),(b,a)):
            out.append({'order_id':source[0],'source_account_id':source[1],'candidate_account_id':target[1],'amount_scaled_units':source[2],'order_date':source[3]})
    return out

def build_sql(tables,swaps):
    s,m,c,i,a,g=(tables[key] for key in ('source','mapping','candidate','issues','impacts','metrics'))
    mappings=' UNION ALL '.join(f"SELECT {literal(row['order_id'])} order_id,{literal(row['candidate_account_id'])} candidate_account_id" for row in swaps)
    return [
        ('mapping',f'CREATE TABLE {m} USING DELTA AS '+mappings),
        ('candidate',f'CREATE TABLE {c} USING DELTA AS SELECT s.order_id,coalesce(m.candidate_account_id,s.account_id) account_id,s.amount_scaled_units,s.order_date FROM {s} s LEFT JOIN {m} m ON s.order_id=m.order_id'),
        ('issues',f'''CREATE TABLE {i} USING DELTA AS SELECT coalesce(s.order_id,c.order_id) order_id,s.account_id source_account_id,c.account_id candidate_account_id,CAST(s.amount_scaled_units AS STRING) source_amount,CAST(c.amount_scaled_units AS STRING) candidate_amount,CAST(s.order_date AS STRING) source_date,CAST(c.order_date AS STRING) candidate_date FROM {s} s FULL OUTER JOIN {c} c ON s.order_id=c.order_id WHERE s.order_id IS NULL OR c.order_id IS NULL OR NOT(s.account_id<=>c.account_id) OR NOT(s.amount_scaled_units<=>c.amount_scaled_units) OR NOT(s.order_date<=>c.order_date)'''),
        ('impacts',f'''CREATE TABLE {a} USING DELTA AS WITH b AS (SELECT account_id,sum(amount_scaled_units) source_units,CAST(0 AS DECIMAL(38,0)) candidate_units FROM {s} GROUP BY account_id UNION ALL SELECT account_id,CAST(0 AS DECIMAL(38,0)),sum(amount_scaled_units) FROM {c} GROUP BY account_id) SELECT account_id,sum(source_units) source_units,sum(candidate_units) candidate_units,sum(candidate_units)-sum(source_units) delta_units FROM b GROUP BY account_id'''),
        ('metrics',f'''CREATE TABLE {g} USING DELTA AS SELECT 'source' side,CAST(count(*) AS STRING) row_count,CAST(sum(amount_scaled_units) AS STRING) total_scaled_units,{checksum_sql('account_id')} owner_checksum_unsigned64,{checksum_sql('order_id')} order_checksum_unsigned64 FROM {s} UNION ALL SELECT 'candidate',CAST(count(*) AS STRING),CAST(sum(amount_scaled_units) AS STRING),{checksum_sql('account_id')},{checksum_sql('order_id')} FROM {c}''')]

def verify(swaps,discovery,issues,impacts,metrics):
    expected=Counter((r['order_id'],r['source_account_id'],r['candidate_account_id'],r['amount_scaled_units'],r['amount_scaled_units'],r['order_date'],r['order_date']) for r in swaps)
    if Counter(tuple(row) for row in issues)!=expected or len(issues)!=50: raise CloudError('Full joined findings do not equal the independent 50-row injection specification.')
    delta=defaultdict(int)
    for row in swaps:
        amount=integer(row['amount_scaled_units']);delta[row['source_account_id']]-=amount;delta[row['candidate_account_id']]+=amount
    got={}
    for row in impacts:
        if len(row)!=4 or row[0] in got: raise CloudError('Invalid account effect results.')
        source,candidate,change=map(integer,row[1:]);got[row[0]]=change
        if candidate-source!=change: raise CloudError('Account effect arithmetic inconsistent.')
    wanted={key:value for key,value in delta.items() if value}
    if got!=wanted or sum(got.values())!=0: raise CloudError('Account effects disagree with independent injection ledger.')
    observed={}
    for row in metrics:
        if len(row)!=5 or row[0] in observed: raise CloudError('Invalid full-table metrics.')
        observed[row[0]]=list(map(integer,row[1:]))
    expected_metrics=[discovery['row_count'],integer(discovery['total_scaled_units']),integer(discovery['owner_checksum_unsigned64']),integer(discovery['order_checksum_unsigned64'])]
    if observed!={'source':expected_metrics,'candidate':expected_metrics}: raise CloudError('Source/candidate count, amount or checksum invariants disagree.')
    return {'exact_injected_findings':True,'independent_account_deltas':True,'row_counts_preserved':True,'amounts_dates_unchanged_full_join':True,'total_scaled_units_preserved':True,'owner_multiset_checksum_preserved':True,'order_checksum_preserved':True,'changed_owner_rows':50,'impacted_customer_buckets':len(got),'absolute_account_delta_scaled_units':str(sum(abs(value) for value in got.values()))}

def save(path,state):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(state,indent=2)+'\n');temporary.replace(path)

def run(runner,output,catalog='workspace',schema='default',profile=None,resume=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);path=output/'state.json'
    if resume:
        state=json.loads(path.read_text())
        if state['warehouse_id']!=runner.warehouse_id or state['profile']!=profile or state['tables']!=names(catalog,schema,state['prefix']): raise CloudError('Resume configuration differs.')
        if state['attempted']: raise CloudError('Previous write has an ambiguous outcome; inspect query history and choose a fresh output. No automatic retry.')
    else:
        if path.exists(): raise CloudError('Checkpoint exists; use --resume or a fresh output directory.')
        discovery=discover(runner);prefix='tpch_swap_'+uuid.uuid4().hex[:16]
        state={'status':'unverified','warehouse_id':runner.warehouse_id,'profile':profile,'prefix':prefix,'tables':names(catalog,schema,prefix),'discovery':discovery,'completed':[],'attempted':None,'statement_ids':list(runner.statement_ids),'timings':[],'swaps':None,'data_kind':'synthetic_benchmark'}
        with path.open('x') as file:file.write(json.dumps(state,indent=2)+'\n')
    state['status']='unverified';save(path,state)
    def query(label,sql):
        start=time.monotonic()
        try:return runner.execute(sql)
        finally:
            state['timings'].append({'operation':label,'elapsed_seconds':time.monotonic()-start,'boundary':'CLI SQL submit, warehouse startup if needed, polling and complete result fetch; no GPU attribution'})
            state['statement_ids'] += [sid for sid in runner.statement_ids if sid not in state['statement_ids']];save(path,state)
    def mutation(label,sql):
        if label in state['completed']:return
        state['attempted']=label;save(path,state)
        query(label,sql)
        state['completed'].append(label);state['attempted']=None;save(path,state)
    try:
        t=state['tables']
        mutation('source',f'CREATE TABLE {t["source"]} USING DELTA AS SELECT CAST(o_orderkey AS STRING) order_id,CAST(o_custkey AS STRING) account_id,CAST(o_totalprice*100 AS DECIMAL(38,0)) amount_scaled_units,o_orderdate order_date FROM {SOURCE}')
        frozen=query('validate_frozen_source',f'''SELECT CAST(count(*) AS STRING),CAST(count(DISTINCT order_id) AS STRING),CAST(count_if(order_id IS NULL OR account_id IS NULL OR amount_scaled_units IS NULL OR order_date IS NULL) AS STRING),CAST(sum(amount_scaled_units) AS STRING),{checksum_sql('account_id')},{checksum_sql('order_id')} FROM {t['source']}''')
        d=state['discovery']
        expected_frozen=[d['row_count'],d['row_count'],0,integer(d['total_scaled_units']),integer(d['owner_checksum_unsigned64']),integer(d['order_checksum_unsigned64'])]
        if len(frozen)!=1 or len(frozen[0])!=6 or list(map(integer,frozen[0]))!=expected_frozen:
            raise CloudError('Frozen source key/null/count/amount/checksum validation differs from discovery.')
        state['frozen_source_validation']=frozen;save(path,state)
        if state['swaps'] is None:
            rows=query('select_50_distinct_customers',f'''SELECT order_id,account_id,CAST(amount_scaled_units AS STRING),CAST(order_date AS STRING) FROM (SELECT *,row_number() OVER(PARTITION BY account_id ORDER BY order_id) customer_rank FROM {t['source']}) WHERE customer_rank=1 ORDER BY order_id LIMIT 50''')
            state['swaps']=injection(rows);save(path,state)
        for label,sql in build_sql(t,state['swaps']):mutation(label,sql)
        issues=query('verify_full_join',f"SELECT order_id,source_account_id,candidate_account_id,source_amount,candidate_amount,source_date,candidate_date FROM {t['issues']}")
        impacts=query('verify_nonzero_account_effects',f"SELECT account_id,CAST(source_units AS STRING),CAST(candidate_units AS STRING),CAST(delta_units AS STRING) FROM {t['impacts']} WHERE delta_units<>0")
        metrics=query('verify_full_metrics',f"SELECT side,row_count,total_scaled_units,owner_checksum_unsigned64,order_checksum_unsigned64 FROM {t['metrics']}")
        state['verification']=verify(state['swaps'],state['discovery'],issues,impacts,metrics)
        state.update(status='verified_narrow_sql_experiment',completed_at=datetime.now(timezone.utc).isoformat(),issue_rows=issues,account_effect_rows=impacts,metric_rows=metrics,limitations=['Synthetic TPC-H wholesale benchmark, not observed bank data or an actual migration.','Amounts are arbitrary benchmark price units scaled by 100; no currency asserted.','Narrow order ownership/amount/date reconciliation, not full CutoverGuard engine equivalence.','Permutation-invariant owner checksum cannot detect swapped owners; exact order-key findings are required.','No GPU runtime, CPU/GPU comparison, or speedup is measured.'])
        save(path,state);(output/'report.json').write_text(json.dumps(state,indent=2)+'\n');return state
    except BaseException:
        state['status']='unverified';save(path,state);raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True);group.add_argument('--discover',action='store_true');group.add_argument('--execute',action='store_true')
    p.add_argument('--warehouse-id',required=True);p.add_argument('--profile');p.add_argument('--catalog',default='workspace');p.add_argument('--schema',default='default');p.add_argument('--output',type=Path,default=ROOT/'tpch_results/run-001');p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    try:
        runner=StatementRunner(CLIClient(args.profile),args.warehouse_id,timeout=900)
        if args.discover:
            if args.resume:p.error('--resume applies only to --execute')
            result=discover(runner);result['statement_ids']=runner.statement_ids
            args.output.mkdir(parents=True,exist_ok=True)
            with (args.output/'discovery.json').open('x') as file:file.write(json.dumps(result,indent=2)+'\n')
            print('Read-only discovery verified. Observed source rows:',result['row_count']);print('Discovery:',args.output/'discovery.json')
        else:
            print('Synthetic TPC-H SQL experiment; creates six unique Delta tables. Compute/startup may be charged.')
            result=run(runner,args.output,args.catalog,args.schema,args.profile,args.resume)
            print('Narrow SQL experiment verified. Rows:',result['discovery']['row_count'],'Changed owners: 50');print('Evidence:',args.output/'report.json')
        return 0
    except (CloudError,ValueError,OSError,KeyError) as exc:print('TPC-H experiment UNVERIFIED:',str(exc));return 1
if __name__=='__main__':sys.exit(main())
