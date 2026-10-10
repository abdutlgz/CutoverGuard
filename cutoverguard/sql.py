"""Databricks SQL generation. Every run creates new Delta objects; no replacement."""
from datetime import datetime, timezone
import re


def ident(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid SQL identifier')
    return '`' + value.replace('`', '``') + '`'


def literal(value):
    """UTF-8 hex avoids SQL quoting and backslash interpretation of supplied IDs."""
    if value is None:
        return 'CAST(NULL AS STRING)'
    return "decode(unhex('" + str(value).encode('utf-8').hex() + "'), 'UTF-8')"


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()


def table_names(catalog, schema, prefix):
    if not re.fullmatch(r'cutoverguard_[a-f0-9]{16}', prefix):
        raise ValueError('Invalid generated table prefix')
    return {name: '.'.join(ident(v) for v in (catalog, schema, prefix + '_' + name))
            for name in ('manifest', 'profiles', 'transactions', 'issues', 'impacts', 'totals')}


def _rows_select(rows, columns):
    types = dict(columns)
    if not rows:
        return 'SELECT ' + ', '.join(f'CAST(NULL AS {typ}) AS {ident(name)}' for name, typ in columns) + ' WHERE FALSE'
    # Databricks requires analysis-time constants inside VALUES. Keep only hex
    # string literals there; decode in the projection, where functions are allowed.
    def hex_constant(value):
        return 'NULL' if value is None else "'" + str(value).encode('utf-8').hex() + "'"
    values = ',\n'.join('(' + ', '.join(hex_constant(v) for v in row) + ')' for row in rows)
    projection = ', '.join(f"CAST(decode(unhex({ident(name)}), 'UTF-8') AS {types[name]}) AS {ident(name)}" for name, _ in columns)
    return f'SELECT {projection} FROM VALUES {values} AS v(' + ','.join(ident(n) for n, _ in columns) + ')'


def build_pipeline(bundle, tables, run_id):
    """Yield named CREATE TABLE statements, including computed findings and impacts."""
    import json
    p, t, i, a, c = (tables[x] for x in ('profiles', 'transactions', 'issues', 'impacts', 'totals'))
    yield 'manifest', f'CREATE TABLE {tables["manifest"]} USING DELTA AS SELECT {literal(run_id)} AS run_id, {literal(json.dumps(bundle, sort_keys=True, separators=(",", ":")))} AS input_json'
    profiles, transactions = [], []
    for side in ('source', 'candidate'):
        snapshot = bundle[side]
        start, end = utc(snapshot['window_start']), utc(snapshot['window_end'])
        for row in snapshot['profiles']:
            profiles.append([side, row['account_id'], row['customer_id']])
        for row in snapshot['transactions']:
            time = utc(row['event_time'])
            # Match normalized instants, never lexical raw offsets. SQL decides window membership.
            transactions.append([side, row['event_id'], row['account_id'], row['amount_minor'], row['currency'], time, start, end])
    yield 'profiles', f'CREATE TABLE {p} USING DELTA AS ' + _rows_select(profiles, [('side','STRING'),('account_id','STRING'),('customer_id','STRING')])
    yield 'transactions', f'CREATE TABLE {t} USING DELTA AS ' + _rows_select(transactions, [('side','STRING'),('event_id','STRING'),('account_id','STRING'),('amount_minor','DECIMAL(38,0)'),('currency','STRING'),('event_time','STRING'),('window_start','STRING'),('window_end','STRING')])
    # Data comparison itself is performed on platform, not by the local oracle.
    pieces = []
    for typ, condition, field, sv, cv in [
        ('profile_missing','c.account_id IS NULL','account_id','s.account_id','c.account_id'),
        ('profile_extra','s.account_id IS NULL','account_id','s.account_id','c.account_id'),
        ('customer_mismatch','s.account_id IS NOT NULL AND c.account_id IS NOT NULL AND s.customer_id <> c.customer_id','customer_id','s.customer_id','c.customer_id')]:
        pieces.append(f"SELECT 'identity' check_id, '{typ}' type, coalesce(s.account_id,c.account_id) key, '{field}' field, {sv} source_value, {cv} candidate_value FROM ps s FULL OUTER JOIN pc c ON s.account_id=c.account_id WHERE {condition}")
    for typ, condition, field in [
        ('event_missing','c.event_id IS NULL','event_id'), ('event_extra','s.event_id IS NULL','event_id'),
        ('account_mismatch','s.event_id IS NOT NULL AND c.event_id IS NOT NULL AND s.account_id <> c.account_id','account_id'),
        ('amount_mismatch','s.event_id IS NOT NULL AND c.event_id IS NOT NULL AND s.amount_minor <> c.amount_minor','amount_minor'),
        ('currency_mismatch','s.event_id IS NOT NULL AND c.event_id IS NOT NULL AND s.currency <> c.currency','currency'),
        ('timestamp_mismatch','s.event_id IS NOT NULL AND c.event_id IS NOT NULL AND s.event_time <> c.event_time','event_time')]:
        pieces.append(f"SELECT 'transactions' check_id, '{typ}' type, coalesce(s.event_id,c.event_id) key, '{field}' field, CAST(s.{field} AS STRING) source_value, CAST(c.{field} AS STRING) candidate_value FROM ts s FULL OUTER JOIN tc c ON s.event_id=c.event_id WHERE {condition}")
    yield 'issues', f'CREATE TABLE {i} USING DELTA AS WITH ps AS (SELECT * FROM {p} WHERE side=\'source\'), pc AS (SELECT * FROM {p} WHERE side=\'candidate\'), ts AS (SELECT * FROM {t} WHERE side=\'source\' AND CAST(event_time AS TIMESTAMP)>=CAST(window_start AS TIMESTAMP) AND CAST(event_time AS TIMESTAMP)<CAST(window_end AS TIMESTAMP)), tc AS (SELECT * FROM {t} WHERE side=\'candidate\' AND CAST(event_time AS TIMESTAMP)>=CAST(window_start AS TIMESTAMP) AND CAST(event_time AS TIMESTAMP)<CAST(window_end AS TIMESTAMP)) ' + ' UNION ALL '.join(pieces)
    yield 'impacts', f'''CREATE TABLE {a} USING DELTA AS WITH balances AS (
SELECT side, account_id, currency, sum(amount_minor) amount FROM {t}
WHERE CAST(event_time AS TIMESTAMP)>=CAST(window_start AS TIMESTAMP) AND CAST(event_time AS TIMESTAMP)<CAST(window_end AS TIMESTAMP)
GROUP BY side,account_id,currency), s AS (SELECT * FROM balances WHERE side='source'), c AS (SELECT * FROM balances WHERE side='candidate')
SELECT coalesce(s.account_id,c.account_id) account_id,coalesce(s.currency,c.currency) currency,
coalesce(s.amount,CAST(0 AS DECIMAL(38,0))) source_minor,coalesce(c.amount,CAST(0 AS DECIMAL(38,0))) candidate_minor,
coalesce(c.amount,CAST(0 AS DECIMAL(38,0)))-coalesce(s.amount,CAST(0 AS DECIMAL(38,0))) delta_minor
FROM s FULL OUTER JOIN c ON s.account_id=c.account_id AND s.currency=c.currency'''
    yield 'totals', f'''CREATE TABLE {c} USING DELTA AS WITH changed AS (
SELECT DISTINCT key FROM {i} WHERE check_id='transactions'), inwindow AS (
SELECT * FROM {t} WHERE CAST(event_time AS TIMESTAMP)>=CAST(window_start AS TIMESTAMP) AND CAST(event_time AS TIMESTAMP)<CAST(window_end AS TIMESTAMP)), affected AS (
SELECT s.currency,abs(s.amount_minor) amount FROM inwindow s JOIN changed k ON s.event_id=k.key WHERE s.side='source'
UNION ALL SELECT c.currency,abs(c.amount_minor) amount FROM inwindow c JOIN changed k ON c.event_id=k.key
LEFT JOIN inwindow s ON s.side='source' AND s.event_id=c.event_id WHERE c.side='candidate' AND (s.event_id IS NULL OR s.currency<>c.currency)), notional AS (
SELECT currency,sum(amount) affected_notional_minor FROM affected GROUP BY currency)
SELECT a.currency,sum(a.source_minor) source_minor,sum(a.candidate_minor) candidate_minor,
sum(a.delta_minor) net_delta_minor,sum(abs(a.delta_minor)) absolute_account_delta_minor,
coalesce(n.affected_notional_minor,CAST(0 AS DECIMAL(38,0))) affected_notional_minor
FROM {a} a LEFT JOIN notional n ON a.currency=n.currency GROUP BY a.currency,n.affected_notional_minor'''
