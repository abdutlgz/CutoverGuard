-- Databricks notebook source
-- MAGIC %md
-- MAGIC # CutoverGuard — Same total, different owner
-- MAGIC Original Knight Hacks IX project for financial migration review. All records are synthetic.
-- MAGIC This notebook only reads the two actual successful SQL runs; it creates no objects.
-- MAGIC Select SQL warehouse 515dff82a7a2c282. Zero net difference does not establish correct ownership.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 1. Two runs: defective → corrected

-- COMMAND ----------

SELECT 'defective' scenario, currency, source_minor, candidate_minor, net_delta_minor, absolute_account_delta_minor, affected_notional_minor, (SELECT count(*) FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_issues`) primary_findings FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_totals`
UNION ALL
SELECT 'fixed' scenario, currency, source_minor, candidate_minor, net_delta_minor, absolute_account_delta_minor, affected_notional_minor, (SELECT count(*) FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_issues`) primary_findings FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_totals`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 2. The exact transaction ownership breaks

-- COMMAND ----------

SELECT 'defective' scenario, check_id,type,key,field,source_value,candidate_value FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_issues`
UNION ALL
SELECT 'fixed' scenario, check_id,type,key,field,source_value,candidate_value FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_issues`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 3. Account effects; differences are not losses

-- COMMAND ----------

SELECT 'defective' scenario, account_id,currency,source_minor,candidate_minor,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_impacts`
UNION ALL
SELECT 'fixed' scenario, account_id,currency,source_minor,candidate_minor,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_impacts`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 4. Plot allocation effects beside zero firm net
-- MAGIC Add a bar visualization: x=label, y=delta_major, series=scenario; filter currency=USD. Preserve sign and zero baseline. Do not sum account absolute differences into loss. Fixed bars are zero.

-- COMMAND ----------

SELECT 'defective' scenario, account_id label,currency,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major,'Account allocation difference' measure FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_impacts` UNION ALL SELECT 'defective' scenario,'Firm net' label,currency,net_delta_minor delta_minor,CAST(net_delta_minor/100 AS DECIMAL(38,2)) delta_major,'Firm net difference' measure FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_totals`
UNION ALL
SELECT 'fixed' scenario, account_id label,currency,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major,'Account allocation difference' measure FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_impacts` UNION ALL SELECT 'fixed' scenario,'Firm net' label,currency,net_delta_minor delta_minor,CAST(net_delta_minor/100 AS DECIMAL(38,2)) delta_major,'Firm net difference' measure FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_totals`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 5. Original snapshots and fingerprints

-- COMMAND ----------

SELECT 'defective' scenario,run_id,input_json FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_manifest`
UNION ALL
SELECT 'fixed' scenario,run_id,input_json FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_manifest`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 6. Preserved leading-zero account IDs

-- COMMAND ----------

SELECT 'defective' scenario,side,account_id,customer_id FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_profiles`
UNION ALL
SELECT 'fixed' scenario,side,account_id,customer_id FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_profiles`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC 7. The inputs used by SQL

-- COMMAND ----------

SELECT 'defective' scenario,side,event_id,account_id,amount_minor,currency,event_time,window_start,window_end FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_transactions`
UNION ALL
SELECT 'fixed' scenario,side,event_id,account_id,amount_minor,currency,event_time,window_start,window_end FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_transactions`;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Evidence boundary
-- MAGIC The existing Delta reconciliation runs succeeded and were compared to the exact Python oracle.
-- MAGIC The optional Lakeflow extension in this pack has not run yet.
-- MAGIC Completeness and dependency metadata are supplied declarations; ready for review is human review, not an automatic production cutover.
-- MAGIC No BNY API, live mainframe ingestion, savings measurement or GPU workload is claimed.
