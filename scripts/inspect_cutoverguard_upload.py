"""Read-only Volume/run-directory inspection; never upload, mkdir or clear a checkpoint."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cutoverguard.databricks import CloudError
from cutoverguard.file_ingestion import classify_cli_error


def listing(path,profile=None,run=subprocess.run):
    command=['databricks','fs','ls','dbfs:'+path,'--output','json']
    if profile:
        command+=['--profile',profile]
    try:
        result=run(command,capture_output=True,text=True,timeout=30)
    except FileNotFoundError:
        return {'status':'cli_unavailable'}
    except subprocess.TimeoutExpired:
        return {'status':'timed_out'}
    if result.returncode:
        return {'status':classify_cli_error(result.stderr)}
    try:
        rows=json.loads(result.stdout)
    except (ValueError,TypeError):
        return {'status':'unexpected_response'}
    if not isinstance(rows,list):
        return {'status':'unexpected_response'}
    names=[]
    for row in rows:
        name=row if isinstance(row,str) else row.get('path',row.get('name')) if isinstance(row,dict) else None
        if not isinstance(name,str):
            return {'status':'unexpected_response'}
        names.append(name.rstrip('/').rsplit('/',1)[-1])
    return {'status':'accessible','entry_count':len(names),
            'prepared_files_present':{name:name in names for name in ('manifest.csv','profiles.csv','transactions.csv')}}


def inspect(output,run=subprocess.run):
    output=Path(output)
    state=json.loads((output/'state.json').read_text())
    remote=state.get('remote_directory','')
    if not re.fullmatch(r'/Volumes/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*/cutoverguard_[a-f0-9]{16}',remote):
        raise CloudError('Invalid generated Volume directory in checkpoint')
    profile=state.get('profile')
    if profile is not None and not isinstance(profile,str):
        raise CloudError('Invalid profile in checkpoint')
    diagnostic={'checkpoint_status':state.get('status'),
                'attempted_stage':state.get('attempted'),
                'completed_stages':state.get('completed'),
                'volume_directory':remote.rsplit('/',1)[0],
                'run_directory':remote,
                'volume_listing':listing(remote.rsplit('/',1)[0],profile,run),
                'run_listing':listing(remote,profile,run),
                'write_permissions_verified':False,
                'remote_file_contents_verified':False,
                'checkpoint_modified':False}
    folder=output/'diagnostics';folder.mkdir(exist_ok=True)
    (folder/'upload-inspection.json').write_text(json.dumps(diagnostic,indent=2)+'\n')
    return diagnostic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(inspect(args.output),indent=2))
        return 0
    except (CloudError,ValueError,OSError,KeyError) as error:
        print('Upload inspection UNVERIFIED:',error)
        return 1


if __name__=='__main__':
    sys.exit(main())
