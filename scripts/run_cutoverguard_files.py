"""Prepare or execute public retail ownership-swaps or exact baseline through a UC Volume."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.file_ingestion import prepare,execute
from cutoverguard.databricks import CLIClient,StatementRunner,CloudError

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=ROOT/'public_retail_validation/ownership_swaps-bundle.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--volume',help='Existing catalog.schema.volume with READ/WRITE VOLUME privileges')
    p.add_argument('--warehouse-id')
    p.add_argument('--catalog',default='workspace'); p.add_argument('--schema',default='default')
    p.add_argument('--profile'); p.add_argument('--execute',action='store_true'); p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    try:
        if args.resume:
            state=json.loads((args.output/'state.json').read_text())
            if args.warehouse_id and args.warehouse_id!=state['warehouse_id']: raise CloudError('Resume warehouse differs.')
            if args.profile and args.profile!=state['profile']: raise CloudError('Resume profile differs.')
        else:
            if not args.volume or not args.warehouse_id: p.error('--volume and --warehouse-id required to prepare')
            state=prepare(json.loads(args.input.read_text()),args.output,args.volume,args.warehouse_id,args.catalog,args.schema,args.profile)
        if not args.execute:
            print('Prepared file ingestion; remote execution UNVERIFIED. Files/SQL:',args.output)
            return 0
        print('Historical public-retail extract with controlled candidate. Uploads unique files and creates new Delta tables; compute may start.')
        report=execute(args.output,StatementRunner(CLIClient(state['profile']),state['warehouse_id'],timeout=600))
        print('Full imported rows and SQL reconciliation match local oracle. Status:',report['status'])
        print('Report:',args.output/'report.json')
        return 0
    except (CloudError,ValueError,OSError,KeyError) as exc:
        print('File-ingestion run UNVERIFIED:',str(exc)); return 1
if __name__=='__main__': sys.exit(main())
