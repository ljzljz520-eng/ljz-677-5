"""集中配置：所有参数均可通过环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# 数据目录与 SQLite 数据库文件（持久存储）
APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("INSPECTION_DATA_DIR", str(APP_ROOT / "data")))
DB_PATH = Path(os.environ.get("INSPECTION_DB", str(DATA_DIR / "inspection.db")))

# HTTP 服务
HTTP_HOST = os.environ.get("INSPECTION_HOST", "127.0.0.1")
HTTP_PORT = _get_int("INSPECTION_PORT", 8000)

# 监管接口
REGULATOR_URL = os.environ.get(
    "REGULATOR_URL", "http://127.0.0.1:9100/v1/inspection/batch"
)
REGULATOR_TIMEOUT = _get_int("REGULATOR_TIMEOUT", 5)  # 秒，超过视为超时
REGULATOR_MAX_RETRY = _get_int("REGULATOR_MAX_RETRY", 3)
REGULATOR_RETRY_BACKOFF = float(os.environ.get("REGULATOR_RETRY_BACKOFF", "0.05"))

# 业务批处理
BATCH_SIZE = _get_int("BATCH_SIZE", 5)          # 每批提交记录数
MAX_WORKERS = _get_int("MAX_WORKERS", 1)        # 并发提交批次的线程数
VALIDATE_VIN_CHECKSUM = _get_bool("VALIDATE_VIN_CHECKSUM", True)

# 模拟监管接口（演示/测试用）：命中的车牌触发对应故障
MOCK_TIMEOUT_PLATES = {
    p.strip() for p in os.environ.get("MOCK_TIMEOUT_PLATES", "京A88888").split(",") if p.strip()
}
MOCK_ERROR_PLATES = {
    p.strip() for p in os.environ.get("MOCK_ERROR_PLATES", "沪B99999").split(",") if p.strip()
}
MOCK_REJECT_PLATES = {
    p.strip() for p in os.environ.get("MOCK_REJECT_PLATES", "粤B22222").split(",") if p.strip()
}
MOCK_DELAY = _get_int("MOCK_DELAY", 8)          # 模拟超时延迟（应 > REGULATOR_TIMEOUT）

# 导入列名（中文表头）别名
FIELD_ALIASES = {
    "plate_no": ["车牌", "车牌号", "车牌号码", "plate", "plate_no"],
    "vin": ["车架号", "车辆识别代号", "vin", "frame_no"],
    "result": ["检测结果", "结果", "检验结论", "result"],
    "organization": ["机构", "检测机构", "检验机构", "机构名称", "organization"],
    "inspect_date": ["日期", "检测日期", "检验日期", "date", "inspect_date"],
}

# 结果标准化映射
RESULT_MAP = {
    "合格": "合格", "通过": "合格", "pass": "合格", "passed": "合格", "1": "合格",
    "不合格": "不合格", "不通过": "不合格", "fail": "不合格", "failed": "不合格", "0": "不合格",
    "未检": "未检", "待检": "未检", "pending": "未检",
}
