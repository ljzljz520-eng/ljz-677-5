import json
import os
import tempfile
import unittest

from inspection_system.regulator import (
    CODE_OK, CODE_RECORD_REJECTED, RegulatorClient, TransientError,
)
from inspection_system.service import InspectionService

CSV_HEADER = "车牌,车架号,检测结果,机构,日期\n"


def csv_row(plate, vin, result="合格", org="测试检测站", date="2026-09-20"):
    return f"{plate},{vin},{result},{org},{date}\n"


def vin(i):
    from tools.generate_sample import make_vin
    return make_vin(i)


class FakeTransport:
    """可编程传输：按车牌返回不同响应，记录调用次数。"""
    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.calls = 0

    def post(self, url, payload, timeout):
        self.calls += 1
        data = json.loads(payload.decode("utf-8"))
        plates = [r["plateNo"] for r in data["records"]]
        if self.behavior == "always_timeout":
            raise TransientError("模拟超时")
        if self.behavior == "always_500":
            return 500, json.dumps({"code": 5000, "message": "服务器错误"}).encode()
        if self.behavior == "batch_reject":
            return 200, json.dumps({"code": 4001, "message": "整批业务驳回"}).encode()
        if self.behavior == "partial":
            recs = data["records"]
            rejected = [{"recordId": recs[0]["recordId"], "vin": recs[0]["vin"],
                         "reason": "有未处理违法"}]
            accepted = [r["recordId"] for r in recs[1:]]
            return 200, json.dumps({"code": CODE_RECORD_REJECTED,
                                    "message": "部分驳回",
                                    "data": {"accepted": accepted,
                                             "rejected": rejected}}).encode()
        return 200, json.dumps({"code": CODE_OK, "message": "ok",
                                "data": {"accepted": [r["recordId"] for r in data["records"]]}}).encode()


class PipelineTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, transport=None, **client_kw):
        if transport:
            return InspectionService(
                self.db,
                client_factory=lambda: RegulatorClient(
                    transport=transport, max_retry=1, backoff=0, timeout=5, **client_kw))
        return InspectionService(self.db)


class TestImportAndBatch(PipelineTestBase):
    def test_invalid_rows_go_to_exceptions_and_valid_batched(self):
        svc = self.service()
        content = CSV_HEADER
        content += csv_row("京A12345", vin(1))
        content += csv_row("坏牌", vin(2))                      # 车牌错误
        content += csv_row("津A22334", "SHORT")                 # VIN 错误
        content += csv_row("冀A33445", vin(3), result="待定")   # 结果错误
        svc.import_csv(content, "t.csv")
        stats = svc.validate_and_batch(batch_size=2)
        self.assertEqual(stats["scanned"], 4)
        self.assertEqual(stats["valid"], 1)
        self.assertEqual(stats["invalid"], 3)
        self.assertEqual(stats["batches_created"], 1)
        excs = svc.list_exceptions()
        types = {e["error_type"] for e in excs}
        self.assertEqual(types, {"validation_error"})

    def test_duplicate_vin_date_detected(self):
        svc = self.service()
        content = CSV_HEADER
        content += csv_row("京A12345", vin(7), date="2026-09-20")
        content += csv_row("津A22334", vin(7), date="2026-09-20")  # 同VIN同日
        svc.import_csv(content)
        stats = svc.validate_and_batch()
        self.assertEqual(stats["valid"], 1)
        self.assertEqual(stats["invalid"], 1)
        self.assertTrue(any(e["error_type"] == "duplicate" for e in svc.list_exceptions()))


class TestSubmission(PipelineTestBase):
    def _seed(self, svc, n=6, prefix="京A"):
        content = CSV_HEADER
        for i in range(1, n + 1):
            content += csv_row(f"{prefix}{10000 + i}", vin(10 + i))
        svc.import_csv(content)
        svc.validate_and_batch(batch_size=3)

    def test_successful_submission(self):
        svc = self.service(FakeTransport("ok"))
        self._seed(svc)
        result = svc.process_pending()
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["completed"], 2)
        records = svc.list_records("submitted")
        self.assertEqual(len(records), 6)

    def test_timeout_then_retry_success(self):
        t = FakeTransport("ok")
        t.behavior = "always_timeout"
        svc = self.service(t)
        self._seed(svc, n=3)
        r = svc.process_pending()
        self.assertEqual(r["timeout"], 1)
        self.assertEqual(t.calls, 2)  # 1 次初始 + 1 次重试
        batches = svc.list_batches("timeout")
        self.assertEqual(len(batches), 1)
        # 监管恢复后重试
        t.behavior = "ok"
        r2 = svc.retry_timeouts()
        self.assertEqual(r2["completed"], 1)
        self.assertEqual(len(svc.list_records("submitted")), 3)

    def test_500_retried_and_marks_timeout(self):
        svc = self.service(FakeTransport("always_500"))
        self._seed(svc, n=3)
        r = svc.process_pending()
        self.assertEqual(r["timeout"], 1)

    def test_business_batch_reject_goes_to_exceptions_no_retry(self):
        t = FakeTransport("batch_reject")
        svc = self.service(t)
        self._seed(svc, n=3)
        r = svc.process_pending()
        self.assertEqual(r["rejected"], 1)
        self.assertEqual(t.calls, 1)  # 业务错误不重试
        excs = svc.list_exceptions("business_reject")
        self.assertEqual(len(excs), 3)
        self.assertEqual(svc.list_batches("rejected")[0]["status"], "rejected")

    def test_partial_rejection(self):
        svc = self.service(FakeTransport("partial"))
        self._seed(svc, n=3)
        r = svc.process_pending()
        self.assertEqual(r["partial"], 1)
        self.assertEqual(r["results"][0]["accepted"], 2)
        self.assertEqual(r["results"][0]["rejected"], 1)
        excs = svc.list_exceptions("business_reject")
        self.assertEqual(len(excs), 1)
        self.assertEqual(len(svc.list_records("submitted")), 2)
        self.assertEqual(len(svc.list_records("rejected")), 1)


if __name__ == "__main__":
    unittest.main()
