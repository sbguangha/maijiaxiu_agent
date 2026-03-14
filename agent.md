# 买家秀智能生成 Agent (V2.2)

本项目是一个基于 **LangChain** 和 **LangGraph** 构建的电商买家秀生成智能体。V2.2 版本实现了评价文字生成、AI 生活化配图生成，并**新增影刀微信自动发送**的全链路闭环。

## 🤖 系统架构

### 1. 文本生成与配图 (agent_graph.py / agent_tools.py / image_generator.py)
- **模型**: Kimi (Moonshot-v1-8k) 用于评价生成
- **配图模型**: doubao-seedream-4-5-251128 用于生活场景图生成
- **数据持久化**: 飞书多维表格存储商品主图和生成的买家秀图片

### 2. 微信发送队列 (outbox.py / app.py)
- **设计目标**: 解耦内容生成与微信发送，支持多人、多类型消息（文本+图片+表格文件）
- **队列特性**: SQLite 持久化、幂等入队、自动重试(5s/15s/60s/300s)、死信机制
- **数据协议**: 支持 `target_contacts`(多人)、`review_text`、`image_paths`、`file_paths`

### 3. 影刀对接接口 (V2.2 新增)
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

## 🚀 启动与依赖

### 启动服务
```bash
python app.py  # 运行在 http://127.0.0.1:8000
```

### 关键依赖
```bash
pip install fastapi uvicorn python-multipart python-dotenv requests pydantic
# 影刀依赖（如需本地调试）
pip install pyautogui pyperclip pywin32 pillow
```

## 📁 项目文件清单

### 核心文件
- `app.py` - FastAPI 主服务，V2.2 新增影刀对接接口
- `agent_graph.py` - LangGraph Agent 文本生成逻辑
- `agent_tools.py` - 工具函数（URL 解析、卖点提取、评价生成）
- `image_generator.py` - 配图生成与飞书存储
- `outbox.py` - 发送队列核心（任务/重试/死信）

### 影刀对接
- `yingdao_polling_client.py` - 影刀 HTTP 客户端封装
- `YINGDAO_FLOW_BLUEPRINT.md` - 影刀流程节点设计
- `YINGDAO_HTTP_EXAMPLES.md` - 接口调用示例
- `YINGDAO_SOP.md` - 运维指南
- `benchmark_e2e_yingdao.py` - E2E 压测脚本

### 数据目录
- `data/outbox.db` - SQLite 队列数据库
- `data/generated_images/` - 生成的配图本地存储
- `data/outbox_files/` - 入队文件（表格等）
- `logs/outbox_heartbeat.jsonl` - 心跳日志
- `logs/delivery_failures/` - 失败截图目录

## 🔧 已完成功能 (Context Handoff)

### V2.1 基础能力
- [x] 评价文本生成（Kimi）
- [x] 生活化配图生成（doubao-seedream-4-5）
- [x] 飞书多维表格存储
- [x] 429 过载自动重试

### V2.2 影刀发送（本次实现）
- [x] Outbox 队列系统（多人/多附件/重试/死信）
- [x] 影刀 HTTP 轮询接口（next/ack-success/ack-failure/heartbeat）
- [x] 影刀流程蓝图与 SOP 文档
- [x] E2E 压测脚本（验证成功率/重试分布）

## 📋 当前进度与待办

### 影刀流程当前状态
- **已配置**: 无限循环 -> 取任务 -> 设置变量 -> IF 判断 -> 微信发送(录制) -> ack-success
- **待调试**: `currentTaskId` 变量在 HTTP 请求体中的正确引用语法
- **已知问题**: 影刀 JSON 协议体中变量引用语法需用对象变量方式，避免 `${}` 语法错误

### 下一步工作（优先级排序）
1. **影刀变量语法调通** - 确保 `task_id` 能正确从 `currentTask` 传递到 HTTP 请求
2. **前端入队集成** - 在 `static/index.html` 添加目标联系人输入框，生成后自动入队
3. **全流程 E2E 测试** - 上传图片 -> 生成评价配图 -> 自动入队 -> 影刀发送 -> 微信收到
4. **稳定性优化** - 增加 OCR/模板校验、异常重试、失败截图

## 🚨 接手注意事项

1. **环境检查**: 确保 `python app.py` 在 127.0.0.1:8000 运行，影刀才能取到任务
2. **任务创建**: 测试前需先造任务，可用 `YINGDAO_HTTP_EXAMPLES.md` 里的 PowerShell 命令
3. **影刀调试**: 变量语法建议先用"设置变量"节点提取，再 fx 引用，避免 JSON 转义问题
4. **数据库**: `data/outbox.db` 会自动迁移旧表结构，无需手动处理

## 💬 关键上下文

- **目标微信版本**: 个人微信 PC 4.1.7.59（不支持 WeChatFerry/wxauto，走影刀 RPA 路线）
- **发送方式**: 影刀视觉 RPA（非协议/Hook），稳定性依赖微信 UI 不变
- **风险**: 微信版本升级可能导致录制流程失效，需预留维护成本
