# 影刀发送流程蓝图（V1）

本文档对应 Python 端接口：
- `GET /outbox/next`
- `POST /outbox/ack-success`
- `POST /outbox/ack-failure`
- `POST /outbox/heartbeat`

## 一、任务字段协议（影刀读取）

`/outbox/next` 返回的 `task` 字段包含：

- `task_id`: 任务唯一 ID
- `target_contacts`: 目标联系人数组（多人）
- `review_text`: 文本内容
- `image_paths`: 本地图片路径数组
- `file_paths`: 本地文件路径数组（支持 xlsx/csv）
- `retry_count` / `max_retry`

## 二、影刀节点编排（推荐）

1. **启动检查**
   - 检查微信已登录
   - 检查窗口可见且非最小化
   - 上报心跳 `/outbox/heartbeat`

2. **拉取任务**
   - 请求 `/outbox/next?worker_id=xxx`
   - 若 `task=null`，等待 2-5 秒后重试

3. **循环联系人（ForEach target_contacts）**
   - Ctrl+F 搜索联系人
   - 进入会话
   - OCR/模板校验会话标题

4. **发送文本**
   - 粘贴 `review_text`
   - 回车发送
   - 校验最后一条消息气泡

5. **发送图片（Loop image_paths）**
   - 判断路径是否存在
   - 发送图片
   - 校验缩略图气泡

6. **发送文件（Loop file_paths）**
   - 判断路径是否存在
   - 发送文件（xlsx/csv）
   - 校验文件气泡和文件名

7. **回写结果**
   - 全部联系人成功：`/outbox/ack-success`
   - 任一步失败：`/outbox/ack-failure`（带 `step`、`error_message`、`screenshot_path`）

## 三、失败分流建议

- `SearchAndOpenChat` 失败：重试 1 次，仍失败直接回写失败
- `VerifyChatTitle` 失败：截图后回写失败（避免误发）
- `SendImagesLoop` 或 `SendFilesLoop` 某一项失败：记录失败路径，回写失败

## 四、影刀脚本节点建议

影刀可用 Python 脚本节点调用：
- `yingdao_polling_client.py`

用法：
1. 初始化 `YingdaoClient(base_url, worker_id)`
2. `fetch_next_task()`
3. 成功后 `ack_success(task_id)`
4. 失败时 `ack_failure(task_id, step, error_message, screenshot_path)`

## 五、注意事项

- 同名联系人务必做标题校验，避免误发。
- 建议固定分辨率和输入法，降低 UI 漂移。
- 回写前务必先截图，保证故障可追溯。
