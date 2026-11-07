"""Read-only pilot dashboard. Bind only to loopback; use an SSH tunnel to view."""
import html
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .workspace_manager import safe_path

VISIBLE = {'.py','.md','.txt','.json','.toml','.js','.ts','.tsx','.jsx','.css','.html','.yml','.yaml'}


def read_source(root,relative):
    if not relative or any(part.startswith('.') for part in Path(relative).parts):
        raise ValueError('Hidden/private paths are not shown')
    candidate=safe_path(Path(root)/relative)
    if not candidate.resolve().is_relative_to(Path(root).resolve()) or candidate.suffix not in VISIBLE:
        raise ValueError('Path is outside the visible project source')
    if not candidate.is_file() or candidate.stat().st_size>262144:
        raise ValueError('Only source files up to 256 KiB are shown')
    return candidate.read_text(encoding='utf8')


def serve_viewer(state,port=8765):
    database=(Path(state)/'relay.db').resolve()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            try:
                query=parse_qs(urlsplit(self.path).query)
                db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
                try:
                    rooms=[dict(r) for r in db.execute('SELECT * FROM rooms')]
                    tasks=[dict(r) for r in db.execute('SELECT id,room_id,state,revision,commit_sha FROM tasks ORDER BY created DESC')]
                    events=[dict(r) for r in db.execute('SELECT task_id,kind,created FROM events ORDER BY id DESC LIMIT 100')]
                finally:db.close()
                content='<h1>Relay workspace</h1><p>Read-only pilot. Tasks advance only through verified handoffs.</p>'
                for title,rows in [('Rooms',rooms),('Tasks',tasks),('Recent workflow events',events)]:
                    content+=f'<h2>{title}</h2><pre>'+html.escape(json.dumps(rows,indent=2))+'</pre>'
                if 'room' in query and 'file' in query:
                    room=next(r for r in rooms if r['room_id']==query['room'][0])
                    source=read_source(room['path'],query['file'][0])
                    content+='<h2>Source</h2><pre>'+html.escape(source)+'</pre>'
                body=('<!doctype html><meta charset="utf-8"><title>Relay</title><style>'
                      'body{max-width:1000px;margin:40px auto;padding:20px;font:16px system-ui;background:#101828;color:#e6edf6}'
                      'pre{white-space:pre-wrap;background:#1d2939;padding:20px;border-radius:10px;overflow-wrap:anywhere}'
                      '</style>'+content).encode()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(body)
            except (ValueError,StopIteration,OSError):self.send_error(404,'Not available')
    print(f'Read-only viewer: http://127.0.0.1:{port}; no public listener',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
