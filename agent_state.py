"""
LangGraph Agent 全局状态定义
用于在 StateGraph 各节点之间显式传递数据。
"""

from __future__ import annotations

from typing import TypedDict, List, Optional, Dict, Any


class AgentState(TypedDict):
    """买家秀生成 Agent 的单商品处理状态。

    设计原则：
    - 所有中间结果都显式放在 State 中，不依赖全局变量或闭包。
    - 控制流字段（crawl_success / skip_image_generation / error_message）
      供 conditional_edges 做路由决策。
    - 批处理字段已移至 batch_graph.py 的 BatchState，职责分离。
    """

    # ========== 输入层 ==========
    user_input: str
    thread_id: str

    review_count: int
    image_count: int
    target_contacts: List[str]
    product_image_bytes: Optional[bytes]
    outfit_image_bytes_list: List[bytes]
    require_confirmation: bool

    # ========== 商品信息层 ==========
    product_url: Optional[str]
    product_info: Optional[str]
    product_name: Optional[str]
    selling_points: Optional[str]

    # ========== 生成结果层 ==========
    reviews_raw: Optional[str]
    reviews_formatted: Optional[str]
    image_result: Optional[Dict[str, Any]]
    local_image_paths: List[str]

    # ========== 质检层 ==========
    critique_result: Optional[str]
    critique_passed: bool
    critique_feedback: Optional[str]
    review_generation_attempts: int

    # ========== 控制流 ==========
    crawl_success: bool
    skip_image_generation: bool
    error_message: Optional[str]

    # ========== 人工确认层 ==========
    approval_status: Optional[str]
    approval_id: Optional[str]
    approval_note: Optional[str]

    # ========== 输出层 ==========
    task_id: Optional[str]
    final_reply: Optional[str]
