# 影刀发送端运维 SOP

## 1. 目标

将发送执行器迁移到影刀，Python 侧只负责任务管理与回写。

## 2. 环境准备

1. 启动 `app.py`，确保 `http://127.0.0.1:8000` 可访问。
2. 微信 PC 端登录并保持窗口可见。
3. 固定分辨率与缩放，避免模板漂移。
4. 影刀流程里配置 `worker_id`（每个机器人实例唯一）。

## 3. 影刀流程最小闭环

1. 取任务：`GET /outbox/next`
2. 发送文本：联系人校验 + 发送 + 回执校验
3. 成功回写：`POST /outbox/ack-success`
4. 失败回写：`POST /outbox/ack-failure`

## 4. 扩展到图片与表格

1. 循环 `image_paths` 逐张发送并校验缩略图。
2. 循环 `file_paths` 逐个发送并校验文件名气泡。
3. 任一项失败即回写 `ack-failure`，让 Python 触发重试。

## 5. 监控与排障

1. 查任务：`GET /outbox/tasks`
2. 查心跳：`logs/outbox_heartbeat.jsonl`
3. 查死信：状态 `dead_letter`
4. 回补：`POST /outbox/requeue/{task_id}`

## 6. 压测流程

1. 启动 `app.py`
2. 执行：
   - `python benchmark_e2e_yingdao.py --tasks 30 --fail-ratio 0.2`
3. 观察 `status_dist` 与 `retry_dist` 是否符合预期。

## 7. 上线建议

1. 先灰度单联系人，验证 20 条连续任务。
2. 再扩展到多人发送，逐步放量。
3. 配置报警：连续失败、死信堆积、心跳中断。
