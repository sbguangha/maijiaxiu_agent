"""
批量生成子图（Map-Reduce 并行版）

使用 LangGraph Send 机制并行处理飞书需求表的每一行，
利用 SQLite Checkpoint 保证批量处理中断后可恢复。

并发控制：默认最多同时处理 3 行，避免 API 过载（429）。
"""

from __future__ import annotations

import os
import sqlite3
import logging
import threading
from typing import Any, Dict, List, Optional, TypedDict, Annotated, Union

import operator
from langgraph.graph import StateGraph, END, CompiledStateGraph
from langgraph.types import Send
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger("batch-graph")

# =====================================================================
# 并发控制
# =====================================================================
from config import settings  # pylint: disable=import-outside-toplevel

_MAX_CONCURRENT_ROWS = int(os.getenv("BATCH_MAX_CONCURRENT", "3"))
_ROW_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_ROWS)


class BatchState(TypedDict):
    """批量处理专用状态（Map-Reduce 模式）。"""

    batch_id: str
    feishu_token: Optional[str]
    source_rows: List[Any]
    default_contacts: List[str]
    require_confirmation: bool

    row: Optional[Any]
    row_results: Annotated[List[Dict[str, Any]], operator.add]
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
            "row_results": [],
            "success_count": 0,
            "error_count": 0,
        }
    except Exception as e:
        logger.exception("[batch-%s] 读取飞书失败", state["batch_id"])
        return {
            "error_message": f"读取飞书失败: {str(e)}",
            "source_rows": [],
        }


def map_rows(state: BatchState) -> List[Send]:
    """Map 阶段：对每一行发送并行任务到 process_row 节点。"""
    rows = state.get("source_rows", [])
    if not rows:
        return []

    logger.info(
        "[batch-%s] 启动并行处理，共 %d 行，最大并发 %d",
        state["batch_id"], len(rows), _MAX_CONCURRENT_ROWS,
    )

    return [
        Send(
            "process_row",
            {
                "row": row,
                "feishu_token": state.get("feishu_token", ""),
            },
        )
        for row in rows
    ]


def process_row_node(state: BatchState) -> Dict[str, Any]:
    """Reduc 阶段中的单行处理节点（通过 Send 并行调用）。"""
    from feishu_reader import (  # pylint: disable=import-outside-toplevel
        download_attachment,
        update_source_row_status,
    )
    from agent_graph_v2 import run_agent_v2  # pylint: disable=import-outside-toplevel

    row = state.get("row")
    if row is None:
        logger.warning("[batch] process_row 接收到空 row，跳过")
        return {"row_results": []}

    batch_id = state["batch_id"]
    row_result: Dict[str, Any] = {
        "record_id": row.record_id,
        "product_title": row.product_title,
        "status": "pending",
        "review_text": "",
        "image_urls": [],
        "task_id": None,
        "error": None,
    }

    with _ROW_SEMAPHORE:
        try:
            logger.info("[batch-%s] 并行处理: %s", batch_id, row.product_title)

            image_bytes = None
            outfit_bytes_list: List[bytes] = []
            token = state.get("feishu_token", "")
            if token and row.image_file_tokens:
                image_bytes = download_attachment(token, row.image_file_tokens[0])
                for outfit_token in row.outfit_image_file_tokens:
                    b = download_attachment(token, outfit_token)
                    if b and len(b) > 100:
                        outfit_bytes_list.append(b)

            contacts = row.wechat_contacts or state.get("default_contacts", [])
            result = run_agent_v2(
                user_input=f"帮我生成{row.review_count}条评价，商品：{row.product_title}",
                product_image_bytes=image_bytes,
                outfit_image_bytes_list=outfit_bytes_list,
                target_contacts=contacts,
                review_count=row.review_count,
                image_count=row.image_count,
                require_confirmation=False,
            )

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
            logger.info("[batch-%s] 完成: %s", batch_id, row.product_title)

        except Exception as e:
            logger.exception("[batch-%s] 失败: %s", batch_id, row.product_title)
            row_result.update({"status": "error", "error": str(e)})

    return {"row_results": [row_result]}


def aggregate_node(state: BatchState) -> Dict[str, Any]:
    """汇总所有并行处理的结果。"""
    results = state.get("row_results", [])
    total = len(state.get("source_rows", []))
    success = sum(1 for r in results if r.get("status") == "success")
    errors = sum(1 for r in results if r.get("status") == "error")
    msg = f"批量处理完成：{success}/{total} 成功，{errors} 失败"
    logger.info("[batch-%s] %s", state["batch_id"], msg)
    return {
        "final_message": msg,
        "success_count": success,
        "error_count": errors,
    }


# =====================================================================
# Graph 工厂函数
# =====================================================================

def _build_batch_workflow() -> StateGraph:
    """构建批量子图结构（Map-Reduce 并行模式）。"""
    workflow = StateGraph(BatchState)
    workflow.add_node("fetch_rows", fetch_rows_node)
    workflow.add_node("process_row", process_row_node)
    workflow.add_node("aggregate", aggregate_node)
    workflow.set_entry_point("fetch_rows")
    workflow.add_conditional_edges("fetch_rows", map_rows, ["process_row"])
    workflow.add_edge("process_row", "aggregate")
    workflow.add_edge("aggregate", END)
    return workflow


def _create_sqlite_checkpointer(db_path: str) -> SqliteSaver:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


def build_batch_graph(
    checkpointer: Union[SqliteSaver, MemorySaver, None] = None,
) -> CompiledStateGraph:
    """构建并编译批量处理图。

    Args:
        checkpointer: 可选自定义 checkpointer
    """
    workflow = _build_batch_workflow()

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)

    try:
        cp = _create_sqlite_checkpointer(settings.paths.batch_checkpoint_db)
        logger.info("Batch Graph 编译成功（并行模式，最大并发 %d）", _MAX_CONCURRENT_ROWS)
        return workflow.compile(checkpointer=cp)
    except Exception as e:
        logger.warning("Batch Graph SQLite Checkpoint 失败 (%s)，回退到内存", e)
        return workflow.compile(checkpointer=MemorySaver())


# =====================================================================
# 模块级懒加载单例（向后兼容）
# =====================================================================

_compiled_batch_graph: Optional[CompiledStateGraph] = None


def _get_compiled_batch_graph() -> CompiledStateGraph:
    global _compiled_batch_graph  # pylint: disable=global-statement
    if _compiled_batch_graph is None:
        _compiled_batch_graph = build_batch_graph()
    return _compiled_batch_graph


try:
    compiled_batch_graph = build_batch_graph()
except Exception:
    compiled_batch_graph = build_batch_graph(checkpointer=MemorySaver())


# =====================================================================
# 对外 API
# =====================================================================

def run_batch_generate(
    batch_id: str,
    default_contacts: List[str],
    require_confirmation: bool = False,
    graph: Optional[CompiledStateGraph] = None,
) -> Dict[str, Any]:
    """启动批量生成流程。"""
    initial_state: BatchState = {
        "batch_id": batch_id,
        "feishu_token": None,
        "source_rows": [],
        "default_contacts": default_contacts,
        "require_confirmation": require_confirmation,
        "row": None,
        "row_results": [],
        "success_count": 0,
        "error_count": 0,
        "final_message": None,
        "error_message": None,
    }
    config = {"configurable": {"thread_id": batch_id}}
    g = graph if graph is not None else _get_compiled_batch_graph()
    try:
        result = g.invoke(initial_state, config=config)
        return dict(result)
    except Exception as e:
        logger.exception("批量生成异常")
        return {
            **initial_state,
            "error_message": str(e),
            "final_message": f"批量生成异常: {str(e)}",
        }


def get_batch_state(
    batch_id: str,
    graph: Optional[CompiledStateGraph] = None,
) -> Optional[Dict[str, Any]]:
    """查询某个 batch 的当前执行状态。"""
    config = {"configurable": {"thread_id": batch_id}}
    g = graph if graph is not None else _get_compiled_batch_graph()
    try:
        state = g.get_state(config)
        if state:
            return dict(state.values)
        return None
    except Exception as e:
        logger.warning("查询 batch 状态失败: %s", e)
        return None
