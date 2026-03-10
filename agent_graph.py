"""
V2.0 LangGraph Agent
使用 ReAct 模式构建真正的 Agent，能自主决策调用工具。
"""

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from agent_tools import parse_product_url, extract_selling_points, generate_reviews

load_dotenv()

# Agent 的"大脑"——负责思考和决策的 LLM
agent_llm = ChatOpenAI(
    api_key=os.getenv("MOONSHOT_API_KEY"),
    base_url="https://api.moonshot.cn/v1",
    model="moonshot-v1-8k",
    temperature=0.3,
    max_retries=3,
)

# Agent 可以使用的工具箱
tools = [parse_product_url, extract_selling_points, generate_reviews]

# 系统提示词：告诉 Agent 它是谁、该怎么工作
AGENT_SYSTEM_PROMPT = """你是一个专业的电商买家秀自动生成助手，专门服务于服装服饰类商品。

你的工作流程如下：
1. 如果用户给了你一个商品链接（URL），先调用 parse_product_url 工具来解析商品信息。
   - 如果解析成功，拿到商品标题后继续下一步。
   - 如果解析失败（被反爬虫拦截），直接告诉用户解析失败，请他提供商品标题和卖点。
2. 如果你已经有了商品标题/描述信息，调用 extract_selling_points 工具来提取核心卖点。
3. 有了商品名称和卖点后，调用 generate_reviews 工具来生成买家评价。
4. 将生成的评价整理后返回给用户。

重要规则：
- 你每次只能调用一个工具。
- 不要编造商品信息，必须基于用户提供的内容或工具返回的结果。
- 如果用户直接给了商品标题和卖点，可以跳过步骤1和2，直接生成评价。
- 生成的条数默认为5条，除非用户指定了其他数量。
"""

# 创建 ReAct Agent
agent = create_react_agent(
    model=agent_llm,
    tools=tools,
    prompt=AGENT_SYSTEM_PROMPT,
)


def run_agent(user_input: str) -> str:
    """
    运行 Agent，传入用户的自然语言指令，返回最终结果。
    """
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_input}]}
    )
    
    # 提取最后一条 AI 消息作为最终输出
    final_message = result["messages"][-1].content
    return final_message
