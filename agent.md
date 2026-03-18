# 买家秀智能生成 Agent (V3.0)

本项目是一个基于 **LangChain** 和 **LangGraph** 构建的电商买家秀生成智能体。

- **V2.0-V2.2**: 实现了评价文字生成、AI 生活化配图生成，影刀微信自动发送闭环
- **V3.0** (本次): 新增从飞书需求表批量读取商品信息，自动生成评价和晒图并入队发送

---

## 🤖 系统架构

### 1. 文本生成与配图 (agent_graph.py / agent_tools.py / image_generator.py)
- **模型**: Kimi (Moonshot-v1-8k) 用于评价生成
- **配图模型**: doubao-seedream-4-5-251128 用于生活场景图生成
- **数据持久化**: 飞书多维表格存储商品主图和生成的买家秀图片

### 2. 微信发送队列 (outbox.py / app.py)
- **设计目标**: 解耦内容生成与微信发送，支持多人、多类型消息（文本+图片+表格文件）
- **队列特性**: SQLite 持久化、幂等入队、自动重试(5s/15s/60s/300s)、死信机制
- **数据协议**: 支持 `target_contacts`(多人)、`review_text`、`image_paths`、`file_paths`

### 3. 影刀对接接口 (V2.2)
影刀通过 HTTP 轮询方式对接：
- `GET /outbox/next` - 取待发送任务
- `POST /outbox/ack-success` - 成功回写
- `POST /outbox/ack-failure` - 失败回写（触发重试/死信）
- `POST /outbox/heartbeat` - 健康心跳
- `POST /outbox/requeue/{task_id}` - 死信回补
- `POST /outbox/enqueue` - 手动入队

**影刀端交付物**:
- `yingdao_polling_client.py` - 影刀可引用的 Python 客户端
- `YINGDAO_FLOW_BLUEPRINT.md` - 影刀节点编排蓝图
- `YINGDAO_HTTP_EXAMPLES.md` - HTTP 接口调用示例
- `YINGDAO_SOP.md` - 运维 SOP

### 4. 批量生成功能 (V3.0 新增)
从飞书《评价晒图agent_需求表格》读取商品列表，逐行生成：
- `GET /batch-generate/preview` - 预览需求表内容（不触发生成）
- `POST /batch-generate` - 开始批量生成（逐行处理，实时返回进度）
- `GET /batch-generate/status` - 查询批量任务状态

**涉及文件**:
- `feishu_reader.py` - 读取需求表 + 下载附件图片
- 前端 UI 已替换为批量处理界面

---

## 🚀 启动与依赖

### 启动服务
```bash
python app.py  # 运行在 http://127.0.0.1:8000
```

### 关键依赖
```bash
pip install fastapi uvicorn python-multipart python-dotenv requests pydantic
# AI 生成依赖
pip install langchain-openai langgraph langchain-core
# 影刀依赖（如需本地调试）
pip install pyautogui pyperclip pywin32 pillow
```

---

## 📁 项目文件清单

### 核心文件
- `app.py` - FastAPI 主服务（V3.0 新增批量生成端点）
- `agent_graph.py` - LangGraph Agent 文本生成逻辑
- `agent_tools.py` - 工具函数（URL 解析、卖点提取、评价生成）
- `image_generator.py` - 配图生成与飞书存储
- `outbox.py` - 发送队列核心（任务/重试/死信）
- **`feishu_reader.py` (V3.0 新增)** - 飞书需求表读取 + 附件下载

### 影刀对接
- `yingdao_polling_client.py` - 影刀 HTTP 客户端封装
- `YINGDAO_FLOW_BLUEPRINT.md` - 影刀流程节点设计
- `YINGDAO_HTTP_EXAMPLES.md` - 接口调用示例
- `YINGDAO_SOP.md` - 运维指南
- `benchmark_e2e_yingdao.py` - E2E 压测脚本

### 前端 (V3.0 已替换)
- `static/index.html` - 批量处理界面（读取需求表 + 批量生成按钮）
- `static/script.js` - 批量处理逻辑（预览/进度/结果展示）
- `static/style.css` - 新 UI 样式

### 数据目录
- `data/outbox.db` - SQLite 队列数据库
- `data/generated_images/` - 生成的配图本地存储
- `data/outbox_files/` - 入队文件（表格等）
- `logs/outbox_heartbeat.jsonl` - 心跳日志
- `logs/delivery_failures/` - 失败截图目录

---

## 🔧 已完成功能

### V2.1 基础能力
- [x] 评价文本生成（Kimi）
- [x] 生活化配图生成（doubao-seedream-4-5）
- [x] 飞书多维表格存储（旧表）
- [x] 429 过载自动重试

### V2.2 影刀发送
- [x] Outbox 队列系统（多人/多附件/重试/死信）
- [x] 影刀 HTTP 轮询接口（next/ack-success/ack-failure/heartbeat）
- [x] 影刀流程蓝图与 SOP 文档
- [x] E2E 压测脚本

### V3.0 批量生成（本次实现）
- [x] 新增 `feishu_reader.py` 读取需求表
- [x] `GET /batch-generate/preview` 预览接口
- [x] `POST /batch-generate` 批量生成接口（支持动态评价数/晒图数）
- [x] `GET /batch-generate/status` 进度查询
- [x] 前端 UI 替换为批量处理界面
- [x] 下载附件图片并传入 `run_image_generation`

---

## 📋 当前进度与待办

### 当前状态（V3.0 刚完成）
- **已完成**: 批量生成功能开发，前端 UI 已替换
- **待测试**: 全流程 E2E 测试（飞书需求表 -> 批量生成 -> 影刀发送）
- **已知问题**: 飞书附件下载需确认权限；中文路径可能导致影刀文件选择问题

### 下一步工作（优先级排序）
1. **飞书权限检查** - 确认应用对新表有 `bitable:record:readonly` 和 `drive:media:readonly` 权限
2. **E2E 全流程测试** - 飞书需求表录入 -> 读取 -> 批量生成 -> 入队 -> 影刀发送
3. **影刀文件路径优化** - 考虑用剪贴板粘贴替代键盘输入，解决中文路径被截断问题
4. **稳定性增强** - 增加行级错误隔离（一行失败不影响其他行）

---

## 🚨 接手注意事项

1. **环境检查**: 确保 `python app.py` 在 127.0.0.1:8000 运行
2. **飞书配置**: `.env` 中必须有 `FEISHU_SOURCE_APP_TOKEN` 和 `FEISHU_SOURCE_TABLE_ID`（新表）
3. **字段名精确匹配**: 新表列名必须是 `商品标题`、`商品平铺图`、`评价数量（个）`、`晒图数量（组）`
4. **前端已替换**: 旧的手动上传 UI 已删除，现在是批量处理界面
5. **影刀流程**: 保持不变，仍通过 `/outbox/next` 轮询取任务

---

## 💬 关键上下文

- **目标微信版本**: 个人微信 PC 4.1.7.59（走影刀 RPA 路线）
- **两张飞书表**:
  - 新表（需求源）: `RsntbWqoaaDlUzsO6atc1kKonge` / `tblg6LRZra5FGtgM`
  - 旧表（结果写入）: `Ossab9kfraHqFNssdiuciUtlnKy` / `tbl6oJqpHgRfoeO4`
- **批量生成流程**: 读取新表 -> 逐行下载平铺图 -> 按"评价数量"生成评价 -> 按"晒图数量"生成配图 -> 写入旧表 -> 入队 outbox
