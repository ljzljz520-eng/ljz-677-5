import unittest

from inspection_system import config
from inspection_system.validators import (
    normalize_date,
    validate_date,
    validate_organization,
    validate_plate,
    validate_record,
    validate_vin,
)


class TestPlateValidator(unittest.TestCase):
    def test_common_plates_ok(self):
        for p in ["京A12345", "沪B99999", "粤B22222", "京C88888"]:
            self.assertIsNone(validate_plate(p), p)

    def test_new_energy_plate_ok(self):
        self.assertIsNone(validate_plate("京AD12345"))  # 8 位新能源
        self.assertIsNone(validate_plate("粤BF99999"))

    def test_bad_plates(self):
        for p in ["", "12345", "京12345", "AA12345", "京AI1234", "京A1234"]:
            self.assertIsNotNone(validate_plate(p), repr(p))

    def test_normalize_strips_space_uppercase(self):
        self.assertIsNone(validate_plate(" 京a12345 "))


class TestVinValidator(unittest.TestCase):
    def setUp(self):
        config.VALIDATE_VIN_CHECKSUM = True

    def test_generated_vins_have_valid_checksum(self):
        from tools.generate_sample import make_vin
        for i in [1, 2, 50, 101, 2024]:
            self.assertIsNone(validate_vin(make_vin(i)), make_vin(i))

    def test_invalid_format(self):
        self.assertIsNotNone(validate_vin("SHORT1"))          # 长度不足
        self.assertIsNotNone(validate_vin("ABCDEFGHIJKLMNOPQ"))  # 含 I/O/Q
        self.assertIsNotNone(validate_vin(""))

    def test_checksum_can_be_disabled(self):
        config.VALIDATE_VIN_CHECKSUM = False
        try:
            self.assertIsNone(validate_vin("LSGAB52L000000999"))
        finally:
            config.VALIDATE_VIN_CHECKSUM = True


class TestOtherFields(unittest.TestCase):
    def test_result_accepted(self):
        from inspection_system.validators import normalize_result
        for raw, expect in [("合格", "合格"), ("通过", "合格"), ("pass", "合格"),
                            ("不合格", "不合格"), ("fail", "不合格"), ("未检", "未检")]:
            v, err = normalize_result(raw)
            self.assertIsNone(err)
            self.assertEqual(v, expect)
        _, err = normalize_result("待定")
        self.assertIsNotNone(err)

    def test_organization(self):
        self.assertIsNone(validate_organization("某检测站"))
        self.assertIsNotNone(validate_organization(""))

    def test_date_formats(self):
        self.assertIsNone(validate_date("2026-09-26"))
        self.assertIsNone(validate_date("2026/09/26"))
        self.assertIsNone(validate_date("20260926"))
        self.assertIsNotNone(validate_date("2026-13-40"))
        self.assertEqual(normalize_date("2026/09/26"), "2026-09-26")

    def test_validate_record_collects_all_errors(self):
        record, errors = validate_record({
            "plate_no": "XX", "vin": "BAD", "result": "待定",
            "organization": "", "inspect_date": "bad",
        })
        self.assertIsNone(record)
        self.assertGreaterEqual(len(errors), 5)


if __name__ == "__main__":
    unittest.main()
