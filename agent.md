# 买家秀智能生成 Agent (V2.1)

本项目是一个基于 **LangChain** 和 **LangGraph** 构建的电商买家秀生成智能体。V2.1 版本实现了评价文字生成与 **AI 生活化配图生成** 的全链路闭环，并接入了 **飞书多维表格** 作为持久化存储。

## 🤖 系统架构

### 1. 文本生成逻辑 (agent_graph.py / agent_tools.py)
- **模型**: Kimi (Moonshot-v1-8k)
- **核心工具**:
  - `parse_product_url`: 抓取电商页面标题。
  - `extract_selling_points`: 提炼商品核心卖点。
  - `generate_reviews`: 自动回写评价内容到飞书表格。

### 2. 生图与图生图逻辑 (image_generator.py)
- **场景生成**: Kimi 根据商品名生成 5 个自然、生活化的 doubao Prompt。
- **生图模型**: **doubao-seedream-4-5-251128**。
- **参数配置**:
  - `size`: **2K** (注意：模型要求像素必须 >3.6M，1024x1024 会报错)。
  - `image`: 使用用户上传的白底图转换的 **Base64 Data URI** (避开了飞书鉴权 URL 的下载问题)。
- **重试机制**: 对 Kimi 和 doubao 的 429 过载错误内置了指数退避重试逻辑。

### 3. 数据持久化 (Feishu Bitable)
- **配置**: `.env` 中维护 `FEISHU_APP_ID`, `APP_SECRET`, `APP_TOKEN`, `TABLE_ID`。
- **字段映射**:
  - `商品主图`: 用户上传的原始白底图（附件类型）。
  - `ai生成的买家秀图片`: doubao 生成的生活场景图（附件类型）。
  - `评价内容` / `商品名称`: 自动同步。

## 🚀 启动与依赖
- **启动命令**: `python app.py` (运行在 http://127.0.0.1:8000)
- **关键依赖**: `fastapi`, `langchain`, `python-multipart` (处理图片上传), `requests`。

## 🔧 已修复的问题 (Context Handoff)
- [x] **生图报错 400**: 已将 `size` 从 `1024x1024` 修改为 `2K`。
- [x] **生图报错 403/Forbidden**: 已将 Feishu 下载链接改为 Base64 传参。
- [x] **UX 优化**: 上传图片后会自动弹出商品名确认窗口，避免空参数发送。
- [x] **接口过载**: 增加了对 429 报错的 3 次自动重试。

## 📋 下一步规划
- [ ] 接入爬虫工具（抓取同款买家秀图片）。
- [ ] 导出生成的评价和图片为本地文件夹包。
