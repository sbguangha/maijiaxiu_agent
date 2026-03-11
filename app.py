"""
V2.0 买家秀生成 Agent - Web 服务入口
提供：
  1. 根路径 "/" 返回聊天界面
  2. POST "/chat" 接收自然语言指令，调用 LangGraph Agent
  3. POST "/generate-lifestyle-images" 上传商品白底图，生成买家秀配图
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
    title="买家秀生成 Agent V2.0",
    version="2.0",
    description="输入商品链接或描述，Agent 自主决策生成真实买家评价。"
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


# ===== 聊天 API：前端调用 =====
@app.post("/chat")
async def chat(request: Request):
    """
    接收前端的自然语言指令，交给 LangGraph Agent 处理。
    前端发送: {"user_input": "帮我生成5条评价，商品：xxx"}
    返回: {"reply": "Agent 生成的完整回复文本"}
    """
    try:
        payload = await request.json()
        user_input = payload.get("user_input", "")
        
        if not user_input.strip():
            return JSONResponse(content={"reply": "请输入商品名称、卖点或链接。"})
        
        # 调用 LangGraph Agent（同步调用，在线程池中执行）
        result = await asyncio.to_thread(run_agent, user_input)
        
        return JSONResponse(content={"reply": result})
    
    except Exception as e:
        return JSONResponse(
            content={"reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试，可能是 API 暂时过载。"},
            status_code=200  # 前端统一用200处理，错误信息在reply里
        )


# ===== 图片生成 API：上传白底图 → 生成买家秀配图 =====
@app.post("/generate-lifestyle-images")
async def generate_lifestyle_images(
    file: UploadFile = File(...),
    product_name: str = Form(default="服装商品"),
    scene_count: int = Form(default=5)
):
    """
    接收用户上传的商品白底图，调用 doubao 图生图 API 生成多个场景的买家秀配图。
    结果自动写入飞书多维表格。
    """
    try:
        # 读取上传的图片文件
        image_bytes = await file.read()
        
        if not image_bytes:
            return JSONResponse(content={"status": "error", "message": "未收到图片文件"})
        
        # 在线程池中执行（避免阻塞）
        result = await asyncio.to_thread(
            run_image_generation, image_bytes, product_name, scene_count
        )
        
        return JSONResponse(content=result)
    
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"⚠️ 图片生成出错：{str(e)}"},
            status_code=200
        )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

