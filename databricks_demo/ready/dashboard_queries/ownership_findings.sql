SELECT 'defective' scenario, check_id,type,key,field,source_value,candidate_value FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_issues`
UNION ALL
SELECT 'fixed' scenario, check_id,type,key,field,source_value,candidate_value FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_issues`;
