"""Deterministic room updates: no model usage, no agent mentions.

Reserve each notification before sending. A crash/network ambiguity may omit a
notice, but never retries a possibly delivered notice. Status can be requested
again with a new human message. This does not affect task result processing.
"""
import hashlib
import json


class Notifications:
    def __init__(self, runner, adapter, config):
        self.runner, self.adapter, self.config = runner, adapter, config

    def post_once(self, key, room, text):
        ledger = self.runner.ledger
        key = 'notice:' + hashlib.sha256(key.encode()).hexdigest()
        with ledger.transaction() as db:
            if db.execute('SELECT 1 FROM metadata WHERE key=?', (key,)).fetchone(): return
            db.execute('INSERT INTO metadata VALUES(?,?)', (key, 'sending'))
        try:
            result = self.adapter.request('POST', f'/agents/{self.adapter.agent}/message',
                                          {'room_id': room, 'content': text})
            if not result.get('event_id'): raise ValueError('Missing receipt')
            outcome = 'sent'
        except Exception:
            outcome = 'uncertain'
        with ledger.transaction() as db:
            db.execute('UPDATE metadata SET value=? WHERE key=?', (outcome, key))
            if outcome == 'uncertain':
                ledger.event(db, None, 'notice_uncertain', {'room_id': room, 'notice_key': key}, self.runner.clock())

    def flush(self):
        if not self.config.get('status_messages', False): return
        ledger = self.runner.ledger
        paused = bool(ledger.rows("SELECT 1 FROM metadata WHERE key='dispatch_paused' AND value='true'"))
        pact = getattr(self.runner, 'pact', None)
        pact_rooms = set()
        if pact:
            pact_rooms = {s[k] for s in pact.workflows.values() for k in ('team_room_id', 'engine_room_id')}
            for row in ledger.rows('SELECT task_id FROM pact_tasks'):
                task = self.runner.task(row['task_id'])
                spec = pact.profile(task['id'])
                if spec['workflow'] not in pact.workflows: continue
                text = pact.presentation(task['id'])
                self.pact_post('pact-state:' + task['id'] + ':' + text, task['room_id'], text)
            for row in ledger.rows("SELECT id,task_id,data FROM events WHERE kind='pact_query'"):
                data = json.loads(row['data'])
                text = pact.evidence_summary(row['task_id']) if data['command'] == 'evidence' else pact.presentation(row['task_id'], detail=data['command'] == 'brief')
                self.pact_post('pact-query:' + str(row['id']), data['room_id'], text)
        for room in self.config['rooms']:
            if room in pact_rooms: continue
            tasks = ledger.rows('SELECT id,state,revision,repairs,commit_sha FROM tasks WHERE server_id=? AND room_id=? ORDER BY created DESC LIMIT 5',
                                (self.config['server_id'], room))
            lines = ['**Relay status**', 'Dispatch: **paused** (running work may finish).' if paused else 'Dispatch: **enabled**.']
            for task in tasks:
                line = f"• `{task['id'][:12]}` — **{task['state']}** · revision {task['revision']} · repairs {task['repairs']}"
                lines.append(line)
                key = json.dumps(task, sort_keys=True)
                detail = '\nDraft PR details prepared; nothing has been pushed or published.' if task['state'] == 'READY_FOR_APPROVAL' else ''
                if task['state'] == 'BLOCKED': detail = '\nStopped for operator attention; no blind retry.'
                self.post_once('task:' + key, room, '**Relay workflow**\n' + line + detail)
            if not tasks: lines.append('No tasks in this room yet.')
            for event in ledger.rows("SELECT id,data FROM events WHERE kind='control_received' ORDER BY id DESC LIMIT 20"):
                data = json.loads(event['data'])
                if data['room_id'] == room:
                    self.post_once('control:' + str(event['id']), room, '\n'.join(lines))

    def pact_post(self, key, room, text):
        # Keep paragraphs and copyable commands intact instead of cutting at
        # arbitrary characters. Command fences are short and always one block.
        chunks, current = [], ''
        for block in text.split('\n\n'):
            while len(block) > 1650:
                cut = block.rfind(' ', 0, 1650)
                if cut < 1: cut = 1650
                if current: chunks.append(current); current = ''
                chunks.append(block[:cut]); block = block[cut:].lstrip()
            candidate = current + ('\n\n' if current else '') + block
            if len(candidate) > 1650:
                chunks.append(current); current = block
            else: current = candidate
        if current: chunks.append(current)
        for index, chunk in enumerate(chunks):
            heading = f'**Plan / update · {index + 1} of {len(chunks)}**\n\n' if len(chunks) > 1 else ''
            self.post_once(key + ':formatted:' + str(index), room, heading + chunk)
