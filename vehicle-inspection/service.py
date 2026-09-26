# -*- coding: utf-8 -*-
"""业务逻辑：导入建批、分批校验、提交监管、超时重试、异常列表、报告汇总持久化"""
import json
from datetime import datetime

from db import tx, rows_to_dicts
from validator import validate_record
from regulator import RegulatorTimeout

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 导入建批 ----------

def create_batch(records):
    if not records:
        raise ValueError("导入记录不能为空")
    batch_no = "B" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO batches(batch_no,status,total_count,pending_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (batch_no, "PENDING", len(records), len(records), NOW(), NOW()))
        batch_id = cur.lastrowid
        for r in records:
            conn.execute(
                "INSERT INTO inspection_records"
                "(batch_id,plate_no,vin,result,org_code,inspection_date,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,'PENDING',?,?)",
                (batch_id, (r.get("plate_no") or "").strip().upper(),
                 (r.get("vin") or "").strip().upper(),
                 (r.get("result") or "").strip(),
                 (r.get("org_code") or "").strip().upper(),
                 (r.get("inspection_date") or "").strip(), NOW(), NOW()))
    return {"batch_id": batch_id, "batch_no": batch_no, "total": len(records)}


# ---------- 批次提交 / 重试 ----------

def _add_exception(conn, record, batch_no, error_type, message):
    """写入异常列表（同一记录同一类型未处理的异常不重复写入）"""
    dup = conn.execute(
        "SELECT 1 FROM exceptions WHERE record_id=? AND error_type=? AND status='OPEN'",
        (record["id"], error_type)).fetchone()
    if dup:
        return
    conn.execute(
        "INSERT INTO exceptions(record_id,batch_id,batch_no,plate_no,vin,error_type,error_message,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (record["id"], record["batch_id"], batch_no, record["plate_no"],
         record["vin"], error_type, message, NOW()))


def _is_duplicate(conn, rec):
    """业务规则：同一车牌同一检测日期已成功上报过的，视为重复上报"""
    return conn.execute(
        "SELECT 1 FROM inspection_records"
        " WHERE plate_no=? AND inspection_date=? AND status='SUBMITTED' AND batch_id<>?",
        (rec["plate_no"], rec["inspection_date"], rec["batch_id"])).fetchone() is not None


def _refresh_batch_counts(conn, batch_id):
    c = conn.execute(
        "SELECT"
        " SUM(CASE WHEN status='SUBMITTED' THEN 1 ELSE 0 END) ok,"
        " SUM(CASE WHEN status IN ('INVALID','FAILED') THEN 1 ELSE 0 END) bad,"
        " SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) pend"
        " FROM inspection_records WHERE batch_id=?", (batch_id,)).fetchone()
    ok, bad, pend = c["ok"] or 0, c["bad"] or 0, c["pend"] or 0
    if pend > 0:
        status = "TIMEOUT"
    elif bad == 0:
        status = "SUCCESS"
    elif ok == 0:
        status = "FAILED"
    else:
        status = "PARTIAL"
    conn.execute(
        "UPDATE batches SET success_count=?,fail_count=?,pending_count=?,status=?,updated_at=?"
        " WHERE id=?", (ok, bad, pend, status, NOW(), batch_id))
    return {"success": ok, "failed": bad, "pending": pend, "status": status}


def _persist_batch_report(conn, batch):
    """批次报告汇总 -> 持久化到 report_summaries"""
    counts = conn.execute(
        "SELECT"
        " COUNT(*) total,"
        " SUM(CASE WHEN status='SUBMITTED' THEN 1 ELSE 0 END) ok,"
        " SUM(CASE WHEN status IN ('INVALID','FAILED') THEN 1 ELSE 0 END) bad"
        " FROM inspection_records WHERE batch_id=?", (batch["id"],)).fetchone()
    exc = conn.execute(
        "SELECT COUNT(*) c FROM exceptions WHERE batch_id=? AND status='OPEN'",
        (batch["id"],)).fetchone()["c"]
    payload = {
        "batch_no": batch["batch_no"], "status": batch["status"],
        "retry_count": batch["retry_count"],
        "by_org": rows_to_dicts(conn.execute(
            "SELECT org_code, COUNT(*) total,"
            " SUM(CASE WHEN status='SUBMITTED' THEN 1 ELSE 0 END) success"
            " FROM inspection_records WHERE batch_id=? GROUP BY org_code",
            (batch["id"],)).fetchall()),
    }
    conn.execute(
        "INSERT INTO report_summaries(report_type,report_key,report_date,total_records,"
        " success_records,failed_records,exception_count,payload,created_at,updated_at)"
        " VALUES('BATCH',?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(report_type,report_key) DO UPDATE SET"
        " total_records=excluded.total_records, success_records=excluded.success_records,"
        " failed_records=excluded.failed_records, exception_count=excluded.exception_count,"
        " payload=excluded.payload, updated_at=excluded.updated_at",
        (batch["batch_no"], NOW()[:10], counts["total"] or 0, counts["ok"] or 0,
         counts["bad"] or 0, exc, json.dumps(payload, ensure_ascii=False), NOW(), NOW()))


def submit_batch(batch_id, regulator, is_retry=False):
    """校验并提交一个批次。超时抛 RegulatorTimeout 前会把批次标记为 TIMEOUT（可重试）。"""
    with tx() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise KeyError(f"批次不存在: {batch_id}")
        if batch["status"] == "SUCCESS":
            raise ValueError("批次已全部上报成功，无需重复提交")
        if is_retry:
            if batch["status"] not in ("TIMEOUT", "PARTIAL", "FAILED"):
                raise ValueError(f"批次状态 {batch['status']} 不允许重试")
            conn.execute("UPDATE batches SET retry_count=retry_count+1,updated_at=? WHERE id=?",
                         (NOW(), batch_id))

        pending = conn.execute(
            "SELECT * FROM inspection_records WHERE batch_id=? AND status='PENDING'"
            " ORDER BY id", (batch_id,)).fetchall()
        if not pending:
            raise ValueError("批次内没有待提交的记录")

        # 1) 格式校验：失败记录 -> INVALID + 异常列表
        to_submit = []
        for rec in pending:
            errors, norm = validate_record(dict(rec))
            if errors:
                msg = "; ".join(errors)
                conn.execute(
                    "UPDATE inspection_records SET status='INVALID',error_message=?,updated_at=? WHERE id=?",
                    (msg, NOW(), rec["id"]))
                _add_exception(conn, rec, batch["batch_no"], "VALIDATION", msg)
            else:
                conn.execute(
                    "UPDATE inspection_records SET plate_no=?,vin=?,result=?,org_code=?,"
                    " inspection_date=?,updated_at=? WHERE id=?",
                    (norm["plate_no"], norm["vin"], norm["result"], norm["org_code"],
                     norm["inspection_date"], NOW(), rec["id"]))
                to_submit.append({**dict(rec), **norm})

        # 2) 业务预检：重复上报 -> FAILED + 异常列表
        final = []
        for rec in to_submit:
            if _is_duplicate(conn, rec):
                msg = f"重复上报: {rec['plate_no']} 于 {rec['inspection_date']} 已上报"
                conn.execute(
                    "UPDATE inspection_records SET status='FAILED',error_message=?,updated_at=? WHERE id=?",
                    (msg, NOW(), rec["id"]))
                _add_exception(conn, rec, batch["batch_no"], "BUSINESS", msg)
            else:
                final.append(rec)

        # 3) 调监管接口（超时 -> 批次 TIMEOUT，记录保持 PENDING 等待重试）
        if final:
            try:
                results = regulator.submit_batch(batch["batch_no"], final)
            except RegulatorTimeout as e:
                counts = _refresh_batch_counts(conn, batch_id)
                conn.execute("UPDATE batches SET status='TIMEOUT',error_message=?,updated_at=? WHERE id=?",
                             (str(e), NOW(), batch_id))
                _add_exception(conn, {"id": None, "batch_id": batch_id,
                                      "plate_no": None, "vin": None},
                               batch["batch_no"], "TIMEOUT", str(e))
                batch2 = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
                _persist_batch_report(conn, batch2)
                return {"batch_id": batch_id, "batch_no": batch["batch_no"],
                        "status": "TIMEOUT", "message": str(e), **counts}

            for rec, res in zip(final, results):
                if res["status"] == "OK":
                    conn.execute(
                        "UPDATE inspection_records SET status='SUBMITTED',error_message=NULL,updated_at=? WHERE id=?",
                        (NOW(), rec["id"]))
                else:
                    conn.execute(
                        "UPDATE inspection_records SET status='FAILED',error_message=?,updated_at=? WHERE id=?",
                        (res["message"], NOW(), rec["id"]))
                    _add_exception(conn, rec, batch["batch_no"], "BUSINESS", res["message"])

        counts = _refresh_batch_counts(conn, batch_id)
        batch2 = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        _persist_batch_report(conn, batch2)   # 报告汇总持久化
        return {"batch_id": batch_id, "batch_no": batch["batch_no"],
                "message": "处理完成", **counts}


# ---------- 查询 ----------

def list_batches():
    with tx() as conn:
        return rows_to_dicts(conn.execute(
            "SELECT * FROM batches ORDER BY id DESC").fetchall())


def list_records(batch_id):
    with tx() as conn:
        return rows_to_dicts(conn.execute(
            "SELECT * FROM inspection_records WHERE batch_id=? ORDER BY id",
            (batch_id,)).fetchall())


def list_exceptions(status=None):
    with tx() as conn:
        if status:
            return rows_to_dicts(conn.execute(
                "SELECT * FROM exceptions WHERE status=? ORDER BY id DESC", (status,)).fetchall())
        return rows_to_dicts(conn.execute(
            "SELECT * FROM exceptions ORDER BY id DESC").fetchall())


def resolve_exception(exc_id):
    with tx() as conn:
        cur = conn.execute(
            "UPDATE exceptions SET status='RESOLVED',resolved_at=? WHERE id=? AND status='OPEN'",
            (NOW(), exc_id))
        if cur.rowcount == 0:
            raise KeyError(f"异常不存在或已处理: {exc_id}")
        return {"id": exc_id, "status": "RESOLVED"}


# ---------- 报告汇总（按日+机构） ----------

def generate_daily_report(report_date=None):
    """按 检测日期+机构 汇总，upsert 持久化到 report_summaries"""
    with tx() as conn:
        where, args = "", []
        if report_date:
            where, args = "WHERE inspection_date=?", [report_date]
        groups = conn.execute(
            f"SELECT inspection_date, org_code, COUNT(*) total,"
            f" SUM(CASE WHEN status='SUBMITTED' THEN 1 ELSE 0 END) ok,"
            f" SUM(CASE WHEN status IN ('INVALID','FAILED') THEN 1 ELSE 0 END) bad"
            f" FROM inspection_records {where} GROUP BY inspection_date, org_code",
            args).fetchall()
        written = 0
        for g in groups:
            exc = conn.execute(
                "SELECT COUNT(*) c FROM exceptions e JOIN inspection_records r ON e.record_id=r.id"
                " WHERE r.inspection_date=? AND r.org_code=?",
                (g["inspection_date"], g["org_code"])).fetchone()["c"]
            key = f"{g['inspection_date']}|{g['org_code']}"
            conn.execute(
                "INSERT INTO report_summaries(report_type,report_key,report_date,org_code,"
                " total_records,success_records,failed_records,exception_count,payload,created_at,updated_at)"
                " VALUES('DAILY',?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(report_type,report_key) DO UPDATE SET"
                " total_records=excluded.total_records, success_records=excluded.success_records,"
                " failed_records=excluded.failed_records, exception_count=excluded.exception_count,"
                " updated_at=excluded.updated_at",
                (key, g["inspection_date"], g["org_code"], g["total"], g["ok"] or 0,
                 g["bad"] or 0, exc,
                 json.dumps({"date": g["inspection_date"], "org": g["org_code"]},
                            ensure_ascii=False), NOW(), NOW()))
            written += 1
        return {"written": written, "date": report_date or "ALL"}


def list_reports(report_type=None):
    with tx() as conn:
        if report_type:
            rows = conn.execute(
                "SELECT * FROM report_summaries WHERE report_type=? ORDER BY id DESC",
                (report_type,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM report_summaries ORDER BY id DESC").fetchall()
        return rows_to_dicts(rows)
