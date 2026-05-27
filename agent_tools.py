"""
Agent 工具集
- parse_product_url: 爬取电商商品页面（@tool 装饰，可由 Agent 决策调用）
- extract_selling_points: 从商品信息提取卖点（纯函数，由图节点调用）
- generate_reviews: 生成买家评价（纯函数，由图节点调用）
"""

import re
import time
import logging
import requests
import json
from bs4 import BeautifulSoup
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableSerializable

from config import create_moonshot_llm, settings

logger = logging.getLogger("agent-tools")

_llm = create_moonshot_llm(temperature=0.7, max_retries=3)


# ========== 链接解析器 ==========

def parse_product_url(url: str) -> str:
    """根据电商商品链接（淘宝、天猫、京东等），尝试抓取并返回商品的标题信息。
    
    如果链接无法访问或被反爬虫拦截，会返回失败信息。
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
        
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        
        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = meta_tag["content"].strip()
        
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


# ========== 卖点提取（纯函数） ==========

def _build_selling_points_chain() -> RunnableSerializable:
    """构建卖点提取 chain。"""
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
    return prompt | _llm | StrOutputParser()


def extract_selling_points(product_info: str) -> str:
    """根据商品标题和描述信息，提取 3-5 个核心卖点（顿号分隔）。"""
    chain = _build_selling_points_chain()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = chain.invoke({"product_info": product_info})
            return result.strip()
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.warning("提取卖点 API 过载，%d 秒后重试...", wait_time)
                time.sleep(wait_time)
            else:
                if attempt == max_retries - 1:
                    return f"提取卖点失败（API过载）：{str(e)}"
                raise


# ========== 评价生成（纯函数） ==========

def _build_reviews_chain() -> RunnableSerializable:
    """构建评价生成 chain。"""
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
    return prompt | _llm | StrOutputParser()


def _write_reviews_to_feishu(product_name: str, result_text: str) -> None:
    """将生成的评价批量写入飞书结果表。"""
    try:
        from feishu_reader import get_tenant_access_token  # pylint: disable=import-outside-toplevel

        feishu_app_token = settings.feishu.app_token
        feishu_table_id = settings.feishu.table_id

        if not all([feishu_app_token, feishu_table_id]):
            return

        token = get_tenant_access_token()

        records = []
        blocks = re.split(r'评价\d+：?', result_text)
        for block in blocks:
            if not block.strip():
                continue
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

        if records:
            write_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{feishu_app_token}/tables/{feishu_table_id}/records/batch_create"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8"
            }
            requests.post(write_url, headers=headers, json={"records": records})
    except Exception as e:
        logger.warning("写入飞书失败: %s", str(e))


def generate_reviews(product_name: str, selling_points: str, count: int = 5, write_feishu: bool = True) -> str:
    """根据商品名称和卖点，生成指定数量的真实风格买家评价。"""
    chain = _build_reviews_chain()
    result = ""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = chain.invoke({
                "product_name": product_name,
                "selling_points": selling_points,
                "count": count
            })
            break
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.warning("生成评价 API 过载，%d 秒后重试...", wait_time)
                time.sleep(wait_time)
            else:
                if attempt == max_retries - 1:
                    return "生成评价失败（API过载），请稍后重试。"
                raise

    if write_feishu:
        _write_reviews_to_feishu(product_name, result)
    return result
