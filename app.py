"""
V3.0 买家秀生成 Agent - Web 服务入口

能力：
1. 文本/图片生成
2. 发送任务入队（面向影刀）
3. outbox 取单与回写接口（供影刀轮询）
4. 批量生成：从飞书需求表读取，逐行生成评价+晒图并入队（LangGraph V2）
"""
from __future__ import annotations
import sys
import io
import os
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from typing import Any, Dict, List, Optional
from uuid import uuid4

try:
    from langgraph.errors import GraphInterrupt
except Exception:
    try:
        from langgraph.types import GraphInterrupt
    except Exception:
        class GraphInterrupt(Exception):
            """LangGraph interrupt signal -- raise when graph hits interrupt()."""
            pass

from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

import requests

logger = logging.getLogger("outbox-api")

from outbox import (
    ensure_outbox_db,
    enqueue_task,
    reserve_next_task,
    peek_next_due_task,
    ack_success,
    ack_failure,
    list_tasks,
    requeue_dead_letter,
    recover_processing_task,
    create_delivery_approval as db_create_approval,
    get_delivery_approval as db_get_approval,
    list_delivery_approvals as db_list_approvals,
    confirm_delivery_approval as db_confirm_approval,
    reject_delivery_approval as db_reject_approval,
)
from utils import (
    extract_product_name,
    normalize_review_block,
    format_review_text_for_delivery,
    materialize_generated_images,
    guess_image_ext,
    parse_target_contacts,
)
import uvicorn

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ===== FastAPI 应用 =====
app = FastAPI(
    title="买家秀生成 Agent V3.0",
    version="3.0",
    description="从飞书需求表批量读取商品信息，生成评价和晒图，入队给影刀发送。"
)

# 挂载静态资源
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
from config import settings  # pylint: disable=wrong-import-position

OUTBOX_DB_PATH = settings.outbox.db_path
GENERATED_IMAGE_DIR = settings.paths.generated_image_dir
OUTBOX_FILE_DIR = settings.outbox.file_dir
DEFAULT_TARGET_CONTACTS = settings.outbox.default_target_contacts
HEARTBEAT_FILE = settings.outbox.heartbeat_file


REQUIRE_DELIVERY_CONFIRMATION = settings.outbox.require_confirmation


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


class OutboxRecoverRequest(BaseModel):
    task_id: str = Field(..., description="任务 ID")
    note: str = Field(default="", description="恢复备注")


def _get_agent_v2():
    """延迟导入 LangGraph V2 Agent 接口。"""
    from agent_graph_v2 import (  # pylint: disable=import-outside-toplevel
        run_agent_v2,
        resume_agent_v2,
        get_agent_state,
        run_agent,
    )
    return run_agent_v2, resume_agent_v2, get_agent_state, run_agent


def _get_batch_graph():
    """延迟导入批量子图接口。"""
    from batch_graph import (  # pylint: disable=import-outside-toplevel
        run_batch_generate,
        get_batch_state,
    )
    return run_batch_generate, get_batch_state


class OutboxEnqueueRequest(BaseModel):
    target_contacts: List[str] = Field(default_factory=list)
    review_text: str = Field(default="")
    image_paths: List[str] = Field(default_factory=list)
    file_paths: List[str] = Field(default_factory=list)
    max_retry: int = Field(default=4, ge=1, le=10)


class DeliveryApprovalActionRequest(BaseModel):
    note: str = Field(default="", description="确认/驳回备注")


# ===== 根路径：返回聊天页面 =====
@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = os.path.join(BASE_DIR, "static", "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/generated-images/{filename}")
async def serve_generated_image(filename: str):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(GENERATED_IMAGE_DIR, safe_name)
    if not os.path.isfile(file_path):
        return JSONResponse(content={"error": "not found"}, status_code=404)
    return FileResponse(file_path, media_type="image/jpeg")


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

        _, _, _, run_agent = _get_agent_v2()
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
    流程：调用 LangGraph V2 Agent 一次性完成评价生成、配图生成、[人工确认]、入队发送。
    """
    run_agent_v2, _, get_agent_state, _ = _get_agent_v2()
    image_bytes = await file.read()

    file_paths: List[str] = []
    if table_file is not None:
        file_paths.extend(await _save_uploaded_files([table_file], prefix="table"))

    # 如果前端没传联系人，强制使用环境变量
    raw_contacts = target_contacts or target_contact
    if not raw_contacts:
        raw_contacts = DEFAULT_TARGET_CONTACTS
    contacts = parse_target_contacts(raw_contacts)

    thread_id = str(uuid4())
    require_confirmation = REQUIRE_DELIVERY_CONFIRMATION if enqueue_for_delivery and contacts else False

    try:
        logger.info("启动 Agent V2，thread_id=%s, require_confirmation=%s", thread_id, require_confirmation)
        result = await asyncio.to_thread(
            run_agent_v2,
            user_input=user_input,
            thread_id=thread_id,
            product_image_bytes=image_bytes,
            outfit_image_bytes_list=[],
            target_contacts=contacts,
            review_count=5,
            image_count=2,
            require_confirmation=require_confirmation,
        )

        # 正常完成（未启用人工确认，或确认已快速通过）
        review_text = result.get("reviews_formatted", "")
        image_result = result.get("image_result")
        local_images = result.get("local_image_paths", [])
        task_id = result.get("task_id")

        queue_result = _task_to_dict_from_state(result) if task_id else None

        return JSONResponse(content={
            "reply": review_text,
            "image_result": image_result,
            "queue_result": queue_result,
            "queue_message": "已加入发送队列" if task_id else "未入队",
            "require_confirmation": False,
            "approval_result": None,
            "materialized_images": local_images,
            "materialized_files": file_paths,
            "thread_id": thread_id,
        })

    except GraphInterrupt as e:
        # 图在 human_approval 节点中断，需要人工确认
        logger.info("Agent V2 中断等待人工确认，thread_id=%s", thread_id)
        state = get_agent_state(thread_id)
        review_text = state.get("reviews_formatted", "") if state else ""
        local_images = state.get("local_image_paths", []) if state else []
        product_name = state.get("product_name", "未命名商品") if state else "未命名商品"

        approval_result = _create_delivery_approval(
            source="chat-with-image",
            product_title=product_name,
            target_contacts=contacts,
            review_text=review_text,
            image_paths=local_images,
            file_paths=file_paths,
            max_retry=4,
            extra={"thread_id": thread_id, "interrupt_payload": getattr(e, "value", None)},
        )

        return JSONResponse(content={
            "reply": review_text,
            "image_result": state.get("image_result") if state else None,
            "queue_result": None,
            "queue_message": "待人工确认后发送",
            "require_confirmation": True,
            "approval_result": approval_result,
            "materialized_images": local_images,
            "materialized_files": file_paths,
            "thread_id": thread_id,
        })

    except Exception as e:
        logger.exception("chat-with-image 异常")
        return JSONResponse(
            content={
                "reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
                "image_result": None,
                "thread_id": thread_id,
            },
            status_code=200
        )


def _task_to_dict_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """从 AgentState 字典中提取出 task 相关信息。"""
    return {
        "task_id": state.get("task_id"),
        "target_contacts": state.get("target_contacts", []),
        "review_text": state.get("reviews_formatted", ""),
        "image_paths": state.get("local_image_paths", []),
        "file_paths": [],
        "status": "completed" if state.get("task_id") else "pending",
    }


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


def _create_delivery_approval(
    *,
    source: str,
    product_title: str,
    target_contacts: List[str],
    review_text: str,
    image_paths: List[str],
    file_paths: List[str],
    max_retry: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    approval = db_create_approval(
        OUTBOX_DB_PATH,
        source=source,
        product_title=product_title,
        target_contacts=target_contacts,
        review_text=review_text,
        image_paths=image_paths,
        file_paths=file_paths,
        max_retry=max_retry,
        extra=extra,
    )
    return approval.to_dict()


def _is_image_review_approval(approval) -> bool:
    return (approval.source == "batch-image-review") or (
        (approval.extra or {}).get("approval_type") == "image_review"
    )


def _read_local_file_bytes(path: str) -> bytes:
    if not path or not os.path.isfile(path):
        raise RuntimeError(f"本地候选文件不存在: {path}")
    with open(path, "rb") as f:
        return f.read()


def _confirm_image_review_approval(existing, note: str):
    """图片审核通过：写飞书结果表、更新源需求状态、入队微信发送。"""
    from image_generator import commit_images_to_feishu
    from feishu_reader import get_tenant_access_token, update_source_row_status

    extra = existing.extra or {}
    product_image_path = extra.get("product_image_path")
    if not product_image_path:
        raise RuntimeError("approval 缺少 product_image_path，无法写回飞书")
    if not existing.image_paths:
        raise RuntimeError("approval 缺少候选图片，无法写回飞书")

    commit_result = commit_images_to_feishu(
        existing.product_title,
        product_image_path,
        existing.image_paths,
    )
    if commit_result.get("status") != "success":
        raise RuntimeError(commit_result.get("message") or "写入飞书失败")

    source_record_id = extra.get("source_record_id")
    if source_record_id:
        try:
            token = get_tenant_access_token()
            update_source_row_status(token, source_record_id, "已处理")
        except Exception as e:
            logger.warning("更新源需求表状态失败，继续确认审核: %s", e)
            commit_result["source_status_warning"] = str(e)

    task_id = None
    if existing.target_contacts:
        task = enqueue_task(
            db_path=OUTBOX_DB_PATH,
            target_contacts=existing.target_contacts,
            review_text=existing.review_text,
            image_paths=existing.image_paths,
            file_paths=existing.file_paths,
            max_retry=existing.max_retry,
        )
        task_id = task.task_id

    updated = db_confirm_approval(
        OUTBOX_DB_PATH,
        existing.approval_id,
        note=note,
        enqueue=False,
        queued_task_id=task_id or "__no_delivery__",
    )
    if not updated:
        raise RuntimeError("更新审核记录失败")
    return updated, task_id, commit_result


def _regenerate_image_review_approval(existing, note: str):
    """图片审核驳回：保留原记录为 rejected，重新生成候选图并创建新 pending 记录。"""
    from image_generator import run_image_generation

    extra = existing.extra or {}
    product_image_path = extra.get("product_image_path")
    outfit_image_paths = extra.get("outfit_image_paths") or []

    product_image_bytes = _read_local_file_bytes(product_image_path)
    outfit_image_bytes_list = [
        _read_local_file_bytes(path)
        for path in outfit_image_paths
        if path and os.path.isfile(path)
    ]

    image_result = run_image_generation(
        image_bytes=product_image_bytes,
        product_name=existing.product_title,
        scene_count=int(extra.get("image_count") or max(len(existing.image_paths), 1)),
        outfit_image_bytes_list=outfit_image_bytes_list or None,
        commit_to_feishu=False,
    )
    if image_result.get("status") != "success":
        raise RuntimeError(image_result.get("message") or "重新生成候选图片失败")

    local_image_paths = materialize_generated_images(image_result)
    if not local_image_paths:
        raise RuntimeError("重新生成的候选图片未成功保存到本地")

    db_reject_approval(OUTBOX_DB_PATH, existing.approval_id, note=note or "图片驳回，已重新生成")

    new_extra = {
        **extra,
        "previous_approval_id": existing.approval_id,
        "regenerate_count": int(extra.get("regenerate_count") or 0) + 1,
        "image_reference_mode": image_result.get("reference_mode"),
        "image_reference_fields": image_result.get("reference_fields", []),
        "reject_note": note,
    }
    new_approval = db_create_approval(
        OUTBOX_DB_PATH,
        source=existing.source,
        product_title=existing.product_title,
        target_contacts=existing.target_contacts,
        review_text=existing.review_text,
        image_paths=local_image_paths,
        file_paths=existing.file_paths,
        max_retry=existing.max_retry,
        extra=new_extra,
    )
    return new_approval, image_result


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


@app.get("/delivery-approvals")
async def list_delivery_approvals(
    status: str = Query(default="pending"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        normalized_status = (status or "").strip().lower() or "pending"
        items = db_list_approvals(OUTBOX_DB_PATH, status=normalized_status, limit=limit)
        return JSONResponse(
            content={
                "require_confirmation": REQUIRE_DELIVERY_CONFIRMATION,
                "count": len(items),
                "approvals": [a.to_dict() for a in items],
            }
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/delivery-approvals/{approval_id}/confirm")
async def confirm_delivery_approval(
    approval_id: str,
    payload: Optional[DeliveryApprovalActionRequest] = None,
):
    try:
        existing = db_get_approval(OUTBOX_DB_PATH, approval_id)
        if not existing:
            return JSONResponse(content={"error": "approval_not_found"}, status_code=404)
        if existing.status == "rejected":
            return JSONResponse(content={"error": "approval_already_rejected"}, status_code=400)
        if existing.queued_task_id and existing.queued_task_id != "__pending_enqueue__":
            return JSONResponse(content={"approval": existing.to_dict(), "message": "already_confirmed"})

        note = payload.note if payload else ""
        if _is_image_review_approval(existing):
            updated, task_id, commit_result = await asyncio.to_thread(
                _confirm_image_review_approval,
                existing,
                note,
            )
            return JSONResponse(content={
                "approval": updated.to_dict(),
                "task": {"task_id": task_id} if task_id else None,
                "commit_result": commit_result,
                "message": "queued" if task_id else "confirmed",
            })

        thread_id = existing.extra.get("thread_id") if existing.extra else None

        # 如果存在 LangGraph thread_id，先恢复图执行（由图的 enqueue_delivery 节点入队）
        task_id = None
        if thread_id:
            _, resume_agent_v2, _, _ = _get_agent_v2()
            result = await asyncio.to_thread(resume_agent_v2, thread_id, "confirmed", note)
            task_id = result.get("task_id")

        # 更新 approval 记录，但不重复 enqueue
        updated = db_confirm_approval(
            OUTBOX_DB_PATH,
            approval_id,
            note=note,
            enqueue=False,
            queued_task_id=task_id or "__langgraph__",
        )
        if not updated:
            return JSONResponse(content={"error": "confirm_failed"}, status_code=500)

        task_dict = _task_to_dict_from_state(result) if task_id else None
        return JSONResponse(content={
            "approval": updated.to_dict(),
            "task": task_dict,
            "message": "queued",
        })
    except Exception as e:
        logger.exception("确认发送失败")
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.post("/delivery-approvals/{approval_id}/reject")
async def reject_delivery_approval(
    approval_id: str,
    payload: Optional[DeliveryApprovalActionRequest] = None,
):
    try:
        existing = db_get_approval(OUTBOX_DB_PATH, approval_id)
        if not existing:
            return JSONResponse(content={"error": "approval_not_found"}, status_code=404)
        if existing.queued_task_id and existing.queued_task_id != "__pending_enqueue__":
            return JSONResponse(content={"error": "approval_already_confirmed"}, status_code=400)

        note = payload.note if payload else ""
        if _is_image_review_approval(existing):
            new_approval, image_result = await asyncio.to_thread(
                _regenerate_image_review_approval,
                existing,
                note,
            )
            return JSONResponse(content={
                "approval": new_approval.to_dict(),
                "image_result": image_result,
                "message": "regenerated",
            })

        thread_id = existing.extra.get("thread_id") if existing.extra else None

        # 如果存在 LangGraph thread_id，恢复图执行并驳回
        if thread_id:
            _, resume_agent_v2, _, _ = _get_agent_v2()
            await asyncio.to_thread(resume_agent_v2, thread_id, "rejected", note)

        updated = db_reject_approval(OUTBOX_DB_PATH, approval_id, note=note)
        if not updated:
            return JSONResponse(content={"error": "reject_failed"}, status_code=500)

        return JSONResponse(content={"approval": updated.to_dict(), "message": "rejected"})
    except Exception as e:
        logger.exception("驳回发送失败")
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.get("/agent/state/{thread_id}")
async def get_agent_thread_state(thread_id: str):
    """查询 LangGraph Agent 某条 thread 的当前执行状态。"""
    try:
        _, _, get_agent_state_fn, _ = _get_agent_v2()
        state = get_agent_state_fn(thread_id)
        if state is None:
            return JSONResponse(content={"error": "thread_not_found"}, status_code=404)
        return JSONResponse(content={"thread_id": thread_id, "state": state})
    except Exception as e:
        logger.exception("查询 Agent 状态失败")
        return JSONResponse(content={"error": str(e)}, status_code=500)


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


@app.get("/outbox/peek")
async def peek_outbox_task():
    """
    只查看下一条可处理任务，不改变任务状态。
    """
    try:
        task = peek_next_due_task(OUTBOX_DB_PATH)
        if not task:
            return JSONResponse(content={"task": None, "message": "no_due_task"})
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/ack-success")
async def post_outbox_ack_success(payload: OutboxAckSuccessRequest):
    try:
        logger.info(
            "ack-success request: task_id=%s worker_id=%s note=%s",
            payload.task_id,
            payload.worker_id,
            payload.note,
        )
        task = ack_success(OUTBOX_DB_PATH, payload.task_id, payload.worker_id)
        if not task:
            logger.warning("ack-success task_not_found: task_id=%s", payload.task_id)
            return JSONResponse(content={"error": "task_not_found"}, status_code=404)
        logger.info(
            "ack-success updated: task_id=%s status=%s retry_count=%s",
            task.task_id,
            task.status,
            task.retry_count,
        )
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
        logger.exception("ack-success exception: task_id=%s", payload.task_id)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/ack_success")
async def post_outbox_ack_success_alias(payload: OutboxAckSuccessRequest):
    """
    兼容下划线路径，避免客户端路径拼写差异导致未命中。
    """
    return await post_outbox_ack_success(payload)


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


@app.post("/outbox/ack_failure")
async def post_outbox_ack_failure_alias(payload: OutboxAckFailureRequest):
    """
    兼容下划线路径，避免客户端路径拼写差异导致未命中。
    """
    return await post_outbox_ack_failure(payload)


@app.post("/outbox/requeue/{task_id}")
async def post_outbox_requeue(task_id: str):
    try:
        task = requeue_dead_letter(OUTBOX_DB_PATH, task_id)
        if not task:
            return JSONResponse(content={"error": "task_not_found"}, status_code=404)
        return JSONResponse(content={"task": _task_to_dict(task)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/outbox/recover-processing")
async def post_outbox_recover_processing(payload: OutboxRecoverRequest):
    try:
        task = recover_processing_task(OUTBOX_DB_PATH, payload.task_id, payload.note)
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


# ===== 批量生成 V2（LangGraph Send 并行） =====

def _get_feishu_reader():
    from feishu_reader import (  # pylint: disable=import-outside-toplevel
        fetch_all_source_rows,
        download_attachment,
        update_source_row_status,
    )
    return fetch_all_source_rows, download_attachment, update_source_row_status


@app.post("/batch-generate-v2")
async def batch_generate_v2():
    """
    使用 LangGraph 批量子图处理飞书需求表。
    每行都是一个 checkpoint，支持中断恢复。
    """
    batch_id = str(uuid4())[:8]
    try:
        run_batch_generate, _ = _get_batch_graph()
        default_contacts = parse_target_contacts(DEFAULT_TARGET_CONTACTS)

        logger.info("[batch-v2-%s] 启动批量生成", batch_id)
        result = await asyncio.to_thread(
            run_batch_generate,
            batch_id=batch_id,
            default_contacts=default_contacts,
            require_confirmation=REQUIRE_DELIVERY_CONFIRMATION,
        )

        return JSONResponse(content={
            "batch_id": batch_id,
            "message": result.get("final_message", "完成"),
            "require_confirmation": REQUIRE_DELIVERY_CONFIRMATION,
            "total": len(result.get("source_rows", [])),
            "success_count": result.get("success_count", 0),
            "error_count": result.get("error_count", 0),
            "results": result.get("row_results", []),
            "error": result.get("error_message"),
        })
    except Exception as e:
        logger.exception("[batch-v2-%s] 批量生成异常", batch_id)
        return JSONResponse(
            content={"batch_id": batch_id, "error": str(e)},
            status_code=500,
        )


@app.get("/batch-generate-v2/status")
async def batch_generate_v2_status(batch_id: str = Query(default="")):
    """查询 LangGraph 批量子图的执行状态。"""
    if not batch_id:
        return JSONResponse(content={"error": "batch_id required"}, status_code=400)
    try:
        _, get_batch_state_fn = _get_batch_graph()
        state = get_batch_state_fn(batch_id)
        if state is None:
            return JSONResponse(content={"error": "batch_id not found"}, status_code=404)
        return JSONResponse(content={
            "batch_id": batch_id,
            "current_index": state.get("current_index", 0),
            "total": len(state.get("source_rows", [])),
            "success_count": state.get("success_count", 0),
            "error_count": state.get("error_count", 0),
            "final_message": state.get("final_message"),
            "error": state.get("error_message"),
            "results": state.get("row_results", []),
        })
    except Exception as e:
        logger.exception("查询 batch-v2 状态失败")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/batch-generate/preview")
async def batch_generate_preview():
    """
    只读取飞书需求表，返回所有行的预览（不触发生成）。
    """
    try:
        fetch_all_source_rows, _, _ = _get_feishu_reader()
        _, rows = await asyncio.to_thread(fetch_all_source_rows)
        return JSONResponse(content={
            "require_confirmation": REQUIRE_DELIVERY_CONFIRMATION,
            "total": len(rows),
            "processable_total": sum(1 for r in rows if r.should_process),
            "rows": [
                {
                    "record_id": r.record_id,
                    "product_title": r.product_title,
                    "has_image": len(r.image_file_tokens) > 0,
                    "has_outfit_images": len(r.outfit_image_file_tokens) > 0,
                    "outfit_image_count": len(r.outfit_image_file_tokens),
                    "review_count": r.review_count,
                    "image_count": r.image_count,
                    "wechat_contacts": r.wechat_contacts,
                    "processing_status": r.processing_status,
                    "should_process": r.should_process,
                }
                for r in rows
            ],
        })
    except Exception as e:
        logger.exception("预览飞书需求表失败")
        return JSONResponse(content={"error": str(e)}, status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
