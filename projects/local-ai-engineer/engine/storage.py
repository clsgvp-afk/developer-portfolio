import hashlib
import json
import re
import sqlite3
import time


def redact(text):
    return re.sub(r'(?i)(api[_-]?key|password|secret|token)(\s*[=:]\s*)[^\s,;]+', r'\1\2[REDACTED]', str(text))


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS memories(
            id INTEGER PRIMARY KEY, category TEXT, content TEXT, verified INTEGER,
            fingerprint TEXT UNIQUE, created REAL);
          CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, project TEXT, goal TEXT, status TEXT, state TEXT, updated REAL);
          CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, task TEXT, kind TEXT, detail TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS practice(
            id INTEGER PRIMARY KEY, exercise TEXT, passed INTEGER, attempts INTEGER, created REAL);
          CREATE TABLE IF NOT EXISTS task_control(task TEXT PRIMARY KEY, action TEXT NOT NULL);
        ''')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(memories)')}
            if 'origin' not in columns:
                self.db.execute("ALTER TABLE memories ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'")
        try:
            self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS lookup USING fts5(content, content=memories, content_rowid=id)')
            self.db.executescript('''
              CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN
                INSERT INTO lookup(rowid,content) VALUES(new.id,new.content); END;
            ''')
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False

    def remember(self, category, content, verified=False, origin='local'):
        content = redact(content)[:16000]
        digest = hashlib.sha256((category + content).encode()).hexdigest()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO memories(category,content,verified,fingerprint,created,origin) VALUES(?,?,?,?,?,?)',
                            (category, content, int(verified), digest, time.time(), origin))

    def control(self, task, action=None):
        if action is not None:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO task_control VALUES(?,?)', (task, action))
        row = self.db.execute('SELECT action FROM task_control WHERE task=?', (task,)).fetchone()
        return row[0] if row else None

    def clear_control(self, task):
        with self.db:
            self.db.execute('DELETE FROM task_control WHERE task=?', (task,))

    def retrieve(self, query, limit=4):
        words = re.findall(r'\w{3,}', query)[:12]
        if not words:
            return []
        if self.fts:
            rows = self.db.execute('SELECT m.* FROM lookup JOIN memories m ON m.id=lookup.rowid WHERE lookup MATCH ? ORDER BY m.verified DESC, rank, m.created DESC LIMIT ?',
                                   (' OR '.join('"' + w + '"' for w in words), limit))
        else:
            rows = self.db.execute('SELECT * FROM memories WHERE ' + ' OR '.join('content LIKE ?' for _ in words) + ' ORDER BY verified DESC, created DESC LIMIT ?',
                                   (*['%' + w + '%' for w in words], limit))
        return [dict(r) for r in rows]

    def save_task(self, task):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?)',
                            (task['id'], task['project'], task['goal'], task['status'], json.dumps(task, ensure_ascii=False), time.time()))

    def task(self, task_id=None):
        row = self.db.execute('SELECT state FROM tasks ' + ('WHERE id=?' if task_id else 'ORDER BY updated DESC LIMIT 1'),
                              (task_id,) if task_id else ()).fetchone()
        return json.loads(row[0]) if row else None

    def event(self, task, kind, detail):
        with self.db:
            self.db.execute('INSERT INTO events(task,kind,detail,created) VALUES(?,?,?,?)', (task, kind, redact(detail)[:12000], time.time()))

    def command_result(self, task, kind, result):
        detail = json.dumps({k:redact(v) if isinstance(v,str) else v for k,v in result.items()}, ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT INTO events(task,kind,detail,created) VALUES(?,?,?,?)', (task,kind,detail,time.time()))

    def history(self):
        return [dict(r) for r in self.db.execute('SELECT id,project,goal,status FROM tasks ORDER BY updated DESC LIMIT 20')]

    def practice_result(self, exercise, passed, attempts):
        with self.db:
            self.db.execute('INSERT INTO practice(exercise,passed,attempts,created) VALUES(?,?,?,?)', (exercise, int(passed), attempts, time.time()))

    def stats(self):
        rows = [dict(r) for r in self.db.execute('SELECT kind,COUNT(*) AS count FROM events GROUP BY kind')]
        practice = dict(self.db.execute('SELECT COUNT(*) AS total,COALESCE(SUM(passed),0) AS passed,COALESCE(AVG(attempts),0) AS average_attempts FROM practice').fetchone())
        return {'tasks': self.db.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0], 'events': rows, 'practice': practice}

    def export(self, path):
        with open(path, 'w', encoding='utf-8') as out:
            for row in self.db.execute('SELECT category,content FROM memories WHERE verified=1'):
                out.write(json.dumps(dict(row), ensure_ascii=False) + '\n')
