SELECT 'defective' scenario,run_id,input_json FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_manifest`
UNION ALL
SELECT 'fixed' scenario,run_id,input_json FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_manifest`;
