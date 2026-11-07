import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path


def safe_path(path):
    """Reject symlinks/junctions in existing path components, including the root."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Symlink/junction workspace paths are not permitted')
    return path


class WorkspaceManager:
    def __init__(self, root, ledger):
        self.root = safe_path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger = ledger

    def validate(self, path):
        root = safe_path(self.root)
        path = safe_path(path)
        if path.parent != root or path.resolve().parent != root.resolve():
            raise ValueError('Workspace must be a direct child of the authorized root')
        return path

    def ensure_workspace(self, server_id, room_id, room_name):
        for value in (server_id, room_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError('A stable server identity and actual Switch room ID are required')
        identity = {'server_id': server_id, 'room_id': room_id}
        slug = re.sub(r'[^a-z0-9]+', '-', str(room_name).lower()).strip('-')[:40] or 'room'
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
        with self.ledger.transaction() as db:
            row = db.execute('SELECT path FROM rooms WHERE server_id=? AND room_id=?',
                             (server_id, room_id)).fetchone()
            path = self.validate(row['path'] if row else self.root / f'{slug}--{key}')
            marker = path / '.relay-room.json'
            if path.exists():
                if not path.is_dir() or marker.is_symlink() or not marker.is_file():
                    raise ValueError('Existing unowned folder will not be adopted or overwritten')
                if json.loads(marker.read_text(encoding='utf8')) != identity:
                    raise ValueError('Workspace ownership marker mismatch')
                if not row:
                    # A previous attempt may have created the folder before its DB commit.
                    self._git_init(path)
            else:
                if row:
                    raise ValueError('Mapped workspace is missing; recover it instead of silently replacing it')
                path.mkdir(mode=0o700)
                with marker.open('x', encoding='utf8') as stream:
                    json.dump(identity, stream, sort_keys=True)
                self._git_init(path)
            if not row:
                db.execute('INSERT INTO rooms VALUES(?,?,?,?)', (server_id, room_id, room_name, str(path)))
                self.ledger.event(db, None, 'workspace_created', identity, time.time())
            return path

    @staticmethod
    def _git_init(path):
        gitdir = path / '.git'
        if gitdir.is_symlink():
            raise ValueError('Git directory must not be a symlink')
        if gitdir.exists():
            if not gitdir.is_dir():
                raise ValueError('External Git directory/worktree requires explicit adoption')
            return
        subprocess.run(['git', 'init', '--initial-branch=main', str(path)], check=True,
                       capture_output=True, timeout=30)
        # Do not create commits, configure author identity or add remotes automatically.
