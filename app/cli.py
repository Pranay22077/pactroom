import argparse
import json
from pathlib import Path
from .ledger import Ledger
from .workspace_manager import WorkspaceManager
from .runner import Runner
from .evidence import Evidence


def main():
    parser=argparse.ArgumentParser(description='Relay operator CLI; do not expose to untrusted agents')
    parser.add_argument('--root',type=Path,default=Path.home()/'relay-projects')
    parser.add_argument('--state',type=Path,default=Path.home()/'relay-state')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('status')
    view=sub.add_parser('viewer');view.add_argument('--port',type=int,default=8765)
    room=sub.add_parser('workspace')
    for name in ('server','room','name'):room.add_argument('--'+name,required=True)
    task=sub.add_parser('submit')
    for name in ('server','room','name','source','request'):task.add_argument('--'+name,required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('task_id')
    for name in ('serve','probe-switch'):
        cmd=sub.add_parser(name);cmd.add_argument('--config',type=Path,required=True)
        if name=='serve':cmd.add_argument('--until-task',help='Stop before dispatching another task when this task reaches readiness or a blocker')
    args=parser.parse_args()
    ledger=Ledger(args.state/'relay.db');ws=WorkspaceManager(args.root,ledger)
    runner=Runner(ledger,ws,Evidence(args.state))
    if args.command=='init': print('Relay initialized; existing projects and Switch deployment unchanged.')
    elif args.command=='workspace': print(ws.ensure_workspace(args.server,args.room,args.name))
    elif args.command=='submit': print(runner.submit(args.server,args.room,args.name,args.source,args.request))
    elif args.command=='status':
        print(json.dumps({'rooms':ledger.rows('SELECT * FROM rooms'),
                          'tasks':ledger.rows('SELECT id,room_id,state,revision FROM tasks'),
                          'handoffs':ledger.rows('SELECT id,role,status FROM handoffs')},indent=2))
    elif args.command=='inspect':
        print(json.dumps({'task':runner.task(args.task_id),
                          'events':ledger.rows('SELECT * FROM events WHERE task_id=? ORDER BY id',(args.task_id,))},indent=2))
    elif args.command=='probe-switch':
        from .switch_adapter import SwitchAdapter
        print(json.dumps(SwitchAdapter(json.loads(args.config.read_text())).rooms(),indent=2))
    elif args.command=='serve':
        from .service import serve
        from .switch_adapter import StreamReconciliationRequired
        try:
            serve(runner,json.loads(args.config.read_text()),args.state,args.until_task)
        except StreamReconciliationRequired:
            print('Switch history gap or connection eviction: operator reconciliation required. No automatic resend.')
            raise SystemExit(78)
    elif args.command=='viewer':
        from .viewer import serve_viewer
        serve_viewer(args.state,args.port)


if __name__=='__main__': main()
