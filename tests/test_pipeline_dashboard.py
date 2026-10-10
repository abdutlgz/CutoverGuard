import pytest
from scripts.build_pipeline_dashboard import queries,build_pipeline
def test_pipeline_queries_read_gold_and_keep_scenarios_separate():
    q=queries()
    assert 'gold_cutover_decision' in q['scale_kpis']
    assert 'gold_order_issues' in q['scale_drilldown']
    assert 'gold_customer_effects' in q['scale_effects']
    assert 'migration_bad' in q['pipeline_replay'] and 'migration_fixed' in q['pipeline_replay']
    assert 'recorded_query_seconds' not in q['scale_kpis']
    assert '50 wrong-owner' not in q['scale_checks']
    with pytest.raises(ValueError):queries('bad; DROP TABLE test')
