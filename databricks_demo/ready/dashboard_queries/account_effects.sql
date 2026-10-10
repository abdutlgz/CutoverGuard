SELECT 'defective' scenario, account_id,currency,source_minor,candidate_minor,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_impacts`
UNION ALL
SELECT 'fixed' scenario, account_id,currency,source_minor,candidate_minor,delta_minor,CAST(delta_minor/100 AS DECIMAL(38,2)) delta_major FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_impacts`;
