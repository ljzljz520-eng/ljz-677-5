"""SQLite 持久层：连接管理、初始化建表、基础数据访问。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_no TEXT NOT NULL,
    vin TEXT NOT NULL,
    result TEXT NOT NULL,
    organization TEXT NOT NULL,
    inspect_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'imported',
    batch_id INTEGER,
    error_message TEXT,
    source_file TEXT,
    source_row INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_vin_date ON records(vin, inspect_date);
CREATE INDEX IF NOT EXISTS idx_records_batch ON records(batch_id);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    total INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    response_code INTEGER,
    request_body TEXT,
    response_body TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_attempt_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);

CREATE TABLE IF NOT EXISTS exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER,
    plate_no TEXT,
    vin TEXT,
    organization TEXT,
    inspect_date TEXT,
    result TEXT,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    source_file TEXT,
    source_row INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES records(id)
);
CREATE INDEX IF NOT EXISTS idx_exceptions_type ON exceptions(error_type);
CREATE INDEX IF NOT EXISTS idx_exceptions_record ON exceptions(record_id);

CREATE TABLE IF NOT EXISTS report_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    total_imported INTEGER NOT NULL,
    total_submitted INTEGER NOT NULL,
    total_accepted INTEGER NOT NULL,
    total_rejected INTEGER NOT NULL,
    total_exceptions INTEGER NOT NULL,
    pending_batches INTEGER NOT NULL,
    timeout_batches INTEGER NOT NULL,
    by_result TEXT NOT NULL,
    by_organization TEXT NOT NULL,
    by_status TEXT NOT NULL,
    by_error_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db_path() -> Path:
    return Path(config.DB_PATH)


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else get_db_path()
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


def log_action(conn: sqlite3.Connection, action: str, entity: str = "",
               entity_id: int | None = None, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(action, entity, entity_id, detail, created_at) "
        "VALUES (?,?,?,?,?)",
        (action, entity, entity_id, detail, utcnow()),
    )
