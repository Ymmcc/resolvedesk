from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, plan TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
 module TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, module TEXT NOT NULL, version TEXT NOT NULL,
 content TEXT NOT NULL, customer_id TEXT REFERENCES customers(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, vector TEXT NOT NULL, fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, module TEXT NOT NULL, version TEXT NOT NULL,
 description TEXT NOT NULL, status TEXT NOT NULL, resolution TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), question TEXT NOT NULL,
 status TEXT NOT NULL, mode TEXT NOT NULL, result TEXT, error TEXT,
 created_at TEXT NOT NULL, duration_ms REAL
);
CREATE TABLE IF NOT EXISTS tickets (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
 run_id TEXT UNIQUE REFERENCES runs(id), title TEXT NOT NULL, module TEXT NOT NULL,
 version TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL,
 resolution TEXT NOT NULL DEFAULT '', problem_id TEXT REFERENCES problems(id),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id),
 actor TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_drafts (
 id TEXT PRIMARY KEY, ticket_id TEXT UNIQUE NOT NULL REFERENCES tickets(id),
 title TEXT NOT NULL, content TEXT NOT NULL, module TEXT NOT NULL, version TEXT NOT NULL,
 customer_id TEXT NOT NULL REFERENCES customers(id), status TEXT NOT NULL,
 document_id TEXT REFERENCES documents(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
 actor TEXT NOT NULL, key TEXT NOT NULL, operation TEXT NOT NULL, resource_id TEXT NOT NULL,
 PRIMARY KEY(actor, key)
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('index_revision', 0);
CREATE INDEX IF NOT EXISTS ix_tickets_customer ON tickets(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_customer ON tasks(customer_id);
CREATE INDEX IF NOT EXISTS ix_runs_customer ON runs(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_events_ticket ON ticket_events(ticket_id, id);
"""


class Database:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def initialize(self):
        con = self.connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def all(self, sql, args=()):
        con = self.connect()
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.transaction() as con:
            con.execute(sql, args)

    def index_snapshot(self):
        con = self.connect()
        try:
            con.execute("BEGIN")
            revision = con.execute("SELECT value FROM metadata WHERE key='index_revision'").fetchone()[0]
            rows = [dict(r) for r in con.execute("""SELECT c.*, d.title, d.module, d.version,
                d.customer_id FROM chunks c JOIN documents d ON d.id=c.document_id ORDER BY c.id""")]
            return revision, rows
        finally:
            con.close()

    @staticmethod
    def bump_index(con):
        con.execute("UPDATE metadata SET value=value+1 WHERE key='index_revision'")
