# 车辆年检记录上报系统

工作人员导入 **车牌、车架号（VIN）、检测结果、检测机构、检测日期**；后端**分批校验**并提交
**监管接口**；超时/瞬时故障批次可**重试**；监管**业务错误**进入**异常列表**；汇总报告
**持久化到 SQLite 数据库**。

> **零第三方依赖**：仅使用 Python 3.10+ 标准库（`sqlite3` / `http.server` / `urllib` / `csv` /
> `concurrent.futures`），无需 pip install、无需联网即可运行与测试。

---

## 一、功能总览

| 需求 | 实现 |
|------|------|
| 导入车牌/车架号/检测结果/机构/日期 | CSV（中文表头）、JSON、multipart 文件上传；字段别名识别、UTF-8 BOM 兼容 |
| 后端分批校验 | 校验车牌、VIN(含校验位)、结果、机构、日期；VIN+日期去重；按 `BATCH_SIZE` 切批 |
| 提交监管接口 | HTTP POST 批量提交，记录请求/响应原文、尝试次数、监管接收/驳回明细 |
| 超时批次可重试 | 超时/连接失败/HTTP 5xx 自动重试（指数退避），失败置 `timeout`，可手动/接口再重试 |
| 业务错误进入异常列表 | 整批驳回(4001)与逐条驳回(4220)不重试，写入 `exceptions` 并关联记录 |
| 报告汇总写入持久存储 | 汇总写入 `report_snapshots` 表；明细存于 `records/batches/exceptions`（SQLite WAL） |

### 状态机

- **记录**：`imported → validated → pending → submitting → submitted`
  （异常分支：`invalid`=校验/重复失败，`rejected`=监管业务驳回）
- **批次**：`pending → submitting → completed`；异常分支 `partial`（部分驳回）、
  `timeout`（可重试）、`rejected`（整批业务驳回）

---

## 二、目录结构

```
.
├── run.py                       # 统一入口: python3 run.py <命令>
├── requirements.txt             # 无第三方依赖
├── inspection_system/
│   ├── config.py                # 环境变量配置
│   ├── validators.py            # 车牌/VIN/结果/机构/日期校验与标准化
│   ├── importer.py              # CSV/JSON 解析与入库
│   ├── db.py                    # SQLite 连接、建表、审计日志
│   ├── batcher.py               # 分批校验、去重、切批
│   ├── regulator.py             # 监管接口客户端、超时判定、重试、错误分类
│   ├── processor.py             # 批次提交流程与状态落库（支持多线程）
│   ├── reports.py               # 汇总统计与快照持久化
│   ├── service.py               # 服务门面（进程锁，线程安全）
│   ├── cli.py                   # 命令行
│   ├── server.py                # HTTP API + 静态页面
│   └── web/                     # 原生 HTML/CSS/JS 界面（无需构建）
├── tools/
│   ├── mock_regulator.py        # 模拟监管接口（超时/500/业务驳回）
│   ├── generate_sample.py       # 生成演示样例（VIN 校验位自动正确）
│   └── run_demo.sh              # 一键端到端演示
├── sample_data/vehicles.csv     # 26 行样例（19 合法 + 7 异常）
└── tests/                       # 23 个自动化测试（unittest）
```

---

## 三、快速开始

### 1) 一键演示（推荐先跑）

```bash
bash tools/run_demo.sh
```

脚本会自动启动模拟监管接口，完整演示：
批1**超时**→重试、批2 **HTTP 500**、批3**业务逐条驳回**、异常列表、报告持久化。

### 2) 手动分步运行（CLI）

```bash
python3 run.py initdb                                   # 初始化数据库
python3 run.py import sample_data/vehicles.csv          # 导入
python3 run.py validate                                 # 分批校验（异常入异常列表）
python3 run.py process                                  # 提交监管接口
python3 run.py retry                                    # 重试全部超时批次
python3 run.py retry 1 2                                # 仅重试指定批次 ID
python3 run.py exceptions                               # 查看异常列表
python3 run.py exceptions --type business_reject        # 按类型过滤
python3 run.py batches --status timeout                 # 批次列表
python3 run.py report                                   # 生成汇总并写入数据库
python3 run.py report --no-save                         # 仅查看不落库
python3 run.py snapshots                                # 历史报告快照
```

### 3) Web 界面 + HTTP API（需另启监管接口）

```bash
# 终端 A：模拟监管接口
python3 -m tools.mock_regulator --port 9100
# 终端 B：应用服务
python3 run.py serve --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

页面提供：①导入上报（文件→分批校验→提交→重试→保存报告）②批次管理 ③异常列表（类型过滤）
④汇总报告卡片与历史快照。

---

## 四、HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/health` | 健康检查 |
| POST | `/api/import` | 导入；`text/csv`、`multipart/form-data(file)`、`application/json` |
| POST | `/api/batch/validate` | 对 imported 记录分批校验并生成批次 |
| POST | `/api/process` | 提交全部 pending 批次 |
| POST | `/api/retry` | 重试超时批次；body 可选 `{"batch_ids":[1,2]}` |
| GET  | `/api/batches[?status=]` | 批次列表 |
| GET  | `/api/batches/{id}` | 批次详情（含记录与错误） |
| GET  | `/api/records[?status=]` | 记录列表 |
| GET  | `/api/exceptions[?error_type=]` | 异常列表（validation_error/duplicate/business_reject） |
| GET  | `/api/report` | 当前实时汇总 |
| POST | `/api/report/generate` | 生成汇总并写入 `report_snapshots` |
| GET  | `/api/reports/snapshots` | 历史报告快照 |

统一响应：`{"ok": true, "data": ...}`，失败：`{"ok": false, "error": "..."}`。

JSON 导入示例：

```json
{"records":[{"plate_no":"京A12345","vin":"LSGAB52L300000002",
"result":"合格","organization":"某检测站","inspect_date":"2026-09-26"}]}
```

---

## 五、校验规则

- **车牌**：省份简称 + 发牌机关字母 + 5（普通7位）/6（新能源8位）位字母数字（不含 I/O）。
- **车架号 VIN**：17 位 `[0-9A-HJ-NPR-Z]`，默认校验第 9 位**加权校验位**
  （可用 `VALIDATE_VIN_CHECKSUM=false` 关闭）。
- **检测结果**：合格/通过/pass→`合格`；不合格/不通过/fail→`不合格`；未检/待检→`未检`。
- **机构**：非空、≤120 字。**日期**：支持 `YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD` 等并标准化。
- **重复判定**：规范化 `VIN + 检测日期` 相同视为重复；仅与更早且已通过校验的记录比对，
  保证“首次出现保留、重复行入异常”。
- 一条记录的**全部字段错误会一次性收集**（而非遇错即停），便于工作人员一次改完。

## 六、重试与错误分类

| 情况 | 分类 | 行为 |
|------|------|------|
| 连接超时 / 读超时 / 连接拒绝 | `TransientError` 可重试 | 自动重试 `REGULATOR_MAX_RETRY` 次（线性退避），仍失败批次置 `timeout` |
| HTTP 5xx | `TransientError` 可重试 | 同上，可通过 `/api/retry` 再次重试 |
| HTTP 4xx / code=4001 整批驳回 | `BusinessError` 不重试 | 批次 `rejected`，记录进异常列表 |
| code=4220 逐条驳回 | 部分业务错误 | 接收项置 `submitted`，驳回项进异常列表，批次 `partial` |

监管接口约定的响应封装：

```json
{"code":0,"message":"接收成功","data":{"accepted":[101,102],"rejected":[
  {"recordId":103,"vin":"...","reason":"有未处理违法"}]}}
```

---

## 七、配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `INSPECTION_DB` | `data/inspection.db` | SQLite 数据库路径 |
| `INSPECTION_PORT` | `8000` | Web/API 端口 |
| `REGULATOR_URL` | `http://127.0.0.1:9100/v1/inspection/batch` | 监管接口地址 |
| `REGULATOR_TIMEOUT` | `5` | 超时秒数 |
| `REGULATOR_MAX_RETRY` | `3` | 最大自动重试次数 |
| `REGULATOR_RETRY_BACKOFF` | `0.05` | 退避基数（×尝试次数） |
| `BATCH_SIZE` | `5` | 每批记录数 |
| `MAX_WORKERS` | `1` | 批次并发提交线程数 |
| `VALIDATE_VIN_CHECKSUM` | `true` | 是否校验 VIN 校验位 |
| `MOCK_TIMEOUT_PLATES / MOCK_ERROR_PLATES / MOCK_REJECT_PLATES` | 京A88888 / 沪B99999 / 粤B22222 | 模拟端故障触发车牌 |

---

## 八、持久化数据表

- `records`：全部导入记录、状态、所属批次、错误信息、来源文件/行号。
- `batches`：批次号、状态、数量、接收/驳回数、尝试次数、请求/响应原文、最近错误。
- `exceptions`：校验错误 / 重复 / 业务驳回，异常列表的数据源。
- `report_snapshots`：每次生成的汇总快照（按结果/机构/状态/错误类型分布，JSON 存列）。
- `audit_log`：导入、建批、提交、驳回、超时等操作审计。

数据库使用 WAL 模式，文件位于磁盘，**进程重启数据不丢失**。

---

## 九、测试

```bash
python3 -m unittest discover -s tests -v
# 23 个用例：字段校验、去重、切批、超时重试、5xx、整批/逐条业务驳回、报告持久化、HTTP API 全链路
```

测试通过注入 `Transport`（假传输）离线模拟监管端行为，无需真实网络。
