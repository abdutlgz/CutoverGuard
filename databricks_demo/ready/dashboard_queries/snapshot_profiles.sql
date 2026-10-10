SELECT 'defective' scenario,side,account_id,customer_id FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_profiles`
UNION ALL
SELECT 'fixed' scenario,side,account_id,customer_id FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_profiles`;
