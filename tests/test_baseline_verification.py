import pytest
from scripts.verify_aggregate_baseline import verify
from tests.test_historical_cloud_import import example_report
from cutoverguard.aggregate_baseline import compute
from cutoverguard.databricks import CloudError


class Runner:
    warehouse_id='test-warehouse'
    statement_ids=['invented-test-statement']
    def __init__(self,report,changed=False):self.report=report;self.changed=changed
    def execute(self,sql):
        baseline=compute(self.report);rows=[[str(i),r['name'],'PASS',r['source'],r['candidate'],'1','🟢 PASS'] for i,r in enumerate(baseline['checks'],1)]
        rows.append(['5','CutoverGuard: invoice ownership','BLOCKED','—','2 wrong-owner rows','1','🔴 BLOCKED'])
        if self.changed:rows[3][4]='wrong-checksum'
        return rows


def test_readonly_verification_requires_exact_checksum_and_values():
    report=example_report();report['execution']['warehouse_id']='test-warehouse'
    assert verify(report,Runner(report))['status']=='verified'
    with pytest.raises(CloudError):verify(report,Runner(report,True))


def test_wrong_warehouse_cannot_verify():
    report=example_report();report['execution']['warehouse_id']='other'
    with pytest.raises(ValueError,match='warehouse'):verify(report,Runner(report))
