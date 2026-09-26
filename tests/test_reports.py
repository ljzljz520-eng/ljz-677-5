import os
import tempfile
import unittest

from tests.test_pipeline import CSV_HEADER, FakeTransport, csv_row, vin
from inspection_system.service import InspectionService


class TestReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "report.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _svc(self, transport=None):
        from inspection_system.regulator import RegulatorClient
        if transport:
            return InspectionService(self.db, client_factory=lambda: RegulatorClient(
                transport=transport, max_retry=0, backoff=0, timeout=5))
        return InspectionService(self.db)

    def test_report_numbers_and_persistence(self):
        svc = self._svc(FakeTransport("partial"))
        content = CSV_HEADER
        for i in range(1, 4):
            content += csv_row(f"京A{20000+i}", vin(20 + i),
                               result=("不合格" if i == 2 else "合格"))
        content += csv_row("坏牌", vin(99))  # 校验异常
        svc.import_csv(content)
        svc.validate_and_batch(batch_size=10)
        svc.process_pending()

        report = svc.get_report()
        self.assertEqual(report["total_imported"], 4)
        self.assertEqual(report["total_submitted"], 2)
        self.assertEqual(report["total_rejected"], 1)
        self.assertEqual(report["total_exceptions"], 2)  # 1 校验 + 1 业务
        self.assertEqual(report["by_result"].get("不合格"), 1)

        snap = svc.generate_report_snapshot()
        self.assertTrue(snap["persisted"])
        snaps = svc.list_report_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["total_submitted"], 2)
        self.assertIn("validation_error", snaps[0]["by_error_type"])
        self.assertIn("business_reject", snaps[0]["by_error_type"])

    def test_multiple_snapshots_accumulate(self):
        svc = self._svc()
        svc.import_csv(CSV_HEADER + csv_row("京A30001", vin(31)))
        svc.validate_and_batch()
        svc.generate_report_snapshot()
        svc.generate_report_snapshot()
        self.assertEqual(len(svc.list_report_snapshots()), 2)


if __name__ == "__main__":
    unittest.main()
