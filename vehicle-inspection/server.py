# -*- coding: utf-8 -*-
"""HTTP 服务：基于标准库 http.server 的 REST 接口 + 静态页面"""
import csv
import io
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import service
from db import init_db
from regulator import MockRegulatorClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

REGULATOR = MockRegulatorClient.from_env()

CSV_FIELDS = ["plate_no", "vin", "result", "org_code", "inspection_date"]


def parse_csv(text):
    """解析 CSV 文本 -> list[dict]，支持带表头或不带表头"""
    text = text.strip()
    if not text:
        return []
    sample = text.splitlines()[0].lower()
    has_header = "plate" in sample or "车牌" in sample
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if has_header:
        rows = rows[1:]
    return [dict(zip(CSV_FIELDS, [c.strip() for c in r])) for r in rows]


class Handler(BaseHTTPRequestHandler):
    server_version = "VehicleInspection/1.0"

    # ---------- 工具 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8") or "{}")
        return raw.decode("utf-8")

    def _route(self):
        path = self.path.split("?", 1)[0]
        m = re.match(r"^/api/batches/(\d+)/(submit|retry|records)$", path)
        if m:
            return ("batch_action", int(m.group(1)), m.group(2))
        m = re.match(r"^/api/exceptions/(\d+)/resolve$", path)
        if m:
            return ("exception_resolve", int(m.group(1)), None)
        return (path, None, None)

    def log_message(self, fmt, *args):
        pass  # 静默日志

    # ---------- GET ----------
    def do_GET(self):
        route, arg, _ = self._route()
        try:
            if self.path == "/" or self.path == "/index.html":
                return self._static("index.html")
            if route == "/api/batches":
                return self._json(service.list_batches())
            if route == "batch_action" and _ == "records":
                return self._json(service.list_records(arg))
            if route == "/api/exceptions":
                q = self._query()
                return self._json(service.list_exceptions(q.get("status")))
            if route == "/api/reports":
                q = self._query()
                return self._json(service.list_reports(q.get("type")))
            if route == "/api/stats":
                return self._json(self._stats())
            return self._error(404, "接口不存在")
        except Exception as e:
            return self._error(500, str(e))

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    def _stats(self):
        batches = service.list_batches()
        exc = service.list_exceptions("OPEN")
        return {
            "batches": len(batches),
            "timeout_batches": sum(1 for b in batches if b["status"] == "TIMEOUT"),
            "open_exceptions": len(exc),
            "reports": len(service.list_reports()),
        }

    def _static(self, name):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return self._error(404, "页面不存在")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- POST ----------
    def do_POST(self):
        route, arg, action = self._route()
        try:
            if route == "/api/batches":            # JSON 导入建批
                data = self._body()
                return self._json(service.create_batch(data.get("records", [])), 201)
            if route == "/api/batches/import":     # CSV 导入建批
                text = self._body()
                records = parse_csv(text if isinstance(text, str) else text.get("csv", ""))
                return self._json(service.create_batch(records), 201)
            if route == "batch_action" and action == "submit":
                return self._json(service.submit_batch(arg, REGULATOR))
            if route == "batch_action" and action == "retry":
                return self._json(service.submit_batch(arg, REGULATOR, is_retry=True))
            if route == "exception_resolve":
                return self._json(service.resolve_exception(arg))
            if route == "/api/reports/generate":
                data = self._body()
                date = data.get("date") if isinstance(data, dict) else None
                return self._json(service.generate_daily_report(date))
            return self._error(404, "接口不存在")
        except KeyError as e:
            return self._error(404, str(e))
        except ValueError as e:
            return self._error(400, str(e))
        except Exception as e:
            return self._error(500, str(e))


def main():
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"车辆年检记录上报系统已启动: http://127.0.0.1:{port}")
    print(f"数据库: {service.__dict__ and __import__('db').DB_PATH}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
