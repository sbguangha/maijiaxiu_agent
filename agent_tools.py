"""
V2.0 Agent 工具集
定义三个 @tool 装饰器函数，供 LangGraph Agent 自主调用。
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()

# 初始化一个共享的 LLM 实例，给需要调用模型的工具使用
_llm = ChatOpenAI(
    api_key=os.getenv("MOONSHOT_API_KEY"),
    base_url="https://api.moonshot.cn/v1",
    model="moonshot-v1-8k",
    temperature=0.7,
    max_retries=3,
)


# ========== Tool 1: 链接解析器 ==========
@tool
def parse_product_url(url: str) -> str:
    """根据电商商品链接（淘宝、天猫、京东等），尝试抓取并返回商品的标题信息。
    
    如果链接无法访问或被反爬虫拦截，会返回失败信息，此时你应该直接询问用户提供商品标题和卖点。
    
    Args:
        url: 电商商品链接，例如 https://item.taobao.com/item.htm?id=xxx
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 提取标题
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        
        # 尝试提取 meta description
        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = meta_tag["content"].strip()
        
        # 尝试提取 keywords
        keywords = ""
        kw_tag = soup.find("meta", attrs={"name": "keywords"})
        if kw_tag and kw_tag.get("content"):
            keywords = kw_tag["content"].strip()

        if not title or "验证" in title or "登录" in title or "安全" in title:
            return f"解析失败：链接 {url} 可能被反爬虫拦截，无法获取商品信息。建议直接提供商品标题和卖点信息。"
        
        result = f"商品标题：{title}"
        if meta_desc:
            result += f"\n商品描述：{meta_desc}"
        if keywords:
            result += f"\n关键词：{keywords}"
        return result
        
    except requests.RequestException as e:
        return f"解析失败：无法访问链接 {url}，错误信息：{str(e)}。建议直接提供商品标题和卖点信息。"


# ========== Tool 2: 卖点提取器 ==========
@tool
def extract_selling_points(product_info: str) -> str:
    """根据商品标题和描述信息，利用 AI 分析并提取该商品的 3-5 个核心卖点。
    
    返回卖点列表，用顿号分隔。这些卖点将用于后续生成买家评价。
    
    Args:
        product_info: 商品的标题、描述或关键词等文字信息。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个资深的电商运营专家，专门分析服装服饰类商品的核心卖点。
请根据提供的商品信息，提取出 3-5 个最能打动消费者的核心卖点。

要求：
1. 卖点要简短精炼，每个 2-6 个字
2. 聚焦消费者真正关心的点（如：显瘦、面料舒适、洋气、百搭等）
3. 不要编造商品信息里没有暗示的卖点
4. 只输出卖点，用顿号分隔，不要输出任何其他内容"""),
        ("user", "商品信息：{product_info}\n\n请提取核心卖点：")
    ])
    
    chain = prompt | _llm | StrOutputParser()
    result = chain.invoke({"product_info": product_info})
    return result.strip()


# ========== Tool 3: 评价生成器 ==========
@tool
def generate_reviews(product_name: str, selling_points: str, count: int = 5) -> str:
    """根据商品名称和卖点，生成指定数量的真实风格买家评价。
    
    生成的评价具有不同人设视角（微胖女孩、面料党、凑单型等），高度逼真，无 AI 痕迹。
    每条评价包含评价正文和配图建议。
    
    Args:
        product_name: 商品名称/标题
        selling_points: 商品核心卖点，用顿号或逗号分隔
        count: 要生成的评价条数，默认5条
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的淘宝/天猫【服装服饰类】"真实买家秀"生成引擎。
你的任务是根据提供的商品信息，生成高度逼真、毫无 AI 痕迹的买家评价。

### 🚨 绝对不可跨越的红线（反 AI 味规则）：
1. **禁用华丽辞藻**：绝对禁止使用"犹如、宛若、极致的、前所未有、令人惊叹、简直是"等书面语词汇。
2. **禁用排比句和过度吹捧**：普通买家不会像写诗一样评价衣服。
3. **字数错落有致**：不要每条都很长！有的买家很懒只有10个字以内（如"版型不错，挺显瘦的"），有的买家絮絮叨叨会有50字左右的分享。
4. **允许不完美**：真实的评价往往夹杂着极小的不满，例如"物流有点慢，但衣服好看就原谅了"、"线头需要自己剪一下，不过这个价位要什么自行车"。

### 👗 服装类专属语料库与人设提示：
- **常用黑话**：绝绝子、闭眼入、踩雷、版型绝了、显瘦、梨形身材天菜、质感在线、起球、起静电、不扎人、百搭、上身图。
- **人设视角**：
  A. 【微胖/梨形身材】：看重显瘦、遮肉、版型。
  B. 【面料质感党】：看重纯棉/真丝/羊毛，起球、缩水。
  C. 【敷衍凑单型】：语气随意，如"挺好的 习惯好评"。
  D. 【疯狂安利型】：激动，emoji，"室友要链接了"。
  E. 【犹豫退货型】：想退后来留下了。

### 输出格式：
每条评价按以下格式输出，评价之间用空行分隔：

评价X：
内容：（评价正文）
配图建议：（从以下选一个：对镜自拍全身照、半身侧面显瘦照、衣服平铺细节图、面料特写图、拆快递开箱图、搭配穿搭图、户外街拍图、办公室随手拍）
"""),
        ("user", "商品名称：{product_name}\n商品卖点：{selling_points}\n\n请生成 {count} 条买家评价。")
    ])
    
    chain = prompt | _llm | StrOutputParser()
    result = chain.invoke({
        "product_name": product_name,
        "selling_points": selling_points,
        "count": count
    })
    
    # --- 自动回写飞书逻辑 ---
    try:
        FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
        FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
        FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN")
        FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID")
        
        if all([FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN, FEISHU_TABLE_ID]):
            # 1. 获取 token
            token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            token_resp = requests.post(token_url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).json()
            
            if token_resp.get("code") == 0:
                token = token_resp.get("tenant_access_token")
                
                # 2. 解析大模型返回的纯文本结果，提取成结构化字典
                records = []
                # 按照 "评价1：" "评价2：" 分块
                blocks = re.split(r'评价\d+：?', result)
                for block in blocks:
                    if not block.strip(): 
                        continue
                    
                    # 提取内容和配图建议
                    content_match = re.search(r'内容：(.*?)(?=\n配图建议：|$)', block, re.DOTALL)
                    photo_match = re.search(r'配图建议：(.*?)(?=\n评价|$)', block, re.DOTALL)
                    
                    if content_match:
                        records.append({
                            "fields": {
                                "商品名称": product_name,
                                "评价内容": content_match.group(1).strip(),
                                "配图建议": photo_match.group(1).strip() if photo_match else ""
                            }
                        })
                
                # 3. 批量发送给飞书表格
                if records:
                    write_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/batch_create"
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8"
                    }
                    requests.post(write_url, headers=headers, json={"records": records})
    except Exception as e:
        # 即使写入飞书失败，也不影响大模型把结果返回给前端用户
        print(f"写入飞书失败: {str(e)}")
        
    return result
