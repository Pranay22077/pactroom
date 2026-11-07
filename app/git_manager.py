import re
import subprocess


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def verify_candidate(path, commit):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('An exact 40-character Git commit is required')
    if git(path, 'rev-parse', '--show-toplevel').replace('\\', '/') != str(path.resolve()).replace('\\', '/'):
        raise ValueError('Workspace is not its own repository')
    if git(path, 'rev-parse', 'HEAD') != commit:
        raise ValueError('Candidate commit is no longer HEAD')
    if git(path, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Commit all intended files before review; working tree is not clean')
    return commit
