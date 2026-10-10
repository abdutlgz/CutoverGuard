SELECT 'defective' scenario,side,event_id,account_id,amount_minor,currency,event_time,window_start,window_end FROM `workspace`.`default`.`cutoverguard_222947f1bcdd4351_transactions`
UNION ALL
SELECT 'fixed' scenario,side,event_id,account_id,amount_minor,currency,event_time,window_start,window_end FROM `workspace`.`default`.`cutoverguard_3c068b368cca461b_transactions`;
