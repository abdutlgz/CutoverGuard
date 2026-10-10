"""Execute synthetic CutoverGuard reconciliation in new Databricks Delta tables."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.databricks import CLIClient, StatementRunner, CloudError, run_cloud


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warehouse-id',required=True)
    parser.add_argument('--profile')
    parser.add_argument('--catalog',default='workspace')
    parser.add_argument('--schema',default='default')
    parser.add_argument('--input',type=Path,help='Synthetic input bundle JSON; defaults to defective fixture')
    parser.add_argument('--fixture',default='defective',help='Used only without --input; run fixed too for correction evidence')
    parser.add_argument('--output',type=Path,default=ROOT/'cutover_results'/'databricks-report.json')
    parser.add_argument('--timeout',type=int,default=180,help='Per-statement timeout seconds')
    args=parser.parse_args()
    if not 1<=args.timeout<=900:
        parser.error('--timeout must be 1..900')
    try:
        from cutoverguard.fixtures import fixture
        bundle=json.loads(args.input.read_text()) if args.input else fixture(args.fixture)
        # Reserve output before compute to avoid overwriting evidence from earlier runs.
        output=args.output.resolve()
        output.parent.mkdir(parents=True,exist_ok=True)
        evidence=output.with_suffix('.execution.json')
        if output.exists() or evidence.exists():
            raise CloudError('Output/evidence already exists; choose a new --output path.')
        runner=StatementRunner(CLIClient(args.profile),args.warehouse_id,args.timeout)
        print('Synthetic cloud run: may start compute. Creates new tables only; no tables dropped/overwritten.')
        report=run_cloud(bundle,runner,args.catalog,args.schema,evidence,print)
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print('Databricks SQL findings and exact monetary results match the local oracle.')
        print('Report:',output)
        print('Execution evidence:',evidence)
        print('Status:',report['status'],'Run:',report['run_id'])
        return 0
    except (CloudError,ValueError,OSError) as exc:
        print('Cloud reconciliation UNVERIFIED:',str(exc))
        return 1
    except KeyboardInterrupt:
        print('Interrupted; inspect evidence and query history. Cancellation is requested when an active statement ID is known.')
        return 130


if __name__=='__main__':
    sys.exit(main())
