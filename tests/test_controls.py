import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.ledger import Ledger
from app.runner import Runner
from app.workspace_manager import WorkspaceManager
from app.evidence import Evidence
from app.switch_adapter import EventRouter
from app.notifications import Notifications


class ControlsTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1] / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        root = Path(self.temp.name)
        self.ledger = Ledger(root / 'state' / 'relay.db')
        self.runner = Runner(self.ledger, WorkspaceManager(root / 'projects', self.ledger), Evidence(root / 'state'))
        self.config = {'server_id': 's', 'rooms': {'r': 'general'}, 'humans': ['human'],
                       'roles': {'planner': {'name': 'xpplanner', 'sender_id': 'planner'}}, 'status_messages': True}
        self.router = EventRouter(self.runner, self.config)

    def tearDown(self): self.temp.cleanup()

    def message(self, body, sender='human', source='m1', room='r'):
        return self.router.consume({'type': 'message', 'room_id': room, 'payload':
            {'sender': sender, 'sender_kind': 'user', 'message_id': source, 'body': body}})

    def test_pause_blocks_dispatch_and_duplicate_resume_does_not_unpause(self):
        self.message('@relay-runner /relay resume')
        self.message('@relay-runner /relay pause', source='m2')
        self.message('@relay-runner /relay start Build tested add function', source='m3')
        self.message('@relay-runner /relay resume')  # replay old command
        transport = Mock()
        self.assertFalse(self.runner.dispatch_one(transport))
        transport.send.assert_not_called()
        self.message('@relay-runner /relay resume', source='m4')
        transport.send.return_value = 'receipt'
        self.assertTrue(self.runner.dispatch_one(transport))

    def test_untrusted_quoted_and_other_room_commands_ignored(self):
        self.message('@relay-runner /relay pause', sender='stranger')
        self.message('> @relay-runner /relay pause')
        self.message('@relay-runner /relay pause', room='other')
        self.assertEqual(self.ledger.rows('SELECT * FROM metadata'), [])

    def test_status_has_no_agent_call_and_notice_is_not_repeated(self):
        self.message('@relay-runner /relay status')
        adapter = Mock(agent='runner')
        adapter.request.return_value = {'event_id': 'sent'}
        notices = Notifications(self.runner, adapter, self.config)
        notices.flush(); notices.flush()
        self.assertEqual(adapter.request.call_count, 1)
        self.assertEqual(self.ledger.rows('SELECT * FROM handoffs'), [])
        self.assertNotIn('@', adapter.request.call_args.args[2]['content'])

    def test_uncertain_notice_never_blindly_retries(self):
        self.message('@relay-runner /relay status')
        adapter = Mock(agent='runner')
        adapter.request.side_effect = ConnectionError('private failure details')
        notices = Notifications(self.runner, adapter, self.config)
        notices.flush(); notices.flush()
        self.assertEqual(adapter.request.call_count, 1)
        self.assertEqual(self.ledger.rows('SELECT value FROM metadata')[0]['value'], 'uncertain')
