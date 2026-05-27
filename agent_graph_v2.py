"""
V3.0 LangGraph StateGraph Agent
使用显式状态机编排买家秀生成全流程：
  解析输入 → 爬取/提取卖点 → 生成评价 → 生成配图 → [人工确认] → 入队发送
支持 SQLite Checkpoint 持久化、中断恢复、人机协同。
"""

from __future__ import annotations

import os
import sqlite3
import logging
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, END
from langgraph.types import Command
from langgraph.checkpoint.sqlite import SqliteSaver

from agent_state import AgentState
from agent_nodes import (
    parse_input_node,
    crawl_url_node,
    extract_selling_points_node,
    generate_reviews_node,
    critique_reviews_node,
    generate_images_node,
    enqueue_delivery_node,
    format_output_node,
    human_approval_node,
    route_after_parse_input,
    route_after_crawl,
    route_after_generate_reviews,
    route_after_generate_images,
    route_after_human_approval,
    route_after_critique,
)

logger = logging.getLogger("agent-graph-v2")

# =====================================================================
# 构建 StateGraph
# =====================================================================

workflow = StateGraph(AgentState)

# 注册所有节点
workflow.add_node("parse_input", parse_input_node)
workflow.add_node("crawl_url", crawl_url_node)
workflow.add_node("extract_selling_points", extract_selling_points_node)
workflow.add_node("generate_reviews", generate_reviews_node)
workflow.add_node("critique_reviews", critique_reviews_node)
workflow.add_node("generate_images", generate_images_node)
workflow.add_node("human_approval", human_approval_node)
workflow.add_node("enqueue_delivery", enqueue_delivery_node)
workflow.add_node("format_output", format_output_node)

# 入口
workflow.set_entry_point("parse_input")

# 条件边：解析输入后，有 URL 则爬取，否则直接提取卖点
workflow.add_conditional_edges(
    "parse_input",
    route_after_parse_input,
    {
        "crawl_url": "crawl_url",
        "extract_selling_points": "extract_selling_points",
    },
)

# 爬取后（无论成败）都进入卖点提取
workflow.add_conditional_edges(
    "crawl_url",
    route_after_crawl,
    {
        "extract_selling_points": "extract_selling_points",
    },
)

# 提取卖点后生成评价
workflow.add_edge("extract_selling_points", "generate_reviews")

# 生成评价后先质检
workflow.add_edge("generate_reviews", "critique_reviews")

# 质检后：不通过则重试生成，通过则根据配图/确认条件继续分流
workflow.add_conditional_edges(
    "critique_reviews",
    route_after_critique,
    {
        "retry": "generate_reviews",
        "generate_images": "generate_images",
        "human_approval": "human_approval",
        "enqueue_delivery": "enqueue_delivery",
    },
)

# 生成配图后：需要确认则中断，否则直接入队
workflow.add_conditional_edges(
    "generate_images",
    route_after_generate_images,
    {
        "human_approval": "human_approval",
        "enqueue_delivery": "enqueue_delivery",
    },
)

# 人工确认后：确认则入队，驳回则结束
workflow.add_conditional_edges(
    "human_approval",
    route_after_human_approval,
    {
        "enqueue_delivery": "enqueue_delivery",
        "format_output": "format_output",
    },
)

# 入队后格式化输出
workflow.add_edge("enqueue_delivery", "format_output")

# 结束
workflow.add_edge("format_output", END)


# =====================================================================
# 持久化层（SQLite Checkpoint）
# =====================================================================

def _get_checkpointer():
    """获取 SQLite Checkpointer，支持跨线程。"""
    from config import settings  # pylint: disable=import-outside-toplevel
    checkpoint_db = settings.paths.checkpoint_db
    os.makedirs(os.path.dirname(os.path.abspath(checkpoint_db)), exist_ok=True)
    # SqliteSaver 需要一个 sqlite3.Connection；check_same_thread=False 允许多线程
    conn = sqlite3.connect(checkpoint_db, check_same_thread=False)
    return SqliteSaver(conn)


try:
    checkpointer = _get_checkpointer()
    compiled_graph = workflow.compile(checkpointer=checkpointer)
    from config import settings  # pylint: disable=import-outside-toplevel
    logger.info("Agent Graph V2 编译成功，使用 SQLite Checkpoint: %s", settings.paths.checkpoint_db)
except Exception as e:
    logger.warning("SQLite Checkpoint 初始化失败 (%s)，回退到内存模式", e)
    from langgraph.checkpoint.memory import MemorySaver
    checkpointer = MemorySaver()
    compiled_graph = workflow.compile(checkpointer=checkpointer)


# =====================================================================
# 对外 API（替代旧版 run_agent）
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
) -> Dict[str, Any]:
    """运行完整的买家秀生成图。

    返回包含 final_reply、reviews_raw、image_result、task_id 等的字典。
    """
    if thread_id is None:
        import uuid
        thread_id = str(uuid.uuid4())

    initial_state: AgentState = {
        "user_input": user_input,
        "thread_id": thread_id,
        "product_url": None,
        "product_info": None,
        "product_name": None,
        "selling_points": None,
        "review_count": review_count,
        "image_count": image_count,
        "target_contacts": target_contacts or [],
        "product_image_bytes": product_image_bytes,
        "outfit_image_bytes_list": outfit_image_bytes_list or [],
        "require_confirmation": require_confirmation,
        "reviews_raw": None,
        "reviews_formatted": None,
        "image_result": None,
        "local_image_paths": [],
        "crawl_success": False,
        "skip_image_generation": False,
        "error_message": None,
        "critique_result": None,
        "critique_passed": False,
        "critique_feedback": None,
        "review_generation_attempts": 0,
        "approval_status": None,
        "approval_id": None,
        "approval_note": None,
        "task_id": None,
        "final_reply": None,
        "batch_id": None,
        "batch_results": [],
        "batch_total": 0,
        "batch_processed": 0,
    }

    config = {"configurable": {"thread_id": thread_id}}

    # 如果启用了人工确认，图会在 human_approval 节点前中断
    # 调用方需要用 resume_agent_v2 恢复
    try:
        result = compiled_graph.invoke(initial_state, config=config)
        return dict(result)
    except Exception as e:
        logger.exception("Agent Graph V2 执行异常")
        return {
            **initial_state,
            "final_reply": f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
            "error_message": str(e),
        }


def get_agent_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """查询某个 thread 的当前状态（用于前端轮询人工确认进度）。"""
    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = compiled_graph.get_state(config)
        if state:
            return dict(state.values)
        return None
    except Exception as e:
        logger.warning("查询状态失败: %s", e)
        return None


def resume_agent_v2(thread_id: str, approval_status: str, approval_note: str = "") -> Dict[str, Any]:
    """恢复一个被中断的 Agent 执行（人工确认后调用）。

    Args:
        thread_id: 原会话 ID
        approval_status: "confirmed" 或 "rejected"
        approval_note: 确认/驳回备注
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        # 先更新状态中的 approval 信息
        current = compiled_graph.get_state(config)
        if current is None:
            return {"error": "thread_not_found", "final_reply": "未找到对应的会话记录"}

        # 使用 Command(resume=...) 恢复执行
        # interrupt 会在 human_approval_node 返回后继续
        # 我们传入的值会被当作 interrupt 的返回值
        # 但 human_approval_node 目前返回的是固定的 {"approval_status": "pending"}
        # 所以需要一种方式把 approval_status 注入 State

        # 使用 Command(resume=...) 恢复被 interrupt 暂停的节点
        # human_approval_node 中的 interrupt() 会接收到这个 resume 值
        result = compiled_graph.invoke(
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


# 兼容旧接口（聊天模式，无图、无确认）
def run_agent(user_input: str) -> str:
    """兼容 V1 接口：纯文字聊天，返回字符串回复。"""
    result = run_agent_v2(
        user_input=user_input,
        review_count=5,
        image_count=0,
        require_confirmation=False,
    )
    return result.get("final_reply", "") or result.get("reviews_formatted", "") or "生成失败"
