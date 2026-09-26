# -*- coding: utf-8 -*-
"""年检记录格式校验：车牌、车架号、检测结果、机构、日期"""
import re
from datetime import datetime, date

# 普通蓝牌/新能源：省份汉字 + 发牌机关字母 + 5~6 位序号
PLATE_RE = re.compile(r"^[一-龥][A-Z][A-Z0-9]{5,6}$")
# VIN：17 位，字母数字且不含 I、O、Q
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

RESULT_MAP = {
    "合格": "合格", "PASS": "合格", "pass": "合格", "P": "合格",
    "不合格": "不合格", "FAIL": "不合格", "fail": "不合格", "F": "不合格",
}


def validate_record(rec):
    """返回 (errors, normalized_record)。errors 为空列表表示校验通过。"""
    errors = []
    norm = dict(rec)

    plate = (rec.get("plate_no") or "").strip().upper()
    if not plate:
        errors.append("车牌不能为空")
    elif not PLATE_RE.match(plate):
        errors.append(f"车牌格式非法: {plate}")
    norm["plate_no"] = plate

    vin = (rec.get("vin") or "").strip().upper()
    if not vin:
        errors.append("车架号不能为空")
    elif not VIN_RE.match(vin):
        errors.append(f"车架号须为17位且不含I/O/Q: {vin}")
    norm["vin"] = vin

    raw_result = (rec.get("result") or "").strip()
    if raw_result not in RESULT_MAP:
        errors.append(f"检测结果非法(应为 合格/不合格): {raw_result or '<空>'}")
        norm["result"] = raw_result
    else:
        norm["result"] = RESULT_MAP[raw_result]

    org = (rec.get("org_code") or "").strip().upper()
    if not org:
        errors.append("检测机构不能为空")
    norm["org_code"] = org

    d = (rec.get("inspection_date") or "").strip()
    try:
        parsed = datetime.strptime(d, "%Y-%m-%d").date()
        if parsed > date.today():
            errors.append(f"检测日期不能晚于今天: {d}")
    except ValueError:
        errors.append(f"检测日期格式非法(YYYY-MM-DD): {d or '<空>'}")
    norm["inspection_date"] = d

    return errors, norm
