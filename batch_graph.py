"""
批量生成子图（Map-Reduce 简化版）

负责从飞书需求表读取多行，逐行调用主图生成评价+晒图，
利用 SQLite Checkpoint 保证批量处理中断后可恢复。
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger("batch-graph")


class BatchState(TypedDict):
    """批量处理专用状态（与 AgentState 分离，职责单一）。"""

    batch_id: str
    feishu_token: Optional[str]
    source_rows: List[Any]           # SourceRow 对象列表
    current_index: int
    default_contacts: List[str]
    require_confirmation: bool

    # 结果汇总
    batch_results: List[Dict[str, Any]]
    success_count: int
    error_count: int
    final_message: Optional[str]
    error_message: Optional[str]


# =====================================================================
# 节点函数
# =====================================================================

def fetch_rows_node(state: BatchState) -> Dict[str, Any]:
    """读取飞书需求表，过滤出待处理行。"""
    from feishu_reader import fetch_all_source_rows  # pylint: disable=import-outside-toplevel

    try:
        token, rows = fetch_all_source_rows()
        process_rows = [row for row in rows if row.should_process]
        logger.info(
            "[batch-%s] 读取 %d 条，待处理 %d 条",
            state["batch_id"], len(rows), len(process_rows),
        )
        return {
            "feishu_token": token,
            "source_rows": process_rows,
            "current_index": 0,
            "batch_results": [],
            "success_count": 0,
            "error_count": 0,
        }
    except Exception as e:
        logger.exception("[batch-%s] 读取飞书失败", state["batch_id"])
        return {
            "error_message": f"读取飞书失败: {str(e)}",
            "source_rows": [],
            "current_index": 0,
        }


def process_row_node(state: BatchState) -> Dict[str, Any]:
    """处理当前索引的一行需求。"""
    from feishu_reader import (  # pylint: disable=import-outside-toplevel
        download_attachment,
        update_source_row_status,
    )
    from agent_graph_v2 import run_agent_v2  # pylint: disable=import-outside-toplevel

    idx = state["current_index"]
    rows = state.get("source_rows", [])
    if idx >= len(rows):
        return {}

    row = rows[idx]
    batch_id = state["batch_id"]
    row_result: Dict[str, Any] = {
        "index": idx + 1,
        "record_id": row.record_id,
        "product_title": row.product_title,
        "status": "pending",
        "review_text": "",
        "image_urls": [],
        "task_id": None,
        "error": None,
    }

    try:
        logger.info(
            "[batch-%s] [%d/%d] 处理: %s",
            batch_id, idx + 1, len(rows), row.product_title,
        )

        # 下载商品平铺图
        image_bytes = None
        outfit_bytes_list: List[bytes] = []
        token = state.get("feishu_token", "")
        if token and row.image_file_tokens:
            image_bytes = download_attachment(token, row.image_file_tokens[0])
            for outfit_token in row.outfit_image_file_tokens:
                b = download_attachment(token, outfit_token)
                if b and len(b) > 100:
                    outfit_bytes_list.append(b)

        # 调用主图处理单行
        contacts = row.wechat_contacts or state.get("default_contacts", [])
        result = run_agent_v2(
            user_input=f"帮我生成{row.review_count}条评价，商品：{row.product_title}",
            product_image_bytes=image_bytes,
            outfit_image_bytes_list=outfit_bytes_list,
            target_contacts=contacts,
            review_count=row.review_count,
            image_count=row.image_count,
            require_confirmation=False,  # 批量模式跳过人工确认
        )

        # 回写飞书状态
        if token:
            update_source_row_status(token, row.record_id, "已处理")

        row_result.update({
            "status": "success",
            "review_text": result.get("reviews_formatted", ""),
            "image_urls": (
                result.get("image_result", {}).get("image_urls", [])
                if result.get("image_result")
                else []
            ),
            "task_id": result.get("task_id"),
        })
        logger.info("[batch-%s] [%d/%d] 完成: %s", batch_id, idx + 1, len(rows), row.product_title)

    except Exception as e:
        logger.exception("[batch-%s] [%d/%d] 失败: %s", batch_id, idx + 1, len(rows), row.product_title)
        row_result.update({"status": "error", "error": str(e)})

    # 更新统计
    new_results = state.get("batch_results", []) + [row_result]
    success = sum(1 for r in new_results if r["status"] == "success")
    errors = sum(1 for r in new_results if r["status"] == "error")

    return {
        "current_index": idx + 1,
        "batch_results": new_results,
        "success_count": success,
        "error_count": errors,
    }


def route_after_row(state: BatchState) -> str:
    """判断是否还有下一行需要处理。"""
    rows = state.get("source_rows", [])
    idx = state.get("current_index", 0)
    if idx < len(rows):
        return "next"
    return "done"


def aggregate_node(state: BatchState) -> Dict[str, Any]:
    """汇总所有行处理结果。"""
    total = len(state.get("source_rows", []))
    success = state.get("success_count", 0)
    errors = state.get("error_count", 0)
    msg = f"批量处理完成：{success}/{total} 成功，{errors} 失败"
    logger.info("[batch-%s] %s", state["batch_id"], msg)
    return {
        "final_message": msg,
    }


# =====================================================================
# 构建批量子图
# =====================================================================

workflow = StateGraph(BatchState)

workflow.add_node("fetch_rows", fetch_rows_node)
workflow.add_node("process_row", process_row_node)
workflow.add_node("aggregate", aggregate_node)

workflow.set_entry_point("fetch_rows")
workflow.add_edge("fetch_rows", "process_row")
workflow.add_conditional_edges(
    "process_row",
    route_after_row,
    {"next": "process_row", "done": "aggregate"},
)
workflow.add_edge("aggregate", END)

# Checkpoint
from config import settings  # pylint: disable=import-outside-toplevel
batch_checkpoint_db = settings.paths.batch_checkpoint_db
os.makedirs(os.path.dirname(os.path.abspath(batch_checkpoint_db)), exist_ok=True)
try:
    _conn = __import__("sqlite3").connect(batch_checkpoint_db, check_same_thread=False)
    batch_checkpointer = SqliteSaver(_conn)
    compiled_batch_graph = workflow.compile(checkpointer=batch_checkpointer)
    logger.info("Batch Graph 编译成功")
except Exception as e:
    logger.warning("Batch Graph SQLite Checkpoint 失败 (%s)，回退到内存", e)
    from langgraph.checkpoint.memory import MemorySaver  # pylint: disable=import-outside-toplevel
    batch_checkpointer = MemorySaver()
    compiled_batch_graph = workflow.compile(checkpointer=batch_checkpointer)


def run_batch_generate(
    batch_id: str,
    default_contacts: List[str],
    require_confirmation: bool = False,
) -> Dict[str, Any]:
    """启动批量生成流程。

    返回最终 BatchState 字典（含 batch_results、final_message 等）。
    """
    initial_state: BatchState = {
        "batch_id": batch_id,
        "feishu_token": None,
        "source_rows": [],
        "current_index": 0,
        "default_contacts": default_contacts,
        "require_confirmation": require_confirmation,
        "batch_results": [],
        "success_count": 0,
        "error_count": 0,
        "final_message": None,
        "error_message": None,
    }
    config = {"configurable": {"thread_id": batch_id}}
    try:
        result = compiled_batch_graph.invoke(initial_state, config=config)
        return dict(result)
    except Exception as e:
        logger.exception("批量生成异常")
        return {
            **initial_state,
            "error_message": str(e),
            "final_message": f"批量生成异常: {str(e)}",
        }


def get_batch_state(batch_id: str) -> Optional[Dict[str, Any]]:
    """查询某个 batch 的当前执行状态。"""
    config = {"configurable": {"thread_id": batch_id}}
    try:
        state = compiled_batch_graph.get_state(config)
        if state:
            return dict(state.values)
        return None
    except Exception as e:
        logger.warning("查询 batch 状态失败: %s", e)
        return None
