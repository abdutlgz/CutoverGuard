-- Read-only verification of isolated demonstration promotion outputs.
SELECT
 (SELECT count(*) FROM workspace.information_schema.tables WHERE table_schema='cutoverguard_promotion_bad' AND table_name='promoted_orders') AS defective_promoted_tables,
 (SELECT count(*) FROM workspace.cutoverguard_promotion_fixed.promoted_orders) AS corrected_promoted_orders,
 (SELECT status FROM workspace.cutoverguard_promotion_fixed.cutover_gate) AS corrected_gate_status,
 (SELECT order_findings FROM workspace.cutoverguard_migration_fixed.gold_cutover_decision) AS corrected_ownership_findings,
 (SELECT crosswalk_findings FROM workspace.cutoverguard_migration_fixed.gold_cutover_decision) AS corrected_crosswalk_findings,
 (SELECT sum(delta_scaled_units) FROM workspace.cutoverguard_migration_bad.gold_customer_effects) AS defective_signed_effect_net,
 (SELECT count(*) FROM workspace.cutoverguard_migration_bad.gold_customer_effects WHERE delta_scaled_units<>0) AS changed_customer_buckets,
 (SELECT count(*) FROM workspace.cutoverguard_migration_fixed.silver_source s FULL OUTER JOIN workspace.cutoverguard_promotion_fixed.promoted_orders p ON s.order_id=p.order_id WHERE s.order_id IS NULL OR p.order_id IS NULL OR NOT(s.account_id<=>p.account_id) OR NOT(s.amount_scaled_units<=>p.amount_scaled_units) OR NOT(s.event_utc<=>p.event_utc)) AS promoted_row_differences;
