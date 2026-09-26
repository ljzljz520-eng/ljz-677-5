"""服务门面：对外统一入口，串联 导入 -> 分批校验 -> 提交 -> 重试 -> 异常 -> 报告。

进程内用锁串行化批处理，避免 HTTP 多线程并发重复提交。
传输层可注入，便于离线演示与单元测试。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from . import batcher, importer, processor, reports
from .db import connect, get_db_path, init_db
from .regulator import RegulatorClient, Transport


class InspectionService:
    def __init__(self, db_path: str | Path | None = None,
                 transport: Transport | None = None,
                 client_factory: Callable[[], RegulatorClient] | None = None):
        self.db_path = str(db_path) if db_path else str(get_db_path())
        self._transport = transport
        self._client_factory = client_factory
        self._lock = threading.RLock()
        init_db(self.db_path)

    # ---- 内部工具 ----
    def _conn(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def _client(self) -> RegulatorClient:
        if self._client_factory:
            return self._client_factory()
        return RegulatorClient(transport=self._transport) if self._transport else RegulatorClient()

    # ---- 导入 ----
    def import_csv(self, content: bytes | str, source_file: str = "upload.csv") -> dict:
        rows = importer.parse_csv(content)
        return self._persist_import(rows, source_file)

    def import_json(self, content: bytes | str, source_file: str = "api.json") -> dict:
        rows = importer.parse_json(content)
        return self._persist_import(rows, source_file)

    def _persist_import(self, rows, source_file: str) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                stats = importer.insert_imported(conn, rows, source_file=source_file)
                conn.commit()
                return stats
            finally:
                conn.close()

    # ---- 分批校验 ----
    def validate_and_batch(self, batch_size: int | None = None) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                stats = batcher.validate_and_batch(conn, batch_size=batch_size)
                conn.commit()
                return stats
            finally:
                conn.close()

    # ---- 提交 ----
    def process_pending(self) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                return processor.process_pending(
                    conn, self.db_path, client=self._client())
            finally:
                conn.close()

    def retry_timeouts(self, batch_ids: list[int] | None = None) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                return processor.retry_timeouts(
                    conn, self.db_path, client=self._client(), batch_ids=batch_ids)
            finally:
                conn.close()

    # ---- 一键流水线 ----
    def run_pipeline(self, content: bytes | str | None = None,
                     fmt: str = "csv", source_file: str = "pipeline",
                     batch_size: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if content is not None:
            if fmt == "json":
                result["import"] = self.import_json(content, source_file)
            else:
                result["import"] = self.import_csv(content, source_file)
        result["batch"] = self.validate_and_batch(batch_size=batch_size)
        result["submit"] = self.process_pending()
        result["report"] = self.get_report()
        return result

    # ---- 查询 ----
    def list_batches(self, status: str | None = None) -> list[dict]:
        conn = self._conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM batches WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_batch(self, batch_id: int) -> dict | None:
        conn = self._conn()
        try:
            b = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not b:
                return None
            recs = conn.execute(
                "SELECT id, plate_no, vin, result, organization, inspect_date, status, "
                "error_message FROM records WHERE batch_id=? ORDER BY id", (batch_id,)
            ).fetchall()
            data = dict(b)
            data["records"] = [dict(r) for r in recs]
            return data
        finally:
            conn.close()

    def list_records(self, status: str | None = None, limit: int = 200) -> list[dict]:
        conn = self._conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM records WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_exceptions(self, error_type: str | None = None) -> list[dict]:
        conn = self._conn()
        try:
            if error_type:
                rows = conn.execute(
                    "SELECT * FROM exceptions WHERE error_type=? ORDER BY id DESC",
                    (error_type,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM exceptions ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---- 报告 ----
    def get_report(self) -> dict:
        conn = self._conn()
        try:
            return reports.build_report(conn)
        finally:
            conn.close()

    def generate_report_snapshot(self) -> dict:
        conn = self._conn()
        try:
            report = reports.build_report(conn)
            snapshot_id = reports.save_report(conn, report)
            report["snapshot_id"] = snapshot_id
            report["persisted"] = True
            return report
        finally:
            conn.close()

    def list_report_snapshots(self, limit: int = 20) -> list[dict]:
        conn = self._conn()
        try:
            return reports.list_snapshots(conn, limit=limit)
        finally:
            conn.close()
