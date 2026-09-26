# -*- coding: utf-8 -*-
"""端到端冒烟测试：导入 -> 校验 -> 超时 -> 重试 -> 异常 -> 报告持久化"""
import os, sys, tempfile

os.environ["INSPECTION_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import service
from db import init_db, get_conn
from regulator import MockRegulatorClient

PASS = 0
def check(name, cond):
    global PASS
    assert cond, f"FAILED: {name}"
    PASS += 1
    print(f"  ✓ {name}")

init_db()

print("[1] 导入建批")
records = [
    {"plate_no": "京A12345", "vin": "LFV2A21K8C3000001", "result": "合格",   "org_code": "JG1001", "inspection_date": "2026-09-20"},
    {"plate_no": "沪B67890", "vin": "LSGKB54E1HA000002", "result": "FAIL",   "org_code": "JG1002", "inspection_date": "2026-09-21"},
    {"plate_no": "粤C24680", "vin": "LGBH52E07NY000003", "result": "合格",   "org_code": "JG9999", "inspection_date": "2026-09-22"},  # 机构未备案 -> 业务错误
    {"plate_no": "BAD",      "vin": "SHORT",             "result": "合格",   "org_code": "JG1001", "inspection_date": "2026-09-22"},  # 格式错误
]
b = service.create_batch(records)
check("批次创建", b["total"] == 4)

print("[2] 提交遇超时 -> 批次 TIMEOUT，可重试")
reg_timeout = MockRegulatorClient(timeout_rate=1.0, latency=0)
r = service.submit_batch(b["batch_id"], reg_timeout)
check("超时返回 TIMEOUT", r["status"] == "TIMEOUT")
check("格式错误记录已判 INVALID", r["failed"] == 1)
batch = [x for x in service.list_batches() if x["id"] == b["batch_id"]][0]
check("批次状态 TIMEOUT", batch["status"] == "TIMEOUT")

print("[3] 重试 -> 成功提交，业务错误进异常列表")
reg_ok = MockRegulatorClient(timeout_rate=0.0, latency=0)
r = service.submit_batch(b["batch_id"], reg_ok, is_retry=True)
check("重试后 PARTIAL（有业务错误）", r["status"] == "PARTIAL")
check("成功 2 条", r["success"] == 2)
check("失败 2 条（1格式+1业务）", r["failed"] == 2)
batch = [x for x in service.list_batches() if x["id"] == b["batch_id"]][0]
check("重试计数为 1", batch["retry_count"] == 1)

exc = service.list_exceptions("OPEN")
types = sorted(e["error_type"] for e in exc)
check("异常列表含 VALIDATION+BUSINESS+TIMEOUT", types == ["BUSINESS", "TIMEOUT", "VALIDATION"])
check("业务错误为机构未备案", any("未备案" in e["error_message"] for e in exc))

print("[4] 重复上报 -> 业务错误")
b2 = service.create_batch([
    {"plate_no": "京A12345", "vin": "LFV2A21K8C3000001", "result": "合格", "org_code": "JG1001", "inspection_date": "2026-09-20"},
])
r2 = service.submit_batch(b2["batch_id"], reg_ok)
check("重复批次 FAILED", r2["status"] == "FAILED")
check("重复上报进入异常", any("重复上报" in e["error_message"] for e in service.list_exceptions("OPEN")))

print("[5] 报告汇总持久化")
res = service.generate_daily_report()
check("日汇总写入 4 组(日期+机构)", res["written"] == 4)
reports = service.list_reports()
check("报告表含 BATCH 与 DAILY", {"BATCH", "DAILY"} <= {r["report_type"] for r in reports})
with get_conn() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM report_summaries").fetchone()["c"]
check("报告已落库 report_summaries", n == len(reports) and n >= 5)
daily = [r for r in reports if r["report_type"] == "DAILY" and r["org_code"] == "JG1001" and r["report_date"] == "2026-09-20"]
check("JG1001/2026-09-20 汇总成功1条", daily and daily[0]["success_records"] == 1)

print("[6] 异常处理闭环")
eid = service.list_exceptions("OPEN")[0]["id"]
service.resolve_exception(eid)
check("异常标记已处理", all(e["id"] != eid for e in service.list_exceptions("OPEN")))

print(f"\n全部通过 ✅ 共 {PASS} 项断言")
