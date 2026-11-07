import hashlib
import json
import time
import uuid
from .git_manager import verify_candidate
from .workspace_manager import safe_path

ROLES = {'planner':'xpplanner', 'coder':'xpcoder', 'reviewer':'xpreviewer', 'steward':'xpsteward'}
ACTIVE = "('pending','sending','sent','uncertain','timed_out')"


class Runner:
    def __init__(self, ledger, workspaces, evidence, clock=time.time):
        self.ledger, self.workspaces, self.evidence, self.clock = ledger, workspaces, evidence, clock

    def task(self, task_id):
        rows = self.ledger.rows('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not rows: raise ValueError('Unknown task')
        return rows[0]

    def submit(self, server_id, room_id, room_name, source_id, request, *, workflow=None):
        """Trusted operator boundary. Live adapter must validate human/source identity first."""
        if not source_id or not isinstance(request,str) or not request.strip() or len(request)>20000:
            raise ValueError('Nonempty source ID and request (max 20k characters) required')
        self.workspaces.ensure_workspace(server_id,room_id,room_name)
        with self.ledger.transaction() as db:
            old = db.execute('SELECT id FROM tasks WHERE server_id=? AND room_id=? AND source_id=?',
                             (server_id,room_id,source_id)).fetchone()
            if old: return old['id']
            task_id = uuid.uuid4().hex
            db.execute('INSERT INTO tasks(id,server_id,room_id,source_id,request,state,created) VALUES(?,?,?,?,?,?,?)',
                       (task_id,server_id,room_id,source_id,request,'NEW',self.clock()))
            self.ledger.event(db,task_id,'submitted',{'source_id':source_id},self.clock())
            if workflow is not None:
                self.pact.register(db, task_id, workflow)
        self.schedule()
        return task_id

    def _path(self, task):
        row = self.ledger.rows('SELECT name FROM rooms WHERE server_id=? AND room_id=?',
                               (task['server_id'],task['room_id']))[0]
        return self.workspaces.ensure_workspace(task['server_id'],task['room_id'],row['name'])

    def _handoff(self, db, task, role, extra=None):
        pact = getattr(self, 'pact', None)
        if pact and pact.defer(db, task, role, extra):
            return
        handoff_id = uuid.uuid4().hex
        # Reports are outside the candidate checkout: a report must not dirty
        # the exact commit that the Reviewer is being asked to approve.
        artifact_key = hashlib.sha256(task['id'].encode()).hexdigest()
        artifacts = safe_path(self.workspaces.root / '.relay-artifacts' / artifact_key)
        artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        room = db.execute('SELECT path FROM rooms WHERE server_id=? AND room_id=?',
                          (task['server_id'],task['room_id'])).fetchone()
        payload = dict(handoff_id=handoff_id, task_id=task['id'], server_id=task['server_id'],
                       room_id=task['room_id'], role=role, agent_name=ROLES[role],
                       revision=task['revision'], workspace=room['path'], request=task['request'],
                       brief=task['brief'], brief_hash=task['brief_hash'], commit_sha=task['commit_sha'],
                       artifact_dir=str(artifacts))
        payload.update(extra or {})
        if pact:
            pact.prepare(db, task, payload)
        db.execute('INSERT INTO handoffs(id,task_id,role,revision,status,payload,created) VALUES(?,?,?,?,?,?,?)',
                   (handoff_id,task['id'],role,task['revision'],'pending',json.dumps(payload),self.clock()))
        state = {'planner':'PLANNING','coder':'IMPLEMENTING','reviewer':'REVIEWING','steward':'STEWARD'}[role]
        db.execute('UPDATE tasks SET state=? WHERE id=?',(state,task['id']))
        self.ledger.event(db,task['id'],'handoff_queued',{'handoff_id':handoff_id,'role':role},self.clock())

    def schedule(self):
        with self.ledger.transaction() as db:
            if db.execute(f'SELECT 1 FROM handoffs WHERE status IN {ACTIVE}').fetchone(): return
            pact = getattr(self, 'pact', None)
            if pact and pact.schedule(db): return
            task = db.execute("SELECT * FROM tasks WHERE state='NEW' ORDER BY created,id LIMIT 1").fetchone()
            if task: self._handoff(db,dict(task),'planner')

    def dispatch_one(self, adapter):
        with self.ledger.transaction() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='dispatch_paused' AND value='true'").fetchone():
                return False
            row = db.execute("SELECT * FROM handoffs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
            if not row: return False
            row = dict(row)
            db.execute("UPDATE handoffs SET status='sending', deadline=? WHERE id=?",
                       (self.clock()+1200,row['id']))
            db.execute('INSERT INTO runs(id,task_id,role,started) VALUES(?,?,?,?)',
                       (row['id'],row['task_id'],row['role'],self.clock()))
        try:
            receipt = adapter.send(json.loads(row['payload']))
            if not isinstance(receipt,str) or not receipt: raise ValueError('No dispatch receipt')
        except Exception:
            # A network failure may happen after the remote server accepts the message.
            # Never blindly retry and generate duplicate coding sessions.
            with self.ledger.transaction() as db:
                db.execute("UPDATE handoffs SET status='uncertain' WHERE id=?",(row['id'],))
                self.ledger.event(db,row['task_id'],'dispatch_uncertain',{'handoff_id':row['id']},self.clock())
            raise
        with self.ledger.transaction() as db:
            db.execute("UPDATE handoffs SET status='sent',receipt=? WHERE id=?",(receipt,row['id']))
            self.ledger.event(db,row['task_id'],'dispatch_sent',{'handoff_id':row['id'],'receipt':receipt},self.clock())
        return True

    def control(self, room_id, source_id, actor, command):
        """Called only after transport-authenticated human and room checks."""
        if command not in ('status', 'pause', 'resume'): raise ValueError('Unknown control')
        key = 'control:' + room_id + ':' + source_id
        with self.ledger.transaction() as db:
            if db.execute('SELECT 1 FROM inbox WHERE source_id=?', (key,)).fetchone(): return 'duplicate'
            db.execute('INSERT INTO inbox VALUES(?,?)', (key, self.clock()))
            if command != 'status':
                db.execute("INSERT INTO metadata VALUES('dispatch_paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           ('true' if command == 'pause' else 'false',))
            self.ledger.event(db, None, 'control_received', {'room_id': room_id, 'command': command,
                              'actor': actor, 'source_id': source_id}, self.clock())
        return command

    def recover(self):
        """Run only at exclusive worker startup; never automatically resend an ambiguous send."""
        with self.ledger.transaction() as db:
            db.execute("UPDATE handoffs SET status='uncertain' WHERE status='sending'")
        self.expire()

    def expire(self):
        """Expire overdue handoffs and block their parent tasks atomically."""
        expected_states = {
            "planner": "PLANNING",
            "coder": "IMPLEMENTING",
            "reviewer": "REVIEWING",
            "steward": "STEWARD",
        }

        with self.ledger.transaction() as db:
            now = self.clock()

            rows = db.execute(
                """
                SELECT
                    h.id, h.task_id, h.role, h.revision,
                    h.status,
                    t.state AS task_state,
                    t.revision AS task_revision
                FROM handoffs h
                JOIN tasks t ON t.id = h.task_id
                WHERE h.status IN ('sent', 'timed_out')
                  AND h.deadline IS NOT NULL
                  AND h.deadline < ?
                ORDER BY h.created, h.id
                """,
                (now,),
            ).fetchall()

            for row in rows:
                if row["status"] == "sent":
                    updated = db.execute(
                        """
                        UPDATE handoffs
                        SET status='timed_out'
                        WHERE id=? AND status='sent'
                        """,
                        (row["id"],),
                    ).rowcount

                    if updated:
                        self.ledger.event(
                            db,
                            row["task_id"],
                            "handoff_timeout",
                            {"handoff_id": row["id"]},
                            now,
                        )

                # Never change a task that already advanced
                # or whose specification revision changed.
                if (
                    row["task_revision"] != row["revision"]
                    or row["task_state"] != expected_states.get(row["role"])
                ):
                    continue

                changed = db.execute(
                    """
                    UPDATE tasks
                    SET state='BLOCKED'
                    WHERE id=? AND revision=? AND state=?
                    """,
                    (
                        row["task_id"],
                        row["revision"],
                        row["task_state"],
                    ),
                ).rowcount

                if not changed:
                    continue

                reason = {
                    "reason": "agent_handoff_timeout",
                    "handoff_id": row["id"],
                    "role": row["role"],
                    "requires_operator_recovery": True,
                }

                db.execute(
                    """
                    UPDATE runs
                    SET finished=?, outcome='blocked', detail=?
                    WHERE id=? AND finished IS NULL
                    """,
                    (now, json.dumps(reason), row["id"]),
                )

                self.ledger.event(
                    db,
                    row["task_id"],
                    "task_blocked",
                    reason,
                    now,
                )


    def complete(self, handoff_id, server_id, room_id, role, result, source_id, *, reconciliation_actor=None):
        """Only a transport-authenticated role may call this; JSON role labels are insufficient."""
        if not source_id: raise ValueError('A verified result event ID is required')
        # Identity and freshness checks precede any test execution.
        rows = self.ledger.rows('SELECT * FROM handoffs WHERE id=?',(handoff_id,))
        if not rows: raise ValueError('Unknown handoff')
        handoff = rows[0]; task = self.task(handoff['task_id'])
        expected_room = json.loads(handoff['payload']).get('result_room_id', task['room_id'])
        if (task['server_id'],expected_room,handoff['role']) != (server_id,room_id,role):
            raise ValueError('Cross-room or wrong-role completion rejected')
        if handoff['status']=='done': return task['state']
        allowed_status = ('timed_out','uncertain') if reconciliation_actor else ('sent',)
        if handoff['status'] not in allowed_status or task['revision'] != handoff['revision']:
            raise ValueError('Handoff is not sent/current; resolve uncertain or stale work first')
        pact = getattr(self, 'pact', None)
        if pact and pact.settle_change(task, handoff, source_id):
            self.schedule()
            return self.task(task['id'])['state']
        if pact:
            result = pact.validate_result(task, role, result)
        path = self._path(task)
        evidence = None
        if result.get('status')=='blocked':
            blocker=result.get('blocker')
            if not isinstance(blocker,str) or not blocker.strip():raise ValueError('Blocked result must explain the blocker')
            with self.ledger.transaction() as db:
                current=db.execute('SELECT revision FROM tasks WHERE id=?',(task['id'],)).fetchone()
                active=db.execute('SELECT status FROM handoffs WHERE id=?',(handoff_id,)).fetchone()
                if current['revision']!=task['revision'] or active['status'] not in allowed_status:
                    raise ValueError('Task changed before blocker recording')
                db.execute("UPDATE handoffs SET status='done' WHERE id=?",(handoff_id,))
                db.execute("UPDATE tasks SET state='BLOCKED' WHERE id=?",(task['id'],))
                db.execute("UPDATE runs SET finished=?,outcome='blocked',detail=? WHERE id=?",
                           (self.clock(),json.dumps(result),handoff_id))
                self.ledger.event(db,task['id'],'agent_blocked',{'role':role,'source_id':source_id,'blocker':blocker},self.clock())
            self.schedule()
            return 'BLOCKED'
        if role == 'planner':
            brief = result.get('brief')
            if not isinstance(brief,str) or not brief.strip() or len(brief)>30000:
                raise ValueError('Planner must return an actionable brief')
        if role in ('coder','reviewer','steward'):
            commit = result.get('commit_sha')
            verify_candidate(path,commit)
            if role!='coder' and commit!=task['commit_sha']: raise ValueError('Stale candidate')
        if role=='coder':
            evidence = self.evidence.run(path,commit,task['brief_hash'],task['revision'],task['id'])
        if role=='reviewer':
            if result.get('verdict') not in ('approved','changes_requested'): raise ValueError('Invalid review verdict')
            if result.get('brief_hash')!=task['brief_hash']: raise ValueError('Stale review brief')
        with self.ledger.transaction() as db:
            current = dict(db.execute('SELECT * FROM tasks WHERE id=?',(task['id'],)).fetchone())
            active = db.execute('SELECT status FROM handoffs WHERE id=?',(handoff_id,)).fetchone()
            if current['revision']!=task['revision'] or active['status'] not in allowed_status:
                raise ValueError('Task changed during result verification')
            key = f'{server_id}:{room_id}:{source_id}'
            if db.execute('SELECT 1 FROM inbox WHERE source_id=?',(key,)).fetchone():
                raise ValueError('Source event was already used')
            db.execute('INSERT INTO inbox VALUES(?,?)',(key,self.clock()))
            if reconciliation_actor:
                self.ledger.event(db,task['id'],'completion_reconciled',
                                  {'handoff_id':handoff_id,'source_id':source_id,
                                   'actor':reconciliation_actor,'previous_status':handoff['status']},self.clock())
            db.execute("UPDATE handoffs SET status='done' WHERE id=?",(handoff_id,))
            db.execute('UPDATE runs SET finished=?,outcome=?,detail=? WHERE id=?',
                       (self.clock(),'completed',json.dumps(result),handoff_id))
            self.ledger.event(db,task['id'],'result_received',{'role':role,'source_id':source_id},self.clock())
            if role=='planner':
                task.update(brief=brief,brief_hash=hashlib.sha256(brief.encode()).hexdigest())
                db.execute('UPDATE tasks SET brief=?,brief_hash=? WHERE id=?',(brief,task['brief_hash'],task['id']))
                self._handoff(db,task,'coder')
            elif role=='coder':
                task['commit_sha']=commit
                db.execute('UPDATE tasks SET commit_sha=? WHERE id=?',(commit,task['id']))
                self.ledger.event(db,task['id'],'tests_recorded',evidence,self.clock())
                if evidence['passed']:
                    self._handoff(db,task,'reviewer',{'test_evidence':evidence})
                else: self._repair(db,task,{
                    'message':'Controlled unittest suite failed. Run the same command and diagnose it before resubmitting.',
                    'command':evidence['command'],'exit_code':evidence['exit_code'],
                    'test_count':evidence['test_count'],'timed_out':evidence['timed_out'],
                    'commit_sha':evidence['commit_sha']})
            elif role=='reviewer':
                # Reviewer receives real test evidence from Runner, not a self-reported pass.
                db.execute('INSERT INTO reviews(task_id,revision,brief_hash,commit_sha,verdict,evidence,created) VALUES(?,?,?,?,?,?,?)',
                           (task['id'],task['revision'],task['brief_hash'],commit,result['verdict'],json.dumps(result),self.clock()))
                if result['verdict']=='approved': self._handoff(db,task,'steward')
                else: self._repair(db,task,result.get('findings','Review requested changes'))
            else:
                state = 'READY_FOR_ACCEPTANCE' if pact and pact.profile(task['id']) else 'READY_FOR_APPROVAL'
                db.execute('UPDATE tasks SET state=? WHERE id=?',(state,task['id']))
                self.ledger.event(db,task['id'],'publication_prepared',result,self.clock())
        self.schedule()
        return self.task(task['id'])['state']

    def recheck_blocked_candidate(self,task_id,actor,reason):
        """Operator-only: validate existing code after correcting a test-runner fault.

        Does not reset repair counts, change code, invent an agent completion or publish.
        """
        task=self.task(task_id)
        if task['state']!='BLOCKED' or not actor or not reason:raise ValueError('Blocked task and operator reason required')
        evidence=self.evidence.run(self._path(task),task['commit_sha'],task['brief_hash'],task['revision'],task_id)
        if not evidence['passed']:raise ValueError('Existing candidate still fails controlled tests')
        with self.ledger.transaction() as db:
            current=dict(db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone())
            if current!=task or db.execute(f'SELECT 1 FROM handoffs WHERE status IN {ACTIVE}').fetchone():
                raise ValueError('Task changed or another handoff is active; do not override it')
            self.ledger.event(db,task_id,'operator_recheck',{'actor':actor,'reason':reason,'evidence':evidence},self.clock())
            self._handoff(db,task,'reviewer',{'test_evidence':evidence})
        return 'REVIEWING'

    def _repair(self,db,task,reason):
        task['repairs'] += 1
        db.execute('UPDATE tasks SET repairs=? WHERE id=?',(task['repairs'],task['id']))
        if task['repairs'] > 2:
            db.execute("UPDATE tasks SET state='BLOCKED' WHERE id=?",(task['id'],))
            self.ledger.event(db,task['id'],'repair_limit',{'reason':reason},self.clock())
        else: self._handoff(db,task,'coder',{'repair_reason':reason,'repair_cycle':task['repairs']})

    def revise(self,task_id,request,source_id):
        if getattr(self, 'pact', None) and self.pact.profile(task_id):
            raise ValueError('Use authenticated Pact change command for this task')
        if not request.strip() or not source_id: raise ValueError('Revision text/source required')
        with self.ledger.transaction() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task: raise ValueError('Unknown task')
            if db.execute(f'SELECT 1 FROM handoffs WHERE task_id=? AND status IN {ACTIVE}',(task_id,)).fetchone():
                raise ValueError('Wait for or explicitly reconcile the active session before revising')
            db.execute("UPDATE tasks SET revision=revision+1,request=?,state='NEW',brief=NULL,brief_hash=NULL,commit_sha=NULL,repairs=0 WHERE id=?",(request,task_id))
            self.ledger.event(db,task_id,'requirements_revised',{'source_id':source_id},self.clock())
        self.schedule()

    def approve_publication(self,task_id,actor_id,source_id):
        # CLI operator action only in v0.1; no natural-language approval inference.
        task=self.task(task_id)
        verify_candidate(self._path(task),task['commit_sha'])
        with self.ledger.transaction() as db:
            fresh=dict(db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone())
            if fresh!=task or task['state']!='READY_FOR_APPROVAL': raise ValueError('Task is not ready for approval')
            review=db.execute("SELECT 1 FROM reviews WHERE task_id=? AND revision=? AND brief_hash=? AND commit_sha=? AND verdict='approved'",
                              (task_id,task['revision'],task['brief_hash'],task['commit_sha'])).fetchone()
            if not review or not actor_id or not source_id: raise ValueError('Fresh review and actual human identity/source required')
            db.execute('INSERT INTO approvals(task_id,revision,commit_sha,actor_id,source_id,action,created) VALUES(?,?,?,?,?,?,?)',
                       (task_id,task['revision'],task['commit_sha'],actor_id,source_id,'draft_pr',self.clock()))
            self.ledger.event(db,task_id,'publication_authorized',{'actor_id':actor_id,'source_id':source_id},self.clock())
        # Publishing is intentionally a separate operation; approval alone never pushes.
