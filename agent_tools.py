"""
Agent 工具集
- generate_reviews: 根据飞书商品标题和衣服事实生成买家评价
"""

import re
import time
import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableSerializable

from config import create_text_llm, settings

logger = logging.getLogger("agent-tools")


def _get_llm():
    return create_text_llm(temperature=0.7, max_retries=3)


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

只生成一次。不要输出质检过程，不要因为觉得不够好而重写。少量不顺的句子留给人工修改。

### 输出格式：
每条评价按以下格式输出，评价之间用空行分隔：

评价X：
内容：（评价正文）
配图建议：（从以下选一个：对镜自拍全身照、半身侧面显瘦照、衣服平铺细节图、面料特写图、拆快递开箱图、搭配穿搭图、户外街拍图、办公室随手拍）
"""),
        ("user", "商品标题：{product_name}\n衣服事实：{garment_line}\n\n请生成 {count} 条买家评价。颜色、版型和长短以衣服事实为准。标题里的营销词不要写进评价。")
    ])
    return prompt | _get_llm() | StrOutputParser()


def _write_reviews_to_feishu(product_name: str, result_text: str) -> None:
    """将生成的评价批量写入飞书结果表。"""
    try:
        from lark_cli import base_batch_create  # pylint: disable=import-outside-toplevel

        feishu_app_token = settings.feishu.app_token
        feishu_table_id = settings.feishu.table_id

        if not all([feishu_app_token, feishu_table_id]):
            return

        records = []
        blocks = re.split(r'评价\d+：?', result_text)
        for block in blocks:
            if not block.strip():
                continue
            content_match = re.search(r'内容：(.*?)(?=\n配图建议：|$)', block, re.DOTALL)
            photo_match = re.search(r'配图建议：(.*?)(?=\n评价|$)', block, re.DOTALL)
            if content_match:
                records.append({
                    "商品名称": product_name,
                    "评价内容": content_match.group(1).strip(),
                    "配图建议": photo_match.group(1).strip() if photo_match else "",
                })

        if records:
            base_batch_create(feishu_app_token, feishu_table_id, records)
    except Exception as e:
        logger.warning("写入飞书失败: %s", str(e))


def generate_reviews(
    product_name: str,
    count: int = 5,
    write_feishu: bool = True,
    garment_line: str = "",
) -> str:
    """根据飞书商品标题和衣服事实，生成指定数量的真实风格买家评价。"""
    chain = _build_reviews_chain()
    result = ""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = chain.invoke({
                "product_name": product_name,
                "garment_line": garment_line or "未看图，只根据商品标题，不要编造颜色和版型",
                "count": count,
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
