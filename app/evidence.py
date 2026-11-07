"""Controlled test profile. Tests execute project code: this is NOT a security sandbox."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from .git_manager import verify_candidate


class Evidence:
    def __init__(self, state_dir):
        self.root = Path(state_dir) / 'evidence'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def run(self, path, commit, brief_hash, revision, task_id):
        verify_candidate(path, commit)
        # Fixed wrapper prevents arbitrary shell commands from model-produced messages.
        started = time.time()
        with tempfile.TemporaryDirectory(prefix='review-', dir=self.root) as temp:
            clone = Path(temp) / 'checkout'
            archive=Path(temp)/'candidate.zip'
            subprocess.run(['git','-C',str(path),'archive','--format=zip',f'--output={archive}',commit],
                           check=True,capture_output=True,timeout=60)
            clone.mkdir()
            with zipfile.ZipFile(archive) as files:
                for item in files.infolist():
                    destination=(clone/item.filename).resolve()
                    if not destination.is_relative_to(clone.resolve()) or '\\' in item.filename:
                        raise ValueError('Unsafe path in candidate archive')
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Symlink artifacts require a separately isolated test runner')
                files.extractall(clone)
            # Both conventional layouts are allowed, chosen from the fixed commit,
            # never from a model-provided command. Prefer an explicit tests folder.
            test_root = 'tests' if (clone/'tests').is_dir() else '.'
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', test_root, '-v']
            env = {k:v for k,v in os.environ.items() if k in
                   ('PATH','SystemRoot','WINDIR','TEMP','TMP','LANG','LC_ALL')}
            env.update(PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0')
            # Private HOME avoids handing test processes normal CLI credential locations.
            # The shared Unix account can still read other files; OS isolation comes later.
            home = Path(temp)/'home'; home.mkdir()
            env['HOME'] = str(home); env['USERPROFILE'] = str(home)
            log = Path(temp)/'tests.log'
            timed_out = False
            with log.open('wb') as out:
                try:
                    result = subprocess.run(command, cwd=clone, env=env, stdout=out,
                                            stderr=subprocess.STDOUT, timeout=90)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = None; timed_out = True
            output = log.read_bytes()
            # An empty test suite must not qualify as successful delivery.
            import re
            counts = re.findall(rb'Ran (\d+) tests? in', output)
            count = int(counts[-1]) if counts else 0
            record = dict(task_id=task_id, revision=revision, brief_hash=brief_hash,
                          commit_sha=commit, command=command, exit_code=code,
                          timed_out=timed_out, test_count=count,
                          passed=code == 0 and count > 0 and not timed_out,
                          output_sha256=hashlib.sha256(output).hexdigest(),
                          started=started, finished=time.time())
            verify_candidate(path, commit)
            key = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
            (self.root / f'{key}.log').write_bytes(output)
            (self.root / f'{key}.json').write_text(json.dumps(record, indent=2),encoding='utf8')
            return record
