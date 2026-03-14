"""
端到端压测（影刀协议层）

前提：app.py 已启动在 127.0.0.1:8000

流程：
1. 调 /outbox/enqueue 造任务（多人 + 文本 + 图片路径 + 文件路径）
2. 模拟影刀 worker 调 /outbox/next 拉取
3. 按失败概率回写 ack-success / ack-failure
4. 汇总状态分布与重试分布
"""

from __future__ import annotations

import argparse
import random
import time
from collections import Counter

import requests


def enqueue_tasks(base_url: str, count: int) -> None:
    for i in range(count):
        payload = {
            "target_contacts": ["A联系人", "B联系人"] if i % 2 == 0 else ["C联系人"],
            "review_text": f"压测消息 #{i}",
            "image_paths": [f"D:/fake/images/{i}_1.png", f"D:/fake/images/{i}_2.png"],
            "file_paths": [f"D:/fake/files/{i}.xlsx"],
            "max_retry": 4,
        }
        resp = requests.post(f"{base_url}/outbox/enqueue", json=payload, timeout=15)
        if resp.status_code >= 300:
            raise RuntimeError(f"enqueue failed: {resp.status_code} {resp.text}")


def run_worker_simulation(base_url: str, fail_ratio: float, worker_id: str) -> None:
    idle_rounds = 0
    while True:
        task_resp = requests.get(
            f"{base_url}/outbox/next",
            params={"worker_id": worker_id},
            timeout=15,
        )
        task_resp.raise_for_status()
        task = task_resp.json().get("task")
        if not task:
            idle_rounds += 1
            if idle_rounds > 4:
                break
            time.sleep(1)
            continue

        idle_rounds = 0
        if random.random() < fail_ratio:
            fail_payload = {
                "task_id": task["task_id"],
                "worker_id": worker_id,
                "step": "VerifyDeliveryAck",
                "error_message": "mock failure",
                "screenshot_path": "D:/fake/screenshots/mock.png",
            }
            requests.post(f"{base_url}/outbox/ack-failure", json=fail_payload, timeout=15).raise_for_status()
        else:
            ok_payload = {
                "task_id": task["task_id"],
                "worker_id": worker_id,
                "note": "mock success",
            }
            requests.post(f"{base_url}/outbox/ack-success", json=ok_payload, timeout=15).raise_for_status()


def summarize(base_url: str) -> dict:
    resp = requests.get(f"{base_url}/outbox/tasks", params={"limit": 200}, timeout=15)
    resp.raise_for_status()
    tasks = resp.json().get("tasks", [])
    status_dist = Counter(t["status"] for t in tasks)
    retry_dist = Counter(t["retry_count"] for t in tasks)
    return {
        "task_count": len(tasks),
        "status_dist": dict(status_dist),
        "retry_dist": dict(sorted(retry_dist.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="影刀协议端到端压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tasks", type=int, default=30)
    parser.add_argument("--fail-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--worker-id", default="bench-yingdao")
    args = parser.parse_args()

    random.seed(args.seed)
    enqueue_tasks(args.base_url, args.tasks)
    run_worker_simulation(args.base_url, args.fail_ratio, args.worker_id)
    result = summarize(args.base_url)
    print("=== E2E 压测结果 ===")
    print(result)


if __name__ == "__main__":
    main()
