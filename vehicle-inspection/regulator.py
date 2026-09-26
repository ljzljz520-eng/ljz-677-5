# -*- coding: utf-8 -*-
"""监管接口客户端（模拟实现）。
真实环境中替换 submit_batch 内部为 HTTP 调用即可，异常契约保持不变：
  - 超时/网络错误 -> 抛出 RegulatorTimeout（批次可重试）
  - 业务错误      -> 在每条记录结果中返回 BIZ_ERROR（进入异常列表）
"""
import os
import time
import random


class RegulatorTimeout(Exception):
    """监管接口调用超时（可重试）"""


# 监管侧已备案的检测机构（模拟）
DEFAULT_KNOWN_ORGS = {"JG1001", "JG1002", "JG1003"}


class MockRegulatorClient:
    def __init__(self, known_orgs=None, timeout_rate=0.2, latency=0.05, rng=None):
        self.known_orgs = set(known_orgs or DEFAULT_KNOWN_ORGS)
        self.timeout_rate = timeout_rate      # 模拟超时概率
        self.latency = latency                # 模拟网络延迟(秒)
        self.rng = rng or random.Random()

    @classmethod
    def from_env(cls):
        return cls(timeout_rate=float(os.environ.get("REG_TIMEOUT_RATE", "0.2")),
                   latency=float(os.environ.get("REG_LATENCY", "0.05")))

    def submit_batch(self, batch_no, records):
        """提交一个批次的记录。records: list[dict]
        返回 list[dict]: [{"index": i, "status": "OK"/"BIZ_ERROR", "message": str}]
        """
        time.sleep(self.latency)
        if self.rng.random() < self.timeout_rate:
            raise RegulatorTimeout(f"监管接口响应超时(批次 {batch_no})")

        results = []
        for i, r in enumerate(records):
            if r["org_code"] not in self.known_orgs:
                results.append({"index": i, "status": "BIZ_ERROR",
                                "message": f"检测机构未备案: {r['org_code']}"})
            else:
                results.append({"index": i, "status": "OK", "message": ""})
        return results
