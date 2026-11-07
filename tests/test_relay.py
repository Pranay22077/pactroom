import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.service import serve
from app.ledger import Ledger
from app.workspace_manager import WorkspaceManager
from app.runner import Runner
from app.evidence import Evidence
from app.switch_adapter import EventRouter, parse_sse


class FakeTransport:
    def __init__(self,fail=False):self.fail=fail;self.sent=[]
    def send(self,payload):
        self.sent.append(payload)
        if self.fail:raise ConnectionError('uncertain delivery')
        return 'receipt-'+payload['handoff_id']


class RelayTests(unittest.TestCase):
    def setUp(self):
        temp_root=Path(__file__).resolve().parents[1]/'.runtime'
        temp_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=temp_root);self.base=Path(self.tmp.name)
        self.ledger=Ledger(self.base/'state'/'relay.db')
        self.ws=WorkspaceManager(self.base/'projects',self.ledger)
        self.runner=Runner(self.ledger,self.ws,Evidence(self.base/'state'))
        self.transport=FakeTransport()

    def tearDown(self):self.tmp.cleanup()

    def start(self,room='a',source='first'):
        return self.runner.submit('server',room,'general',source,'Build a small tested Python library')

    def dispatch(self):
        self.assertTrue(self.runner.dispatch_one(self.transport))
        return self.transport.sent[-1]

    def complete(self,handoff,result,source=None):
        return self.runner.complete(handoff['handoff_id'],'server',handoff['room_id'],handoff['role'],
                                    result,source or 'event-'+handoff['handoff_id'])

    def commit(self,task_id,fail=False,zero=False):
        path=self.runner._path(self.runner.task(task_id))
        tests=path/'tests';tests.mkdir(exist_ok=True)
        (tests/'test_app.py').write_text('' if zero else
            'import unittest\nclass TestApp(unittest.TestCase):\n def test_sum(self): self.assertEqual(1+1,'+('3' if fail else '2')+')\n')
        def git(*args):return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True).stdout.strip()
        git('add','.')
        git('-c','user.name=Relay Test','-c','user.email=relay-test@example.invalid','commit','--allow-empty','-m','test candidate')
        return git('rev-parse','HEAD')

    def to_review(self):
        task=self.start();p=self.dispatch();self.complete(p,{'brief':'Implement sum with unittest coverage.'})
        c=self.dispatch();sha=self.commit(task);self.complete(c,{'commit_sha':sha})
        return task,self.dispatch(),sha

    def test_rooms_isolated_and_rename_stable(self):
        a=self.ws.ensure_workspace('s','a','general');b=self.ws.ensure_workspace('s','b','general')
        self.assertNotEqual(a,b)
        self.assertEqual(a,self.ws.ensure_workspace('s','a','renamed'))
        self.assertNotEqual(a,self.ws.ensure_workspace('other-server','a','general'))

    def test_review_report_outside_checkout_does_not_dirty_candidate(self):
        task, handoff, sha = self.to_review()
        report_dir = Path(handoff['artifact_dir'])
        self.assertNotIn(self.runner._path(self.runner.task(task)), report_dir.parents)
        (report_dir / 'review.md').write_text('Independent review report')
        outcome = self.complete(handoff, {'commit_sha': sha,
            'brief_hash': handoff['brief_hash'], 'verdict': 'approved', 'findings': 'Reviewed'})
        self.assertEqual(outcome, 'STEWARD')

    def test_rejection_reason_never_contains_arbitrary_error_text(self):
        from app.service import rejection_reason
        self.assertEqual(rejection_reason(ValueError('Commit all intended files before review; working tree is not clean')), 'dirty_candidate')
        self.assertEqual(rejection_reason(ValueError('private message content')), 'invalid_result_or_envelope')

    def test_send_explicit_result_contract_and_report_directory(self):
        from app.switch_adapter import SwitchAdapter
        task, handoff, sha = self.to_review()
        adapter = SwitchAdapter({'switch_url': 'http://localhost:8000', 'runner_agent_id': 'runner',
            'token_file': 'unused', 'rooms': {'a': 'general'}, 'roles': {'reviewer': {'name': 'xpreviewer'}}})
        with patch.object(adapter, 'request', return_value={'event_id': 'receipt'}) as request:
            self.assertEqual(adapter.send(handoff), 'receipt')
        body = request.call_args.args[2]['content']
        self.assertIn(handoff['artifact_dir'], body)
        self.assertIn('"verdict": "approved"', body)
        self.assertIn('Terminal output or a saved report alone is not delivery', body)

    def test_name_traversal_is_normalized(self):
        path=self.ws.ensure_workspace('s','../../a','../../outside')
        self.assertEqual(path.parent,self.ws.root)

    def test_symlink_rejected(self):
        outside=self.base/'outside';outside.mkdir()
        link=self.base/'link'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Symlink creation unavailable on Windows; rerun on Linux')
        with self.assertRaises(ValueError):WorkspaceManager(link,self.ledger)

    def test_missing_workspace_not_recreated(self):
        p=self.ws.ensure_workspace('s','a','a');p.rename(p.with_name('saved'))
        with self.assertRaises(ValueError):self.ws.ensure_workspace('s','a','a')

    def test_workspace_marker_tamper_rejected(self):
        p=self.ws.ensure_workspace('s','a','a');(p/'.relay-room.json').write_text('{}')
        with self.assertRaises(ValueError):self.ws.ensure_workspace('s','a','a')

    def test_duplicate_request_and_global_queue(self):
        first=self.start();self.assertEqual(first,self.start())
        second=self.start(room='b');self.assertNotEqual(first,second)
        self.assertEqual(self.runner.task(second)['state'],'NEW')
        self.assertEqual(len(self.ledger.rows('SELECT * FROM handoffs')),1)

    def test_cross_room_and_wrong_role_rejected(self):
        self.start();h=self.dispatch()
        with self.assertRaises(ValueError):self.runner.complete(h['handoff_id'],'server','other','planner',{'brief':'x'},'e')
        with self.assertRaises(ValueError):self.runner.complete(h['handoff_id'],'server','a','coder',{'brief':'x'},'e')

    def test_uncertain_delivery_never_retries(self):
        self.start()
        with self.assertRaises(ConnectionError):self.runner.dispatch_one(FakeTransport(True))
        self.runner.recover()
        self.assertFalse(self.runner.dispatch_one(self.transport))
        self.assertEqual(self.ledger.rows('SELECT status FROM handoffs')[0]['status'],'uncertain')

    def test_restart_during_send_marks_uncertain(self):
        self.start()
        with self.ledger.transaction() as db:db.execute("UPDATE handoffs SET status='sending'")
        Runner(self.ledger,self.ws,self.runner.evidence).recover()
        self.assertEqual(self.ledger.rows('SELECT status FROM handoffs')[0]['status'],'uncertain')

    def test_timeout_blocks_redispatch(self):
        self.start();self.dispatch()
        with self.ledger.transaction() as db:db.execute('UPDATE handoffs SET deadline=0')
        self.runner.recover()
        self.assertEqual(self.ledger.rows('SELECT status FROM handoffs')[0]['status'],'timed_out')
        self.assertFalse(self.runner.dispatch_one(self.transport))

    def test_full_delivery_and_duplicate_completion(self):
        task,h,sha=self.to_review()
        result={'verdict':'approved','commit_sha':sha,'brief_hash':h['brief_hash']}
        self.assertEqual(self.complete(h,result),'STEWARD')
        self.assertEqual(self.complete(h,result),'STEWARD')
        steward=self.dispatch();self.complete(steward,{'commit_sha':sha,'title':'Add library','body':'Tests passed'})
        self.assertEqual(self.runner.task(task)['state'],'READY_FOR_APPROVAL')
        self.assertEqual(self.ledger.rows('SELECT * FROM approvals'),[])
        self.runner.approve_publication(task,'operator','human-request-1')
        self.assertEqual(len(self.ledger.rows('SELECT * FROM approvals')),1)

    def test_stale_commit_rejected(self):
        task,h,sha=self.to_review();self.commit(task)
        with self.assertRaises(ValueError):self.complete(h,{'verdict':'approved','commit_sha':sha,'brief_hash':h['brief_hash']})

    def test_stale_brief_rejected(self):
        task,h,sha=self.to_review()
        with self.assertRaises(ValueError):self.complete(h,{'verdict':'approved','commit_sha':sha,'brief_hash':'old'})

    def test_failed_tests_return_to_coder(self):
        task=self.start();p=self.dispatch();self.complete(p,{'brief':'Test this'})
        c=self.dispatch();sha=self.commit(task,fail=True);self.complete(c,{'commit_sha':sha})
        self.assertEqual(self.runner.task(task)['state'],'IMPLEMENTING')
        self.assertEqual(self.runner.task(task)['repairs'],1)

    def test_empty_suite_is_not_success(self):
        task=self.start();p=self.dispatch();self.complete(p,{'brief':'Test this'})
        c=self.dispatch();sha=self.commit(task,zero=True);self.complete(c,{'commit_sha':sha})
        self.assertEqual(self.runner.task(task)['repairs'],1)

    def test_root_level_unittest_is_supported(self):
        task=self.start();self.complete(self.dispatch(),{'brief':'Test add'})
        h=self.dispatch();path=self.runner._path(self.runner.task(task))
        (path/'test_add.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_add(self): self.assertEqual(1+1,2)\n')
        subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(path),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','root tests'],check=True,capture_output=True)
        sha=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        self.complete(h,{'commit_sha':sha})
        self.assertEqual(self.runner.task(task)['state'],'REVIEWING')

    def test_repairs_bounded(self):
        task=self.start();self.complete(self.dispatch(),{'brief':'Test this'})
        for _ in range(3):
            h=self.dispatch();sha=self.commit(task,fail=True);self.complete(h,{'commit_sha':sha})
        self.assertEqual(self.runner.task(task)['state'],'BLOCKED')

    def test_events_append_only(self):
        self.start()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.ledger.transaction() as db:db.execute("UPDATE events SET kind='fake'")

    def test_result_requires_verified_sender_and_mentions(self):
        config={'server_id':'server','rooms':{'a':'general'},'humans':['human'],
                'roles':{r:{'name':n,'sender_id':r+'-id'} for r,n in
                         [('planner','xpplanner'),('coder','xpcoder'),('reviewer','xpreviewer'),('steward','xpsteward')]}}
        router=EventRouter(self.runner,config)
        event={'type':'message','room_id':'a','payload':{'sender':'human','message_id':'m1','body':'agent please work'}}
        self.assertEqual(router.consume(event),'ignored')
        event['payload']['body']='@xpplanner /relay start Build library'
        task=router.consume(event);self.assertEqual(task,router.consume(event))
        h=self.dispatch();event['payload'].update(sender='impostor',message_id='m2',body='RELAY_RESULT\n'+json.dumps({'handoff_id':h['handoff_id'],'result':{'brief':'fake'}}))
        with self.assertRaises(ValueError):router.consume(event)

    def test_subscription_and_cursor_checkpoint_precede_dispatch(self):
        """Fast replies must remain readable even when the ledger has no cursor."""
        task_id = self.start()
        config = {
            'live_enabled': True,
            'runner_agent_id': 'runner-test-id',
            'server_id': 'server',
            'rooms': {'a': 'general'},
            'humans': ['human'],
            'roles': {role: {'name': name, 'sender_id': f'{role}-id'} for role, name in (
                ('planner', 'xpplanner'), ('coder', 'xpcoder'),
                ('reviewer', 'xpreviewer'), ('steward', 'xpsteward'))},
        }
        sequence = []
        runner = self.runner
        ledger = self.ledger

        class EndTestStream(Exception):
            pass

        class FakeAdapter:
            def __init__(self, config):
                self.config = config
                self.first_handoff = None

            def frames(self, position):
                self.assert_initial_position(position)
                sequence.append('subscribed')
                yield 'connection_state', {
                    'agent_id': 'runner-test-id',
                    'position': {'room-a': 'cursor-zero'},
                }
                sequence.append('reading-fast-reply')
                assert self.first_handoff is not None, 'No handoff dispatched'
                yield 'message', {
                    'type': 'message', 'room_id': 'a',
                    'position': {'room-a': 'cursor-one'},
                    'payload': {
                        'sender': 'planner-id',
                        'message_id': 'planner-result-message',
                        'body': 'RELAY_RESULT\n' + json.dumps({
                            'handoff_id': self.first_handoff,
                            'result': {'brief': 'Build an independently tested library.'},
                        }),
                    },
                }
                raise EndTestStream()

            def assert_initial_position(self, position):
                assert position is None, 'Fresh ledger unexpectedly has a cursor'

            def send(self, payload):
                # This check would fail with the original pre-subscription send.
                assert sequence and sequence[0] == 'subscribed'
                rows = ledger.rows("SELECT value FROM metadata WHERE key='switch_position'")
                expected = 'cursor-zero' if self.first_handoff is None else 'cursor-one'
                assert rows and json.loads(rows[0]['value']) == {'room-a': expected}
                sequence.append('dispatch-' + payload['role'])
                if self.first_handoff is None:
                    self.first_handoff = payload['handoff_id']
                return 'receipt-' + payload['handoff_id']

        with patch('app.service.SwitchAdapter', FakeAdapter), patch('app.service.worker_lock'):
            with self.assertRaises(EndTestStream):
                serve(runner, config, self.base / 'state')
        self.assertEqual(sequence[:3], ['subscribed', 'dispatch-planner', 'reading-fast-reply'])
        self.assertEqual(runner.task(task_id)['state'], 'IMPLEMENTING')
        checkpoint = ledger.rows("SELECT value FROM metadata WHERE key='switch_position'")
        self.assertEqual(json.loads(checkpoint[0]['value']), {'room-a': 'cursor-one'})

    def test_failed_handshake_never_dispatches(self):
        self.start()
        config = {
            'live_enabled': True, 'runner_agent_id': 'correct-runner',
            'server_id': 'server', 'rooms': {'a': 'general'}, 'humans': [],
            'roles': {role: {'name': name, 'sender_id': role + '-id'} for role, name in (
                ('planner', 'xpplanner'), ('coder', 'xpcoder'),
                ('reviewer', 'xpreviewer'), ('steward', 'xpsteward'))},
        }
        sent = []

        class BadHandshake:
            def __init__(self, config):
                pass

            def frames(self, position):
                yield 'connection_state', {'agent_id': 'wrong-runner'}

            def send(self, payload):
                sent.append(payload)
                raise AssertionError('Must not dispatch before authenticating stream')

        with patch('app.service.SwitchAdapter', BadHandshake), patch('app.service.worker_lock'):
            with self.assertRaisesRegex(ValueError, 'authenticated Runner connection_state'):
                serve(self.runner, config, self.base / 'state')
        self.assertEqual(sent, [])

    def test_sse_parser(self):
        stream=io.BytesIO(b': keepalive\n\nevent: message\nid: 3\ndata: {"x":1}\n\n')
        self.assertEqual(list(parse_sse(stream)),[('keepalive',{}),('message',{'x':1})])

    def test_late_planner_result_requires_explicit_reconciliation(self):
        task=self.start(); h=self.dispatch()
        with self.ledger.transaction() as db:
            db.execute('UPDATE handoffs SET deadline=0 WHERE id=?',(h['handoff_id'],))
        self.runner.expire()
        with self.assertRaises(ValueError):self.complete(h,{'brief':'verified late brief'})
        state=self.runner.complete(h['handoff_id'],'server','a','planner',
                                   {'brief':'verified late brief'},'late-event',
                                   reconciliation_actor='operator')
        self.assertEqual(state,'IMPLEMENTING')
        self.assertEqual(len(self.transport.sent),1)  # no second Planner request
        self.assertEqual(len(self.ledger.rows("SELECT * FROM events WHERE kind='completion_reconciled'")),1)
        self.assertEqual(self.complete(h,{'brief':'verified late brief'},'late-event'),'IMPLEMENTING')

    def test_reconciliation_cannot_accept_wrong_room(self):
        self.start();h=self.dispatch()
        with self.ledger.transaction() as db:db.execute("UPDATE handoffs SET status='timed_out'")
        with self.assertRaises(ValueError):
            self.runner.complete(h['handoff_id'],'server','other','planner',{'brief':'x'},'late',reconciliation_actor='operator')

    def test_agent_blocker_stops_instead_of_waiting_for_timeout(self):
        task=self.start();h=self.dispatch()
        self.assertEqual(self.complete(h,{'status':'blocked','blocker':'Needs a project dependency'}),'BLOCKED')
        self.assertEqual(self.runner.task(task)['state'],'BLOCKED')
        self.assertFalse(self.runner.dispatch_one(self.transport))

    def test_operator_recheck_reuses_commit_without_resetting_repairs(self):
        task,h,sha=self.to_review()
        self.complete(h,{'status':'blocked','blocker':'Operator must correct test profile'})
        with self.ledger.transaction() as db:db.execute('UPDATE tasks SET repairs=2 WHERE id=?',(task,))
        self.assertEqual(self.runner.recheck_blocked_candidate(task,'operator','Test profile corrected'),'REVIEWING')
        self.assertEqual(self.runner.task(task)['commit_sha'],sha)
        self.assertEqual(self.runner.task(task)['repairs'],2)
        self.assertEqual(self.ledger.rows("SELECT role FROM handoffs WHERE status='pending'")[0]['role'],'reviewer')

    def test_message_rejection_does_not_kill_worker(self):
        config={'live_enabled':True,'runner_agent_id':'r','server_id':'server',
                'rooms':{'a':'general'},'humans':[],
                'roles':{role:{'name':role,'sender_id':role} for role in ('planner','coder','reviewer','steward')}}
        class Adapter:
            def __init__(self,config):pass
            def send(self,payload):raise AssertionError('No work expected')
            def frames(self,position):
                yield 'connection_state',{'agent_id':'r','position':{'s':0}}
                yield 'message',{'type':'message','room_id':'a','position':{'s':1},
                                'payload':{'sender':'planner','message_id':'bad-json','body':'RELAY_RESULT\nnot json'}}
                yield 'keepalive',{'position':{'s':2}}
        with patch('app.service.SwitchAdapter',Adapter), patch('app.service.worker_lock'):
            serve(self.runner,config,self.base/'state')
        rows=self.ledger.rows("SELECT data FROM events WHERE kind='message_rejected'")
        self.assertEqual(len(rows),1)
        self.assertNotIn('not json',rows[0]['data'])
        self.assertEqual(json.loads(self.ledger.rows('SELECT value FROM metadata')[0]['value']),{'s':2})


if __name__=='__main__':unittest.main()
