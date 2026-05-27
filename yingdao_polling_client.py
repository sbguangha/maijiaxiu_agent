"""
影刀侧可调用的 HTTP 客户端（Python 脚本节点可复用）

用途：
1. 拉取待发送任务
2. 回写成功/失败
3. 上报心跳
"""

from __future__ import annotations
import json
from typing import Any

import requests


class YingdaoClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", worker_id: str = "yingdao-worker"):
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id

    def fetch_next_task(self) -> dict[str, Any] | None:
        url = f"{self.base_url}/outbox/next"
        resp = requests.get(url, params={"worker_id": self.worker_id}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("task")

    def ack_success(self, task_id: str, note: str = "") -> dict[str, Any]:
        url = f"{self.base_url}/outbox/ack-success"
        payload = {
            "task_id": task_id,
            "worker_id": self.worker_id,
            "note": note,
        }
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def ack_failure(
        self,
        task_id: str,
        step: str,
        error_message: str,
        screenshot_path: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url}/outbox/ack-failure"
        payload = {
            "task_id": task_id,
            "worker_id": self.worker_id,
            "step": step,
            "error_message": error_message,
            "screenshot_path": screenshot_path,
        }
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def heartbeat(self, status: str = "ok", current_task_id: str = "", meta: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/outbox/heartbeat"
        payload = {
            "worker_id": self.worker_id,
            "status": status,
            "current_task_id": current_task_id,
            "meta": meta or {},
        }
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()


def _demo() -> None:
    client = YingdaoClient()
    task = client.fetch_next_task()
    print("FETCH:", json.dumps(task, ensure_ascii=False, indent=2))
    if task:
        client.heartbeat(status="running", current_task_id=task["task_id"], meta={"demo": True})
        # 演示场景默认直接成功回写；真实影刀流程中请在发送成功后再回写
        result = client.ack_success(task["task_id"], note="demo ack")
        print("ACK:", json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _demo()
