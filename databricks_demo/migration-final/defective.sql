-- CutoverGuard: synthetic TPC-H customer-crosswalk migration.
-- Separate from the verified 50-order experiment: this bug remaps ALL orders
-- belonging to the 50 selected customers. Actual new finding count must be measured.
-- Prices have arbitrary units, no currency. Dates interpreted as UTC midnight.
-- Use a NEW dedicated schema and a development pipeline. Never production.
-- Evidence-only pipeline: no failing expectation and no promotion target.

CREATE OR REFRESH MATERIALIZED VIEW bronze_legacy_orders AS
SELECT order_id,account_id legacy_customer_id,amount_scaled_units,order_date FROM `workspace`.`default`.`YOUR_FROZEN_TPCH_SOURCE`;

CREATE OR REFRESH MATERIALIZED VIEW bronze_crosswalk_reference AS
SELECT DISTINCT legacy_customer_id,legacy_customer_id expected_cloud_customer_id
FROM bronze_legacy_orders;

CREATE OR REFRESH MATERIALIZED VIEW bronze_bug_pairs AS
SELECT '184501' legacy_customer_id, '735016' cloud_customer_id
UNION ALL
SELECT '735016' legacy_customer_id, '184501' cloud_customer_id
UNION ALL
SELECT '487741' legacy_customer_id, '335404' cloud_customer_id
UNION ALL
SELECT '335404' legacy_customer_id, '487741' cloud_customer_id
UNION ALL
SELECT '412190' legacy_customer_id, '663700' cloud_customer_id
UNION ALL
SELECT '663700' legacy_customer_id, '412190' cloud_customer_id
UNION ALL
SELECT '35033' legacy_customer_id, '46844' cloud_customer_id
UNION ALL
SELECT '46844' legacy_customer_id, '35033' cloud_customer_id
UNION ALL
SELECT '556907' legacy_customer_id, '682312' cloud_customer_id
UNION ALL
SELECT '682312' legacy_customer_id, '556907' cloud_customer_id
UNION ALL
SELECT '92948' legacy_customer_id, '673426' cloud_customer_id
UNION ALL
SELECT '673426' legacy_customer_id, '92948' cloud_customer_id
UNION ALL
SELECT '133178' legacy_customer_id, '321592' cloud_customer_id
UNION ALL
SELECT '321592' legacy_customer_id, '133178' cloud_customer_id
UNION ALL
SELECT '468169' legacy_customer_id, '19439' cloud_customer_id
UNION ALL
SELECT '19439' legacy_customer_id, '468169' cloud_customer_id
UNION ALL
SELECT '446113' legacy_customer_id, '61616' cloud_customer_id
UNION ALL
SELECT '61616' legacy_customer_id, '446113' cloud_customer_id
UNION ALL
SELECT '575374' legacy_customer_id, '560257' cloud_customer_id
UNION ALL
SELECT '560257' legacy_customer_id, '575374' cloud_customer_id
UNION ALL
SELECT '709379' legacy_customer_id, '517496' cloud_customer_id
UNION ALL
SELECT '517496' legacy_customer_id, '709379' cloud_customer_id
UNION ALL
SELECT '547030' legacy_customer_id, '248267' cloud_customer_id
UNION ALL
SELECT '248267' legacy_customer_id, '547030' cloud_customer_id
UNION ALL
SELECT '367732' legacy_customer_id, '464479' cloud_customer_id
UNION ALL
SELECT '464479' legacy_customer_id, '367732' cloud_customer_id
UNION ALL
SELECT '429073' legacy_customer_id, '170597' cloud_customer_id
UNION ALL
SELECT '170597' legacy_customer_id, '429073' cloud_customer_id
UNION ALL
SELECT '718526' legacy_customer_id, '511906' cloud_customer_id
UNION ALL
SELECT '511906' legacy_customer_id, '718526' cloud_customer_id
UNION ALL
SELECT '320692' legacy_customer_id, '365356' cloud_customer_id
UNION ALL
SELECT '365356' legacy_customer_id, '320692' cloud_customer_id
UNION ALL
SELECT '496976' legacy_customer_id, '273319' cloud_customer_id
UNION ALL
SELECT '273319' legacy_customer_id, '496976' cloud_customer_id
UNION ALL
SELECT '648010' legacy_customer_id, '346141' cloud_customer_id
UNION ALL
SELECT '346141' legacy_customer_id, '648010' cloud_customer_id
UNION ALL
SELECT '561664' legacy_customer_id, '367768' cloud_customer_id
UNION ALL
SELECT '367768' legacy_customer_id, '561664' cloud_customer_id
UNION ALL
SELECT '300923' legacy_customer_id, '680965' cloud_customer_id
UNION ALL
SELECT '680965' legacy_customer_id, '300923' cloud_customer_id
UNION ALL
SELECT '353632' legacy_customer_id, '488078' cloud_customer_id
UNION ALL
SELECT '488078' legacy_customer_id, '353632' cloud_customer_id
UNION ALL
SELECT '365134' legacy_customer_id, '280333' cloud_customer_id
UNION ALL
SELECT '280333' legacy_customer_id, '365134' cloud_customer_id
UNION ALL
SELECT '23333' legacy_customer_id, '645083' cloud_customer_id
UNION ALL
SELECT '645083' legacy_customer_id, '23333' cloud_customer_id
UNION ALL
SELECT '645458' legacy_customer_id, '204982' cloud_customer_id
UNION ALL
SELECT '204982' legacy_customer_id, '645458' cloud_customer_id
UNION ALL
SELECT '358444' legacy_customer_id, '363034' cloud_customer_id
UNION ALL
SELECT '363034' legacy_customer_id, '358444' cloud_customer_id;

CREATE OR REFRESH MATERIALIZED VIEW bronze_migration_crosswalk AS
SELECT c.legacy_customer_id,coalesce(b.cloud_customer_id,c.legacy_customer_id) cloud_customer_id
FROM bronze_crosswalk_reference c LEFT JOIN bronze_bug_pairs b
ON c.legacy_customer_id=b.legacy_customer_id;

CREATE OR REFRESH MATERIALIZED VIEW typed_source AS
SELECT order_id,legacy_customer_id account_id,CAST(amount_scaled_units AS DECIMAL(38,0)) amount_scaled_units,
order_date,CAST(order_date AS TIMESTAMP_NTZ) event_utc,
count(*) OVER (PARTITION BY order_id) key_count FROM bronze_legacy_orders;

CREATE OR REFRESH MATERIALIZED VIEW typed_candidate AS
SELECT s.order_id,c.cloud_customer_id account_id,CAST(s.amount_scaled_units AS DECIMAL(38,0)) amount_scaled_units,
s.order_date,CAST(s.order_date AS TIMESTAMP_NTZ) event_utc,
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
(SELECT CAST(pmod(sum(CAST(conv(substring(sha2(legacy_customer_id,256),1,16),16,10) AS DECIMAL(38,0))),CAST(18446744073709551616 AS DECIMAL(38,0))) AS STRING) FROM bronze_legacy_orders) legacy_owner_checksum,
(SELECT CAST(pmod(sum(CAST(conv(substring(sha2(order_id,256),1,16),16,10) AS DECIMAL(38,0))),CAST(18446744073709551616 AS DECIMAL(38,0))) AS STRING) FROM bronze_legacy_orders) legacy_key_checksum,
(SELECT count(*) FROM silver_source) source_rows,
(SELECT count(*) FROM silver_candidate) candidate_rows,
(SELECT sum(amount_scaled_units) FROM silver_source) source_total,
(SELECT sum(amount_scaled_units) FROM silver_candidate) candidate_total,
(SELECT count(*) FROM (SELECT legacy_customer_id FROM bronze_migration_crosswalk GROUP BY legacy_customer_id HAVING count(*)<>1)) duplicate_crosswalk_keys
) SELECT *,CASE WHEN order_findings=0 AND crosswalk_findings=0 AND quarantined_rows=0
AND duplicate_crosswalk_keys=0 AND legacy_rows>0 AND source_rows=legacy_rows AND candidate_rows=legacy_rows
AND legacy_rows=7500000 AND legacy_total=113343921524625
AND legacy_owner_checksum='7186124301794411862' AND legacy_key_checksum='15214096825819894155'
AND source_total<=>candidate_total THEN 'ready' ELSE 'blocked' END status FROM diagnostics;

