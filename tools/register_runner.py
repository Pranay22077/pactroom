"""Run ONLY on AWS after explicit authorization. Never prints credentials."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    home=Path.home();state=home/'relay-state';state.mkdir(mode=0o700,exist_ok=True)
    token_file=state/'runner-token';identity_file=state/'runner-identity.json'
    if token_file.exists() or identity_file.exists():
        raise SystemExit('Runner credential/identity already exists; refusing to register or overwrite')
    values={}
    for line in (home/'relay-switch'/'.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);values[key.strip()]=value.strip().strip('\"').strip("'")
    token=values.get('AGENT_REGISTRATION_TOKEN')
    if not token:raise SystemExit('Existing registration token unavailable; no changes made')
    data={'name':'relay-runner','display_name':'Relay Runner',
          'description':'Deterministic Relay workspace and task coordinator. No model; no agent-management privilege.',
          'connector_type':'relay-runner','overwrite':False,
          'integration_profile':{'connection_model':'always_on','message_exchange':True,
          'pre_invocation_mediation':[],'post_invocation_mediation':[],'event_reporting':[]}}
    req=Request('http://127.0.0.1:8000/agents',data=json.dumps(data).encode(),method='POST',
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=30) as response:result=json.load(response)
    except HTTPError as error:
        raise SystemExit(f'Registration returned HTTP {error.code}; body omitted; no overwrite attempted') from None
    if not result.get('api_key') or not result.get('id'):raise SystemExit('Unexpected registration response; inspect without re-registering')
    fd=os.open(token_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(result['api_key'])
    identity={'id':result['id'],'name':'relay-runner'}
    fd=os.open(identity_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(identity,stream)
    print('Created Relay Runner identity; credential saved privately on AWS.')
    print('Agent ID:',identity['id'])


if __name__=='__main__':main()
