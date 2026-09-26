"""导入：CSV / JSON 解析（支持中文表头别名），原始记录先落库为 imported。"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from . import config
from .db import log_action, utcnow


def _map_headers(fieldnames) -> dict[str, str]:
    """返回 标准字段 -> 实际表头 的映射。"""
    mapping: dict[str, str] = {}
    if not fieldnames:
        return mapping
    normalized = {str(f).strip().lower(): f for f in fieldnames}
    for std, aliases in config.FIELD_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                mapping[std] = normalized[alias.lower()]
                break
    return mapping


def parse_csv(content: bytes | str) -> list[dict[str, Any]]:
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig")
    else:
        text = content
    reader = csv.DictReader(io.StringIO(text))
    mapping = _map_headers(reader.fieldnames)
    missing = [f for f in ("plate_no", "vin", "result", "organization", "inspect_date")
               if f not in mapping]
    if missing:
        raise ValueError("CSV 缺少必需列: " + ", ".join(missing))
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(reader, start=2):  # 表头占第 1 行
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        row = {std: (raw.get(col) or "").strip() for std, col in mapping.items()}
        row["_source_row"] = idx
        rows.append(row)
    return rows


def parse_json(content: bytes | str) -> list[dict[str, Any]]:
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("records", [])
    if not isinstance(data, list):
        raise ValueError("JSON 应为记录数组或 {\"records\": [...]}")
    rows = []
    for idx, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            continue
        rows.append({
            "plate_no": str(raw.get("plate_no", "")).strip(),
            "vin": str(raw.get("vin", "")).strip(),
            "result": str(raw.get("result", "")).strip(),
            "organization": str(raw.get("organization", "")).strip(),
            "inspect_date": str(raw.get("inspect_date", "")).strip(),
            "_source_row": idx,
        })
    return rows


def insert_imported(conn: sqlite3.Connection, rows: list[dict[str, Any]],
                    source_file: str = "") -> dict[str, int]:
    """将解析后的原始行以 imported 状态写入（校验在分批阶段执行）。"""
    inserted = 0
    now = utcnow()
    for row in rows:
        conn.execute(
            "INSERT INTO records(plate_no, vin, result, organization, inspect_date, "
            "status, source_file, source_row, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'imported', ?, ?, ?, ?)",
            (
                str(row.get("plate_no", "")).strip(),
                str(row.get("vin", "")).strip(),
                str(row.get("result", "")).strip(),
                str(row.get("organization", "")).strip(),
                str(row.get("inspect_date", "")).strip(),
                source_file,
                int(row.get("_source_row") or 0),
                now, now,
            ),
        )
        inserted += 1
    log_action(conn, "import", "records", detail=f"source={source_file} count={inserted}")
    return {"imported": inserted}
