# 买家秀智能生成 Agent

从飞书需求表读取商品，生成评价和买家秀图片。开启人工确认时，先审核再写回飞书。

## 模型

- 评价、场景句：`qwen3.7-plus`
- 看平铺图、检查成图硬伤：`qwen3.8-omni-flash`
- 生图：`doubao-seedream-4-5`
- 两个 Qwen 模型共用 `DASHSCOPE_BASE_URL`，默认 `https://maas.qianwenaiapi.com/compatible-mode/v1`
- 密钥：`DASHSCOPE_API_KEY`、`DOUBAO_API_KEY`

## 生图

按飞书「晒图数量」逐张生成。

- 先看商品平铺图，抽出颜色、版型、长短。标题里的营销词不采用。
- 没有穿搭参考图，或张数多于参考图时，按不重复的拍摄槽位写场景句，再交给豆包。
- 参考图够用时，沿用参考图的姿势和场景，只把衣服换成平铺图那件。
- 成图只重做硬伤：衣服颜色、图案、领口、袖长、衣长不对，或正脸被磨平、影棚柔光、背景被清空、衣服没有任何褶皱。每张最多重做一次，新图没有更好就留旧图。
- 人工审核时勾选哪张，就只重做哪张，其余保留。

## 批量接口

- `GET /batch-generate/preview` — 预览需求表
- `POST /batch-generate-v2` — 开始批量生成
- `GET /batch-generate-v2/status` — 查询批量任务状态

`REQUIRE_DELIVERY_CONFIRMATION=true` 时，只有看图没通过、张数不够或没看成的结果才进人审。张数齐且没有硬伤时，Agent 自己写回飞书。

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

浏览器打开 http://127.0.0.1:8000。依赖见 `requirements.txt`。

## 核心文件

- `app.py` — Web 入口和审核
- `agent_graph_v2.py` — 单商品流程
- `agent_nodes.py` / `agent_state.py` / `agent_tools.py` — 节点、状态、评价
- `batch_graph.py` — 批量处理
- `image_generator.py` — 看图、场景句、生图、飞书写入
- `buyer_show_prompt.py` — 槽位、few-shot、硬伤判定
- `qwen_client.py` — 百炼接口
- `feishu_reader.py` — 需求表
- `outbox.py` — 审核记录
- `config.py` / `utils.py` — 配置与公共工具
- `static/` — 批量处理页面
- `data/buyer_show_slots.json` / `data/buyer_show_fewshots.json` — 拍摄设定和示例

## 流程

读取需求表里的商品标题、平铺图和穿搭参考图 → 一次生成评价 → 逐张生成配图 →（可选）勾选差图单独重做 → 写入飞书结果表并回写处理状态。不访问商品链接。
