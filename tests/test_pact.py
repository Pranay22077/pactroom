import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.ledger import Ledger
from app.workspace_manager import WorkspaceManager
from app.evidence import Evidence
from app.runner import Runner
from app.switch_adapter import EventRouter, SwitchAdapter
from app.notifications import Notifications
from app.pact import validate_workflows


def config():
    return {'server_id': 's', 'runner_agent_id': 'runner', 'rooms': {'old': 'general', 'team': 'pactroom-team', 'engine': 'pactroom-engine'},
            'humans': ['operator'], 'roles': {r: {'name': 'xp' + r, 'sender_id': 'xp-' + r} for r in ('planner','coder','reviewer','steward')},
            'status_messages': True, 'workflows': {'pactroom': {
                'enabled': True, 'team_room_id': 'team', 'engine_room_id': 'engine', 'test_profile': 'python-unittest',
                'required_confirmers': ['client','operations'], 'humans': {'delivery_lead': 'lead', 'client': 'client', 'operations': 'ops'},
                'roles': {r: {'name': 'pact-' + r, 'sender_id': 'pact-' + r} for r in ('planner','coder','reviewer','steward')}}}}


class PactTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1] / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / 'state' / 'relay.db')
        self.runner = Runner(self.ledger, WorkspaceManager(self.root / 'projects', self.ledger), Evidence(self.root / 'state'))
        self.config = config()
        self.router = EventRouter(self.runner, self.config)
        self.counter = 0
        self.transport = Mock()
        self.transport.send.return_value = 'receipt'

    def tearDown(self): self.temp.cleanup()

    def message(self, body, actor='lead', room='team', source=None, kind='user'):
        self.counter += 1
        return self.router.consume({'type':'message','room_id':room,'payload':{'sender':actor,'sender_kind':kind,'message_id':source or str(self.counter),'body':body}})

    def start(self): return self.message('@relay-runner /pact start Build addition')

    def dispatch(self):
        self.assertTrue(self.runner.dispatch_one(self.transport))
        return self.transport.send.call_args.args[0]

    def result(self, payload, result, actor=None, room='engine'):
        return self.message('RELAY_RESULT\n' + json.dumps({'handoff_id':payload['handoff_id'],'result':result}), actor or payload['expected_sender'], room)

    def plan(self, unresolved=None):
        task = self.start(); p = self.dispatch()
        self.assertEqual(p['room_id'], 'engine')
        self.result(p, {'brief': {'objective':'Addition','requirements':[{'id':'R1','owner':'client','rule':'Add numbers','acceptance':'1+1=2'}], 'unresolved_decisions': unresolved or []}})
        return task

    def confirm(self, task, actor):
        t = self.runner.task(task)
        return self.message(f"@relay-runner /pact confirm {task} v{t['revision']} {t['brief_hash']}", actor)

    def commit(self, task):
        path = self.runner._path(self.runner.task(task))
        (path / 'tests').mkdir()
        (path / 'tests' / 'test_app.py').write_text('import unittest\nclass TestApp(unittest.TestCase):\n def test_add(self): self.assertEqual(1+1,2)\n')
        def git(*args): return subprocess.run(['git','-C',str(path),*args], check=True, capture_output=True, text=True).stdout.strip()
        git('add','.')
        git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','candidate')
        return git('rev-parse','HEAD')

    def test_full_delivery_actual_tests_review_copy_and_acceptance(self):
        task = self.plan()
        self.assertEqual(self.runner.task(task)['state'], 'WAITING_FOR_CONFIRMATION')
        self.confirm(task, 'client'); self.assertFalse(self.runner.dispatch_one(self.transport))
        self.confirm(task, 'ops'); builder = self.dispatch()
        sha = self.commit(task)
        self.result(builder, {'commit_sha':sha,'requirement_tests':{'R1':['test_app.TestApp.test_add']}})
        review = self.dispatch()
        self.assertNotEqual(review['workspace'], builder['workspace'])
        self.assertTrue(Path(review['workspace'], 'tests', 'test_app.py').exists())
        self.assertTrue(review['test_evidence']['passed'])
        self.result(review, {'commit_sha':sha,'brief_hash':review['brief_hash'],'verdict':'approved','findings':'Checked R1','requirement_findings':{'R1':'Code and actual test inspected'}})
        steward = self.dispatch()
        self.assertTrue(steward['test_evidence']['passed'])
        self.assertEqual(steward['review_evidence']['verdict'],'approved')
        self.assertIn('R1', steward['requirement_tests'])
        self.result(steward, {'commit_sha':sha,'title':'Addition','body':'Delivered'})
        t = self.runner.task(task)
        self.assertEqual(t['state'], 'READY_FOR_ACCEPTANCE')
        command = f"@relay-runner /pact accept {task} v1 {t['brief_hash']} {sha}"
        with self.assertRaises(ValueError): self.message(command, 'client')
        self.message(command)
        self.assertEqual(self.runner.task(task)['state'], 'ACCEPTED')
        self.assertEqual(self.ledger.rows('SELECT * FROM approvals'), [])
        summary = self.router.pact.evidence_summary(task)
        self.assertIn('Tests: **Passed**', summary)
        self.assertIn('Independent review: **Approved**', summary)
        self.assertIn('Human acceptance: **Recorded', summary)
        self.assertNotIn('test_app.TestApp', summary)
        self.assertLess(len(summary), 1650)
        self.message(f'@relay-runner /pact evidence {task}')
        adapter = Mock(agent='runner'); adapter.request.return_value = {'event_id':'notice'}
        Notifications(self.runner, adapter, self.config).flush()
        evidence_posts = [c.args[2]['content'] for c in adapter.request.call_args_list if 'Delivery evidence' in c.args[2]['content']]
        self.assertEqual(evidence_posts, [summary])
        self.message(f'@relay-runner /pact change {task} Add subtraction')
        summary = self.router.pact.evidence_summary(task)
        self.assertNotIn('Tests: **Passed**', summary)
        self.assertNotIn('Independent review: **Approved**', summary)
        self.assertNotIn('Human acceptance: **Recorded', summary)

    def test_wrong_identity_room_and_quoted_commands(self):
        self.assertEqual(self.message('> @relay-runner /pact start hello'), 'ignored')
        with self.assertRaises(ValueError): self.message('@relay-runner /pact start hello', 'stranger')
        with self.assertRaises(ValueError): self.message('@relay-runner /pact start hello', kind='platform')
        task = self.start(); p = self.dispatch()
        with self.assertRaises(ValueError): self.result(p, {}, actor='pact-coder')
        with self.assertRaises(ValueError): self.result(p, {}, room='team')
        self.assertEqual(self.message('@relay-runner /relay start bypass', 'operator'), 'ignored')
        self.assertEqual(len(self.ledger.rows('SELECT * FROM tasks')), 1)

    def test_unresolved_decision_cannot_confirm(self):
        task = self.plan(['Should late cancellations promote?'])
        self.assertEqual(self.runner.task(task)['state'], 'WAITING_FOR_DECISION')
        with self.assertRaises(ValueError): self.confirm(task, 'client')

    def test_replayed_start_confirmation_and_restart(self):
        task = self.plan(); t = self.runner.task(task)
        body = f"@relay-runner /pact confirm {task} v1 {t['brief_hash']}"
        self.message(body,'client',source='confirm')
        self.router = EventRouter(self.runner, self.config)
        self.assertEqual(self.message(body,'client',source='confirm'), 'duplicate')
        self.confirm(task,'ops')
        self.assertEqual(self.confirm(task,'client'), 'duplicate')
        self.assertEqual(len(self.ledger.rows("SELECT * FROM handoffs WHERE status='pending'")), 1)

    def test_change_while_sent_prevents_old_result_advancing(self):
        task = self.plan(); self.confirm(task,'client'); self.confirm(task,'ops'); builder = self.dispatch()
        self.message(f'@relay-runner /pact change {task} Add subtraction', 'client')
        self.assertEqual(self.runner.task(task)['state'], 'CHANGE_PENDING')
        self.result(builder, {'commit_sha':'obsolete'})
        t = self.runner.task(task)
        self.assertEqual(t['revision'], 2)
        self.assertEqual(t['state'], 'PLANNING')
        self.assertIsNone(t['brief_hash'])
        with self.assertRaises(ValueError): self.result(builder, {'commit_sha':'obsolete'})

    def test_change_pending_unsent_cancels_without_call(self):
        task = self.start()
        self.message(f'@relay-runner /pact change {task} Updated objective')
        p = self.dispatch(); self.assertEqual(p['revision'], 2)
        self.assertEqual(len(self.ledger.rows("SELECT * FROM handoffs WHERE status='superseded'")), 1)

    def test_stale_hash_and_cross_task_confirm_rejected(self):
        task = self.plan()
        with self.assertRaises(ValueError): self.message(f'@relay-runner /pact confirm {task} v1 wrong', 'client')
        with self.assertRaises(ValueError): self.message(f'@relay-runner /pact status {task}', 'client', 'engine')
        with self.assertRaises(ValueError): self.confirm(task, 'lead')

    def test_global_queue_confirmation_does_not_overlap_legacy(self):
        task = self.plan()
        self.runner.submit('s','old','general','old-start','Old task')
        self.confirm(task,'client'); self.confirm(task,'ops')
        self.assertEqual(self.runner.task(task)['state'], 'BUILD_QUEUED')
        self.assertEqual(len(self.ledger.rows("SELECT * FROM handoffs WHERE status='pending'")), 1)

    def test_notifications_team_only_dedup_and_no_protocol(self):
        task = self.plan()
        adapter = Mock(agent='runner'); adapter.request.return_value = {'event_id':'notice'}
        notices = Notifications(self.runner,adapter,self.config)
        notices.flush(); count = adapter.request.call_count; notices.flush()
        self.assertEqual(count,adapter.request.call_count)
        for call in adapter.request.call_args_list:
            self.assertNotEqual(call.args[2]['room_id'],'engine')
            self.assertNotIn('RELAY_RESULT',call.args[2]['content'])
        self.assertIn('/pact confirm', '\n'.join(c.args[2]['content'] for c in adapter.request.call_args_list))

    def test_confirmation_updates_are_compact_and_full_brief_remains_available(self):
        task = self.plan()
        adapter = Mock(agent='runner'); adapter.request.return_value = {'event_id':'notice'}
        notices = Notifications(self.runner, adapter, self.config)
        notices.flush()
        self.assertIn('How we will check it', adapter.request.call_args.args[2]['content'])
        adapter.reset_mock()
        self.confirm(task, 'client')
        notices.flush()
        text = '\n'.join(c.args[2]['content'] for c in adapter.request.call_args_list)
        self.assertIn('Client: Confirmed', text)
        self.assertIn('Operations: Waiting for approval', text)
        self.assertNotIn('How we will check it', text)
        self.assertIn('/pact brief ' + task, text)
        self.assertFalse(self.runner.dispatch_one(self.transport))
        count = adapter.request.call_count
        notices.flush()
        self.assertEqual(count, adapter.request.call_count)
        self.assertIn('How we will check it', self.router.pact.presentation(task, detail=True))
        self.message(f'@relay-runner /pact change {task} Add subtraction')
        p = self.dispatch()
        self.result(p, {'brief': {'objective':'Arithmetic','requirements':[{'id':'R1','owner':'both','rule':'Subtract numbers','acceptance':'2-1=1'}], 'unresolved_decisions': []}})
        text = self.router.pact.presentation(task)
        self.assertIn('How we will check it', text)
        self.assertIn('Client: Waiting for approval', text)

    def test_config_rejects_shared_rooms_or_human_agent_overlap(self):
        bad=copy.deepcopy(self.config); bad['workflows']['pactroom']['engine_room_id']='team'
        with self.assertRaises(ValueError): validate_workflows(bad)
        bad=copy.deepcopy(self.config); bad['workflows']['pactroom']['humans']['client']='pact-coder'
        with self.assertRaises(ValueError): validate_workflows(bad)

    def test_migration_preserves_legacy_data_and_is_repeatable(self):
        self.runner.submit('s','old','general','source','Keep me')
        before=self.ledger.rows('SELECT * FROM tasks')
        EventRouter(self.runner,self.config); EventRouter(self.runner,self.config)
        self.assertEqual(before,self.ledger.rows('SELECT * FROM tasks'))

    def test_resume_blocked_builder_preserves_agreement_and_assigns_branch(self):
        task=self.plan(); self.confirm(task,'client'); self.confirm(task,'ops'); p=self.dispatch()
        self.assertEqual(p['task_branch'],'relay/'+task)
        self.result(p, {'status':'blocked','blocker':'Missing branch in older protocol'})
        before=self.runner.task(task)
        approvals=self.ledger.rows('SELECT * FROM pact_confirmations')
        self.assertEqual(self.router.pact.resume_blocked_builder(task,'operator','Supply designated branch'), 'IMPLEMENTING')
        after=self.runner.task(task)
        for field in ['revision','brief_hash','repairs']: self.assertEqual(before[field],after[field])
        self.assertEqual(approvals,self.ledger.rows('SELECT * FROM pact_confirmations'))
        with self.assertRaises(ValueError):self.router.pact.resume_blocked_builder(task,'operator','Duplicate')
        self.assertEqual(self.dispatch()['task_branch'],'relay/'+task)

    def test_resume_refuses_missing_confirmations(self):
        task=self.plan();self.confirm(task,'client');self.confirm(task,'ops');p=self.dispatch()
        self.result(p,{'status':'blocked','blocker':'setup'})
        with self.ledger.transaction() as db:db.execute('DELETE FROM pact_confirmations WHERE task_id=?',(task,))
        with self.assertRaises(ValueError):self.router.pact.resume_blocked_builder(task,'operator','fix')

    def test_inspection_only_requirement_needs_explicit_limitation(self):
        task=self.plan();t=self.runner.task(task)
        result={'requirement_tests':{'R1':[]}}
        with self.assertRaises(ValueError):self.router.pact.validate_result(t,'coder',result)
        result['uncovered_requirements']={'R1':'Requires independent inspection; not established by automated tests.'}
        self.assertEqual(self.router.pact.validate_result(t,'coder',result),result)

    def test_builder_mapping_required_before_tests(self):
        task=self.plan(); self.confirm(task,'client'); self.confirm(task,'ops'); p=self.dispatch()
        with self.assertRaises(ValueError): self.result(p, {'commit_sha':'0'*40})
        self.assertEqual(self.ledger.rows("SELECT * FROM events WHERE kind='tests_recorded'"), [])

    def test_new_brief_invalidates_old_confirmation(self):
        task=self.plan(); old=self.runner.task(task); self.confirm(task,'client')
        self.message(f'@relay-runner /pact change {task} Change the acceptance example', 'ops')
        p=self.dispatch()
        self.result(p, {'brief':{'objective':'Revised','requirements':[{'id':'R1','owner':'client','rule':'Add numbers','acceptance':'2+2=4'}],'unresolved_decisions':[]}})
        with self.assertRaises(ValueError): self.message(f"@relay-runner /pact confirm {task} v1 {old['brief_hash']}",'ops')
        self.confirm(task,'ops')
        self.assertFalse(self.runner.dispatch_one(self.transport))
        self.confirm(task,'client'); self.assertEqual(self.dispatch()['revision'],2)

    def test_bounded_repairs_keep_confirmations_and_stop(self):
        task=self.plan(); self.confirm(task,'client'); self.confirm(task,'ops')
        sha=self.commit(task)
        for attempt in range(3):
            p=self.dispatch()
            self.result(p, {'commit_sha':sha,'requirement_tests':{'R1':['test_app.TestApp.test_add']}})
            review=self.dispatch()
            self.result(review, {'commit_sha':sha,'brief_hash':review['brief_hash'],'verdict':'changes_requested',
                                 'findings':'Fix behavior', 'requirement_findings':{'R1':'Missing behavior'}})
        self.assertEqual(self.runner.task(task)['state'],'BLOCKED')
        self.assertFalse(self.runner.dispatch_one(self.transport))

    def test_failed_tests_never_reach_reviewer(self):
        task=self.plan(); self.confirm(task,'client'); self.confirm(task,'ops'); p=self.dispatch()
        sha=self.commit(task)
        path=self.runner._path(self.runner.task(task))
        (path/'tests'/'test_app.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail("deliberate test")\n')
        subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(path),'-c','user.name=T','-c','user.email=t@example.invalid','commit','-m','failing test'],check=True,capture_output=True)
        sha=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        self.result(p, {'commit_sha':sha,'requirement_tests':{'R1':['test_app.T.test_bad']}})
        self.assertEqual(self.dispatch()['role'],'coder')
        self.assertEqual(self.ledger.rows('SELECT * FROM reviews'),[])

    def test_changed_candidate_cannot_be_accepted(self):
        task=self.plan(); self.confirm(task,'client'); self.confirm(task,'ops'); p=self.dispatch(); sha=self.commit(task)
        self.result(p, {'commit_sha':sha,'requirement_tests':{'R1':['test_app.TestApp.test_add']}})
        r=self.dispatch(); self.result(r, {'commit_sha':sha,'brief_hash':r['brief_hash'],'verdict':'approved','requirement_findings':{'R1':'checked'}})
        s=self.dispatch(); self.result(s, {'commit_sha':sha,'body':'ready'})
        (self.runner._path(self.runner.task(task))/'unreviewed.txt').write_text('new work')
        with self.assertRaises(ValueError): self.message(f"@relay-runner /pact accept {task} v1 {r['brief_hash']} {sha}")
        self.assertEqual(self.ledger.rows('SELECT * FROM pact_acceptances'), [])


if __name__ == '__main__': unittest.main()
