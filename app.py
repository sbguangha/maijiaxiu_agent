"""
V2.1 买家秀生成 Agent - Web 服务入口
提供：
  1. 根路径 "/" 返回聊天界面
  2. POST "/chat" 纯文字 → 生成评价
  3. POST "/chat-with-image" 文字 + 图片 → 先生成评价，再生成晒图
  4. "/static/*" 静态资源
"""
import os
import asyncio
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from agent_graph import run_agent
from image_generator import run_image_generation
import uvicorn

# ===== FastAPI 应用 =====
app = FastAPI(
    title="买家秀生成 Agent V2.1",
    version="2.1",
    description="输入商品链接或描述，Agent 自主决策生成真实买家评价；可附带白底图生成买家秀配图。"
)

# 挂载静态资源
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ===== 根路径：返回聊天页面 =====
@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = os.path.join(BASE_DIR, "static", "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ===== 纯文字聊天 API =====
@app.post("/chat")
async def chat(request: Request):
    """
    纯文字请求，交给 LangGraph Agent 生成评价。
    """
    try:
        payload = await request.json()
        user_input = payload.get("user_input", "")

        if not user_input.strip():
            return JSONResponse(content={"reply": "请输入商品名称、卖点或链接。"})

        result = await asyncio.to_thread(run_agent, user_input)
        return JSONResponse(content={"reply": result})

    except Exception as e:
        return JSONResponse(
            content={"reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试，可能是 API 暂时过载。"},
            status_code=200
        )


# ===== 文字 + 图片的合并请求 =====
@app.post("/chat-with-image")
async def chat_with_image(
    file: UploadFile = File(...),
    user_input: str = Form(default=""),
):
    """
    接收用户输入的文字 + 商品白底图。
    流程：1. 先用文字生成评价  2. 再用白底图生成晒图
    """
    try:
        image_bytes = await file.read()

        # ===== 第一步：生成评价 =====
        review_text = ""
        if user_input.strip():
            print(f"📝 正在生成评价: {user_input[:50]}...")
            review_text = await asyncio.to_thread(run_agent, user_input)

        # ===== 第二步：生成晒图 =====
        image_result = None
        if image_bytes:
            # 从用户输入中提取商品名称（去掉"生成X条评价"等指令词，留下商品名）
            product_name = _extract_product_name(user_input) or "服装商品"
            print(f"📸 正在生成晒图，商品: {product_name}")
            image_result = await asyncio.to_thread(
                run_image_generation, image_bytes, product_name, 2  # 默认 2 组晒图
            )

        return JSONResponse(content={
            "reply": review_text,
            "image_result": image_result,
        })

    except Exception as e:
        return JSONResponse(
            content={
                "reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
                "image_result": None,
            },
            status_code=200
        )


def _extract_product_name(user_input: str) -> str:
    """
    从用户的自然语言输入中尽量提取出商品名称。
    例如：
      "帮我生成3条评价，商品：白色V领针织开衫" → "白色V领针织开衫"
      "白色连衣裙，生成5条" → "白色连衣裙"
    """
    import re
    # 尝试匹配 "商品：xxx" 或 "商品叫xxx"
    m = re.search(r'商品[：:叫是]\s*(.+?)(?:[，,。]|生成|$)', user_input)
    if m:
        return m.group(1).strip()

    # 去掉常见指令词，剩余部分当商品名
    cleaned = re.sub(r'(帮我|请|生成|写|条评价|条评论|评价|评论|\d+条?|[，,。！])', '', user_input).strip()
    return cleaned if cleaned else ""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
