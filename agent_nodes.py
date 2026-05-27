"""
LangGraph Agent 节点集合
每个节点只做一件事：读取 AgentState → 执行业务逻辑 → 返回需要更新的字段。
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional

from langgraph.types import interrupt

from agent_state import AgentState
from agent_tools import parse_product_url, extract_selling_points, generate_reviews
from config import create_moonshot_llm, settings
from image_generator import run_image_generation
from outbox import enqueue_task
from utils import (
    format_review_text_for_delivery,
    materialize_generated_images,
    extract_product_name,
    URL_PATTERN,
)

logger = logging.getLogger("agent-nodes")


def parse_input_node(state: AgentState) -> Dict[str, Any]:
    """解析用户输入，提取商品链接和商品名称。"""
    user_input = state.get("user_input", "")

    match = URL_PATTERN.search(user_input)
    product_url = match.group(0) if match else None
    product_name = extract_product_name(user_input)

    return {
        "product_url": product_url,
        "product_name": product_name,
    }


def crawl_url_node(state: AgentState) -> Dict[str, Any]:
    """爬取商品链接获取标题和描述。"""

    url = state.get("product_url")
    if not url:
        return {
            "crawl_success": False,
            "product_info": None,
        }

    try:
        result = parse_product_url(url)
        # parse_product_url 返回的是字符串
        if result and "解析失败" not in result:
            return {
                "crawl_success": True,
                "product_info": result,
            }
        else:
            return {
                "crawl_success": False,
                "product_info": result,
                "error_message": result,
            }
    except Exception as e:
        logger.exception("爬取商品链接失败")
        return {
            "crawl_success": False,
            "product_info": None,
            "error_message": f"爬取失败: {str(e)}",
        }


def extract_selling_points_node(state: AgentState) -> Dict[str, Any]:
    """基于商品信息提取核心卖点。"""

    product_info = state.get("product_info") or state.get("product_name") or state.get("user_input", "")
    if not product_info:
        return {
            "selling_points": "",
            "error_message": "缺少商品信息，无法提取卖点",
        }

    try:
        result = extract_selling_points(product_info)
        return {"selling_points": result}
    except Exception as e:
        logger.exception("提取卖点失败")
        return {
            "selling_points": "",
            "error_message": f"提取卖点失败: {str(e)}",
        }


def generate_reviews_node(state: AgentState) -> Dict[str, Any]:
    """根据商品名和卖点生成买家评价。"""

    product_name = state.get("product_name") or "服装商品"
    selling_points = state.get("selling_points") or ""
    count = state.get("review_count", 5)

    prompt = f"帮我生成{count}条评价，商品：{product_name}"
    if selling_points:
        prompt += f"，卖点：{selling_points}"

    # 如果有质检反馈，注入到生成提示中
    feedback = state.get("critique_feedback")
    if feedback:
        prompt += f"\n\n【上一次质检反馈，请务必按此改进】\n{feedback}"

    try:
        raw = generate_reviews(
            product_name=product_name,
            selling_points=selling_points,
            count=count,
        )
        formatted = format_review_text_for_delivery(raw)
        return {
            "reviews_raw": raw,
            "reviews_formatted": formatted,
            "review_generation_attempts": state.get("review_generation_attempts", 0) + 1,
        }
    except Exception as e:
        logger.exception("生成评价失败")
        return {
            "error_message": f"生成评价失败: {str(e)}",
            "reviews_raw": None,
            "reviews_formatted": None,
        }


def critique_reviews_node(state: AgentState) -> Dict[str, Any]:
    """质检节点：用 LLM 检查生成的评价是否有 AI 味。"""
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser


    reviews = state.get("reviews_raw", "")
    if not reviews:
        return {
            "critique_passed": True,
            "critique_result": "无评价内容，跳过质检",
            "critique_feedback": None,
        }

    llm = create_moonshot_llm(temperature=0.3, max_retries=2)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的淘宝/天猫买家秀质检员，专门识别 AI 生成的虚假评价。

请对以下评价进行质检，重点关注：
1. **AI 味**：是否使用华丽辞藻（如"犹如、宛若、极致的、前所未有的"）、排比句、过度煽情
2. **字数千篇一律**：真实评价有的长有的短，不要每条都是 30-50 字
3. **过于完美**：真实评价往往夹杂极小的不满（如物流慢、线头等）
4. **人设雷同**：不同评价是否有明显不同的口吻和视角

输出格式（严格按以下格式）：
质检结果：通过 / 不通过
问题说明：...
改进建议：..."""),
        ("user", "{reviews}"),
    ])

    chain = prompt | llm | StrOutputParser()

    try:
        result = chain.invoke({"reviews": reviews})
        match = re.search(r'质检结果[：:]\s*(通过|不通过)', result)
        passed = match and match.group(1) == "通过"
        feedback = None
        if not passed:
            m = re.search(r"改进建议[：:]\s*(.+?)(?=\Z)", result, re.S)
            if m:
                feedback = m.group(1).strip()
            else:
                feedback = result.strip()[:500]

        return {
            "critique_passed": passed,
            "critique_result": result,
            "critique_feedback": feedback,
        }
    except Exception as e:
        logger.warning("质检调用失败，默认放行: %s", e)
        return {
            "critique_passed": True,
            "critique_result": f"质检调用失败: {str(e)}",
            "critique_feedback": None,
        }


def generate_images_node(state: AgentState) -> Dict[str, Any]:
    """调用图生图模块生成买家秀配图。"""

    if state.get("skip_image_generation"):
        return {"image_result": None, "local_image_paths": []}

    product_name = state.get("product_name") or "服装商品"
    image_count = state.get("image_count", 2)
    image_bytes = state.get("product_image_bytes")
    outfit_bytes_list = state.get("outfit_image_bytes_list") or []

    if not image_bytes:
        return {
            "image_result": None,
            "local_image_paths": [],
            "error_message": "未提供商品白底图，跳过配图生成",
        }

    try:
        result = run_image_generation(
            image_bytes=image_bytes,
            product_name=product_name,
            scene_count=image_count,
            outfit_image_bytes_list=outfit_bytes_list if outfit_bytes_list else None,
        )
        # 提取本地图片路径（从 image_result 中的 URL 落地）
        # 但 run_image_generation 已经会写入飞书并返回 URL，
        # 这里我们暂时只保存 URL，后续 enqueue 时再落地
        local_paths: List[str] = []
        if result and result.get("status") == "success":
            local_paths = materialize_generated_images(result)

        return {
            "image_result": result,
            "local_image_paths": local_paths,
        }
    except Exception as e:
        logger.exception("生成配图失败")
        return {
            "image_result": None,
            "local_image_paths": [],
            "error_message": f"生成配图失败: {str(e)}",
        }


def human_approval_node(state: AgentState) -> Dict[str, Any]:
    """人工确认节点。

    注意：真正的 interrupt 由 graph 层在外部调用前设置，
    这里只做状态的整理和准备，供 interrupt payload 使用。
    实际 interrupt 放在 graph.py 的编译阶段通过 `interrupt` 实现。
    """
    payload = {
        "type": "delivery_approval",
        "review_text": state.get("reviews_formatted", ""),
        "image_paths": state.get("local_image_paths", []),
        "target_contacts": state.get("target_contacts", []),
        "product_name": state.get("product_name", ""),
    }
    # interrupt 会暂停图执行并保存 checkpoint
    # 恢复时，传入的值会作为 result 返回
    result = interrupt(payload)
    return {
        "approval_status": result.get("status", "rejected"),
        "approval_note": result.get("note", ""),
    }


def enqueue_delivery_node(state: AgentState) -> Dict[str, Any]:
    """将生成结果入队到 Outbox，供影刀消费。"""

    db_path = settings.outbox.db_path
    target_contacts = state.get("target_contacts", [])
    review_text = state.get("reviews_formatted", "")
    image_paths = state.get("local_image_paths", [])

    if not target_contacts:
        return {
            "task_id": None,
            "final_reply": "未提供微信联系人，已生成但未入队发送。",
        }

    try:
        task = enqueue_task(
            db_path=db_path,
            target_contacts=target_contacts,
            review_text=review_text,
            image_paths=image_paths,
            file_paths=[],
            max_retry=4,
        )
        return {
            "task_id": task.task_id,
            "final_reply": f"已加入发送队列，任务 ID: {task.task_id}",
        }
    except Exception as e:
        logger.exception("入队发送失败")
        return {
            "task_id": None,
            "final_reply": f"入队失败: {str(e)}",
            "error_message": f"入队失败: {str(e)}",
        }


def format_output_node(state: AgentState) -> Dict[str, Any]:
    """最终输出节点：整理所有结果返回给前端。"""
    reply = state.get("reviews_formatted", "")
    if not reply:
        reply = state.get("reviews_raw", "") or state.get("final_reply", "")

    error = state.get("error_message")
    if error and not reply:
        reply = f"⚠️ {error}"

    return {
        "final_reply": reply,
    }


def handle_error_node(state: AgentState) -> Dict[str, Any]:
    """错误处理节点：收集错误信息，优雅降级。"""
    error = state.get("error_message", "未知错误")
    logger.error("流程异常终止: %s", error)
    return {
        "final_reply": f"⚠️ 处理失败：{error}",
    }


# =====================================================================
# 错误路由
# =====================================================================

def route_on_error(state: AgentState) -> str:
    """若 error_message 非空则进入错误处理。"""
    if state.get("error_message"):
        return "handle_error"
    return "continue"


# =====================================================================
# 条件边路由函数
# =====================================================================

def route_after_parse_input(state: AgentState) -> str:
    """解析输入后：有 URL 先爬取，否则直接提取卖点。"""
    if state.get("product_url"):
        return "crawl_url"
    return "extract_selling_points"


def route_after_crawl(state: AgentState) -> str:
    """爬取后：无论成功失败都继续提取卖点（失败时会回退到用户输入）。"""
    if state.get("error_message"):
        return "handle_error"
    return "extract_selling_points"


def route_after_generate_images(state: AgentState) -> str:
    """生成配图后：如果需要人工确认则中断，否则直接入队。"""
    if state.get("error_message"):
        return "handle_error"
    if state.get("require_confirmation"):
        return "human_approval"
    return "enqueue_delivery"


def route_after_human_approval(state: AgentState) -> str:
    """人工确认后：确认则入队，驳回则结束。"""
    if state.get("error_message"):
        return "handle_error"
    status = state.get("approval_status")
    if status == "confirmed":
        return "enqueue_delivery"
    return "format_output"


def route_after_critique(state: AgentState) -> str:
    """质检后：通过则继续后续流程，不通过则重生成。"""
    MAX_REVIEW_ATTEMPTS = 3
    if state.get("error_message"):
        return "handle_error"
    passed = state.get("critique_passed", True)
    attempts = state.get("review_generation_attempts", 0)

    if not passed and attempts < MAX_REVIEW_ATTEMPTS:
        logger.info("质检不通过，第 %d 次重试生成评价", attempts)
        return "retry"
    if not passed:
        logger.warning("质检不通过，已达最大重试次数 %d，强制放行", MAX_REVIEW_ATTEMPTS)

    if state.get("skip_image_generation"):
        return "human_approval" if state.get("require_confirmation") else "enqueue_delivery"
    if state.get("product_image_bytes"):
        return "generate_images"
    return "human_approval" if state.get("require_confirmation") else "enqueue_delivery"
    if state.get("product_image_bytes"):
        return "generate_images"
    return "human_approval" if state.get("require_confirmation") else "enqueue_delivery"


def route_after_generate_images(state: AgentState) -> str:
    """生成配图后：如果需要人工确认则中断，否则直接入队。"""
    if state.get("require_confirmation"):
        return "human_approval"
    return "enqueue_delivery"


def route_after_human_approval(state: AgentState) -> str:
    """人工确认后：确认则入队，驳回则结束。"""
    status = state.get("approval_status")
    if status == "confirmed":
        return "enqueue_delivery"
    return "format_output"  # rejected 或其他状态直接结束


def route_after_critique(state: AgentState) -> str:
    """质检后：通过则继续后续流程（配图/确认/入队），不通过则重生成。"""
    MAX_REVIEW_ATTEMPTS = 3
    passed = state.get("critique_passed", True)
    attempts = state.get("review_generation_attempts", 0)

    if not passed and attempts < MAX_REVIEW_ATTEMPTS:
        logger.info("质检不通过，第 %d 次重试生成评价", attempts)
        return "retry"
    if not passed:
        logger.warning("质检不通过，已达最大重试次数 %d，强制放行", MAX_REVIEW_ATTEMPTS)

    # 通过或强制放行：继续后续分流逻辑（和原 generate_reviews 后的分流一致）
    if state.get("skip_image_generation"):
        return "human_approval" if state.get("require_confirmation") else "enqueue_delivery"
    if state.get("product_image_bytes"):
        return "generate_images"
    return "human_approval" if state.get("require_confirmation") else "enqueue_delivery"
