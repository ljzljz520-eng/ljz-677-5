"""生成演示样例 CSV：合法记录 + 校验错误 + 重复记录，并植入故障触发车牌。

用法: python3 -m tools.generate_sample [输出路径]
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from inspection_system.validators import _VIN_TRANS, _VIN_WEIGHTS

W = _VIN_WEIGHTS
T = _VIN_TRANS


def make_vin(seq: int) -> str:
    # 8 位厂商特征 + 校验位(第9位) + 8 位序号，共 17 位
    core = "LSGAB52L"
    tail = f"{seq:08d}"
    base = core + "0" + tail
    total = sum(T[ch] * w for ch, w in zip(base, W))
    check = "X" if total % 11 == 10 else str(total % 11)
    return core + check + tail


# (车牌, 结果, 机构, 日期)
GOOD = [
    ("京A88888", "合格", "北京市北方车辆检测有限公司", "2026-09-20"),   # 触发超时
    ("京C12345", "合格", "北京市北方车辆检测有限公司", "2026-09-20"),
    ("沪D23456", "不合格", "上海浦东机动车检测中心", "2026-09-21"),
    ("粤A34567", "合格", "广州市安迅汽车检测站", "2026-09-21"),
    ("苏E45678", "合格", "苏州市新城机动车检测有限公司", "2026-09-22"),
    ("津F11111", "合格", "天津滨海机动车检测站", "2026-09-22"),
    ("沪B99999", "合格", "上海浦东机动车检测中心", "2026-09-22"),   # 触发 500
    ("浙A56789", "合格", "杭州市车辆综合性能检测站", "2026-09-22"),
    ("鲁B67890", "合格", "济南市槐荫机动车检测中心", "2026-09-23"),
    ("川A78901", "不合格", "成都华府机动车检测有限公司", "2026-09-23"),
    ("鄂A89012", "合格", "武汉黄浦机动车检测站", "2026-09-23"),
    ("粤B22222", "合格", "深圳市深南汽车检测有限公司", "2026-09-24"),  # 触发业务驳回
    ("闽A13579", "合格", "福州黄山机动车检测中心", "2026-09-24"),
    ("湘B24680", "合格", "长沙市城南机动车检测站", "2026-09-24"),
    ("豫A11223", "合格", "郑州郑东新区车辆检测有限公司", "2026-09-25"),
    ("赣B33445", "不合格", "南昌市青山湖机动车检测中心", "2026-09-25"),
    ("皖A55667", "合格", "合肥蜀山机动车检测站", "2026-09-25"),
    ("桂A77889", "合格", "南宁市竹溪机动车检测中心", "2026-09-26"),
    ("云A99001", "未检", "昆明高新机动车检测有限公司", "2026-09-26"),
]


def build_rows():
    rows = []
    for i, (plate, result, org, date) in enumerate(GOOD, start=1):
        rows.append([plate, make_vin(i), result, org, date])

    dup_vin = make_vin(2)  # 与第 2 行重复
    # 7 行异常数据
    rows += [
        ["京12345", make_vin(101), "合格", "北京市北方车辆检测有限公司", "2026-09-20"],  # 车牌格式
        ["津A22334", "SHORT123", "合格", "天津滨海机动车检测站", "2026-09-20"],          # VIN 格式
        ["冀A33445", make_vin(102), "待定", "石家庄机动车检测中心", "2026-09-21"],        # 结果非法
        ["晋A44556", make_vin(103), "合格", "", "2026-09-21"],                          # 机构为空
        ["蒙A55667", make_vin(104), "合格", "呼和浩特机动车检测站", "2026/13/40"],       # 日期非法
        ["辽A66778", dup_vin, "合格", "沈阳沈河机动车检测中心", "2026-09-20"],           # 重复记录
        ["XX", "BADVIN", "合格", "某检测站", "not-a-date"],                             # 多字段错误
    ]
    return rows


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample_data/vehicles.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["车牌", "车架号", "检测结果", "机构", "日期"])
        w.writerows(build_rows())
    print(f"已生成样例: {out}（{sum(1 for _ in build_rows())} 行，含 7 行异常数据）")


if __name__ == "__main__":
    main()
