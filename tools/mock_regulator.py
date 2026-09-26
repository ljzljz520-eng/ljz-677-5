"""模拟监管接口服务（演示/联调用，零依赖）。

规则（按批次内车牌命中）：
  - 命中 MOCK_TIMEOUT_PLATES：sleep 超过客户端超时 -> 触发客户端超时重试
  - 命中 MOCK_ERROR_PLATES：返回 HTTP 500 -> 触发瞬时错误重试
  - 命中 MOCK_REJECT_PLATES：整批业务驳回 / 逐条驳回（不可重试）
  - 其余：全部接收

用法: python3 -m tools.mock_regulator [--host 127.0.0.1] [--port 9100]
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from inspection_system import config


class MockRegulatorHandler(BaseHTTPRequestHandler):
    server_version = "MockRegulator/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        print(f"[mock-regulator] {self.address_string()} - {fmt % args}")

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端因超时主动断开，模拟监管端忽略该写失败
            self.close_connection = True

    def do_POST(self):
        if self.path != "/v1/inspection/batch":
            self._send(404, {"code": 4040, "message": "not found", "data": {}})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"code": 4000, "message": "invalid json", "data": {}})
            return

        records = payload.get("records", [])
        plates = {r.get("plateNo", "") for r in records}
        reject_set = set(config.MOCK_REJECT_PLATES)

        if plates & set(config.MOCK_TIMEOUT_PLATES):
            time.sleep(config.MOCK_DELAY)  # 让客户端超时
            self._send(200, {"code": 0, "message": "delayed", "data": {
                "accepted": [r["recordId"] for r in records]}})
            return

        if plates & set(config.MOCK_ERROR_PLATES):
            self._send(500, {"code": 5000, "message": "监管平台内部错误", "data": {}})
            return

        if plates & reject_set:
            rejected = [
                {"recordId": r["recordId"], "vin": r["vin"],
                 "reason": "车辆存在未处理的交通违法记录，监管端不予受理"}
                for r in records if r.get("plateNo") in reject_set
            ]
            accepted = [r["recordId"] for r in records
                        if r.get("plateNo") not in reject_set]
            self._send(200, {"code": 4220,
                             "message": f"{len(rejected)} 条记录业务驳回",
                             "data": {"accepted": accepted, "rejected": rejected}})
            return

        self._send(200, {"code": 0, "message": "接收成功", "data": {
            "accepted": [r["recordId"] for r in records]}})


def serve(host: str = "127.0.0.1", port: int = 9100):
    httpd = ThreadingHTTPServer((host, port), MockRegulatorHandler)
    print(f"[mock-regulator] 模拟监管接口已启动: http://{host}:{port}/v1/inspection/batch")
    print(f"[mock-regulator] 超时车牌={sorted(config.MOCK_TIMEOUT_PLATES)} "
          f"5xx车牌={sorted(config.MOCK_ERROR_PLATES)} "
          f"驳回车牌={sorted(config.MOCK_REJECT_PLATES)} 延迟={config.MOCK_DELAY}s")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-regulator] 已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="模拟监管接口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    serve(args.host, args.port)
