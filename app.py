from fastapi import FastAPI
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langserve import add_routes
from dotenv import load_dotenv
import os

# Load environment variables (OPENAI_API_KEY)
load_dotenv()

# Initialize FastAPI application
app = FastAPI(
    title="小红书文案生成器 API",
    version="1.0",
    description="根据主题自动生成三个爆款小红书标题的智能助手。"
)

# 1. Initialize language model
# 使用 Moonshot (Kimi) 的兼容接口
# Kimi 的大模型名为 moonshot-v1-8k
model = ChatOpenAI(
    api_key=os.getenv("MOONSHOT_API_KEY"),
    base_url="https://api.moonshot.cn/v1",
    model="moonshot-v1-8k",
    temperature=0.7
)

# 2. Create the Prompt Template
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个精通全网爆款逻辑的小红书文案专家。请根据用户提供的主题，自动思考用户的受众痛点，并生成三个极具网感、带有恰当emoji、能引起强烈共鸣或好奇心的小红书爆款标题。每个标题之间请换行，直接输出文本即可不用做别的废话。"),
    ("user", "主题：{topic}")
])

# 3. Create LangChain Expression Language (LCEL) Chain
# The StrOutputParser simply extracts the string output from the LLM message
chain = prompt | model | StrOutputParser()

# 4. Integrate with LangServe
# This automatically wraps our chain into an API and creates a Playground at /copywriter/playground
add_routes(
    app,
    chain,
    path="/copywriter"
)

if __name__ == "__main__":
    import uvicorn
    # Start the application using Uvicorn
    # Make sure port 8000 is available, if not it will throw an error
    uvicorn.run(app, host="127.0.0.1", port=8000)
