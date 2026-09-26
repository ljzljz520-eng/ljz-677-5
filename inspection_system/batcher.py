"""分批：对 imported 记录做校验与去重，合格记录按 BATCH_SIZE 切分为待提交批次。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import config
from .db import log_action, utcnow
from .validators import validate_record

BATCH_NO_PREFIX = "B"


def _next_batch_no(conn: sqlite3.Connection) -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"{BATCH_NO_PREFIX}{today}"
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM batches WHERE batch_no LIKE ?", (prefix + "%",)
    ).fetchone()
    seq = (row["c"] or 0) + 1
    return f"{prefix}{seq:04d}"


def validate_and_batch(conn: sqlite3.Connection,
                       batch_size: int | None = None) -> dict:
    """扫描所有 imported 记录：

    - 字段校验失败 / 与库内（非异常）记录重复 -> 写入 exceptions，记录置 invalid；
    - 通过的记录 -> 更新为 validated，并打包为 pending 批次。
    返回统计字典。
    """
    size = batch_size or config.BATCH_SIZE
    rows = conn.execute(
        "SELECT * FROM records WHERE status='imported' ORDER BY id"
    ).fetchall()

    valid_ids: list[int] = []
    invalid = 0
    now = utcnow()
    # 本轮内去重：规范化 VIN+日期
    seen: set[tuple[str, str]] = set()

    for r in rows:
        normalized, errors = validate_record({
            "plate_no": r["plate_no"],
            "vin": r["vin"],
            "result": r["result"],
            "organization": r["organization"],
            "inspect_date": r["inspect_date"],
            "_source_row": r["source_row"] or 0,
        })
        if errors:
            _mark_exception(
                conn, r, "validation_error", "; ".join(errors), now
            )
            invalid += 1
            continue

        key = (normalized["vin"], normalized["inspect_date"])
        # 仅与更早（id 更小）且已通过校验/已上报的记录比对，避免“首次出现”被后续重复行反咬
        dup = conn.execute(
            "SELECT id FROM records WHERE vin=? AND inspect_date=? "
            "AND status IN ('validated','pending','submitting','submitted',"
            "'rejected','partial','completed') AND id<? LIMIT 1",
            (normalized["vin"], normalized["inspect_date"], r["id"]),
        ).fetchone()
        if key in seen or dup:
            _mark_exception(
                conn, r, "duplicate",
                f"重复记录：相同车架号 {normalized['vin']} 与检测日期 "
                f"{normalized['inspect_date']}"
                + (f"（已存在记录 #{dup['id']}）" if dup else "（本批次内重复）"),
                now,
            )
            invalid += 1
            continue
        seen.add(key)

        conn.execute(
            "UPDATE records SET plate_no=?, vin=?, result=?, organization=?, "
            "inspect_date=?, status='validated', updated_at=? WHERE id=?",
            (normalized["plate_no"], normalized["vin"], normalized["result"],
             normalized["organization"], normalized["inspect_date"], now, r["id"]),
        )
        valid_ids.append(r["id"])

    # 切分批次
    batches_created = 0
    for i in range(0, len(valid_ids), size):
        chunk = valid_ids[i:i + size]
        batch_no = _next_batch_no(conn)
        cur = conn.execute(
            "INSERT INTO batches(batch_no, status, total, attempts, created_at, updated_at) "
            "VALUES (?, 'pending', ?, 0, ?, ?)",
            (batch_no, len(chunk), now, now),
        )
        batch_id = cur.lastrowid
        conn.executemany(
            "UPDATE records SET batch_id=?, status='pending', updated_at=? WHERE id=?",
            [(batch_id, now, rid) for rid in chunk],
        )
        log_action(conn, "create_batch", "batch", batch_id,
                   detail=f"{batch_no} records={len(chunk)}")
        batches_created += 1

    return {
        "scanned": len(rows),
        "valid": len(valid_ids),
        "invalid": invalid,
        "batches_created": batches_created,
    }


def _mark_exception(conn: sqlite3.Connection, record_row: sqlite3.Row,
                    error_type: str, message: str, now: str) -> None:
    conn.execute(
        "INSERT INTO exceptions(record_id, plate_no, vin, organization, inspect_date, "
        "result, error_type, error_message, source_file, source_row, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (record_row["id"], record_row["plate_no"], record_row["vin"],
         record_row["organization"], record_row["inspect_date"], record_row["result"],
         error_type, message, record_row["source_file"], record_row["source_row"], now),
    )
    conn.execute(
        "UPDATE records SET status='invalid', error_message=?, updated_at=? WHERE id=?",
        (message, now, record_row["id"]),
    )
