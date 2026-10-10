-- Separate promotion pipeline. Run only AFTER the evidence pipeline succeeds.
-- Keep the evidence schema frozen while testing promotion; no production approval.
CREATE OR REFRESH MATERIALIZED VIEW cutover_gate (
CONSTRAINT no_wrong_owner_promotion EXPECT (status = 'ready') ON VIOLATION FAIL UPDATE
) AS SELECT * FROM `workspace`.`cutoverguard_migration_bad`.`gold_cutover_decision`;
CREATE OR REFRESH MATERIALIZED VIEW promoted_orders AS
SELECT c.* FROM `workspace`.`cutoverguard_migration_bad`.`silver_candidate` c CROSS JOIN cutover_gate g WHERE g.status='ready';
