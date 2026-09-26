"""字段校验与标准化：车牌、车架号(VIN)、检测结果、机构、日期。"""
from __future__ import annotations

import re
from datetime import datetime

from . import config

# 省份简称 + 1 位发牌机关字母 + 5 位（普通号牌共7位）/ 6 位（新能源号牌共8位），不含 I/O
_PROVINCES = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"
_PLATE_RE = re.compile(rf"^[{_PROVINCES}][A-Z][0-9A-HJ-NP-Z]{{5,6}}$")

# VIN：17 位，仅允许 0-9A-HJ-NPR-Z（不含 I/O/Q）
_VIN_RE = re.compile(r"^[0-9A-HJ-NPR-Z]{17}$")

_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_VIN_TRANS = {
    **{c: i + 1 for i, c in enumerate("ABCDEFGH")},
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    **{c: i for i, c in enumerate("0123456789")},
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}


def normalize_plate(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def normalize_vin(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def validate_plate(value: str) -> str | None:
    plate = normalize_plate(value)
    if not plate:
        return "车牌不能为空"
    if not _PLATE_RE.match(plate):
        return f"车牌格式不正确: {value}"
    return None


def _vin_check_digit_ok(vin: str) -> bool:
    """GB 7258 / FMVSS 校验位（第 9 位）。中国进口/部分车型使用，样例中可通过配置关闭。"""
    try:
        total = sum(_VIN_TRANS[ch] * w for ch, w in zip(vin, _VIN_WEIGHTS))
    except KeyError:
        return False
    check = vin[8]
    expected = "X" if total % 11 == 10 else str(total % 11)
    return check == expected


def validate_vin(value: str) -> str | None:
    vin = normalize_vin(value)
    if not vin:
        return "车架号(VIN)不能为空"
    if not _VIN_RE.match(vin):
        return f"车架号格式不正确（应为17位字母数字且不含I/O/Q）: {value}"
    if config.VALIDATE_VIN_CHECKSUM and not _vin_check_digit_ok(vin):
        return f"车架号校验位无效: {value}"
    return None


def normalize_result(value: str) -> tuple[str | None, str | None]:
    raw = str(value or "").strip()
    norm = config.RESULT_MAP.get(raw.lower(), config.RESULT_MAP.get(raw))
    if norm is None:
        return None, f"检测结果不合法（合格/不合格/未检）: {value}"
    return norm, None


def validate_organization(value: str) -> str | None:
    org = str(value or "").strip()
    if not org:
        return "检测机构不能为空"
    if len(org) > 120:
        return "检测机构名称过长（>120）"
    return None


def validate_date(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return "检测日期不能为空"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            datetime.strptime(raw, fmt)
            return None
        except ValueError:
            continue
    return f"检测日期格式不正确（YYYY-MM-DD）: {value}"


def normalize_date(value: str) -> str:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def validate_record(row: dict) -> tuple[dict | None, list[str]]:
    """对一条原始记录做标准化 + 全量字段校验。

    返回 (标准化后的记录或 None, 错误信息列表)。
    """
    errors: list[str] = []
    plate = normalize_plate(row.get("plate_no"))
    vin = normalize_vin(row.get("vin"))
    if (e := validate_plate(row.get("plate_no"))):
        errors.append(e)
    if (e := validate_vin(row.get("vin"))):
        errors.append(e)
    result, err = normalize_result(row.get("result"))
    if err:
        errors.append(err)
    org = str(row.get("organization") or "").strip()
    if (e := validate_organization(row.get("organization"))):
        errors.append(e)
    if (e := validate_date(row.get("inspect_date"))):
        errors.append(e)
    if errors:
        return None, errors
    return {
        "plate_no": plate,
        "vin": vin,
        "result": result,
        "organization": org,
        "inspect_date": normalize_date(row.get("inspect_date")),
        "source_row": int(row.get("_source_row") or 0),
    }, []
