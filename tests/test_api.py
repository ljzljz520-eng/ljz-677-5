import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.client import RemoteDisconnected

from tests.test_pipeline import CSV_HEADER, csv_row, vin
from inspection_system.regulator import CODE_OK, RegulatorClient
from inspection_system.server import create_server
from inspection_system.service import InspectionService


class FakeOK:
    def post(self, url, payload, timeout):
        data = json.loads(payload.decode())
        body = json.dumps({"code": CODE_OK, "message": "ok",
                           "data": {"accepted": [r["recordId"] for r in data["records"]]}})
        return 200, body.encode()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls.tmp.name, "api.db")
        cls.svc = InspectionService(db, client_factory=lambda: RegulatorClient(
            transport=FakeOK(), max_retry=0, backoff=0))
        cls.httpd = create_server("127.0.0.1", 0, service=cls.svc)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _post(self, path, data=None, headers=None):
        req = urllib.request.Request(
            self.base + path, method="POST",
            data=data if isinstance(data, bytes) else json.dumps(data or {}).encode(),
            headers=headers or {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read())

    def test_health_and_static_index(self):
        self.assertTrue(self._get("/api/health")["ok"])
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertIn("车辆年检", r.read().decode())

    def test_full_workflow_via_api(self):
        content = CSV_HEADER
        for i in range(1, 4):
            content += csv_row(f"京A{40000+i}", vin(40 + i))
        content += csv_row("坏牌", vin(49))
        st, imp = self._post("/api/import", {"records": [
            {"plate_no": "沪A10001", "vin": vin(51), "result": "合格",
             "organization": "API检测站", "inspect_date": "2026-09-20"}
        ]})
        self.assertEqual(st, 201)

        # CSV 文本上传
        st, imp2 = self._post("/api/import", content.encode("utf-8"),
                              {"Content-Type": "text/csv"})
        self.assertEqual(imp2["data"]["imported"], 4)

        st, v = self._post("/api/batch/validate", {})
        self.assertEqual(v["data"]["valid"], 4)
        self.assertEqual(v["data"]["invalid"], 1)

        st, p = self._post("/api/process", {})
        self.assertEqual(p["data"]["completed"], 1)

        excs = self._get("/api/exceptions")["data"]
        self.assertTrue(any(e["error_type"] == "validation_error" for e in excs))

        st, rep = self._post("/api/report/generate", {})
        self.assertEqual(rep["data"]["total_submitted"], 4)
        self.assertTrue(rep["data"]["persisted"])
        snaps = self._get("/api/reports/snapshots")["data"]
        self.assertEqual(len(snaps), 1)

    def test_bad_json_returns_400(self):
        req = urllib.request.Request(
            self.base + "/api/import", data=b"{bad json",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("应返回 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
