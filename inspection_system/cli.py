"""命令行入口：initdb / import / process / retry / exceptions / batches / report / serve。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .db import init_db
from .service import InspectionService


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inspection",
        description="车辆年检记录上报系统（零依赖，SQLite 持久化）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("initdb", help="初始化数据库")

    pi = sub.add_parser("import", help="导入 CSV/JSON（仅入库，不提交）")
    pi.add_argument("file", help="CSV 或 JSON 文件路径")

    pv = sub.add_parser("validate", help="分批校验 imported 记录并生成批次")
    pv.add_argument("--batch-size", type=int, default=None)

    sub.add_parser("process", help="提交所有 pending 批次到监管接口")

    pr = sub.add_parser("retry", help="重试超时批次")
    pr.add_argument("batch_ids", nargs="*", type=int, help="可选：指定批次 ID")

    pe = sub.add_parser("exceptions", help="查看异常列表")
    pe.add_argument("--type", dest="error_type", default=None,
                    choices=["validation_error", "duplicate", "business_reject"])

    pb = sub.add_parser("batches", help="查看批次列表")
    pb.add_argument("--status", default=None)

    prp = sub.add_parser("report", help="生成并持久化汇总报告")
    prp.add_argument("--no-save", action="store_true", help="只查看不写库")

    ps = sub.add_parser("serve", help="启动 HTTP API + Web 界面")
    ps.add_argument("--host", default=config.HTTP_HOST)
    ps.add_argument("--port", type=int, default=config.HTTP_PORT)

    sub.add_parser("snapshots", help="查看历史报告快照")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    svc = InspectionService()

    if args.cmd == "initdb":
        path = init_db()
        _print({"ok": True, "db": str(path)})
    elif args.cmd == "import":
        content = _read(args.file)
        if args.file.lower().endswith(".json"):
            _print({"ok": True, "data": svc.import_json(content, args.file)})
        else:
            _print({"ok": True, "data": svc.import_csv(content, args.file)})
    elif args.cmd == "validate":
        _print({"ok": True, "data": svc.validate_and_batch(args.batch_size)})
    elif args.cmd == "process":
        _print({"ok": True, "data": svc.process_pending()})
    elif args.cmd == "retry":
        _print({"ok": True, "data": svc.retry_timeouts(args.batch_ids or None)})
    elif args.cmd == "exceptions":
        _print({"ok": True, "data": svc.list_exceptions(args.error_type)})
    elif args.cmd == "batches":
        _print({"ok": True, "data": svc.list_batches(args.status)})
    elif args.cmd == "report":
        if args.no_save:
            _print({"ok": True, "data": svc.get_report()})
        else:
            _print({"ok": True, "data": svc.generate_report_snapshot()})
    elif args.cmd == "snapshots":
        _print({"ok": True, "data": svc.list_report_snapshots()})
    elif args.cmd == "serve":
        from .server import create_server
        httpd = create_server(args.host, args.port, service=svc)
        print(f"[cli] 服务启动: http://{args.host}:{args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    else:  # pragma: no cover
        print("未知命令", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
