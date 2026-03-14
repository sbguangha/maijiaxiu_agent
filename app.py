"""
V2.2 买家秀生成 Agent - Web 服务入口

能力：
1. 文本/图片生成（保留）
2. 发送任务入队（面向影刀）
3. outbox 取单与回写接口（供影刀轮询）
"""
from __future__ import annotations
import os
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from outbox import (
    ensure_outbox_db,
    enqueue_task,
    reserve_next_task,
    ack_success,
    ack_failure,
    list_tasks,
    requeue_dead_letter,
)
import uvicorn

# ===== FastAPI 应用 =====
app = FastAPI(
    title="买家秀生成 Agent V2.1",
    version="2.2",
    description="输入商品链接或描述，生成评价和晒图，并入队给影刀发送。"
)

# 挂载静态资源
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
OUTBOX_DB_PATH = os.getenv("OUTBOX_DB_PATH", os.path.join(BASE_DIR, "data", "outbox.db"))
GENERATED_IMAGE_DIR = os.getenv("GENERATED_IMAGE_DIR", os.path.join(BASE_DIR, "data", "generated_images"))
OUTBOX_FILE_DIR = os.getenv("OUTBOX_FILE_DIR", os.path.join(BASE_DIR, "data", "outbox_files"))
DEFAULT_TARGET_CONTACTS = os.getenv("WECHAT_TARGET_CONTACTS", os.getenv("WECHAT_TARGET_CONTACT", ""))
HEARTBEAT_FILE = os.getenv("OUTBOX_HEARTBEAT_FILE", os.path.join(BASE_DIR, "logs", "outbox_heartbeat.jsonl"))


@app.on_event("startup")
async def on_startup():
    ensure_outbox_db(OUTBOX_DB_PATH)
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    os.makedirs(OUTBOX_FILE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(HEARTBEAT_FILE)), exist_ok=True)


class OutboxAckSuccessRequest(BaseModel):
    task_id: str = Field(..., description="任务 ID")
    worker_id: str = Field(default="", description="影刀实例标识")
    note: str = Field(default="", description="补充说明")


class OutboxAckFailureRequest(BaseModel):
    task_id: str = Field(..., description="任务 ID")
    worker_id: str = Field(default="", description="影刀实例标识")
    step: str = Field(default="", description="失败步骤")
    error_message: str = Field(..., description="失败原因")
    screenshot_path: str = Field(default="", description="失败截图路径")


class OutboxHeartbeatRequest(BaseModel):
    worker_id: str = Field(..., description="影刀实例标识")
    status: str = Field(default="ok", description="实例状态")
    current_task_id: str = Field(default="", description="当前任务")
    meta: Dict[str, Any] = Field(default_factory=dict, description="附加信息")


def _get_generation_runners():
    """
    延迟导入生成模块，避免影刀发送服务因为模型依赖缺失而无法启动。
    """
    from agent_graph import run_agent  # pylint: disable=import-outside-toplevel
    from image_generator import run_image_generation  # pylint: disable=import-outside-toplevel
    return run_agent, run_image_generation


class OutboxEnqueueRequest(BaseModel):
    target_contacts: List[str] = Field(default_factory=list)
    review_text: str = Field(default="")
    image_paths: List[str] = Field(default_factory=list)
    file_paths: List[str] = Field(default_factory=list)
    max_retry: int = Field(default=4, ge=1, le=10)


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

        run_agent, _ = _get_generation_runners()
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
    target_contacts: str = Form(default=""),
    target_contact: str = Form(default=""),
    enqueue_for_delivery: bool = Form(default=True),
    table_file: Optional[UploadFile] = File(default=None),
):
    """
    接收用户输入的文字 + 商品白底图。
    流程：1. 先用文字生成评价  2. 再用白底图生成晒图
    """
    try:
        run_agent, run_image_generation = _get_generation_runners()
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

        queue_result = None
        file_paths: List[str] = []
        if table_file is not None:
            file_paths.extend(await _save_uploaded_files([table_file], prefix="table"))

        contacts = _parse_target_contacts(target_contacts or target_contact or DEFAULT_TARGET_CONTACTS)
        local_images = _materialize_generated_images(image_result)

        if enqueue_for_delivery and contacts:
            task = enqueue_task(
                db_path=OUTBOX_DB_PATH,
                target_contacts=contacts,
                review_text=review_text,
                image_paths=local_images,
                file_paths=file_paths,
                max_retry=4,
            )
            queue_result = _task_to_dict(task)

        return JSONResponse(content={
            "reply": review_text,
            "image_result": image_result,
            "queue_result": queue_result,
            "queue_message": (
                "已加入发送队列" if queue_result else "未入队：未提供 target_contacts"
            ),
            "materialized_images": local_images,
            "materialized_files": file_paths,
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


def _parse_target_contacts(raw: str) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace("，", ",").replace("\n", ",").replace(";", ",")
    parts = [item.strip() for item in normalized.split(",")]
    seen: set[str] = set()
    contacts: List[str] = []
    for part in parts:
        if not part:
            continue
        if part in seen:
            continue
        seen.add(part)
        contacts.append(part)
    return contacts


def _materialize_generated_images(image_result: Optional[Dict[str, Any]]) -> List[str]:
    """
    将 image_generator 返回的 URL 落地到本地，供影刀发送器上传。
    """
    if not image_result or not isinstance(image_result, dict):
        return []
    urls = image_result.get("image_urls") or []
    if not urls:
        return []

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_paths: List[str] = []
    for idx, url in enumerate(urls, start=1):
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            ext = _guess_ext(resp.headers.get("Content-Type", ""), url)
            filename = f"ai_{now}_{idx}.{ext}"
            path = os.path.join(GENERATED_IMAGE_DIR, filename)
            with open(path, "wb") as f:
                f.write(resp.content)
            saved_paths.append(path)
        except Exception as e:
            print(f"⚠️ 下载生成图失败: {url}, error={e}")
    return saved_paths


async def _save_uploaded_files(files: List[UploadFile], prefix: str = "file") -> List[str]:
    saved: List[str] = []
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, upload in enumerate(files, start=1):
        if upload is None:
            continue
        content = await upload.read()
        if not content:
            continue
        original = upload.filename or f"{prefix}_{idx}.bin"
        safe_name = original.replace("\\", "_").replace("/", "_").replace(":", "_")
        path = os.path.join(OUTBOX_FILE_DIR, f"{prefix}_{now}_{idx}_{safe_name}")
        with open(path, "wb") as f:
            f.write(content)
        saved.append(path)
    return saved


def _guess_ext(content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if "png" in ct:
        return "png"
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "webp" in ct:
        return "webp"
    tail = url.rsplit(".", 1)[-1].split("?", 1)[0].lower() if "." in url else ""
    if tail in {"png", "jpg", "jpeg", "webp"}:
        return "jpg" if tail == "jpeg" else tail
    return "png"


def _task_to_dict(task) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "target_contacts": task.target_contacts,
        "review_text": task.review_text,
        "image_paths": task.image_paths,
        "file_paths": task.file_paths,
        "status": task.status,
        "retry_count": task.retry_count,
        "max_retry": task.max_retry,
        "last_error": task.last_error,
        "next_retry_at": task.next_retry_at.isoformat() if task.next_retry_at else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "reserved_by": task.reserved_by,
    }


@app.get("/outbox/tasks")
async def get_outbox_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    status: str = Query(default=""),
):
    try:
        tasks = list_tasks(OUTBOX_DB_PATH, limit=limit, status=(status or None))
        return JSONResponse(
            content={
                "count": len(tasks),
                "tasks": [_task_to_dict(task) for task in tasks],
            }
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/enqueue")
async def post_outbox_enqueue(payload: OutboxEnqueueRequest):
    try:
        task = enqueue_task(
            db_path=OUTBOX_DB_PATH,
            target_contacts=payload.target_contacts,
            review_text=payload.review_text,
            image_paths=payload.image_paths,
            file_paths=payload.file_paths,
            max_retry=payload.max_retry,
        )
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.get("/outbox/next")
async def get_next_outbox_task(worker_id: str = Query(default="yingdao-worker")):
    try:
        worker = worker_id.strip() or "yingdao-worker"
        task = reserve_next_task(OUTBOX_DB_PATH, worker)
        if not task:
            return JSONResponse(content={"task": None, "message": "no_due_task"})
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/ack-success")
async def post_outbox_ack_success(payload: OutboxAckSuccessRequest):
    try:
        task = ack_success(OUTBOX_DB_PATH, payload.task_id)
        if not task:
            return JSONResponse(content={"error": "task_not_found"}, status_code=404)
        _append_heartbeat(
            {
                "event": "ack_success",
                "worker_id": payload.worker_id,
                "task_id": payload.task_id,
                "note": payload.note,
            }
        )
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/ack-failure")
async def post_outbox_ack_failure(payload: OutboxAckFailureRequest):
    try:
        detail = payload.error_message
        if payload.step:
            detail = f"[{payload.step}] {detail}"
        if payload.screenshot_path:
            detail = f"{detail} | screenshot={payload.screenshot_path}"
        task = ack_failure(OUTBOX_DB_PATH, payload.task_id, detail)
        if not task:
            return JSONResponse(content={"error": "task_not_found"}, status_code=404)
        _append_heartbeat(
            {
                "event": "ack_failure",
                "worker_id": payload.worker_id,
                "task_id": payload.task_id,
                "step": payload.step,
                "error_message": payload.error_message,
                "screenshot_path": payload.screenshot_path,
            }
        )
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/requeue/{task_id}")
async def post_outbox_requeue(task_id: str):
    try:
        task = requeue_dead_letter(OUTBOX_DB_PATH, task_id)
        if not task:
            return JSONResponse(content={"error": "task_not_found"}, status_code=404)
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/heartbeat")
async def post_outbox_heartbeat(payload: OutboxHeartbeatRequest):
    try:
        data = {
            "event": "heartbeat",
            "worker_id": payload.worker_id,
            "status": payload.status,
            "current_task_id": payload.current_task_id,
            "meta": payload.meta,
        }
        _append_heartbeat(data)
        return JSONResponse(content={"ok": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _append_heartbeat(data: Dict[str, Any]) -> None:
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "trace_id": str(uuid4()),
        **data,
    }
    with open(HEARTBEAT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
