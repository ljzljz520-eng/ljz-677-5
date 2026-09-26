"""HTTP API + 静态页面服务（标准库 http.server）。"""
from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .service import InspectionService

WEB_DIR = Path(__file__).resolve().parent / "web"


class AppHandler(BaseHTTPRequestHandler):
    service: InspectionService = None  # 由 main 注入（类属性，共享单例）
    server_version = "InspectionSystem/1.0"

    def log_message(self, fmt, *args):
        print(f"[api] {self.address_string()} - {fmt % args}")

    # ---------- 工具 ----------
    def _json(self, payload, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._json({"ok": False, "error": message}, status)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _query(self) -> dict[str, str]:
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._json({"ok": True, "service": "vehicle-inspection",
                                   "db": str(config.DB_PATH)})
            if path == "/api/report":
                return self._json({"ok": True, "data": self.service.get_report()})
            if path == "/api/reports/snapshots":
                return self._json({"ok": True, "data": self.service.list_report_snapshots()})
            if path == "/api/batches":
                return self._json({"ok": True, "data": self.service.list_batches(
                    self._query().get("status"))})
            if path == "/api/exceptions":
                return self._json({"ok": True, "data": self.service.list_exceptions(
                    self._query().get("error_type"))})
            if path == "/api/records":
                return self._json({"ok": True, "data": self.service.list_records(
                    self._query().get("status"))})
            m = re.fullmatch(r"/api/batches/(\d+)", path)
            if m:
                data = self.service.get_batch(int(m.group(1)))
                if data is None:
                    return self._error(404, "批次不存在")
                return self._json({"ok": True, "data": data})
            return self._serve_static(path)
        except Exception as e:  # noqa: BLE001
            return self._error(500, f"服务器内部错误: {e}")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/import":
                return self._handle_import()
            if path == "/api/batch/validate":
                return self._json({"ok": True, "data": self.service.validate_and_batch()})
            if path == "/api/process":
                return self._json({"ok": True, "data": self.service.process_pending()})
            if path == "/api/retry":
                body = self._read_body()
                ids = None
                if body:
                    try:
                        ids = json.loads(body).get("batch_ids")
                    except json.JSONDecodeError:
                        ids = None
                return self._json({"ok": True, "data": self.service.retry_timeouts(ids)})
            if path == "/api/report/generate":
                return self._json({"ok": True, "data":
                                   self.service.generate_report_snapshot()})
            return self._error(404, "接口不存在")
        except ValueError as e:
            return self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            return self._error(500, f"服务器内部错误: {e}")

    def _handle_import(self):
        ctype = self.headers.get("Content-Type", "")
        filename = "upload"
        if "application/json" in ctype:
            body = self._read_body()
            # 支持 JSON 内联记录，也支持 {"filename": "...", "content": "..."}
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict) and "content" in obj and "records" not in obj:
                content = obj["content"]
                filename = obj.get("filename", "upload.csv")
                stats = self.service.import_csv(content, filename)
            else:
                stats = self.service.import_json(body, "api.json")
        elif "text/csv" in ctype or "application/csv" in ctype:
            stats = self.service.import_csv(self._read_body(), filename + ".csv")
        elif "multipart/form-data" in ctype:
            content, filename = self._parse_multipart(ctype)
            if content is None:
                return self._error(400, "未找到文件字段 file")
            stats = self.service.import_csv(content, filename)
        else:
            # 默认按 CSV 文本处理
            stats = self.service.import_csv(self._read_body(), "upload.csv")
        self._json({"ok": True, "data": stats}, 201)

    def _parse_multipart(self, ctype: str):
        from email.parser import BytesParser
        from email.policy import default

        body = self._read_body()
        header = f"Content-Type: {ctype}\r\n\r\n".encode()
        msg = BytesParser(policy=default).parsebytes(header + body)
        for part in msg.iter_parts():
            filename = part.get_filename()
            if filename:
                return part.get_payload(decode=True), filename
        return None, None

    # ---------- 静态资源 ----------
    def _serve_static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error(404, "资源不存在")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8"
                         if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str | None = None, port: int | None = None,
                  service: InspectionService | None = None) -> ThreadingHTTPServer:
    AppHandler.service = service or InspectionService()
    httpd = ThreadingHTTPServer((host or config.HTTP_HOST, port or config.HTTP_PORT),
                                AppHandler)
    return httpd


def main():
    httpd = create_server()
    host, port = httpd.server_address[:2]
    print(f"[api] 车辆年检记录上报系统已启动: http://{host}:{port}")
    print(f"[api] 监管接口地址: {config.REGULATOR_URL} (超时 {config.REGULATOR_TIMEOUT}s, "
          f"最多重试 {config.REGULATOR_MAX_RETRY} 次, 批大小 {config.BATCH_SIZE})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[api] 已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
