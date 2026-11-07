import json
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

SCHEMA = """
CREATE TABLE rooms(server_id TEXT NOT NULL, room_id TEXT NOT NULL, name TEXT NOT NULL,
 path TEXT NOT NULL UNIQUE, PRIMARY KEY(server_id, room_id));
CREATE TABLE tasks(id TEXT PRIMARY KEY, server_id TEXT NOT NULL, room_id TEXT NOT NULL,
 source_id TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, brief TEXT, brief_hash TEXT, commit_sha TEXT,
 repairs INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
 UNIQUE(server_id,room_id,source_id),
 FOREIGN KEY(server_id,room_id) REFERENCES rooms(server_id,room_id));
CREATE TABLE handoffs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 role TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 payload TEXT NOT NULL, created REAL NOT NULL, deadline REAL,
 receipt TEXT, UNIQUE(task_id,revision,role,id));
CREATE UNIQUE INDEX one_active_task ON handoffs((1))
 WHERE status IN ('pending','sending','sent','uncertain','timed_out');
CREATE TABLE runs(id TEXT PRIMARY KEY REFERENCES handoffs(id), task_id TEXT NOT NULL,
 role TEXT NOT NULL, started REAL NOT NULL, finished REAL, outcome TEXT, detail TEXT);
CREATE TABLE reviews(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL, brief_hash TEXT NOT NULL, commit_sha TEXT NOT NULL,
 verdict TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE approvals(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL, commit_sha TEXT NOT NULL, actor_id TEXT NOT NULL,
 source_id TEXT NOT NULL, action TEXT NOT NULL, created REAL NOT NULL,
 UNIQUE(task_id,source_id));
CREATE TABLE events(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL,
 data TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TABLE inbox(source_id TEXT PRIMARY KEY, processed REAL NOT NULL);
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version=1;
"""


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
            elif version != 1:
                raise RuntimeError(f'Unsupported ledger version {version}; do not reset it')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=15000')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, params=()):
        db = self.connect()
        try:
            return [dict(row) for row in db.execute(sql, params)]
        finally:
            db.close()

    @staticmethod
    def event(db, task_id, kind, data, now):
        db.execute('INSERT INTO events(task_id,kind,data,created) VALUES(?,?,?,?)',
                   (task_id, kind, json.dumps(data, sort_keys=True), now))
