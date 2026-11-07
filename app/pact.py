"""Opt-in PactRoom extension. No network calls; identity comes from EventRouter.

Additive extension tables retain ledger v1 compatibility. Snapshot workflow authority
per task so a config reload cannot silently change who owns an existing agreement.
"""
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path
from .git_manager import verify_candidate


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def validate_workflows(config):
    enabled = {}
    used_rooms = set()
    agents = set(x['sender_id'] for x in config['roles'].values())
    humans = set(config['humans'])
    for name, spec in config.get('workflows', {}).items():
        if spec.get('enabled') is not True:
            continue
        rooms = (spec['team_room_id'], spec['engine_room_id'])
        if rooms[0] == rooms[1] or used_rooms.intersection(rooms):
            raise ValueError('Workflow rooms must be distinct and exclusive')
        if any(r not in config['rooms'] for r in rooms):
            raise ValueError('Both workflow rooms must be allowlisted')
        if spec.get('test_profile') != 'python-unittest' or spec.get('max_repairs', 2) != 2:
            raise ValueError('Only qualified unittest profile and two repairs supported')
        if spec.get('workspace_owner_room_id', rooms[0]) != rooms[0]:
            raise ValueError('Team room must own the workspace')
        if set(spec['roles']) != {'planner', 'coder', 'reviewer', 'steward'}:
            raise ValueError('Four specialist roles required')
        if set(spec['humans']) != {'delivery_lead', 'client', 'operations'}:
            raise ValueError('Three human roles required')
        if spec.get('required_confirmers') != ['client', 'operations']:
            raise ValueError('Client and operations confirmation required')
        ids = list(spec['humans'].values())
        if len(set(ids)) != 3:
            raise ValueError('Use distinct people for PactRoom human roles')
        humans.update(ids)
        for role in spec['roles'].values():
            if role['sender_id'] in agents:
                raise ValueError('Specialist identities must be unique')
            agents.add(role['sender_id'])
            if not re.fullmatch(r'[A-Za-z0-9_-]+', role['name']):
                raise ValueError('Invalid agent name')
        if any(not isinstance(x, str) or not x or 'REPLACE' in x or 'ACTUAL_' in x or 'VERIFIED_' in x for x in [*rooms, *ids, *agents]):
            raise ValueError('Replace placeholder identities before enabling')
        used_rooms.update(rooms)
        enabled[name] = spec
    if humans & agents or config.get('runner_agent_id') in agents | humans:
        raise ValueError('Human, specialist and Runner identities must be distinct')
    return enabled


class Pact:
    def __init__(self, runner, config):
        self.runner = runner
        self.config = config
        self.workflows = validate_workflows(config)
        # Separate, transactional additive tables: never reset the legacy ledger.
        with runner.ledger.transaction() as db:
            for sql in (
                'CREATE TABLE IF NOT EXISTS pact_tasks(task_id TEXT PRIMARY KEY REFERENCES tasks(id), profile TEXT NOT NULL, pending_change TEXT)',
                'CREATE TABLE IF NOT EXISTS pact_briefs(task_id TEXT NOT NULL, revision INTEGER NOT NULL, hash TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(task_id,revision))',
                'CREATE TABLE IF NOT EXISTS pact_confirmations(task_id TEXT NOT NULL, revision INTEGER NOT NULL, hash TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(task_id,revision,actor))',
                'CREATE TABLE IF NOT EXISTS pact_queue(task_id TEXT PRIMARY KEY, role TEXT NOT NULL, extra TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS pact_acceptances(task_id TEXT NOT NULL, revision INTEGER NOT NULL, hash TEXT NOT NULL, commit_sha TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(task_id,revision,commit_sha))',
                'CREATE TABLE IF NOT EXISTS pact_schema(version INTEGER PRIMARY KEY CHECK(version=1))',
                'INSERT OR IGNORE INTO pact_schema VALUES(1)',
            ):
                db.execute(sql)
        runner.pact = self

    def profile(self, task_id, db=None):
        rows = db.execute('SELECT profile FROM pact_tasks WHERE task_id=?', (task_id,)).fetchall() if db else self.runner.ledger.rows('SELECT profile FROM pact_tasks WHERE task_id=?', (task_id,))
        return json.loads(rows[0]['profile']) if rows else None

    def register(self, db, task_id, workflow):
        spec = dict(self.workflows[workflow], workflow=workflow)
        db.execute('INSERT INTO pact_tasks VALUES(?,?,NULL)', (task_id, canonical(spec)))

    def prepare(self, db, task, payload):
        spec = self.profile(task['id'], db)
        if not spec: return
        payload.update(workflow=spec['workflow'], team_room_id=task['room_id'],
                       task_branch='relay/' + task['id'],
                       room_id=spec['engine_room_id'], result_room_id=spec['engine_room_id'],
                       agent_name=spec['roles'][payload['role']]['name'],
                       expected_sender=spec['roles'][payload['role']]['sender_id'])
        if payload['role'] in ('reviewer', 'steward'):
            row = db.execute("SELECT detail FROM runs WHERE task_id=? AND role='coder' AND outcome='completed' ORDER BY finished DESC LIMIT 1", (task['id'],)).fetchone()
            if row:
                built = json.loads(row['detail'])
                if built.get('commit_sha') == task['commit_sha']:
                    payload['requirement_tests'] = built.get('requirement_tests', {})
                    payload['uncovered_requirements'] = built.get('uncovered_requirements', {})
        if payload['role'] == 'steward':
            row = db.execute("SELECT evidence FROM reviews WHERE task_id=? AND revision=? AND brief_hash=? AND commit_sha=? AND verdict='approved' ORDER BY id DESC LIMIT 1",
                             (task['id'], task['revision'], task['brief_hash'], task['commit_sha'])).fetchone()
            if not row: raise ValueError('Fresh approved review required')
            payload['review_evidence'] = json.loads(row['evidence'])
            row = db.execute("SELECT data FROM events WHERE task_id=? AND kind='tests_recorded' ORDER BY id DESC LIMIT 1", (task['id'],)).fetchone()
            if row:
                tests = json.loads(row['data'])
                if tests['commit_sha'] == task['commit_sha'] and tests['brief_hash'] == task['brief_hash']:
                    payload['test_evidence'] = tests
        if payload['role'] == 'reviewer':
            review = Path(payload['artifact_dir']) / ('review-' + payload['handoff_id'])
            archive = Path(payload['artifact_dir']) / (payload['handoff_id'] + '.zip')
            subprocess.run(['git', '-C', payload['workspace'], 'archive', '--format=zip', f'--output={archive}', task['commit_sha']],
                           check=True, capture_output=True, timeout=60)
            review.mkdir(mode=0o700)
            with zipfile.ZipFile(archive) as files:
                for item in files.infolist():
                    if not (review / item.filename).resolve().is_relative_to(review.resolve()) or '\\' in item.filename or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ValueError('Unsafe review archive path')
                files.extractall(review)
            payload.update(candidate_workspace=payload['workspace'], workspace=str(review),
                           review_copy='Exact commit archive, without Git metadata; do not edit this copy.')

    def defer(self, db, task, role, extra):
        spec = self.profile(task['id'], db)
        if not spec: return False
        if role == 'coder':
            brief = json.loads(task['brief'])
            db.execute('INSERT OR IGNORE INTO pact_briefs VALUES(?,?,?,?)',
                       (task['id'], task['revision'], task['brief_hash'], task['brief']))
            confirmations = {r['actor'] for r in db.execute('SELECT actor FROM pact_confirmations WHERE task_id=? AND revision=? AND hash=?',
                             (task['id'], task['revision'], task['brief_hash']))}
            required = {spec['humans'][r] for r in spec['required_confirmers']}
            if brief['unresolved_decisions'] or not required.issubset(confirmations):
                state = 'WAITING_FOR_DECISION' if brief['unresolved_decisions'] else 'WAITING_FOR_CONFIRMATION'
                db.execute('UPDATE tasks SET state=? WHERE id=?', (state, task['id']))
                return True
        return False

    def schedule(self, db):
        row = db.execute('SELECT q.* FROM pact_queue q JOIN tasks t ON t.id=q.task_id ORDER BY t.created,t.id LIMIT 1').fetchone()
        if not row: return False
        db.execute('DELETE FROM pact_queue WHERE task_id=?', (row['task_id'],))
        task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone())
        self.runner._handoff(db, task, row['role'], json.loads(row['extra']))
        return True

    def resume_blocked_builder(self, task_id, actor, reason):
        """Trusted operator recovery; preserves brief, approvals and repair budget.

        Only an explicitly blocked Builder can resume. Never reconcile an
        uncertain/timed-out session by resending it through this method.
        """
        if not actor or not reason: raise ValueError('Operator identity and reason required')
        with self.runner.ledger.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row: raise ValueError('Unknown task')
            task = dict(row); spec = self.profile(task_id, db)
            if not spec or task['state'] != 'BLOCKED' or task['repairs'] > 2:
                raise ValueError('Eligible blocked Pact task required')
            if db.execute("SELECT 1 FROM handoffs WHERE status IN ('pending','sending','sent','uncertain','timed_out')").fetchone():
                raise ValueError('Active work must settle before operator recovery')
            if db.execute('SELECT pending_change FROM pact_tasks WHERE task_id=?', (task_id,)).fetchone()['pending_change']:
                raise ValueError('Pending requirement change must be reconciled first')
            last = db.execute('SELECT h.role,h.status,r.outcome FROM handoffs h LEFT JOIN runs r ON r.id=h.id WHERE h.task_id=? ORDER BY h.created DESC LIMIT 1', (task_id,)).fetchone()
            if not last or tuple(last) != ('coder', 'done', 'blocked'):
                raise ValueError('Only a completed blocked Builder report can be resumed')
            confirmed = {r['actor'] for r in db.execute('SELECT actor FROM pact_confirmations WHERE task_id=? AND revision=? AND hash=?', (task_id,task['revision'],task['brief_hash']))}
            if not {spec['humans'][r] for r in spec['required_confirmers']}.issubset(confirmed):
                raise ValueError('Current stakeholder confirmations required')
            self.runner._handoff(db, task, 'coder', {'operator_recovery': reason})
            self.runner.ledger.event(db, task_id, 'pact_builder_resumed', {'actor':actor,'reason':reason}, self.runner.clock())
        return self.runner.task(task_id)['state']

    def validate_result(self, task, role, result):
        if not self.profile(task['id']) or result.get('status') == 'blocked': return result
        if role == 'planner':
            brief = result.get('brief')
            if not isinstance(brief, dict) or not isinstance(brief.get('objective'), str) or not brief['objective'].strip():
                raise ValueError('Pact planner must supply a structured brief')
            requirements = brief.get('requirements')
            if not isinstance(requirements, list) or not requirements or len(requirements) > 50:
                raise ValueError('Brief requires 1-50 requirements')
            ids = set()
            for r in requirements:
                if not isinstance(r, dict) or not re.fullmatch(r'R[1-9][0-9]*', str(r.get('id', ''))) or r['id'] in ids:
                    raise ValueError('Unique stable requirement IDs required')
                if r.get('owner') not in ('client', 'operations', 'both') or any(not isinstance(r.get(k), str) or not r[k].strip() for k in ('rule', 'acceptance')):
                    raise ValueError('Requirement rule, owner and acceptance required')
                ids.add(r['id'])
            if not isinstance(brief.get('unresolved_decisions'), list) or any(not isinstance(x, str) for x in brief['unresolved_decisions']):
                raise ValueError('Explicit unresolved decisions required')
            return dict(result, brief=canonical(brief))
        if role == 'coder':
            mapping = result.get('requirement_tests')
            ids = {r['id'] for r in json.loads(task['brief'])['requirements']}
            if not isinstance(mapping, dict) or set(mapping) != ids or any(not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v) for v in mapping.values()):
                raise ValueError('Builder must map every requirement to test identifiers')
            uncovered = result.get('uncovered_requirements', {})
            if not isinstance(uncovered, dict) or any(not mapping[k] and (not isinstance(uncovered.get(k), str) or not uncovered[k].strip()) for k in ids):
                raise ValueError('Untested requirements need explicit inspection evidence or limitations')
        if role == 'reviewer':
            findings = result.get('requirement_findings')
            ids = {r['id'] for r in json.loads(task['brief'])['requirements']}
            if not isinstance(findings, dict) or set(findings) != ids or any(not isinstance(v, str) or not v.strip() for v in findings.values()):
                raise ValueError('Reviewer must report findings for every requirement')
        return result

    def settle_change(self, task, handoff, source):
        rows = self.runner.ledger.rows('SELECT pending_change FROM pact_tasks WHERE task_id=?', (task['id'],))
        if not rows or not rows[0]['pending_change']: return False
        with self.runner.ledger.transaction() as db:
            db.execute("UPDATE handoffs SET status='superseded' WHERE id=?", (handoff['id'],))
            db.execute("UPDATE runs SET finished=?,outcome='superseded' WHERE id=?", (self.runner.clock(), handoff['id']))
            self.runner.ledger.event(db, task['id'], 'superseded_result', {'source_id': source, 'handoff_id': handoff['id']}, self.runner.clock())
            self._revise(db, task, rows[0]['pending_change'])
        return True

    def _revise(self, db, task, request):
        # Preserve cumulative repair budget; do not erase historical confirmations.
        db.execute("UPDATE tasks SET revision=revision+1,request=?,state='NEW',brief=NULL,brief_hash=NULL,commit_sha=NULL WHERE id=?", (request, task['id']))
        db.execute('UPDATE pact_tasks SET pending_change=NULL WHERE task_id=?', (task['id'],))
        db.execute('DELETE FROM pact_queue WHERE task_id=?', (task['id'],))
        self.runner.ledger.event(db, task['id'], 'pact_revision', {'previous_revision': task['revision']}, self.runner.clock())

    def command(self, room, actor, source, command):
        specs = [(n, s) for n, s in self.workflows.items() if s['team_room_id'] == room]
        if not specs: raise ValueError('Pact commands require a configured team room')
        name, configured = specs[0]
        parts = command.split()
        if not parts: raise ValueError('Missing Pact command')
        verb = parts[0]
        if verb == 'start':
            if actor != configured['humans']['delivery_lead'] or len(parts) < 2:
                raise ValueError('Only delivery lead may start a request')
            return self.runner.submit(self.config['server_id'], room, self.config['rooms'][room], source, command.split(None, 1)[1], workflow=name)
        if len(parts) < 2: raise ValueError('Full task ID required')
        task = self.runner.task(parts[1]); spec = self.profile(task['id'])
        if not spec or task['room_id'] != room or task['server_id'] != self.config['server_id'] or actor not in spec['humans'].values():
            raise ValueError('Wrong task room or unauthorized human')
        pending = self.runner.ledger.rows('SELECT pending_change FROM pact_tasks WHERE task_id=?', (task['id'],))[0]['pending_change']
        if verb in ('status', 'brief', 'evidence') and len(parts) == 2:
            with self.runner.ledger.transaction() as db:
                self.runner.ledger.event(db, task['id'], 'pact_query', {'room_id': room, 'source_id': source, 'command': verb}, self.runner.clock())
            return verb
        key = 'pact:' + self.config['server_id'] + ':' + room + ':' + source
        if self.runner.ledger.rows('SELECT 1 FROM inbox WHERE source_id=?', (key,)): return 'duplicate'
        if verb == 'accept':
            verify_candidate(self.runner._path(task), task['commit_sha'])
        with self.runner.ledger.transaction() as db:
            current = dict(db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone())
            if current != task: raise ValueError('Task changed during command')
            if db.execute('SELECT 1 FROM inbox WHERE source_id=?', (key,)).fetchone(): return 'duplicate'
            db.execute('INSERT INTO inbox VALUES(?,?)', (key, self.runner.clock()))
            if verb == 'confirm' and len(parts) == 4:
                required = {spec['humans'][r] for r in spec['required_confirmers']}
                if pending or actor not in required or parts[2:] != [f"v{task['revision']}", task['brief_hash']]:
                    raise ValueError('Confirmation is unauthorized, stale or not eligible')
                if db.execute('SELECT 1 FROM pact_confirmations WHERE task_id=? AND revision=? AND hash=? AND actor=?', (task['id'], task['revision'], task['brief_hash'], actor)).fetchone():
                    return 'duplicate'
                if task['state'] != 'WAITING_FOR_CONFIRMATION':
                    raise ValueError('Confirmation is unauthorized, stale or not eligible')
                db.execute('INSERT OR IGNORE INTO pact_confirmations VALUES(?,?,?,?,?)', (task['id'], task['revision'], task['brief_hash'], actor, source))
                confirmed = {r['actor'] for r in db.execute('SELECT actor FROM pact_confirmations WHERE task_id=? AND revision=? AND hash=?', (task['id'], task['revision'], task['brief_hash']))}
                if required.issubset(confirmed):
                    db.execute('INSERT OR IGNORE INTO pact_queue VALUES(?,?,?)', (task['id'], 'coder', '{}'))
                    db.execute("UPDATE tasks SET state='BUILD_QUEUED' WHERE id=?", (task['id'],))
            elif verb == 'change' and len(parts) >= 3:
                request = task['request'] + '\nRequested change: ' + command.split(None, 2)[2]
                if len(request) > 20000 or pending: raise ValueError('Change too large or already pending')
                active = db.execute("SELECT id,status FROM handoffs WHERE task_id=? AND status IN ('pending','sending','sent','uncertain','timed_out')", (task['id'],)).fetchone()
                if active and active['status'] != 'pending':
                    db.execute('UPDATE pact_tasks SET pending_change=? WHERE task_id=?', (request, task['id']))
                    db.execute("UPDATE tasks SET state='CHANGE_PENDING' WHERE id=?", (task['id'],))
                else:
                    if active: db.execute("UPDATE handoffs SET status='superseded' WHERE id=?", (active['id'],))
                    self._revise(db, task, request)
            elif verb == 'accept' and len(parts) == 5:
                if pending or actor != spec['humans']['delivery_lead'] or task['state'] != 'READY_FOR_ACCEPTANCE' or parts[2:] != [f"v{task['revision']}", task['brief_hash'], task['commit_sha']]:
                    raise ValueError('Delivery acceptance is unauthorized or stale')
                review = db.execute("SELECT 1 FROM reviews WHERE task_id=? AND revision=? AND brief_hash=? AND commit_sha=? AND verdict='approved'", (task['id'], task['revision'], task['brief_hash'], task['commit_sha'])).fetchone()
                if not review: raise ValueError('Fresh approved review required')
                db.execute('INSERT INTO pact_acceptances VALUES(?,?,?,?,?,?)', (task['id'], task['revision'], task['brief_hash'], task['commit_sha'], actor, source))
                db.execute("UPDATE tasks SET state='ACCEPTED' WHERE id=?", (task['id'],))
            else: raise ValueError('Invalid Pact command syntax')
            self.runner.ledger.event(db, task['id'], 'pact_command', {'command': verb, 'actor': actor, 'source_id': source}, self.runner.clock())
        self.runner.schedule()
        return verb

    def presentation(self, task_id, detail=False):
        """Only bounded, mention-neutralized project text; never protocol envelopes."""
        task = self.runner.task(task_id)
        spec = self.profile(task_id)
        labels = {'WAITING_FOR_CONFIRMATION': 'Plan ready for your approval',
                  'WAITING_FOR_DECISION': 'Your decision is needed', 'PLANNING': 'Preparing the plan',
                  'BUILD_QUEUED': 'Approved plan · build queued', 'IMPLEMENTING': 'Building your project',
                  'REVIEWING': 'Independent review', 'STEWARD': 'Preparing delivery',
                  'READY_FOR_ACCEPTANCE': 'Delivery ready for acceptance', 'ACCEPTED': 'Delivery accepted',
                  'CHANGE_PENDING': 'Requirement change recorded', 'BLOCKED': 'Operator attention needed'}
        lines = [f"**PactRoom | {labels.get(task['state'], task['state'])}**\nVersion {task['revision']} · Project `{task_id[:8]}`"]
        if task['brief']:
            brief = json.loads(task['brief'])
            confirmed = {r['actor'] for r in self.runner.ledger.rows('SELECT actor FROM pact_confirmations WHERE task_id=? AND revision=? AND hash=?', (task_id, task['revision'], task['brief_hash']))}
            # Automatic updates after the first confirmation are concise. The
            # The explicit brief command always retains the full plan.
            show_requirements = detail or task['state'] == 'WAITING_FOR_DECISION' or (task['state'] == 'WAITING_FOR_CONFIRMATION' and not confirmed)
            lines.append('**What we are building**\n' + brief['objective'])
            if show_requirements:
                for req in brief['requirements']:
                    owner = {'client': 'Client', 'operations': 'Operations', 'both': 'Client + Operations'}[req['owner']]
                    lines.append(f"**{req['id']} · {owner}**\n{req['rule']}\n\n**How we will check it**\n{req['acceptance']}")
                lines.extend('**Decision needed**\n' + x for x in brief['unresolved_decisions'])
            if task['state'] == 'WAITING_FOR_CONFIRMATION':
                missing = [r for r in spec['required_confirmers'] if spec['humans'][r] not in confirmed]
                checks = ['• ' + r.title() + ': ' + ('Waiting for approval' if r in missing else 'Confirmed') for r in spec['required_confirmers']]
                lines.append('**Approval checklist**\n' + '\n'.join(checks))
                location = 'above' if show_requirements else f'in the version {task["revision"]} plan posted earlier'
                lines.append(f'**Next step**\nRead all requirements {location}. If you agree, send the command below from your own account. Both people must confirm before coding starts. If something needs changing, discuss it first.')
                if not show_requirements:
                    lines.append(f'To read the full plan again: `@relay-runner /pact brief {task_id}`')
                lines.append(f"```text\n@relay-runner /pact confirm {task_id} v{task['revision']} {task['brief_hash']}\n```")
        if task['state'] == 'READY_FOR_ACCEPTANCE':
            lines.append('Reviewed delivery ready; nothing published. Delivery lead may accept:')
            lines.append(f"```text\n@relay-runner /pact accept {task_id} v{task['revision']} {task['brief_hash']} {task['commit_sha']}\n```")
        if task['state'] == 'CHANGE_PENDING': lines.append('Change recorded. Current work must settle before replanning; old work cannot advance delivery.')
        if task['state'] == 'ACCEPTED': lines.append('Delivery accepted by the lead. Nothing pushed, published or deployed.')
        if task['state'] == 'BLOCKED': lines.append('Operator attention needed; no automatic retry.')
        # Break mentions originating in user/model content; restore only inert command examples.
        lines.append(f"Project reference: `{task_id}`")
        return '\n\n'.join(lines).replace('@', '@\u200b').replace('@\u200brelay-runner /pact', '@relay-runner /pact')

    def evidence_summary(self, task_id):
        """Summarize only evidence bound to the current brief and candidate."""
        task = self.runner.task(task_id)
        ledger = self.runner.ledger
        brief = json.loads(task['brief']) if task['brief'] else None
        lines = [f"**PactRoom | Delivery evidence**\nVersion {task['revision']} · Project `{task_id[:8]}`"]
        if brief:
            objective = ' '.join(brief['objective'].split())
            lines.append('**What was requested**\n' + (objective[:237] + '...' if len(objective) > 240 else objective))
        lines.append('**Verification**')
        tests = None
        if task['commit_sha'] and task['brief_hash']:
            for row in ledger.rows("SELECT data FROM events WHERE task_id=? AND kind='tests_recorded' ORDER BY id DESC", (task_id,)):
                item = json.loads(row['data'])
                if item.get('commit_sha') == task['commit_sha'] and item.get('brief_hash') == task['brief_hash']:
                    tests = item
                    break
        if tests:
            outcome = 'Passed' if tests.get('passed') and tests.get('exit_code') == 0 else 'Not passed'
            lines.append(f"• Tests: **{outcome}** · {tests.get('test_count', 0)} tests · exit {tests.get('exit_code')}.")
        else:
            lines.append('• Tests: no matching results recorded for this version and commit.')
        bound = (task_id, task['revision'], task['brief_hash'], task['commit_sha'])
        reviews = ledger.rows('SELECT verdict,evidence FROM reviews WHERE task_id=? AND revision=? AND brief_hash=? AND commit_sha=? ORDER BY id DESC LIMIT 1', bound)
        lines.append('• Independent review: **' + (reviews[0]['verdict'].replace('_', ' ').title() if reviews else 'Not recorded for this candidate') + '**.')
        if brief and task['commit_sha']:
            runs = ledger.rows("SELECT r.detail FROM runs r JOIN handoffs h ON h.id=r.id WHERE r.task_id=? AND h.revision=? AND r.role='coder' AND r.outcome='completed' ORDER BY r.finished DESC", (task_id, task['revision']))
            for row in runs:
                result = json.loads(row['detail'])
                if result.get('commit_sha') != task['commit_sha']: continue
                mapping = result.get('requirement_tests', {})
                ids = [req['id'] for req in brief['requirements']]
                mapped = sum(bool(mapping.get(key)) for key in ids)
                lines.append(f'• Requirement mapping: {mapped}/{len(ids)} have builder-supplied test references; {len(ids)-mapped} need inspection or have no test mapping.')
                break
        accepted = ledger.rows('SELECT 1 FROM pact_acceptances WHERE task_id=? AND revision=? AND hash=? AND commit_sha=?', bound)
        lines.append('• Human acceptance: **' + ('Recorded for this candidate' if accepted else 'Not recorded for this candidate') + '**.')
        lines.append('**Delivery reference**\nCommit: `' + (task['commit_sha'] or 'Not available yet') + '`\nPublication: this workflow does not push or create PRs.')
        lines.append('Detailed test mappings and review records remain on AWS.\nFull requirements: `@relay-runner /pact brief ' + task_id + '`')
        return '\n\n'.join(lines).replace('@', '@\u200b').replace('@\u200brelay-runner /pact', '@relay-runner /pact')
