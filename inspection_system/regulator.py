"""监管接口客户端：HTTP POST 提交、超时/瞬时错误判定与重试。

传输层 (Transport) 被抽象出来，便于注入模拟实现进行测试。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config

# 监管端业务/系统响应码
CODE_OK = 0
CODE_BATCH_REJECTED = 4001        # 整批业务驳回
CODE_RECORD_REJECTED = 4220       # 逐条业务驳回


class RegulatorError(Exception):
    """监管接口调用异常基类。"""

    def __init__(self, message: str, *, retryable: bool, code: int | None = None,
                 response: dict[str, Any] | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.response = response or {}


class TransientError(RegulatorError):
    """超时 / 连接失败 / 5xx，可重试。"""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message, retryable=True, code=code)


class BusinessError(RegulatorError):
    """监管业务驳回，不可重试，进入异常列表。"""

    def __init__(self, message: str, code: int = CODE_BATCH_REJECTED,
                 response: dict[str, Any] | None = None):
        super().__init__(message, retryable=False, code=code, response=response)


@dataclass
class SubmissionResult:
    success: bool
    code: int
    message: str
    accepted: list[int] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


class Transport(Protocol):
    def post(self, url: str, payload: bytes, timeout: float) -> tuple[int, bytes]:
        ...


class UrllibTransport:
    """基于标准库 urllib 的生产传输实现。"""

    def post(self, url: str, payload: bytes, timeout: float) -> tuple[int, bytes]:
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            # 4xx/5xx 仍读取响应体，交给上层解析
            body = e.read() if e.fp else b""
            return e.code, body
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TransientError(f"请求监管接口超时（>{timeout}s）")
            text = str(reason)
            if "timed out" in text.lower():
                raise TransientError(f"请求监管接口超时（>{timeout}s）")
            raise TransientError(f"连接监管接口失败: {text}")


def build_payload(batch_no: str, records: list[sqlite3_Row_like]) -> dict[str, Any]:
    return {
        "batchNo": batch_no,
        "total": len(records),
        "records": [
            {
                "plateNo": r["plate_no"],
                "vin": r["vin"],
                "result": r["result"],
                "organization": r["organization"],
                "inspectDate": r["inspect_date"],
                "recordId": r["id"],
            }
            for r in records
        ],
    }


class RegulatorClient:
    def __init__(self, url: str | None = None, timeout: float | None = None,
                 max_retry: int | None = None, backoff: float | None = None,
                 transport: Transport | None = None):
        self.url = url or config.REGULATOR_URL
        self.timeout = timeout if timeout is not None else config.REGULATOR_TIMEOUT
        self.max_retry = max_retry if max_retry is not None else config.REGULATOR_MAX_RETRY
        self.backoff = backoff if backoff is not None else config.REGULATOR_RETRY_BACKOFF
        self.transport = transport or UrllibTransport()

    def submit(self, batch_no: str, records: list) -> SubmissionResult:
        payload = build_payload(batch_no, records)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        attempts = 0
        last_error: RegulatorError | None = None
        # 总尝试次数 = 1 + max_retry
        for attempt in range(1, self.max_retry + 2):
            attempts = attempt
            try:
                status, resp_bytes = self.transport.post(self.url, body, self.timeout)
            except TransientError as e:
                last_error = e
                if attempt <= self.max_retry:
                    time.sleep(self.backoff * attempt)
                    continue
                raise TransientError(
                    f"{e}；重试 {self.max_retry} 次后仍失败"
                ) from e
            return self._parse_response(status, resp_bytes, attempts)
        # 理论不可达
        raise last_error or TransientError("提交失败")

    @staticmethod
    def _parse_response(status: int, resp_bytes: bytes, attempts: int) -> SubmissionResult:
        text = resp_bytes.decode("utf-8", errors="replace") if resp_bytes else ""
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {}
        code = int(data.get("code", -1)) if isinstance(data, dict) else -1
        message = str(data.get("message", "")) if isinstance(data, dict) else ""

        # HTTP 5xx：视为可重试的瞬时错误（在调用处已重试；若解析到这里则按响应处理）
        if 500 <= status < 600:
            if code == CODE_OK:
                code = -1
            raise TransientError(
                f"监管接口服务器错误 HTTP {status}: {message or text[:200]}",
                code=status,
            )

        accepted: list[int] = []
        rejected: list[dict[str, Any]] = []
        d = data.get("data") if isinstance(data, dict) else None
        if isinstance(d, dict):
            accepted = [int(x) for x in (d.get("accepted") or [])]
            rejected = list(d.get("rejected") or [])

        if code == CODE_OK and not rejected:
            if not accepted and isinstance(d, dict) and d.get("records"):
                accepted = [int(x) for x in d["records"]]
            return SubmissionResult(
                success=True, code=code,
                message=message or "监管接口接收成功",
                accepted=accepted, rejected=rejected,
                attempts=attempts, raw=data,
            )
        if code == CODE_RECORD_REJECTED or rejected:
            return SubmissionResult(
                success=False, code=code or CODE_RECORD_REJECTED,
                message=message or "部分记录被监管驳回",
                accepted=accepted, rejected=rejected,
                attempts=attempts, raw=data,
            )
        # 其余（含 4xx、CODE_BATCH_REJECTED）视为整批业务驳回，不可重试
        raise BusinessError(
            message or f"监管接口业务驳回 HTTP {status} code={code}",
            code=code if code not in (-1, 0) else CODE_BATCH_REJECTED,
            response=data,
        )


# 仅用于类型提示的轻量别名
class sqlite3_Row_like:  # pragma: no cover - 协议占位
    def __getitem__(self, key): ...
