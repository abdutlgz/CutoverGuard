"""Discover UC Volumes or create one isolated managed Volume; never grant/drop."""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.databricks import CLIClient,StatementRunner,CloudError
from cutoverguard.sql import ident

def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value):
        raise CloudError('Catalog/schema must use simple identifiers.')
    return value

def list_volumes(runner,catalog,schema):
    name(catalog); name(schema)
    rows=runner.execute(f'SHOW VOLUMES IN {ident(catalog)}.{ident(schema)}')
    if any(len(row)!=2 or not all(isinstance(value,str) for value in row) for row in rows):
        raise CloudError('Unexpected SHOW VOLUMES response; no creation attempted.')
    return [catalog+'.'+schema+'.'+row[1] for row in rows]

def create_volume(runner,output,catalog,schema,profile=None,resume=False):
    name(catalog);name(schema);output=Path(output)
    if resume:
        state=json.loads(output.read_text())
        if state['catalog']!=catalog or state['schema']!=schema or state['warehouse_id']!=runner.warehouse_id or state['profile']!=profile:
            raise CloudError('Volume resume configuration differs.')
        volume=state['volume_name']
        if not re.fullmatch(r'cutoverguard_files_[a-f0-9]{16}',volume): raise CloudError('Invalid checkpoint Volume name.')
        # Read-back only; an uncertain CREATE is never repeated.
    else:
        volume='cutoverguard_files_'+uuid.uuid4().hex[:16]
        state={'catalog':catalog,'schema':schema,'warehouse_id':runner.warehouse_id,'profile':profile,'volume_name':volume,'volume':catalog+'.'+schema+'.'+volume,'creation_attempted':True,'status':'unverified','statement_ids':[]}
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x') as file: file.write(json.dumps(state,indent=2)+'\n')
    target='.'.join(ident(value) for value in (catalog,schema,volume))
    try:
        if not resume: runner.execute(f"CREATE VOLUME {target} COMMENT 'CutoverGuard public-retail controlled validation files'")
        rows=runner.execute(f'DESCRIBE VOLUME {target}')
        if len(rows)!=1 or len(rows[0])<6 or rows[0][0:3]!=[volume,catalog,schema] or rows[0][5]!='MANAGED':
            raise CloudError('Volume read-back differs; no upload should proceed.')
        state['status']='managed_volume_verified'
        return state
    except BaseException:
        state['status']='unverified'
        raise
    finally:
        state['statement_ids'] += [sid for sid in runner.statement_ids if sid not in state['statement_ids']]
        temp=output.with_suffix('.tmp');temp.write_text(json.dumps(state,indent=2)+'\n');temp.replace(output)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list',action='store_true');mode.add_argument('--create',action='store_true');mode.add_argument('--resume',action='store_true',help='GET/SQL describe only for checkpointed Volume; no repeated CREATE')
    p.add_argument('--warehouse-id',required=True);p.add_argument('--catalog',default='workspace');p.add_argument('--schema',default='default');p.add_argument('--profile')
    p.add_argument('--output',type=Path,default=ROOT/'databricks_files/volume-001.json')
    args=p.parse_args()
    try:
        runner=StatementRunner(CLIClient(args.profile),args.warehouse_id,timeout=600)
        if args.list:
            volumes=list_volumes(runner,args.catalog,args.schema)
            print(json.dumps({'volumes':volumes,'write_permissions_verified':False,'statement_ids':runner.statement_ids},indent=2))
        else:
            state=create_volume(runner,args.output,args.catalog,args.schema,args.profile,args.resume)
            print('Managed Volume read-back verified:',state['volume'])
            print('Checkpoint:',args.output)
            print('Next: supply this exact --volume to scripts/run_cutoverguard_files.py with a fresh --output directory.')
        return 0
    except (CloudError,ValueError,OSError,KeyError) as exc:
        print('Volume setup UNVERIFIED:',str(exc));return 1
if __name__=='__main__': sys.exit(main())
