from copy import deepcopy
from decimal import Decimal
import pytest
from scripts.build_tpch_dashboard import queries,validated,verify_queries
from scripts.run_tpch_scale import names,injection,verify


def report():
    swaps=injection([[str(i),str(100+i),str(i*101),'1996-01-02'] for i in range(1,51)])
    issues=[[x['order_id'],x['source_account_id'],x['candidate_account_id'],x['amount_scaled_units'],x['amount_scaled_units'],x['order_date'],x['order_date']] for x in swaps]
    delta={}
    for x in swaps:
        n=int(x['amount_scaled_units']);a=x['source_account_id'];b=x['candidate_account_id'];delta[a]=delta.get(a,0)-n;delta[b]=delta.get(b,0)+n
    impacts=[[a,'1000',str(1000+n),str(n)] for a,n in delta.items()]
    d={'row_count':7500000,'total_scaled_units':'123456789','owner_checksum_unsigned64':'987','order_checksum_unsigned64':'654'}
    metrics=[[side,'7500000','123456789','987','654'] for side in ('source','candidate')]
    r={'status':'verified_narrow_sql_experiment','attempted':None,'data_kind':'synthetic_benchmark','completed':['source','mapping','candidate','issues','impacts','metrics'],'prefix':'tpch_swap_'+'a'*16,'warehouse_id':'test','completed_at':'2026-10-10T20:00:00+00:00','swaps':swaps,'discovery':d,'issue_rows':issues,'account_effect_rows':impacts,'metric_rows':metrics,'timings':[{'elapsed_seconds':59.8}]}
    r['tables']=names('workspace','default',r['prefix']);r['verification']=verify(swaps,d,issues,impacts,metrics)
    return r


def test_queries_are_read_only_and_distinct_controls():
    q=queries(report())
    assert len(q)==4
    assert all(sql.startswith(('WITH','SELECT')) and 'CREATE ' not in sql for sql in q.values())
    assert 'FULL OUTER JOIN candidate_daily' in q['scale_checks']
    assert "SELECT 5,'Daily price totals'" in q['scale_checks']
    assert 'order_checksum_unsigned64' in q['scale_checks']
    assert 'price_units' in q['scale_drilldown'] and 'USD' not in str(q)
    assert 'DESC,order_id' in q['scale_drilldown']


@pytest.mark.parametrize('mutation',['status','tables','verification'])
def test_tampered_evidence_rejected(mutation):
    r=report()
    if mutation=='status':r['status']='unverified'
    elif mutation=='tables':r['tables']['source']='private.source'
    else:r['verification']['changed_owner_rows']=49
    with pytest.raises(ValueError):validated(r)


class Runner:
    def __init__(self,r,bad=False):self.statement_ids=[];self.n=0;self.r=r;self.bad=bad
    def execute(self,sql):
        self.n+=1;self.statement_ids.append(str(self.n))
        r=self.r
        if self.n==1:return [['7500000','0','0','50','59.8']]
        if self.n==2:
            values=[['Row count','7500000','7500000'],['Total price units','1234567.89','1234567.89'],['Owner-ID checksum','987','987'],['Order-key checksum','654','654'],['Daily price totals','0 mismatched days','0 mismatched days'],['CutoverGuard ownership','—','50 wrong-owner rows']]
            return [[str(i),*v,'🟢 PASS' if i<6 else '🔴 BLOCKED'] for i,v in enumerate(values,1)]
        if self.n==3:return [[x[0],str(Decimal(x[3])/100),'Increase' if int(x[3])>0 else 'Decrease'] for x in r['account_effect_rows']]
        return [[x['order_id'],x['source_account_id'],'bad' if self.bad else x['candidate_account_id'],str(Decimal(x['amount_scaled_units'])/100),x['order_date']] for x in r['swaps']]


def test_live_verifier_requires_exact_order_ownership_and_signed_deltas():
    from cutoverguard.databricks import CloudError
    r=report()
    assert verify_queries(r,Runner(r))['status'].startswith('verified')
    with pytest.raises(CloudError,match='drilldown'):verify_queries(r,Runner(r,True))


def test_pass_labels_do_not_verify_wrong_displayed_values():
    from cutoverguard.databricks import CloudError
    class BadValues(Runner):
        def execute(self,sql):
            rows=super().execute(sql)
            if self.n==2:rows[0][2]='1499'
            return rows
    with pytest.raises(CloudError,match='displayed'):verify_queries(report(),BadValues(report()))


def test_native_widget_names_are_unique_and_duplicates_are_rejected():
    from scripts.build_tpch_dashboard import build,validate_structure
    from test_retail_dashboard import paired_reports
    d,f=paired_reports();body=build(report(),d,f,'a'*64)
    validate_structure(body)
    names=[x['widget']['name'] for x in body['pages'][0]['layout']]
    assert names.count('scale_orders')==1
    assert 'scale_order_drilldown' in names
    body['pages'][0]['layout'].append(deepcopy(body['pages'][0]['layout'][1]))
    with pytest.raises(ValueError,match='Duplicate widget'):validate_structure(body)
