"""sqlite 存储（WAL 模式，web 服务以 mcp 用户、execd 以 root 并发访问）。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  created_at INTEGER,
  last_seen INTEGER,
  expires_at INTEGER,
  ip TEXT,
  ua TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts (
  ip TEXT PRIMARY KEY,
  fails INTEGER DEFAULT 0,
  last_ts INTEGER,
  locked_until INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  nonce TEXT UNIQUE,
  agent TEXT,
  host TEXT,
  argv_json TEXT,
  reason TEXT,
  summary TEXT,
  state TEXT,               -- pending/approved/denied/expired/consumed
  created_at INTEGER,
  expires_at INTEGER,
  decided_at INTEGER,
  decided_by TEXT,
  consumed_at INTEGER
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  actor TEXT,
  kind TEXT,
  host TEXT,
  argv_json TEXT,
  detail TEXT,
  ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals(state, expires_at);
CREATE TABLE IF NOT EXISTS metrics (
  ts INTEGER,
  host TEXT,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_host_ts ON metrics(host, ts DESC);
CREATE TABLE IF NOT EXISTS agents (
  key TEXT PRIMARY KEY,     -- sha256 fingerprint (base64, 无前缀)
  label TEXT,
  enabled INTEGER DEFAULT 1,
  created_at INTEGER,
  last_seen INTEGER,
  last_ip TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == db_path:
        return conn
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _local.conn = conn
    _local.path = db_path
    return conn


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')"
    )


def now() -> int:
    return int(time.time())


def execute(db_path: str, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return connect(db_path).execute(sql, params)


def query_all(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    rows = connect(db_path).execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(db_path: str, sql: str, params: tuple = ()) -> dict | None:
    row = connect(db_path).execute(sql, params).fetchone()
    return dict(row) if row else None


def meta_get(db_path: str, key: str) -> str | None:
    row = query_one(db_path, "SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else None


def meta_set(db_path: str, key: str, value: str) -> None:
    execute(db_path, "INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def json_loads(s: str) -> Any:
    return json.loads(s)
