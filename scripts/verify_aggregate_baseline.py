"""Read-only SQL verification of the new five-row dashboard baseline."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cutover_app import validate_report
from cutoverguard.aggregate_baseline import compute,query
from cutoverguard.databricks import CLIClient,StatementRunner,CloudError


def verify(report,runner):
    report=validate_report(report)
    if report['execution'].get('transport')!='authenticated_cli_volume_csv' or report['execution'].get('warehouse_id')!=runner.warehouse_id:
        raise ValueError('Use the original verified file-run warehouse and report.')
    baseline=compute(report);expected=[]
    for order,row in enumerate(baseline['checks'],1):
        result='PASS' if row['pass'] else 'FAIL'
        expected.append([str(order),row['name'],result,row['source'],row['candidate'],'1',('🟢 ' if row['pass'] else '🔴 ')+result])
    count=baseline['ownership_findings'];result='BLOCKED' if count else 'NO OWNERSHIP FINDINGS'
    expected.append(['5','CutoverGuard: invoice ownership',result,'—',str(count)+' wrong-owner rows','1',('🔴 ' if count else '🟢 ')+result])
    tables=report['execution']['tables'];sql=query(tables['transactions'],tables['issues'])
    observed=runner.execute(sql)
    if observed!=expected:
        raise CloudError('Aggregate SQL output differs from exact local expectations; inspect query history. No verified evidence written.')
    return {'status':'verified','mode':'read_only_databricks_sql','run_id':report['run_id'],'warehouse_id':runner.warehouse_id,
            'statement_ids':runner.statement_ids,'checked_at':datetime.now(timezone.utc).isoformat(),
            'query_sha256':hashlib.sha256(sql.encode()).hexdigest(),'rows':observed,'data_kind':report['data_kind'],
            'scope':baseline['scope'],'checksum_convention':baseline['checksum_convention']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,default=ROOT/'databricks_files/retail-run-002/report.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--profile')
    args=p.parse_args()
    try:
        if args.output.exists():raise ValueError('Choose a fresh output filename.')
        report=json.loads(args.report.read_text());warehouse=validate_report(report)['execution']['warehouse_id']
        result=verify(report,StatementRunner(CLIClient(args.profile),warehouse,timeout=600))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as file:file.write(json.dumps(result,indent=2)+'\n')
        print('Read-only dashboard checks match exact local expectations. Compute may have started.')
        print('Evidence:',args.output)
    except (CloudError,ValueError,OSError,KeyError) as error:
        print('Aggregate baseline UNVERIFIED:',error);sys.exit(1)
