"""Prepare readable Lakeflow crosswalk-migration SQL; never start or publish a pipeline."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_tpch_dashboard import validated
from scripts.run_tpch_scale import checksum_sql,integer

def literal(value):
    # TPC-H integer IDs rendered as readable string literals; never coerce IDs.
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,20}',value):
        raise ValueError('TPC-H crosswalk requires numeric-string IDs.')
    return "'"+value+"'"


def pipeline_sql(report,bug=True):
    validated(report)
    source=report['tables']['source']
    manifest=report['discovery']
    expected_rows=integer(manifest['row_count']);expected_total=integer(manifest['total_scaled_units'])
    expected_owner=integer(manifest['owner_checksum_unsigned64']);expected_keys=integer(manifest['order_checksum_unsigned64'])
    mapping='\nUNION ALL\n'.join(f"SELECT {literal(x['source_account_id'])} legacy_customer_id, {literal(x['candidate_account_id'])} cloud_customer_id" for x in report['swaps'])
    transform='coalesce(b.cloud_customer_id,c.legacy_customer_id)' if bug else 'c.legacy_customer_id'
    return f'''-- CutoverGuard: synthetic TPC-H customer-crosswalk migration.
-- Separate from the verified 50-order experiment: this bug remaps ALL orders
-- belonging to the 50 selected customers. Actual new finding count must be measured.
-- Prices have arbitrary units, no currency. Dates interpreted as UTC midnight.
-- Use a NEW dedicated schema and a development pipeline. Never production.
-- Diagnostic flows can update in parallel; only promoted_orders depends on the gate.

CREATE OR REFRESH MATERIALIZED VIEW bronze_legacy_orders AS
SELECT order_id,account_id legacy_customer_id,amount_scaled_units,order_date FROM {source};

CREATE OR REFRESH MATERIALIZED VIEW bronze_crosswalk_reference AS
SELECT DISTINCT legacy_customer_id,legacy_customer_id expected_cloud_customer_id
FROM bronze_legacy_orders;

CREATE OR REFRESH MATERIALIZED VIEW bronze_bug_pairs AS
{mapping};

CREATE OR REFRESH MATERIALIZED VIEW bronze_migration_crosswalk AS
SELECT c.legacy_customer_id,{transform} cloud_customer_id
FROM bronze_crosswalk_reference c LEFT JOIN bronze_bug_pairs b
ON c.legacy_customer_id=b.legacy_customer_id;

CREATE OR REFRESH MATERIALIZED VIEW typed_source AS
SELECT order_id,legacy_customer_id account_id,CAST(amount_scaled_units AS DECIMAL(38,0)) amount_scaled_units,
order_date,CAST(concat(CAST(order_date AS STRING),'T00:00:00Z') AS TIMESTAMP_LTZ) event_utc,
count(*) OVER (PARTITION BY order_id) key_count FROM bronze_legacy_orders;

CREATE OR REFRESH MATERIALIZED VIEW typed_candidate AS
SELECT s.order_id,c.cloud_customer_id account_id,CAST(s.amount_scaled_units AS DECIMAL(38,0)) amount_scaled_units,
s.order_date,CAST(concat(CAST(s.order_date AS STRING),'T00:00:00Z') AS TIMESTAMP_LTZ) event_utc,
count(*) OVER (PARTITION BY s.order_id) key_count
FROM bronze_legacy_orders s LEFT JOIN bronze_migration_crosswalk c
ON s.legacy_customer_id=c.legacy_customer_id;

CREATE OR REFRESH MATERIALIZED VIEW source_quarantine AS
SELECT * FROM typed_source WHERE order_id IS NULL OR account_id IS NULL OR amount_scaled_units IS NULL OR event_utc IS NULL OR key_count<>1;
CREATE OR REFRESH MATERIALIZED VIEW candidate_quarantine AS
SELECT * FROM typed_candidate WHERE order_id IS NULL OR account_id IS NULL OR amount_scaled_units IS NULL OR event_utc IS NULL OR key_count<>1;

CREATE OR REFRESH MATERIALIZED VIEW silver_source (
CONSTRAINT valid_source EXPECT (order_id IS NOT NULL AND account_id IS NOT NULL AND amount_scaled_units IS NOT NULL AND event_utc IS NOT NULL AND key_count=1) ON VIOLATION DROP ROW
) AS SELECT * FROM typed_source;
CREATE OR REFRESH MATERIALIZED VIEW silver_candidate (
CONSTRAINT valid_candidate EXPECT (order_id IS NOT NULL AND account_id IS NOT NULL AND amount_scaled_units IS NOT NULL AND event_utc IS NOT NULL AND key_count=1) ON VIOLATION DROP ROW
) AS SELECT * FROM typed_candidate;

CREATE OR REFRESH MATERIALIZED VIEW gold_crosswalk_issues AS
SELECT r.legacy_customer_id,r.expected_cloud_customer_id,c.cloud_customer_id migrated_customer_id
FROM bronze_crosswalk_reference r FULL OUTER JOIN bronze_migration_crosswalk c
ON r.legacy_customer_id=c.legacy_customer_id
WHERE r.legacy_customer_id IS NULL OR c.legacy_customer_id IS NULL OR NOT(r.expected_cloud_customer_id<=>c.cloud_customer_id);

CREATE OR REFRESH MATERIALIZED VIEW gold_order_issues AS
SELECT coalesce(s.order_id,c.order_id) order_id,s.account_id original_owner,c.account_id migrated_owner,
s.amount_scaled_units original_amount,c.amount_scaled_units migrated_amount,s.event_utc original_utc,c.event_utc migrated_utc
FROM silver_source s FULL OUTER JOIN silver_candidate c ON s.order_id=c.order_id
WHERE s.order_id IS NULL OR c.order_id IS NULL OR NOT(s.account_id<=>c.account_id)
OR NOT(s.amount_scaled_units<=>c.amount_scaled_units) OR NOT(s.event_utc<=>c.event_utc);

CREATE OR REFRESH MATERIALIZED VIEW gold_customer_effects AS
WITH allocations AS (
SELECT account_id,sum(amount_scaled_units) source_units,CAST(0 AS DECIMAL(38,0)) candidate_units FROM silver_source GROUP BY account_id
UNION ALL SELECT account_id,CAST(0 AS DECIMAL(38,0)),sum(amount_scaled_units) FROM silver_candidate GROUP BY account_id
) SELECT account_id,sum(source_units) source_units,sum(candidate_units) candidate_units,
sum(candidate_units)-sum(source_units) delta_scaled_units FROM allocations GROUP BY account_id;

CREATE OR REFRESH MATERIALIZED VIEW gold_aggregate_checks AS
SELECT 'source' side,count(*) row_count,sum(amount_scaled_units) total_scaled_units FROM silver_source
UNION ALL SELECT 'candidate',count(*),sum(amount_scaled_units) FROM silver_candidate;

CREATE OR REFRESH MATERIALIZED VIEW gold_cutover_decision AS
WITH diagnostics AS (SELECT
(SELECT count(*) FROM gold_order_issues) order_findings,
(SELECT count(*) FROM gold_crosswalk_issues) crosswalk_findings,
(SELECT count(*) FROM source_quarantine)+(SELECT count(*) FROM candidate_quarantine) quarantined_rows,
(SELECT count(*) FROM bronze_legacy_orders) legacy_rows,
(SELECT sum(amount_scaled_units) FROM bronze_legacy_orders) legacy_total,
(SELECT {checksum_sql('legacy_customer_id')} FROM bronze_legacy_orders) legacy_owner_checksum,
(SELECT {checksum_sql('order_id')} FROM bronze_legacy_orders) legacy_key_checksum,
(SELECT count(*) FROM silver_source) source_rows,
(SELECT count(*) FROM silver_candidate) candidate_rows,
(SELECT sum(amount_scaled_units) FROM silver_source) source_total,
(SELECT sum(amount_scaled_units) FROM silver_candidate) candidate_total,
(SELECT count(*) FROM (SELECT legacy_customer_id FROM bronze_migration_crosswalk GROUP BY legacy_customer_id HAVING count(*)<>1)) duplicate_crosswalk_keys
) SELECT *,CASE WHEN order_findings=0 AND crosswalk_findings=0 AND quarantined_rows=0
AND duplicate_crosswalk_keys=0 AND legacy_rows>0 AND source_rows=legacy_rows AND candidate_rows=legacy_rows
AND legacy_rows={expected_rows} AND legacy_total={expected_total}
AND legacy_owner_checksum='{expected_owner}' AND legacy_key_checksum='{expected_keys}'
AND source_total<=>candidate_total THEN 'ready' ELSE 'blocked' END status FROM diagnostics;

CREATE OR REFRESH MATERIALIZED VIEW cutover_gate (
CONSTRAINT no_wrong_owner_promotion EXPECT (status = 'ready') ON VIOLATION FAIL UPDATE
) AS SELECT * FROM gold_cutover_decision;

CREATE OR REFRESH MATERIALIZED VIEW promoted_orders AS
SELECT c.* FROM silver_candidate c CROSS JOIN cutover_gate g WHERE g.status='ready';
-- 'ready' is only this controlled pipeline's supported gate, NOT production approval.
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,default=ROOT/'tpch_results/run-001/report.json');p.add_argument('--output',type=Path,default=ROOT/'databricks_demo/migration-final');p.add_argument('--evidence-schema',default='cutoverguard_migration_bad');a=p.parse_args()
    try:
        r=json.loads(a.report.read_text());a.output.mkdir(parents=True,exist_ok=True)
        for label,bug in [('defective',True),('corrected',False)]:
            sql=pipeline_sql(r,bug).split('CREATE OR REFRESH MATERIALIZED VIEW cutover_gate (')[0]
            sql=sql.replace('-- Diagnostic flows can update in parallel; only promoted_orders depends on the gate.', '-- Evidence-only pipeline: no failing expectation and no promotion target.')
            (a.output/(label+'.sql')).write_text(sql)
        (a.output/'promotion-gate.sql').write_text(promotion_sql(a.evidence_schema))
        print('Prepared evidence-only defective/corrected SQL and separate promotion gate. Live execution UNVERIFIED.')
        return 0
    except (ValueError,KeyError,OSError) as e:print('Preparation failed:',str(e));return 1
def promotion_sql(evidence_schema):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',evidence_schema):raise ValueError('Use a simple dedicated evidence schema.')
    base='`workspace`.`'+evidence_schema+'`'
    return f'''-- Separate promotion pipeline. Run only AFTER the evidence pipeline succeeds.
-- Keep the evidence schema frozen while testing promotion; no production approval.
CREATE OR REFRESH MATERIALIZED VIEW cutover_gate (
CONSTRAINT no_wrong_owner_promotion EXPECT (status = 'ready') ON VIOLATION FAIL UPDATE
) AS SELECT * FROM {base}.`gold_cutover_decision`;
CREATE OR REFRESH MATERIALIZED VIEW promoted_orders AS
SELECT c.* FROM {base}.`silver_candidate` c CROSS JOIN cutover_gate g WHERE g.status='ready';
'''

if __name__=='__main__':sys.exit(main())
