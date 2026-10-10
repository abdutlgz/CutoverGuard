"""Permutation-blind aggregate baseline, not a production sign-off policy."""
from collections import defaultdict
from datetime import datetime,timezone
import hashlib

CHECKSUM_MODULUS=2**64


def owner_hash(account_id):
    """Raw UTF-8 string SHA256 first 16 hex digits, unsigned, no ID cast."""
    return int(hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:16],16)


def major(value):
    whole,cents=divmod(abs(value),100)
    return ('-' if value<0 else '')+str(whole)+'.'+str(cents).zfill(2)


def compute_bundle(bundle):
    """Valid-row baseline; duplicate event rows are intentionally counted.

    Caller establishes validity/completeness. No detector verdict is used here.
    This baseline omits event-ID, duplicate and owner-to-event association checks.
    """
    rows={};sums={};daily={};checksum={}
    for side in ('source','candidate'):
        snapshot=bundle[side]
        start=datetime.fromisoformat(snapshot['window_start']);end=datetime.fromisoformat(snapshot['window_end'])
        selected=[r for r in snapshot['transactions'] if start<=datetime.fromisoformat(r['event_time'])<end]
        rows[side]=len(selected);sums[side]=defaultdict(int);daily[side]=defaultdict(int)
        checksum[side]=sum(owner_hash(r['account_id']) for r in selected)%CHECKSUM_MODULUS
        for r in selected:
            sums[side][r['currency']]+=int(r['amount_minor'])
            key=(r['currency'],datetime.fromisoformat(r['event_time']).astimezone(timezone.utc).date().isoformat())
            daily[side][key]+=int(r['amount_minor'])
    currency_display=lambda side:'; '.join(c+' '+major(v) for c,v in sorted(sums[side].items())) or 'No in-window rows'
    daily_display=lambda side:'; '.join(day+' '+c+' '+major(v) for (c,day),v in sorted(daily[side].items())) or 'No in-window rows'
    return {'scope':'Illustrative baseline: in-window count, separate currency totals, UTC daily totals, unweighted owner-ID checksum. No row identity or ownership association check.',
            'checksum_convention':'SHA256(raw UTF-8 account_id) first16hex → unsigned64; sum over transaction rows modulo 2^64. Owner permutations preserve it; owner-aware row hashes need not.',
            'checks':[
                {'id':'row_count','name':'Row count matches','pass':rows['source']==rows['candidate'],'source':str(rows['source']),'candidate':str(rows['candidate'])},
                {'id':'currency_totals','name':'Currency totals match','pass':dict(sums['source'])==dict(sums['candidate']),'source':currency_display('source'),'candidate':currency_display('candidate')},
                {'id':'daily_totals','name':'UTC daily totals match','pass':dict(daily['source'])==dict(daily['candidate']),'source':daily_display('source'),'candidate':daily_display('candidate')},
                {'id':'account_checksum','name':'Owner-ID checksum matches','pass':checksum['source']==checksum['candidate'],'source':str(checksum['source']),'candidate':str(checksum['candidate'])}]}


def compute(report):
    if report['quarantine'] or len(report['currency_totals'])!=1 or any(c['status']=='unknown' for c in report['checks']):
        raise ValueError('Dashboard baseline requires complete, unambiguous single-currency evidence.')
    result=compute_bundle(report['input_bundle'])
    result.update(run_id=report['run_id'],data_kind=report['data_kind'],
                  ownership_findings=sum(i['type']=='account_mismatch' and i['check_id']=='transactions' for i in report['issues']))
    return result


def query(transactions,issues):
    # Caller validates generated isolated table names before passing them here.
    return f"""WITH eligible AS (
 SELECT side,currency,amount_minor,account_id,(CAST(unix_micros(event_time) AS DECIMAL(38,0))-pmod(CAST(unix_micros(event_time) AS DECIMAL(38,0)),CAST(86400000000 AS DECIMAL(38,0)))) DIV 86400000000 utc_day
 FROM {transactions} WHERE event_time>=window_start AND event_time<window_end
), counts AS (
 SELECT count(CASE WHEN side='source' THEN 1 END) s,count(CASE WHEN side='candidate' THEN 1 END) c FROM eligible
), sums AS (
 SELECT side,currency,sum(amount_minor) amount FROM eligible GROUP BY side,currency
), currency_diff AS (
 SELECT count(*) n FROM (SELECT * FROM sums WHERE side='source') s FULL OUTER JOIN
 (SELECT * FROM sums WHERE side='candidate') c ON s.currency=c.currency
 WHERE s.currency IS NULL OR c.currency IS NULL OR s.amount<>c.amount
), sum_text AS (
 SELECT side,concat_ws('; ',sort_array(collect_list(concat(currency,' ',CAST(CAST(amount/100 AS DECIMAL(38,2)) AS STRING))))) value FROM sums GROUP BY side
), days AS (
 SELECT side,currency,utc_day,sum(amount_minor) amount FROM eligible GROUP BY side,currency,utc_day
), daily_diff AS (
 SELECT count(*) n FROM (SELECT * FROM days WHERE side='source') s FULL OUTER JOIN
 (SELECT * FROM days WHERE side='candidate') c ON s.currency=c.currency AND s.utc_day=c.utc_day
 WHERE s.currency IS NULL OR c.currency IS NULL OR s.amount<>c.amount
), day_text AS (
 SELECT side,concat_ws('; ',sort_array(collect_list(concat(CAST(date_add(DATE'1970-01-01',CAST(utc_day AS INT)) AS STRING),' ',currency,' ',CAST(CAST(amount/100 AS DECIMAL(38,2)) AS STRING))))) value FROM days GROUP BY side
), owner_sums AS (
 SELECT side,pmod(sum(CAST(conv(substring(sha2(account_id,256),1,16),16,10) AS DECIMAL(38,0))),CAST(18446744073709551616 AS DECIMAL(38,0))) value FROM eligible GROUP BY side
), results AS (
 SELECT 1 check_order,'Row count matches' check_name,CASE WHEN s=c THEN 'PASS' ELSE 'FAIL' END result,CAST(s AS STRING) source_value,CAST(c AS STRING) candidate_value FROM counts
 UNION ALL SELECT 2,'Currency totals match',CASE WHEN n=0 THEN 'PASS' ELSE 'FAIL' END,coalesce((SELECT value FROM sum_text WHERE side='source'),'No in-window rows'),coalesce((SELECT value FROM sum_text WHERE side='candidate'),'No in-window rows') FROM currency_diff
 UNION ALL SELECT 3,'UTC daily totals match',CASE WHEN n=0 THEN 'PASS' ELSE 'FAIL' END,coalesce((SELECT value FROM day_text WHERE side='source'),'No in-window rows'),coalesce((SELECT value FROM day_text WHERE side='candidate'),'No in-window rows') FROM daily_diff
 UNION ALL SELECT 4,'Owner-ID checksum matches',CASE WHEN coalesce((SELECT value FROM owner_sums WHERE side='source'),0)=coalesce((SELECT value FROM owner_sums WHERE side='candidate'),0) THEN 'PASS' ELSE 'FAIL' END,CAST(coalesce((SELECT value FROM owner_sums WHERE side='source'),0) AS STRING),CAST(coalesce((SELECT value FROM owner_sums WHERE side='candidate'),0) AS STRING)
 UNION ALL SELECT 5,'CutoverGuard: invoice ownership',CASE WHEN count(*)=0 THEN 'NO OWNERSHIP FINDINGS' ELSE 'BLOCKED' END,'—',concat(CAST(count(*) AS STRING),' wrong-owner rows') FROM {issues} WHERE type='account_mismatch'
)
SELECT *,1 status_marker,CASE WHEN result='PASS' THEN '🟢 PASS' WHEN result='BLOCKED' THEN '🔴 BLOCKED' WHEN result='FAIL' THEN '🔴 FAIL' ELSE '🟢 NO OWNERSHIP FINDINGS' END result_label FROM results ORDER BY check_order"""
