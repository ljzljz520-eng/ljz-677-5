# -*- coding: utf-8 -*-
"""数据库层：SQLite 持久化（批次 / 年检记录 / 异常列表 / 报告汇总）"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "INSPECTION_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "inspection.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no      TEXT UNIQUE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING/PROCESSING/SUCCESS/PARTIAL/TIMEOUT/FAILED
    total_count   INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inspection_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL REFERENCES batches(id),
    plate_no        TEXT NOT NULL,        -- 车牌
    vin             TEXT NOT NULL,        -- 车架号
    result          TEXT NOT NULL,        -- 检测结果: 合格/不合格
    org_code        TEXT NOT NULL,        -- 检测机构
    inspection_date TEXT NOT NULL,        -- 检测日期 YYYY-MM-DD
    status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/SUBMITTED/INVALID/FAILED
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_batch ON inspection_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_records_plate_date ON inspection_records(plate_no, inspection_date);

CREATE TABLE IF NOT EXISTS exceptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id    INTEGER,
    batch_id     INTEGER,
    batch_no     TEXT,
    plate_no     TEXT,
    vin          TEXT,
    error_type   TEXT NOT NULL,           -- VALIDATION/BUSINESS/TIMEOUT
    error_message TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN/RESOLVED
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_exceptions_status ON exceptions(status);

CREATE TABLE IF NOT EXISTS report_summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type      TEXT NOT NULL,       -- BATCH(批次报告) / DAILY(按日+机构汇总)
    report_key       TEXT NOT NULL,       -- 批次号 或 日期|机构
    report_date      TEXT,
    org_code         TEXT,
    total_records    INTEGER NOT NULL DEFAULT 0,
    success_records  INTEGER NOT NULL DEFAULT 0,
    failed_records   INTEGER NOT NULL DEFAULT 0,
    exception_count  INTEGER NOT NULL DEFAULT 0,
    payload          TEXT,                -- JSON 明细
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(report_type, report_key)
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    """事务上下文：正常结束 commit，异常 rollback。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with tx() as conn:
        conn.executescript(SCHEMA)


def rows_to_dicts(rows):
    return [dict(r) for r in rows]
