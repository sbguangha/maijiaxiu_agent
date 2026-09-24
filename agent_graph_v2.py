"""
V3.0 LangGraph StateGraph Agent
使用显式状态机编排买家秀生成全流程：
  解析商品标题 → 生成评价 → 生成配图 → [人工确认] → 输出结果
支持 SQLite Checkpoint 持久化、中断恢复、人机协同。
"""

from __future__ import annotations

import os
import sqlite3
import logging
from typing import Any, Dict, Optional, Union

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import MemorySaver

try:
    from langgraph.errors import GraphInterrupt
except Exception:
    from langgraph.types import GraphInterrupt

from config import settings
from agent_state import AgentState
from agent_nodes import (
    parse_input_node,
    generate_reviews_node,
    generate_images_node,
    format_output_node,
    human_approval_node,
    handle_error_node,
    route_after_generate_reviews,
    route_after_generate_images,
    route_after_human_approval,
    route_on_error,
)

logger = logging.getLogger("agent-graph-v2")


# =====================================================================
# Graph 工厂函数
# =====================================================================

def _build_workflow() -> StateGraph:
    """构建 StateGraph 结构（不含编译）。"""
    workflow = StateGraph(AgentState)

    workflow.add_node("parse_input", parse_input_node)
    workflow.add_node("generate_reviews", generate_reviews_node)
    workflow.add_node("generate_images", generate_images_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("format_output", format_output_node)
    workflow.add_node("handle_error", handle_error_node)

    workflow.set_entry_point("parse_input")
    workflow.add_edge("parse_input", "generate_reviews")
    workflow.add_conditional_edges(
        "generate_reviews",
        route_after_generate_reviews,
        {
            "generate_images": "generate_images",
            "human_approval": "human_approval",
            "format_output": "format_output",
            "handle_error": "handle_error",
        },
    )

    workflow.add_conditional_edges(
        "generate_images",
        route_after_generate_images,
        {"human_approval": "human_approval", "format_output": "format_output", "handle_error": "handle_error"},
    )
    workflow.add_conditional_edges(
        "human_approval",
        route_after_human_approval,
        {"format_output": "format_output", "handle_error": "handle_error"},
    )
    workflow.add_edge("format_output", END)
    workflow.add_edge("handle_error", END)

    return workflow


def _create_sqlite_checkpointer(db_path: str) -> SqliteSaver:
    """创建 SQLite Checkpointer（连接由 SqliteSaver 管理生命周期）。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn)


def build_agent_graph(
    checkpointer: Union[SqliteSaver, MemorySaver, None] = None,
) -> CompiledStateGraph:
    """构建并编译 Agent StateGraph。

    Args:
        checkpointer: 可选自定义 checkpointer，为 None 时自动创建 SQLite checkpointer
    """
    workflow = _build_workflow()

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)

    try:
        cp = _create_sqlite_checkpointer(settings.paths.checkpoint_db)
        logger.info("Agent Graph 编译成功，使用 SQLite Checkpoint: %s", settings.paths.checkpoint_db)
        return workflow.compile(checkpointer=cp)
    except Exception as e:
        logger.warning("SQLite Checkpoint 初始化失败 (%s)，回退到内存模式", e)
        return workflow.compile(checkpointer=MemorySaver())


# =====================================================================
# 模块级懒加载单例（向后兼容）
# =====================================================================

_compiled_graph: Optional[CompiledStateGraph] = None
_graph_lock: Any = None


def _get_compiled_graph() -> CompiledStateGraph:
    """获取模块级编译图（懒加载，线程不安全）。"""
    global _compiled_graph  # pylint: disable=global-statement
    if _compiled_graph is None:
        _compiled_graph = build_agent_graph()
    return _compiled_graph


# 向后兼容别名
compiled_graph: CompiledStateGraph  # type: ignore
try:
    compiled_graph = build_agent_graph()
except Exception:
    compiled_graph = build_agent_graph(checkpointer=MemorySaver())


# =====================================================================
# 对外 API
# =====================================================================

def run_agent_v2(
    user_input: str,
    thread_id: Optional[str] = None,
    product_image_bytes: Optional[bytes] = None,
    outfit_image_bytes_list: Optional[list] = None,
    target_contacts: Optional[list] = None,
    review_count: int = 5,
    image_count: int = 2,
    require_confirmation: bool = False,
    defer_feishu_commit: bool = False,
    graph: Optional[CompiledStateGraph] = None,
) -> Dict[str, Any]:
    """运行完整的买家秀生成图。

    Args:
        graph: 可选的自定义编译图，为 None 时使用模块级单例
    """
    if thread_id is None:
        import uuid
        thread_id = str(uuid.uuid4())

    initial_state: AgentState = {
        "user_input": user_input,
        "thread_id": thread_id,
        "product_name": None,
        "garment_one_line": None,
        "delivery_decision": None,
        "review_count": review_count,
        "image_count": image_count,
        "target_contacts": target_contacts or [],
        "product_image_bytes": product_image_bytes,
        "outfit_image_bytes_list": outfit_image_bytes_list or [],
        "require_confirmation": require_confirmation,
        "defer_feishu_commit": defer_feishu_commit,
        "reviews_raw": None,
        "reviews_formatted": None,
        "image_result": None,
        "local_image_paths": [],
        "skip_image_generation": False,
        "error_message": None,
        "approval_status": None,
        "approval_id": None,
        "approval_note": None,
        "task_id": None,
        "final_reply": None,
    }

    config = {"configurable": {"thread_id": thread_id}}
    g = graph if graph is not None else _get_compiled_graph()

    try:
        result = g.invoke(initial_state, config=config)
        return dict(result)
    except GraphInterrupt:
        raise
    except Exception as e:
        logger.exception("Agent Graph V2 执行异常")
        return {
            **initial_state,
            "final_reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
            "error_message": str(e),
        }


def get_agent_state(thread_id: str, graph: Optional[CompiledStateGraph] = None) -> Optional[Dict[str, Any]]:
    """查询某个 thread 的当前状态（用于前端轮询人工确认进度）。"""
    config = {"configurable": {"thread_id": thread_id}}
    g = graph if graph is not None else _get_compiled_graph()
    try:
        state = g.get_state(config)
        if state:
            return dict(state.values)
        return None
    except Exception as e:
        logger.warning("查询状态失败: %s", e)
        return None


def resume_agent_v2(
    thread_id: str,
    approval_status: str,
    approval_note: str = "",
    graph: Optional[CompiledStateGraph] = None,
) -> Dict[str, Any]:
    """恢复一个被中断的 Agent 执行（人工确认后调用）。

    Args:
        thread_id: 原会话 ID
        approval_status: "confirmed" 或 "rejected"
        approval_note: 确认/驳回备注
    """
    g = graph if graph is not None else _get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        current = g.get_state(config)
        if current is None:
            return {"error": "thread_not_found", "final_reply": "未找到对应的会话记录"}

        result = g.invoke(
            Command(resume={"status": approval_status, "note": approval_note}),
            config=config,
        )
        return dict(result)
    except Exception as e:
        logger.exception("恢复 Agent 执行失败")
        return {
            "error": str(e),
            "final_reply": f"⚠️ 恢复执行失败：{str(e)}",
        }


def run_agent(user_input: str) -> str:
    """兼容 V1 接口：纯文字聊天，返回字符串回复。"""
    result = run_agent_v2(
        user_input=user_input,
        review_count=5,
        image_count=0,
        require_confirmation=False,
    )
    return result.get("final_reply", "") or result.get("reviews_formatted", "") or "生成失败"
