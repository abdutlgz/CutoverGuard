"""Update only the known draft's counters from cents to dollars, guarded by ETag."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_cutoverguard_native import (NativeClient,CloudError,StatementRunner,
    reports,dashboard,dashboard_differences,identifier,validate_sql,sanitize_configuration,configured_host)


def update(r,client,runner,output,host=''):
    output=Path(output);state_path=output/'state.json'
    state=json.loads(state_path.read_text())
    if state.get('host','')!=host:
        raise CloudError('Workspace host differs from saved native setup')
    dashboard_id=identifier(state,'dashboard_id');space_id=identifier(state,'space_id')
    if state.get('warehouse_id')!=runner.warehouse_id:
        raise CloudError('Warehouse differs from saved native setup')
    if state.get('source_run_ids')!={name:report['run_id'] for name,report in r.items()}:
        raise CloudError('Source fingerprints differ from saved native setup')
    path='/api/2.0/lakeview/dashboards/'+dashboard_id
    before=client.request('get',path)
    actual=json.loads(before['serialized_dashboard'])
    desired=dashboard(r,space_id)
    already_updated=not dashboard_differences(desired,actual)
    if not already_updated and dashboard_differences(dashboard(r,space_id,dollars=False),actual):
        raise CloudError('Draft has other edits; refusing to overwrite them. Inspect existing configuration.')
    if not already_updated and (not isinstance(before.get('etag'),str) or not before['etag']):
        raise CloudError('Draft GET omitted ETag; refusing an unguarded update')
    # Check actual dollar-valued query results before changing the draft.
    sql_verification=validate_sql(r,runner,output)
    if not already_updated:
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=output/('dashboard-before-dollars-'+stamp+'.json')
        backup.write_text(json.dumps(sanitize_configuration(actual),indent=2)+'\n')
        state.update(status='dashboard_update_unverified',ui_visual_verification='pending',
                     dashboard_update_backup=backup.name)
        state_path.write_text(json.dumps(state,indent=2)+'\n')
        client.request('patch',path+'?dataset_catalog=workspace&dataset_schema=default',
                       {'etag':before['etag'],'serialized_dashboard':json.dumps(desired)})
    received=json.loads(client.request('get',path)['serialized_dashboard'])
    differences=dashboard_differences(desired,received)
    if differences:
        raise CloudError('Updated draft read-back differs; inspect before retrying: '+differences[0]['path'])
    state.update(status='created_configuration_verified',dashboard_money_units='USD major units',
                 sql_verification=sql_verification,statement_ids=list(runner.statement_ids),
                 ui_visual_verification='pending')
    state_path.write_text(json.dumps(state,indent=2)+'\n')
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Verify live SQL then update the existing draft; never publishes')
    parser.add_argument('--output',type=Path,default=ROOT/'native_databricks/run-001')
    parser.add_argument('--profile')
    args=parser.parse_args()
    try:
        r=reports()
        if not args.apply:
            state=json.loads((args.output/'state.json').read_text())
            target=args.output/'dashboard-dollars.proposed.json'
            target.write_text(json.dumps(dashboard(r,identifier(state,'space_id')),indent=2)+'\n')
            print('Local proposal:',target,'No remote requests made.')
            return 0
        state=json.loads((args.output/'state.json').read_text())
        client=NativeClient(args.profile)
        update(r,client,StatementRunner(client,state['warehouse_id']),args.output,host=configured_host(args.profile))
        print('Existing draft updated and SQL/configuration verified. Refresh dashboard to verify USD counters. Not published.')
        return 0
    except (CloudError,ValueError,KeyError,OSError) as error:
        print('Dashboard update UNVERIFIED:',error)
        return 1


if __name__=='__main__':
    sys.exit(main())
