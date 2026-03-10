import os
from typing import List, Optional
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# 加载环境变量 (包括 MOONSHOT_API_KEY)
load_dotenv()

# --- 1. 定义结构化输出模型 ---
class BuyerReview(BaseModel):
    review_text: str = Field(description="买家秀评价的正文内容。")
    photo_suggest: str = Field(description="为这条评价建议的配图类型，必须非空。从以下选项中选择一个或组合：对镜自拍全身照、半身侧面显瘦照、衣服平铺细节图、面料特写图、拆快递开箱图、搭配其他单品的穿搭图、户外街拍图、办公室/教室随手拍。")

class ReviewGenerationResponse(BaseModel):
    reviews: List[BuyerReview] = Field(description="生成的买家秀评价列表。")

# --- 2. 核心 Agent 类 ---
class ClothingReviewAgent:
    def __init__(self):
        api_key = os.getenv("MOONSHOT_API_KEY")
        if not api_key:
            raise ValueError("环境变量中未找到 MOONSHOT_API_KEY，请在 .env 文件中配置。")

        # 使用支持函数调用/结构化输出的模型，Kimi 支持该特性
        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url="https://api.moonshot.cn/v1",
            model="moonshot-v1-8k",
            temperature=0.8, # 稍微调高温度，增加多样性和随机感
        )
        
        from langchain_core.output_parsers import PydanticOutputParser
        
        # 使用 PydanticOutputParser 兼容性更好
        self.parser = PydanticOutputParser(pydantic_object=ReviewGenerationResponse)

        # 构建系统提示词 (System Prompt - 核心反 AI 味护城河)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个专业的淘宝/天猫【服装服饰类】“真实买家秀”生成引擎。
你的任务是根据提供的商品信息，生成高度逼真、毫无 AI 痕迹的买家评价。

### 🚨 绝对不可跨越的红线（反 AI 味规则）：
1. **禁用华丽辞藻**：绝对禁止使用“犹如、宛若、极致的、前所未有、令人惊叹、简直是”等书面语词汇。
2. **禁用排比句和过度吹捧**：普通买家不会像写诗一样评价衣服。
3. **字数错落有致**：不要每条都很长！有的买家很懒只有10个字以内（如“版型不错，挺显瘦的”），有的买家絮絮叨叨会有50字左右的分享。
4. **允许不完美**：真实的评价往往夹杂着极小的不满，例如“物流有点慢，但衣服好看就原谅了”、“线头需要自己剪一下，不过这个价位要什么自行车”。这是增加真实感的绝杀技。

### 👗 服装类专属语料库与人设提示：
- **常用黑话**：绝绝子、闭眼入、踩雷、版型绝了、显瘦、梨形身材天菜、质感在线、起球、起静电、不扎人、百搭、上身图。
- **推荐人设视角**：
  A. 【微胖/梨形身材】：非常看重显瘦、遮肉、版型、长度是否合适。
  B. 【面料质感党】：看重是否纯棉/真丝/羊毛，会不会起球、扎人、缩水。
  C. 【敷衍凑单型】：语气随意，不带标点符号，或者只发默认好评变体（如：挺好的，习惯好评）。
  D. 【疯狂安利型】：带点激动，使用了好多emoji，可能会说“被室友要链接了”。
  E. 【犹豫退货型】：一开始想退，后来试穿觉得还行留下了。

必须严格遵守以下 JSON 格式输出：
{format_instructions}
"""),
            ("user", "商品名称：{product_name}\n商品卖点：{selling_points}\n\n请生成 {count} 条符合上述要求且互不重复的真实买家评价。")
        ])

        # LCEL Chain
        self.chain = self.prompt | self.llm | self.parser

    def generate_reviews(self, product_name: str, selling_points: List[str], count: int = 5) -> ReviewGenerationResponse:
        """
        生成服装买家秀评价
        """
        selling_points_str = "、".join(selling_points)
        print(f"Agent 开始生成... | 商品: {product_name} | 生成数量: {count}")
        
        response = self.chain.invoke({
            "product_name": product_name,
            "selling_points": selling_points_str,
            "count": count,
            "format_instructions": self.parser.get_format_instructions()
        })
        return response
