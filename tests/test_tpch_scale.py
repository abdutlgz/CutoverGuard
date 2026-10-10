from copy import deepcopy
import json
import pytest
from scripts.run_tpch_scale import (injection,verify,owner_checksum,validate_schema,discover,run,names,build_sql,CloudError)

@pytest.fixture
def selected():
    return [[str(i),str(100+i),str(i+1),'1995-01-01'] for i in range(50)]


def manual_results(selected):
    swaps=injection(selected)
    issues=[[str(i),str(100+i),str(100+(i^1)),str(i+1),str(i+1),'1995-01-01','1995-01-01'] for i in range(50)]
    # Source has exactly one order per customer in this hand-computable test.
    impacts=[[str(100+i),str(i+1),str((i^1)+1),'1' if i%2==0 else '-1'] for i in range(50)]
    d={'row_count':50,'total_scaled_units':'1275','owner_checksum_unsigned64':owner_checksum(range(100,150)),'order_checksum_unsigned64':owner_checksum(range(50))}
    metrics=[[side,'50','1275',d['owner_checksum_unsigned64'],d['order_checksum_unsigned64']] for side in ('source','candidate')]
    return swaps,d,issues,impacts,metrics


def test_pair_injection_exact50_and_checksum_misses_swaps(selected):
    swaps=injection(selected)
    assert len(swaps)==50
    assert swaps[0]['candidate_account_id']=='101' and swaps[1]['candidate_account_id']=='100'
    assert owner_checksum(row['source_account_id'] for row in swaps)==owner_checksum(row['candidate_account_id'] for row in swaps)
    result=verify(*manual_results(selected))
    assert result['absolute_account_delta_scaled_units']=='50'
    assert result['changed_owner_rows']==50 and result['impacted_customer_buckets']==50


@pytest.mark.parametrize('failure',['missing','extra','changed_owner','changed_amount','changed_date','delta','metric_count','metric_checksum'])
def test_fail_closed_independent_injection_and_amounts(selected,failure):
    swaps,d,issues,impacts,metrics=manual_results(selected)
    if failure=='missing':issues.pop()
    elif failure=='extra':issues.append(issues[0][:])
    elif failure=='changed_owner':issues[0][2]='999'
    elif failure=='changed_amount':issues[0][4]='999'
    elif failure=='changed_date':issues[0][6]='1995-01-02'
    elif failure=='delta':impacts[0][3]='999'
    elif failure=='metric_count':metrics[1][1]='49'
    else:metrics[1][3]='123'
    with pytest.raises(CloudError):verify(swaps,d,issues,impacts,metrics)


def test_equal_amount_swaps_are_issues_even_without_account_delta(selected):
    for row in selected:row[2]='5'
    swaps=injection(selected)
    issues=[[r['order_id'],r['source_account_id'],r['candidate_account_id'],'5','5',r['order_date'],r['order_date']] for r in swaps]
    d={'row_count':50,'total_scaled_units':'250','owner_checksum_unsigned64':owner_checksum(range(100,150)),'order_checksum_unsigned64':owner_checksum(range(50))}
    metrics=[[side,'50','250',d['owner_checksum_unsigned64'],d['order_checksum_unsigned64']] for side in ('source','candidate')]
    result=verify(swaps,d,issues,[],metrics)
    assert result['changed_owner_rows']==50 and result['impacted_customer_buckets']==0


def test_invalid_selection_and_exact_decimal_schema(selected):
    with pytest.raises(CloudError):injection(selected[:49])
    selected[1][1]=selected[0][1]
    with pytest.raises(CloudError):injection(selected)
    schema=[['o_orderkey','bigint'],['o_custkey','bigint'],['o_totalprice','decimal(18,2)'],['o_orderdate','date']]
    assert validate_schema(schema)['o_totalprice']=='decimal(18,2)'
    schema[2][1]='double'
    with pytest.raises(CloudError):validate_schema(schema)


class Runner:
    warehouse_id='warehouse'
    def __init__(self,selected,fail=False):
        self.selected=selected;self.fail=fail;self.sql=[];self.statement_ids=[]
        self.swaps,self.d,self.issues,self.impacts,self.metrics=manual_results(selected)
    def execute(self,sql):
        self.sql.append(sql);self.statement_ids.append('s'+str(len(self.sql)))
        if sql.startswith('DESCRIBE'):
            return [['o_orderkey','bigint'],['o_custkey','bigint'],['o_totalprice','decimal(18,2)'],['o_orderdate','date']]
        if 'count(DISTINCT o_orderkey)' in sql:return [['50','50','0','1275',self.d['owner_checksum_unsigned64'],self.d['order_checksum_unsigned64']]]
        if sql.startswith('CREATE'):
            if self.fail:raise CloudError('unknown outcome')
            return []
        if 'count(DISTINCT order_id)' in sql:return [['50','50','0','1275',self.d['owner_checksum_unsigned64'],self.d['order_checksum_unsigned64']]]
        if 'customer_rank=1' in sql:return self.selected
        if 'source_account_id,candidate_account_id' in sql:return self.issues
        if 'delta_units<>0' in sql:return self.impacts
        if 'SELECT side,row_count' in sql:return self.metrics
        raise AssertionError(sql)


def test_discovery_read_only_real_count_no_assumptions(selected):
    r=Runner(selected);d=discover(r)
    assert d['row_count']==50
    assert all(sql.startswith(('DESCRIBE','SELECT')) for sql in r.sql)
    assert 'currency' not in d


def test_safe_unique_tables_complete_evidence_resume(selected,tmp_path):
    r=Runner(selected);result=run(r,tmp_path)
    assert result['status']=='verified_narrow_sql_experiment'
    assert len(result['statement_ids'])==len(r.sql)
    assert sum(sql.startswith('CREATE') for sql in r.sql)==6
    assert not any('DROP ' in sql or 'REPLACE ' in sql for sql in r.sql)
    resumed=Runner(selected);run(resumed,tmp_path,resume=True)
    assert not any(sql.startswith('CREATE') for sql in resumed.sql)
    assert len(resumed.sql)==4
    with pytest.raises(CloudError):run(Runner(selected),tmp_path)


def test_unknown_write_outcome_never_retried(selected,tmp_path):
    with pytest.raises(CloudError):run(Runner(selected,True),tmp_path)
    state=json.loads((tmp_path/'state.json').read_text())
    assert state['attempted']=='source' and state['status']=='unverified'
    retry=Runner(selected)
    with pytest.raises(CloudError,match='ambiguous'):run(retry,tmp_path,resume=True)
    assert retry.sql==[]


def test_target_identifiers_reject_injection():
    with pytest.raises(CloudError):names('workspace;DROP','default','tpch_swap_'+'a'*16)
    with pytest.raises(CloudError):names('workspace','default','existing_table')


def test_frozen_snapshot_null_or_duplicate_refused_before_injection(selected,tmp_path):
    class BadSource(Runner):
        def execute(self,sql):
            if 'count(DISTINCT order_id)' in sql:return [['50','49','1','1275',self.d['owner_checksum_unsigned64'],self.d['order_checksum_unsigned64']]]
            return super().execute(sql)
    r=BadSource(selected)
    with pytest.raises(CloudError,match='Frozen source'):run(r,tmp_path)
    assert not any('customer_rank=1' in sql for sql in r.sql)
    assert sum(sql.startswith('CREATE') for sql in r.sql)==1


def test_gold_query_detects_null_fields_as_mismatches(selected):
    statements=dict(build_sql(names('workspace','default','tpch_swap_'+'a'*16),injection(selected)))
    assert statements['issues'].count('<=>')==3
    assert 'FULL OUTER JOIN' in statements['issues']
    swaps,d,issues,impacts,metrics=manual_results(selected)
    issues[0][6]=None
    with pytest.raises(CloudError):verify(swaps,d,issues,impacts,metrics)
