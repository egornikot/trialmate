"""SQLite storage. One file, no server: the agent's memory and the study team's records."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS studies(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  title TEXT NOT NULL,
  condition TEXT,
  site TEXT,
  coordinator TEXT,
  coordinator_phone TEXT,
  protocol TEXT NOT NULL DEFAULT '',
  nct_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  source TEXT NOT NULL DEFAULT 'demo',
  config TEXT NOT NULL DEFAULT '{}',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS builds(
  id INTEGER PRIMARY KEY,
  nct_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  engine TEXT NOT NULL,
  log TEXT NOT NULL DEFAULT '[]',
  study_id INTEGER,
  error TEXT,
  created_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS patients(
  id INTEGER PRIMARY KEY,
  handle TEXT UNIQUE NOT NULL,
  name TEXT,
  role TEXT NOT NULL DEFAULT 'prospect',
  study_id INTEGER REFERENCES studies(id),
  channel TEXT NOT NULL DEFAULT 'sim',
  space_id TEXT,
  agent_id TEXT,
  facts TEXT NOT NULL DEFAULT '[]',
  opted_out INTEGER NOT NULL DEFAULT 0,
  demo INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_seen_at REAL
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  direction TEXT NOT NULL,
  body TEXT NOT NULL,
  redactions TEXT NOT NULL DEFAULT '',
  external_id TEXT UNIQUE,
  processed INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_patient ON messages(patient_id, id);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  symptom TEXT NOT NULL,
  description TEXT,
  grade INTEGER NOT NULL,
  serious INTEGER NOT NULL DEFAULT 0,
  onset TEXT,
  ongoing INTEGER,
  status TEXT NOT NULL DEFAULT 'new',
  source_message_id INTEGER,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkins(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  took_dose INTEGER,
  wellbeing INTEGER,
  notes TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  title TEXT NOT NULL,
  due_at REAL NOT NULL,
  location TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled'
);
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  body TEXT NOT NULL,
  due_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled',
  created_by TEXT NOT NULL DEFAULT 'agent',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  reason TEXT NOT NULL,
  urgency TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at REAL NOT NULL,
  resolved_at REAL
);
CREATE TABLE IF NOT EXISTS referrals(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  nct_id TEXT NOT NULL,
  trial_title TEXT,
  likely_match TEXT NOT NULL,
  summary TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER NOT NULL REFERENCES patients(id),
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  kind TEXT NOT NULL DEFAULT 'reply',
  idempotency_key TEXT UNIQUE NOT NULL,
  parent_external_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at REAL NOT NULL,
  decided_at REAL,
  sent_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  patient_id INTEGER,
  kind TEXT NOT NULL,
  detail TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def now() -> float:
    return time.time()


class Database:
    """Thread-safe wrapper around one SQLite connection (fine at hackathon scale)."""

    def __init__(self, path: Any) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=10000")
            self._migrate()
            self.conn.executescript(SCHEMA)

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(studies)").fetchall()}
        if cols:
            for name, ddl in (("nct_id", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'active'"),
                              ("source", "TEXT NOT NULL DEFAULT 'demo'"), ("config", "TEXT NOT NULL DEFAULT '{}'"),
                              ("created_at", "REAL")):
                if name not in cols:
                    self.conn.execute(f"ALTER TABLE studies ADD COLUMN {name} {ddl}")

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def insert(self, sql: str, args: tuple = ()) -> int:
        return int(self.run(sql, args).lastrowid)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def get_kv(self, key: str) -> Optional[str]:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self.run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))

    def audit(self, patient_id: Optional[int], kind: str, detail: str = "") -> None:
        self.run("INSERT INTO audit(patient_id,kind,detail,created_at) VALUES(?,?,?,?)",
                 (patient_id, kind, detail[:4000], now()))
