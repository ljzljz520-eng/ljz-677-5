"""批次处理：提交 pending / timeout 批次，落库提交结果，业务错误写入异常列表。"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .db import log_action, utcnow
from .regulator import BusinessError, RegulatorClient, SubmissionResult, TransientError

# 可被重试的批次状态
RETRIABLE_STATUSES = ("pending", "timeout", "retrying")


def _fetch_batch_records(conn: sqlite3.Connection, batch_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM records WHERE batch_id=? AND status IN ('pending','submitting',"
        "'rejected') ORDER BY id", (batch_id,)
    ).fetchall()


def _process_one(db_path: str, batch_id: int, client: RegulatorClient | None,
                 only_statuses: tuple[str, ...]) -> dict:
    """在独立连接中处理单个批次（线程安全）。返回结果摘要。"""
    from .db import connect

    conn = connect(db_path)
    try:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch or batch["status"] not in only_statuses:
            return {"batch_id": batch_id, "skipped": True}

        records = _fetch_batch_records(conn, batch_id)
        if not records:
            return {"batch_id": batch_id, "skipped": True, "reason": "no_records"}

        now = utcnow()
        conn.execute(
            "UPDATE batches SET status='submitting', attempts=attempts+1, "
            "last_attempt_at=?, updated_at=? WHERE id=?",
            (now, now, batch_id),
        )
        conn.execute(
            "UPDATE records SET status='submitting', updated_at=? WHERE batch_id=?",
            (now, batch_id),
        )
        conn.commit()

        payload = {
            "batchNo": batch["batch_no"],
            "total": len(records),
        }
        cli = client or RegulatorClient()
        try:
            result: SubmissionResult = cli.submit(batch["batch_no"], records)
        except TransientError as e:
            return _handle_timeout(conn, batch_id, batch["batch_no"], e, payload)
        except BusinessError as e:
            return _handle_business_reject(conn, batch_id, batch["batch_no"], e, payload)

        return _apply_submission_result(conn, batch_id, batch["batch_no"],
                                        result, payload)
    finally:
        conn.close()


def _handle_timeout(conn, batch_id, batch_no, error, payload) -> dict:
    now = utcnow()
    conn.execute(
        "UPDATE batches SET status='timeout', error_message=?, last_attempt_at=?, "
        "updated_at=?, request_body=? WHERE id=?",
        (str(error), now, now, json.dumps(payload, ensure_ascii=False), batch_id),
    )
    conn.execute(
        "UPDATE records SET status='pending', updated_at=? WHERE batch_id=?",
        (now, batch_id),
    )
    log_action(conn, "batch_timeout", "batch", batch_id, detail=str(error))
    conn.commit()
    return {"batch_id": batch_id, "batch_no": batch_no, "status": "timeout",
            "error": str(error)}


def _handle_business_reject(conn, batch_id, batch_no, error: BusinessError, payload) -> dict:
    now = utcnow()
    conn.execute(
        "UPDATE batches SET status='rejected', error_message=?, response_code=?, "
        "response_body=?, last_attempt_at=?, updated_at=?, request_body=? WHERE id=?",
        (str(error), error.code, json.dumps(error.response, ensure_ascii=False),
         now, now, json.dumps(payload, ensure_ascii=False), batch_id),
    )
    records = conn.execute(
        "SELECT * FROM records WHERE batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()
    for r in records:
        conn.execute(
            "INSERT INTO exceptions(record_id, plate_no, vin, organization, inspect_date, "
            "result, error_type, error_message, source_file, source_row, created_at) "
            "VALUES (?,?,?,?,?,?, 'business_reject', ?, ?, ?, ?)",
            (r["id"], r["plate_no"], r["vin"], r["organization"], r["inspect_date"],
             r["result"], f"监管业务驳回：{error}", r["source_file"], r["source_row"], now),
        )
        conn.execute(
            "UPDATE records SET status='rejected', error_message=?, updated_at=? WHERE id=?",
            (f"监管业务驳回：{error}", now, r["id"]),
        )
    log_action(conn, "batch_rejected", "batch", batch_id,
               detail=f"{len(records)} 条记录业务驳回: {error}")
    conn.commit()
    return {"batch_id": batch_id, "batch_no": batch_no, "status": "rejected",
            "rejected": len(records), "error": str(error)}


def _apply_submission_result(conn, batch_id, batch_no, result: SubmissionResult,
                             payload) -> dict:
    now = utcnow()
    records = {r["id"]: r for r in conn.execute(
        "SELECT * FROM records WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()}
    accepted_set = set(result.accepted)
    # 监管端未回传明细时，默认全部接收
    if not accepted_set and not result.rejected:
        accepted_set = set(records.keys())

    rejected_rows: list[dict] = []
    accepted_count = 0
    for rid, r in records.items():
        if rid in accepted_set:
            conn.execute(
                "UPDATE records SET status='submitted', error_message=NULL, "
                "updated_at=? WHERE id=?", (now, rid))
            accepted_count += 1
        else:
            rej_info = next((x for x in result.rejected
                             if int(x.get("recordId", -1)) == rid), None)
            reason = (rej_info or {}).get("reason", "监管驳回（未说明原因）")
            msg = f"监管驳回：{reason}"
            conn.execute(
                "UPDATE records SET status='rejected', error_message=?, updated_at=? WHERE id=?",
                (msg, now, rid))
            conn.execute(
                "INSERT INTO exceptions(record_id, plate_no, vin, organization, inspect_date, "
                "result, error_type, error_message, source_file, source_row, created_at) "
                "VALUES (?,?,?,?,?,?, 'business_reject', ?, ?, ?, ?)",
                (r["id"], r["plate_no"], r["vin"], r["organization"], r["inspect_date"],
                 r["result"], msg, r["source_file"], r["source_row"], now))
            rejected_rows.append({"record_id": rid, "reason": reason})

    batch_status = "completed" if not rejected_rows else "partial"
    conn.execute(
        "UPDATE batches SET status=?, accepted=?, rejected=?, response_code=?, "
        "response_body=?, last_attempt_at=?, updated_at=?, request_body=? WHERE id=?",
        (batch_status, accepted_count, len(rejected_rows), result.code,
         json.dumps(result.raw, ensure_ascii=False), now, now,
         json.dumps(payload, ensure_ascii=False), batch_id),
    )
    log_action(conn, "batch_submitted", "batch", batch_id,
               detail=f"{batch_no} 接收 {accepted_count} 驳回 {len(rejected_rows)} "
                      f"尝试 {result.attempts}")
    conn.commit()
    return {"batch_id": batch_id, "batch_no": batch_no, "status": batch_status,
            "accepted": accepted_count, "rejected": len(rejected_rows),
            "rejected_detail": rejected_rows, "attempts": result.attempts}


def process_pending(conn: sqlite3.Connection, db_path: str,
                    client: RegulatorClient | None = None,
                    max_workers: int | None = None) -> dict:
    """处理所有 pending 批次（新导入后的主流程）。"""
    rows = conn.execute(
        "SELECT id FROM batches WHERE status='pending' ORDER BY id"
    ).fetchall()
    ids = [r["id"] for r in rows]
    return _run(db_path, ids, client, max_workers or config.MAX_WORKERS,
                only_statuses=("pending",))


def retry_timeouts(conn: sqlite3.Connection, db_path: str,
                   client: RegulatorClient | None = None,
                   max_workers: int | None = None,
                   batch_ids: list[int] | None = None) -> dict:
    """重试超时（或处理中残留 retrying）批次；可限定 batch_ids。"""
    if batch_ids:
        placeholders = ",".join("?" for _ in batch_ids)
        rows = conn.execute(
            f"SELECT id FROM batches WHERE status IN ('timeout','retrying') "
            f"AND id IN ({placeholders}) ORDER BY id", batch_ids
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM batches WHERE status IN ('timeout','retrying') ORDER BY id"
        ).fetchall()
    ids = [r["id"] for r in rows]
    return _run(db_path, ids, client, max_workers or config.MAX_WORKERS,
                only_statuses=("timeout", "retrying", "pending"))


def _run(db_path: str, batch_ids: list[int], client, workers: int,
         only_statuses: tuple[str, ...]) -> dict:
    results = []
    if not batch_ids:
        return {"processed": 0, "results": []}
    workers = max(1, min(workers, len(batch_ids)))
    if workers == 1:
        for bid in batch_ids:
            results.append(_process_one(db_path, bid, client, only_statuses))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, db_path, bid, client, only_statuses): bid
                       for bid in batch_ids}
            for fut in as_completed(futures):
                results.append(fut.result())
    # 保持按 batch_id 排序，输出稳定
    results.sort(key=lambda x: x.get("batch_id", 0))
    summary = {
        "processed": len([r for r in results if not r.get("skipped")]),
        "completed": sum(1 for r in results if r.get("status") == "completed"),
        "partial": sum(1 for r in results if r.get("status") == "partial"),
        "timeout": sum(1 for r in results if r.get("status") == "timeout"),
        "rejected": sum(1 for r in results if r.get("status") == "rejected"),
        "results": results,
    }
    return summary
