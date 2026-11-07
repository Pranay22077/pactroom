"""Switch Core 0.31.0 HTTP/SSE adapter, based on inspected installed source.

Live enablement requires a dedicated Runner agent key and verified identity mapping.
Never reuse a managed agent's connection ID or credentials.
"""
import json
import threading
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class StreamReconciliationRequired(RuntimeError):
    """Do not restart blindly after loss of event history or connection eviction."""


class SwitchAdapter:
    def __init__(self, config):
        self.config = config
        self.base = config['switch_url'].rstrip('/')
        url = urlsplit(self.base)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a server URL without embedded credentials')
        if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('localhost','127.0.0.1','::1')):
            raise ValueError('Remote API requires verified HTTPS; HTTP is allowed only over loopback')
        self.agent = quote(config['runner_agent_id'],safe='')
        self.key_path = Path(config['token_file'])
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request(self, method, route, body=None, headers=None, stream=False):
        token = self.key_path.read_text(encoding='utf8').strip()
        if not token or '\n' in token: raise ValueError('Runner key file is missing or invalid')
        req = Request(self.base+route, method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization':f'Bearer {token}','Content-Type':'application/json',**(headers or {})})
        try:
            response = self.opener.open(req,timeout=45)
        except HTTPError as exc:
            raise RuntimeError(f'Switch API returned HTTP {exc.code}; response body omitted') from None
        if stream: return response
        with response:
            data=response.read(2_000_001)
        if len(data)>2_000_000: raise ValueError('Oversized Switch API response')
        return json.loads(data) if data else {}

    def rooms(self):
        return self.request('GET',f'/agents/{self.agent}/moderation/rooms')

    def send(self,payload):
        if payload['room_id'] not in self.config['rooms']:
            raise ValueError('Room is not allowlisted')
        if payload.get('workflow'):
            spec = self.config.get('workflows', {}).get(payload['workflow'], {})
            identity = spec.get('roles', {}).get(payload['role'], {})
            if (spec.get('enabled') is not True or spec.get('engine_room_id') != payload['room_id']
                    or identity.get('sender_id') != payload.get('expected_sender') or identity.get('name') != payload['agent_name']):
                raise ValueError('Pact dispatch configuration changed; operator reconciliation required')
        agent=payload['agent_name'] if payload.get('workflow') else self.config['roles'][payload['role']]['name']
        content=(f'@{agent}\nRELAY_HANDOFF\n'+json.dumps(payload,ensure_ascii=False)+
                 '\nRead /home/ubuntu/relay-projects/AGENTS.md first. Use the assigned workspace. Return only RELAY_RESULT followed by JSON with '
                 'handoff_id and result. Do not start another agent yourself; Runner owns handoffs. '
                 'Steward: prepare PR details only; do not push, publish or merge.')
        if payload['role']=='coder':
            content+='\nRunner executes Python unittest discovery at tests/ when present, otherwise at repository root. Include test_*.py tests, run discovery yourself, and return a clean exact commit. No arbitrary test command from chat is executed.'
        examples = {
            'planner': {'brief': 'Replace with the actionable implementation brief and acceptance criteria'},
            'coder': {'commit_sha': 'Replace with the real full 40-character candidate commit'},
            'reviewer': {'commit_sha': payload.get('commit_sha'), 'brief_hash': payload.get('brief_hash'),
                         'verdict': 'approved', 'findings': 'Replace with actual findings; use changes_requested if needed'},
            'steward': {'commit_sha': payload.get('commit_sha'), 'title': 'Proposed draft PR title', 'body': 'Actual change and validation summary; publication is not authorized'},
        }
        if payload.get('workflow'):
            # The legacy root policy is explicitly scoped to the legacy workflow.
            content = content.replace('Read /home/ubuntu/relay-projects/AGENTS.md first.',
                                      'Use your configured PactRoom instructions; legacy xp role contracts do not apply to this handoff.')
            examples['planner'] = {'brief': {'objective': 'Actual objective', 'requirements': [
                {'id': 'R1', 'owner': 'client', 'rule': 'Actual business rule', 'acceptance': 'Testable example'}],
                'unresolved_decisions': []}}
            examples['coder']['requirement_tests'] = {'R1': ['tests.test_app.TestApp.test_actual_behavior']}
            examples['reviewer']['requirement_findings'] = {'R1': 'Actual code and test observations for this requirement'}
            content += '\nPactRoom: preserve stable requirement IDs. Return unresolved business decisions explicitly. Builder may act only on this confirmed assignment. Test references are claims for Reviewer to verify. Steward prepares delivery evidence, not publication.'
            content += '\nEvery requirement owner MUST be exactly client, operations, or both. Owner means the human decision owner, not the implementing agent; never use builder or planner as owner. Assign technical defaults to both for human confirmation. Keep objective concise and user-facing; workflow identifiers are already in the envelope.'
            if payload['role'] == 'coder':
                content += '\nThe designated Git task branch is ' + payload['task_branch'] + '. Create it if absent, or use it if already present. On an empty repository use git switch -c with that name before the first commit. Never reset, force-switch, overwrite unrelated work, push or publish. The ownership marker is expected project metadata: include it with intended files in the initial commit. Choose routine reversible implementation defaults without asking for more confirmation.'
        content += '\nCompletion schema (replace descriptive placeholders with actual work):\nRELAY_RESULT\n' + json.dumps({'handoff_id': payload['handoff_id'], 'result': examples[payload['role']]})
        content += '\nPost that standalone result in this room using the Switch messaging tool. Terminal output or a saved report alone is not delivery. If blocked, return result {"status":"blocked","blocker":"actual reason"} with the same handoff_id.'
        content += '\nDo not write reports inside the candidate checkout. Optional report directory: ' + payload.get('artifact_dir', 'not assigned; report findings in the result message only')
        data=self.request('POST',f'/agents/{self.agent}/message',
                          {'room_id':payload['room_id'],'content':content})
        return data.get('event_id')

    def frames(self, position=None):
        query={'connection_id':self.config['connection_id'],'scope':'all','filter':'all',
               'protocol':9,'protocol_accepts':9,'spawn_capable':'false',
               'rooms':','.join(self.config['rooms'])}
        if position is not None: query['start_at']=json.dumps(position,separators=(',',':'))
        else: query['start_from']='head'
        response=self.request('GET',f'/agents/{self.agent}/events?{urlencode(query)}',
                              headers={'Accept':'text/event-stream'},stream=True)
        stop=threading.Event(); failed=[]; worker=None
        def beat(state):
            interval=max(1,min(10,float(state.get('heartbeat_interval_seconds',2))))
            while not stop.wait(interval):
                try:
                    self.request('POST',f'/agents/{self.agent}/connection/beat',
                                 {'connection_id':state['connection_id'],'generation':state['generation']})
                except Exception:
                    failed.append(True); stop.set(); response.close(); return
        try:
            for kind, data in parse_sse(response):
                if failed: raise RuntimeError('Switch heartbeat failed')
                if kind=='connection_state':
                    if data.get('agent_id')!=self.config['runner_agent_id']:
                        raise ValueError('Unexpected authenticated agent identity')
                    worker=threading.Thread(target=beat,args=(data,),daemon=True);worker.start()
                if kind in ('gap','evicted'):
                    raise StreamReconciliationRequired(f'Switch stream reported {kind}; operator reconciliation required')
                yield kind,data
            raise ConnectionError('Switch stream ended; restart will resume the durable cursor')
        finally:
            stop.set(); response.close()
            if worker: worker.join(timeout=1)


def parse_sse(stream):
    kind='message'; pieces=[]; size=0
    while True:
        line=stream.readline(1_000_001)
        if not line: return
        size+=len(line)
        if size>1_000_000: raise ValueError('Oversized SSE frame')
        line=line.decode('utf8').rstrip('\r\n')
        if not line:
            if pieces: yield kind,json.loads('\n'.join(pieces))
            else: yield 'keepalive',{}
            kind='message';pieces=[];size=0
        elif line.startswith('event:'): kind=line[6:].strip()
        elif line.startswith('data:'): pieces.append(line[5:].lstrip())


class EventRouter:
    def __init__(self,runner,config):
        self.runner,self.config=runner,config
        senders=[item['sender_id'] for item in config['roles'].values()]
        if len(senders)!=len(set(senders)) or set(senders)&set(config['humans']):
            raise ValueError('Agent and human identities must be distinct')
        self.pact = None
        if config.get('workflows'):
            from .pact import Pact
            self.pact = Pact(runner, config)

    def consume(self,event):
        if event.get('type')!='message': return 'ignored'
        room=event.get('room_id')
        if room not in self.config['rooms']: return 'ignored'
        p=event.get('payload',{}); sender=p.get('sender'); body=p.get('body','')
        if not isinstance(body,str) or len(body)>40000: raise ValueError('Invalid message body')
        body = body.strip()
        source=p.get('message_id')
        if not isinstance(source,str) or not source: raise ValueError('Missing server message ID')
        server=self.config['server_id']
        if self.pact:
            pact_rooms = {s[k] for s in self.pact.workflows.values() for k in ('team_room_id','engine_room_id')}
            if body.startswith('@relay-runner /pact '):
                if p.get('sender_kind') == 'platform': raise ValueError('Human command required')
                return self.pact.command(room, sender, source, body[len('@relay-runner /pact '):])
            if body.startswith('RELAY_RESULT\n'):
                message = json.loads(body[len('RELAY_RESULT\n'):])
                if isinstance(message, dict) and isinstance(message.get('handoff_id'), str):
                    rows = self.runner.ledger.rows('SELECT * FROM handoffs WHERE id=?', (message['handoff_id'],))
                    if rows:
                        payload = json.loads(rows[0]['payload'])
                        if payload.get('workflow'):
                            if p.get('sender_kind') == 'platform' or sender != payload['expected_sender'] or room != payload['result_room_id']:
                                raise ValueError('Unrecognized Pact result sender or room')
                            if not isinstance(message.get('result'), dict): raise ValueError('Invalid result')
                            if rows[0]['status'] in ('uncertain','timed_out'): return 'ignored'
                            return self.runner.complete(message['handoff_id'], server, room, rows[0]['role'], message['result'], source)
            if room in pact_rooms:
                # Never let the legacy start parser bypass Pact confirmations.
                return 'ignored'
        # Authenticated envelope IDs, never display names or JSON claims, establish authority.
        if sender in self.config['humans'] and p.get('sender_kind')!='platform':
            control_prefix = '@relay-runner /relay '
            if body.startswith(control_prefix):
                command = body[len(control_prefix):]
                if command in ('status', 'pause', 'resume'):
                    return self.runner.control(room, source, sender, command)
                if command.startswith('start '):
                    return self.runner.submit(server,room,self.config['rooms'][room],source,command[6:])
            prefix=f"@{self.config['roles']['planner']['name']} /relay start "
            if body.startswith(prefix):
                return self.runner.submit(server,room,self.config['rooms'][room],source,body[len(prefix):])
            # Publication approval deliberately stays a trusted operator CLI step in v0.1.
            return 'ignored'
        if body.startswith('RELAY_RESULT\n'):
            role=next((role for role,item in self.config['roles'].items() if item['sender_id']==sender),None)
            if role is None or p.get('sender_kind')=='platform': raise ValueError('Unrecognized result sender')
            result=json.loads(body[len('RELAY_RESULT\n'):])
            if not isinstance(result, dict):
                return 'ignored'

            handoff_id = result.get('handoff_id')

            if not isinstance(handoff_id, str) or not isinstance(result.get('result'), dict):
                return 'ignored'

            rows = self.runner.ledger.rows(
                'SELECT status FROM handoffs WHERE id=?',
                (handoff_id,)
            )

            if not rows:
                print(f'[relay] Ignored unknown handoff: {handoff_id}', flush=True)
                return 'ignored'

            if rows[0]['status'] in ('timed_out', 'uncertain'):
                print(
                    f"[relay] Handoff {handoff_id} requires operator reconciliation: "
                    f"{rows[0]['status']}",
                    flush=True
                )
                return 'ignored'

            return self.runner.complete(handoff_id,server,room,role,result['result'],source)
        return 'ignored'
