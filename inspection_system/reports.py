"""汇总报告：统计 + 持久化快照到 report_snapshots 表。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import utcnow


def _group_count(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {row[0]: row[1] for row in conn.execute(sql).fetchall()}


def build_report(conn: sqlite3.Connection) -> dict[str, Any]:
    record_counts = _group_count(
        conn, "SELECT status, COUNT(*) FROM records GROUP BY status"
    )
    batch_counts = _group_count(
        conn, "SELECT status, COUNT(*) FROM batches GROUP BY status"
    )
    error_counts = _group_count(
        conn, "SELECT error_type, COUNT(*) FROM exceptions GROUP BY error_type"
    )
    by_result = _group_count(
        conn,
        "SELECT COALESCE(NULLIF(result,''),'(空)'), COUNT(*) FROM records "
        "WHERE status IN ('submitted','pending','validated','submitting','rejected') "
        "GROUP BY result",
    )
    by_org = _group_count(
        conn,
        "SELECT COALESCE(NULLIF(organization,''),'(空)'), COUNT(*) FROM records "
        "WHERE status <> 'invalid' GROUP BY organization ORDER BY COUNT(*) DESC LIMIT 20",
    )

    total_imported = sum(record_counts.values())
    total_submitted = record_counts.get("submitted", 0)
    total_rejected_records = record_counts.get("rejected", 0)
    total_exceptions = conn.execute(
        "SELECT COUNT(*) FROM exceptions"
    ).fetchone()[0]

    return {
        "generated_at": utcnow(),
        "total_imported": total_imported,
        "total_submitted": total_submitted,
        "total_accepted": total_submitted,
        "total_rejected": total_rejected_records,
        "total_exceptions": total_exceptions,
        "pending_batches": batch_counts.get("pending", 0)
                           + batch_counts.get("timeout", 0),
        "timeout_batches": batch_counts.get("timeout", 0),
        "records_by_status": record_counts,
        "batches_by_status": batch_counts,
        "by_result": by_result,
        "by_organization": by_org,
        "by_error_type": error_counts,
    }


def save_report(conn: sqlite3.Connection, report: dict[str, Any] | None = None) -> int:
    report = report or build_report(conn)
    cur = conn.execute(
        "INSERT INTO report_snapshots(generated_at, total_imported, total_submitted, "
        "total_accepted, total_rejected, total_exceptions, pending_batches, "
        "timeout_batches, by_result, by_organization, by_status, by_error_type) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (report["generated_at"], report["total_imported"], report["total_submitted"],
         report["total_accepted"], report["total_rejected"], report["total_exceptions"],
         report["pending_batches"], report["timeout_batches"],
         json.dumps(report["by_result"], ensure_ascii=False),
         json.dumps(report["by_organization"], ensure_ascii=False),
         json.dumps(report["records_by_status"], ensure_ascii=False),
         json.dumps(report["by_error_type"], ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def list_snapshots(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM report_snapshots ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("by_result", "by_organization", "by_status", "by_error_type"):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                d[key] = {}
        out.append(d)
    return out
