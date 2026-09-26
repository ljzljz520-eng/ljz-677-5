# 车辆年检记录上报系统

零依赖（Python 3 标准库 + SQLite），开箱即用。

## 功能

| 模块 | 说明 |
|------|------|
| 数据导入 | 工作人员通过页面粘贴/上传 CSV，或调用 JSON API 导入：车牌、车架号、检测结果、机构、日期 |
| 分批校验 | 后端按批次校验：车牌格式、VIN(17位不含I/O/Q)、结果枚举、机构非空、日期合法且不晚于当天 |
| 提交监管 | 校验通过的记录批量提交监管接口（`regulator.py` 为模拟实现，可替换为真实 HTTP 调用） |
| 超时重试 | 监管接口超时 → 批次置 `TIMEOUT`、记录保持待提交，可一键重试（记录重试次数） |
| 异常列表 | 格式错误(VALIDATION)、业务错误(BUSINESS，如机构未备案/重复上报)、超时(TIMEOUT) 均入异常表，支持标记处理 |
| 报告汇总 | 每次提交自动生成批次报告(BATCH)；可按 检测日期+机构 生成日汇总(DAILY)，全部 upsert 持久化到 `report_summaries` 表 |

## 运行

```bash
cd vehicle-inspection
python3 server.py          # http://127.0.0.1:8000
```

环境变量：`PORT`(默认8000)、`INSPECTION_DB`(默认 data/inspection.db)、
`REG_TIMEOUT_RATE`(模拟超时概率，默认0.2)、`REG_LATENCY`。

## 测试

```bash
python3 tests/smoke_test.py   # 17 项断言：导入/校验/超时/重试/异常/报告持久化
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/batches | JSON 导入建批 `{records:[...]}` |
| POST | /api/batches/import | CSV 文本导入建批 |
| GET  | /api/batches | 批次列表 |
| GET  | /api/batches/{id}/records | 批清明细 |
| POST | /api/batches/{id}/submit | 校验并提交监管 |
| POST | /api/batches/{id}/retry | 重试（TIMEOUT/PARTIAL/FAILED） |
| GET  | /api/exceptions?status=OPEN | 异常列表 |
| POST | /api/exceptions/{id}/resolve | 标记处理 |
| POST | /api/reports/generate | 生成日汇总 `{date?}` |
| GET  | /api/reports?type=BATCH\|DAILY | 报告列表 |
| GET  | /api/stats | 顶部统计 |

## 数据库（SQLite）

- `batches` 批次（状态/计数/重试次数）
- `inspection_records` 年检记录（车牌/车架号/结果/机构/日期/状态）
- `exceptions` 异常列表（VALIDATION/BUSINESS/TIMEOUT，OPEN/RESOLVED）
- `report_summaries` 报告汇总（BATCH/DAILY，UNIQUE 键 upsert 持久化）

## 接入真实监管接口

替换 `regulator.py` 中 `MockRegulatorClient.submit_batch` 为真实 HTTP 调用，
保持契约：超时/网络错误抛 `RegulatorTimeout`；业务错误按记录返回 `BIZ_ERROR`。
