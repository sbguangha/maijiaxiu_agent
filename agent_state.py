"""
LangGraph Agent 全局状态定义
用于在 StateGraph 各节点之间显式传递数据。
"""

from __future__ import annotations

from typing import TypedDict, Annotated, List, Optional, Dict, Any
import operator


class AgentState(TypedDict):
    """买家秀生成 Agent 的完整状态。

    设计原则：
    - 所有中间结果都显式放在 State 中，不依赖全局变量或闭包。
    - 控制流字段（crawl_success / skip_image_generation / error_message）
      供 conditional_edges 做路由决策。
    """

    # ========== 输入层 ==========
    user_input: str
    thread_id: str

    # 外部传入的配置（可选，未传时走环境变量默认值）
    review_count: int
    image_count: int
    target_contacts: List[str]
    product_image_bytes: Optional[bytes]          # 白底图二进制
    outfit_image_bytes_list: List[bytes]          # 穿搭参考图二进制
    require_confirmation: bool                    # 是否启用人工确认

    # ========== 商品信息层 ==========
    product_url: Optional[str]
    product_info: Optional[str]                   # 爬取或用户提供的原始信息
    product_name: Optional[str]
    selling_points: Optional[str]

    # ========== 生成结果层 ==========
    reviews_raw: Optional[str]                    # LLM 原始输出
    reviews_formatted: Optional[str]              # 格式化后的微信文案
    image_result: Optional[Dict[str, Any]]        # image_generator 返回的完整 dict
    local_image_paths: List[str]                  # 落地到本地的图片路径

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
    approval_status: Optional[str]                # pending / confirmed / rejected
    approval_id: Optional[str]
    approval_note: Optional[str]

    # ========== 输出层 ==========
    task_id: Optional[str]
    final_reply: Optional[str]                    # 返回给前端/用户的消息

    # ========== 批量处理（Map-Reduce）==========
    batch_id: Optional[str]
    batch_results: Annotated[List[Dict[str, Any]], operator.add]
    batch_total: int
    batch_processed: int
