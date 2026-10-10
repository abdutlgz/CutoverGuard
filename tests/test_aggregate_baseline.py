import sqlite3
import pytest
from cutoverguard.aggregate_baseline import compute,compute_bundle,query,owner_hash
from cutoverguard.engine import reconcile
from cutoverguard.fixtures import fixture
from cutoverguard.file_ingestion import micros


def sql_results(bundle):
    # SQLite dialect adaptation tests relational semantics; not Databricks execution.
    db=sqlite3.connect(':memory:')
    db.create_function('unix_micros',1,lambda v:int(micros(v)))
    db.create_function('pmod',2,lambda v,d:v%d)
    import json
    from datetime import date,timedelta
    db.create_function('concat',-1,lambda *a:''.join(str(x) for x in a))
    db.create_function('concat_ws',2,lambda sep,v:sep.join(json.loads(v)))
    db.create_function('sort_array',1,lambda v:json.dumps(sorted(json.loads(v))))
    db.create_function('date_add',2,lambda v,n:(date.fromisoformat(v)+timedelta(days=n)).isoformat())
    class Collect:
        def __init__(self):self.values=[]
        def step(self,v):self.values.append(v)
        def finalize(self):return json.dumps(self.values)
    class Checksum:
        def __init__(self):self.value=0
        def step(self,v):self.value+=owner_hash(v)
        def finalize(self):return str(self.value%(2**64))
    db.create_aggregate('collect_list',1,Collect);db.create_aggregate('owner_checksum',1,Checksum)
    db.execute('CREATE TABLE tx(side TEXT,currency TEXT,amount_minor INTEGER,account_id TEXT,event_time TEXT,window_start TEXT,window_end TEXT)')
    for side in ('source','candidate'):
        snap=bundle[side]
        from datetime import datetime,timezone
        utc=lambda x:datetime.fromisoformat(x).astimezone(timezone.utc).isoformat()
        for r in snap['transactions']:
            db.execute('INSERT INTO tx VALUES(?,?,?,?,?,?,?)',(side,r['currency'],int(r['amount_minor']),r['account_id'],utc(r['event_time']),utc(snap['window_start']),utc(snap['window_end'])))
    db.execute('CREATE TABLE issues(type TEXT)')
    for i in reconcile(bundle)['issues']:
        if i['check_id']!='business':db.execute('INSERT INTO issues VALUES(?)',(i['type'],))
    sql=query('tx','issues').replace(' DIV 86400000000',' / 86400000000').replace("DATE'1970-01-01'","'1970-01-01'").replace(' AS STRING',' AS TEXT')
    sql=sql.replace('pmod(sum(CAST(conv(substring(sha2(account_id,256),1,16),16,10) AS DECIMAL(38,0))),CAST(18446744073709551616 AS DECIMAL(38,0)))','owner_checksum(account_id)')
    return dict((row[1],row[2]) for row in db.execute(sql))


@pytest.mark.parametrize('scenario',['defective','fixed'])
def test_totals_only_pass_both_but_ownership_distinguishes(scenario):
    b=fixture(scenario);r=reconcile(b);local=compute(r)
    assert all(c['pass'] for c in local['checks'])
    sql=sql_results(b)
    assert all(sql[c['name']]=='PASS' for c in local['checks'])
    assert sql['CutoverGuard: invoice ownership']==('BLOCKED' if scenario=='defective' else 'NO OWNERSHIP FINDINGS')


def test_daily_change_cannot_hide_behind_same_grand_total():
    b=fixture('fixed')
    for side in ('source','candidate'):
        b[side]['window_end']='2026-10-12T00:00:00+00:00';b[side]['complete_through']=b[side]['window_end']
        b[side]['captured_at']='2026-10-12T00:05:00+00:00'
    b['candidate']['transactions'][0]['event_time']='2026-10-11T12:00:00+00:00'
    r=reconcile(b);local={c['name']:c['pass'] for c in compute(r)['checks']}
    assert local['Currency totals match'] is True
    assert local['UTC daily totals match'] is False
    assert sql_results(b)['UTC daily totals match']=='FAIL'


def test_utc_boundary_microsecond_is_not_rounded_to_next_day():
    b=fixture('fixed')
    for side in ('source','candidate'):
        b[side]['window_start']='1969-12-30T00:00:00+00:00';b[side]['window_end']='1970-01-02T00:00:00+00:00';b[side]['complete_through']=b[side]['window_end']
        b[side]['transactions'][0]['event_time']='1969-12-31T23:59:59.999999+00:00'
        b[side]['transactions'][1]['event_time']='1970-01-01T00:00:00+00:00'
    b['candidate']['transactions'][0]['event_time']='1970-01-01T00:00:00+00:00'
    assert next(c for c in compute(reconcile(b))['checks'] if c['name']=='UTC daily totals match')['pass'] is False
    assert sql_results(b)['UTC daily totals match']=='FAIL'


def test_ambiguous_metadata_not_a_green_checklist():
    with pytest.raises(ValueError):compute(reconcile(fixture('missing_metadata')))


def test_owner_permutation_preserves_all_controls_and_raw_id_checksum():
    import random
    from copy import deepcopy
    from cutoverguard.defect_experiment import ledger
    rng=random.Random(20261010)
    for trial in range(25):
        source=ledger(trial=trial);candidate=deepcopy(source)
        owners=[r['account_id'] for r in candidate['transactions']];rng.shuffle(owners)
        for row,owner in zip(candidate['transactions'],owners):row['account_id']=owner
        baseline=compute_bundle({'source':source,'candidate':candidate})
        assert all(row['pass'] for row in baseline['checks'])
        assert all(row['source']==row['candidate'] for row in baseline['checks'])
    assert owner_hash('0001')!=owner_hash('1')
