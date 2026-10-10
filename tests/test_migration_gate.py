from scripts.build_migration_gate import pipeline_sql
from test_tpch_dashboard import report


def test_gate_is_downstream_of_findings_quarantine_and_completeness():
    bad=pipeline_sql(report())
    assert 'ON VIOLATION FAIL UPDATE' in bad
    assert "EXPECT (status = 'ready')" in bad
    assert "CROSS JOIN cutover_gate g WHERE g.status='ready'" in bad
    assert 'quarantined_rows=0' in bad and 'candidate_rows=legacy_rows' in bad
    assert 'source_rows=legacy_rows' in bad and 'duplicate_crosswalk_keys=0' in bad
    assert 'legacy_rows=7500000 AND legacy_total=123456789' in bad
    assert "legacy_owner_checksum='987' AND legacy_key_checksum='654'" in bad
    assert 'NOT(s.account_id<=>c.account_id)' in bad
    assert 'NOT(s.amount_scaled_units<=>c.amount_scaled_units)' in bad
    assert 'NOT(s.event_utc<=>c.event_utc)' in bad
    assert 'key_count<>1' in bad and 'count(*) OVER (PARTITION BY s.order_id)' in bad
    assert 'CREATE OR REFRESH MATERIALIZED VIEW source_quarantine' in bad
    assert 'hex' not in bad.lower() and 'USD' not in bad


def test_only_crosswalk_transform_changes_in_corrected_variant():
    r=report();bad=pipeline_sql(r);fixed=pipeline_sql(r,False)
    assert bad.replace('coalesce(b.cloud_customer_id,c.legacy_customer_id)','c.legacy_customer_id')==fixed
    # Independent relation-level example: changing a crosswalk remaps all orders,
    # while ownership joins detect even equal-value effects with zero aggregate delta.
    ledger=[('a','0012',100),('b','0012',100),('c','12',100),('d','12',100)]
    broken={'0012':'12','12':'0012'}
    candidate=[(key,broken[owner],amount) for key,owner,amount in ledger]
    assert sum(x[2] for x in ledger)==sum(x[2] for x in candidate)
    assert sum(a[1]!=b[1] for a,b in zip(ledger,candidate))==4
    assert {owner:sum(x[2] for x in ledger if x[1]==owner) for owner in broken}=={owner:sum(x[2] for x in candidate if x[1]==owner) for owner in broken}


def test_separate_promotion_reads_persisted_evidence_and_rejects_schema_injection():
    import pytest
    from scripts.build_migration_gate import promotion_sql
    sql=promotion_sql('cutoverguard_migration_bad')
    assert '`workspace`.`cutoverguard_migration_bad`.`gold_cutover_decision`' in sql
    assert 'FAIL UPDATE' in sql and 'CROSS JOIN cutover_gate' in sql
    assert 'bronze_legacy_orders' not in sql
    with pytest.raises(ValueError):promotion_sql('bad; DROP SCHEMA default')


def test_pipeline_timestamp_is_an_explicit_utc_instant_without_ntz_feature():
    for bug in (True,False):
        sql=pipeline_sql(report(),bug)
        assert 'TIMESTAMP_NTZ' not in sql
        assert "CAST(concat(CAST(order_date AS STRING),'T00:00:00Z') AS TIMESTAMP_LTZ)" in sql
        assert "CAST(concat(CAST(s.order_date AS STRING),'T00:00:00Z') AS TIMESTAMP_LTZ)" in sql
