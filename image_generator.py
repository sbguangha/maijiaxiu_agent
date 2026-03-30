"""
买家秀配图生成模块
功能：
  1. 用 Kimi 根据商品名称生成多个生活场景的 Prompt
  2. 用 doubao-seedream-4-5 图生图 API 生成买家秀配图
  3. 将生成的图片上传到飞书，并写入多维表格
"""

import os
import re
import json
import time
import base64
import requests
import tempfile
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

# ========== 配置 ==========
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
DOUBAO_API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID")

# Kimi LLM（用于生成场景 Prompt）
_llm = ChatOpenAI(
    api_key=os.getenv("MOONSHOT_API_KEY"),
    base_url="https://api.moonshot.cn/v1",
    model="moonshot-v1-8k",
    temperature=0.8,
    max_retries=3,
)


# ========== 1. 生成场景 Prompt ==========
def generate_scene_prompts(product_name: str, scene_count: int = 2) -> list[str]:
    """
    调用 Kimi，根据商品名称生成 N 个适合 doubao-seedream 的图生图 Prompt。
    每个 Prompt 描述一个不同的买家秀生活场景。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的电商买家秀图片创意总监。
你的任务是根据商品名称，为 AI 图生图模型生成不同生活场景的 Prompt。

生成规则：
1. 每个 Prompt 描述一个独特的生活场景，场景之间不能重复
2. Prompt 必须是中文，描述要具体、有画面感
3. 必须强调"真实感、自然光、手机拍摄质感"，避免过于完美的商业广告感
4. 场景要贴近普通人的日常生活（如街拍、咖啡馆、公园、居家、通勤等）
5. 每个 Prompt 以"一个穿着该商品的"开头，确保商品是画面主角
6. Prompt 长度控制在 30-80 个字之间
7. 只输出 Prompt 列表，每行一个，用数字序号开头（如 1. 2. 3.）
8. 人物默认设定为中国年轻人或普通中国消费者，面部特征自然，避免欧美模特感
9. 不要输出任何其他解释文字"""),
        ("user", """商品名称：{product_name}

请生成 {scene_count} 个不同生活场景的图生图 Prompt，并且每条都必须包含以下硬性约束（可自然融入句子）：
- 保持衣服颜色和参考图一致
- 人物使用中国人特征，长相自然，避免外国人或欧美模特特征""")
    ])

    chain = prompt | _llm | StrOutputParser()

    # 自动重试（应对 429 过载错误）
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = chain.invoke({
                "product_name": product_name,
                "scene_count": scene_count,
            })
            break  # 成功就跳出循环
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # 5s, 10s, 15s
                print(f"  ⚠️ Kimi API 过载，{wait_time} 秒后自动重试（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(wait_time)
            else:
                print(f"  ❌ Kimi 生成 Prompt 失败: {str(e)}")
                return []

    # 解析结果：按行分割，去掉序号
    lines = result.strip().split("\n")
    prompts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 去掉开头的数字序号（如 "1. " "2、"）
        cleaned = re.sub(r'^\d+[.、\)\]]\s*', '', line)
        if cleaned:
            prompts.append(cleaned)

    return prompts[:scene_count]


# ========== 2. 调用 doubao 图生图 ==========
def generate_lifestyle_image(prompt: str, image_base64: str) -> str | None:
    """
    调用 doubao-seedream-4-5 API，传入白底图的 base64 数据和场景 Prompt，
    返回生成的生活场景图片 URL。
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DOUBAO_API_KEY}"
    }

    payload = {
        "model": "doubao-seedream-4-5-251128",
        "prompt": prompt,
        "image": image_base64,
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": False
    }

    try:
        # 自动重试（应对 429 过载错误）
        max_retries = 3
        response = None
        for attempt in range(max_retries):
            response = requests.post(DOUBAO_API_URL, headers=headers, json=payload, timeout=120)
            data = response.json()

            # 检查是否 429 过载
            if response.status_code == 429 or data.get("error", {}).get("type") == "engine_overloaded_error":
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                    print(f"  ⚠️ doubao API 过载，{wait_time} 秒后自动重试（第 {attempt+1}/{max_retries} 次）...")
                    time.sleep(wait_time)
                    continue

            if "data" in data and len(data["data"]) > 0:
                return data["data"][0].get("url")
            else:
                print(f"doubao 生图失败: {json.dumps(data, ensure_ascii=False)}")
                return None

        print(f"doubao API 多次重试后仍失败")
        return None
    except Exception as e:
        print(f"doubao API 调用异常: {str(e)}")
        return None


# ========== 3. 飞书相关函数 ==========
def get_feishu_token() -> str | None:
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }).json()

    if resp.get("code") == 0:
        return resp.get("tenant_access_token")
    print(f"获取飞书 Token 失败: {resp}")
    return None


def upload_image_to_feishu(image_source, token: str, filename: str = "image.png") -> str | None:
    """
    将图片上传到飞书，返回 file_token。
    image_source 可以是：
      - str (URL): 先下载，再上传
      - bytes: 直接上传
    """
    try:
        # 如果是 URL，先下载到内存
        if isinstance(image_source, str):
            img_resp = requests.get(image_source, timeout=30)
            if img_resp.status_code != 200:
                print(f"下载图片失败: HTTP {img_resp.status_code}")
                return None
            image_bytes = img_resp.content
        else:
            image_bytes = image_source

        # 上传到飞书（使用素材上传 API）
        upload_url = f"https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
        headers = {"Authorization": f"Bearer {token}"}

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as f:
                files = {"file": (filename, f, "image/png")}
                data = {
                    "file_name": filename,
                    "parent_type": "bitable_image",
                    "parent_node": FEISHU_APP_TOKEN,
                    "size": str(len(image_bytes))
                }
                resp = requests.post(upload_url, headers=headers, data=data, files=files, timeout=30)
                result = resp.json()

            if result.get("code") == 0:
                return result["data"]["file_token"]
            else:
                print(f"飞书上传失败: {json.dumps(result, ensure_ascii=False)}")
                return None
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        print(f"上传飞书异常: {str(e)}")
        return None


def write_to_feishu_table(product_name: str, product_image_token: str | None,
                          ai_image_tokens: list[str], token: str) -> bool:
    """
    向飞书多维表格写入一行数据：
    - 商品名称：文本
    - 商品主图：用户上传的白底图（附件）
    - ai生成的买家秀图片：doubao 生成的多张场景图（附件列表）
    """
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    fields = {
        "商品名称": product_name,
    }

    # 商品主图（附件字段格式：file_token 列表）
    if product_image_token:
        fields["商品主图"] = [{"file_token": product_image_token}]

    # AI 生成的买家秀图片（多张附件）
    if ai_image_tokens:
        fields["ai生成的买家秀图片"] = [{"file_token": ft} for ft in ai_image_tokens]

    payload = {"fields": fields}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print("✅ 飞书表格写入成功")
            return True
        else:
            print(f"❌ 飞书写入失败: {json.dumps(result, ensure_ascii=False)}")
            return False
    except Exception as e:
        print(f"飞书写入异常: {str(e)}")
        return False


# ========== 4. 主流程编排 ==========
def run_image_generation(image_bytes: bytes, product_name: str, scene_count: int = 2) -> dict:
    """
    完整的图片生成主流程：
    1. 上传白底图到飞书 → 拿到 file_token 和公网 URL
    2. Kimi 生成场景 Prompt
    3. doubao 逐个生成场景图
    4. 每张场景图上传飞书
    5. 写入飞书表格
    """
    result = {
        "status": "error",
        "message": "",
        "prompts": [],
        "image_urls": [],
    }

    # Step 0: 获取飞书 Token
    feishu_token = get_feishu_token()
    if not feishu_token:
        result["message"] = "❌ 获取飞书权限失败，请检查 App ID 和 Secret 配置"
        return result

    # Step 1: 上传用户的白底商品图到飞书
    print("📤 正在上传商品白底图到飞书...")
    product_image_token = upload_image_to_feishu(image_bytes, feishu_token, "product_main.png")
    if not product_image_token:
        result["message"] = "❌ 商品图上传飞书失败"
        return result

    # 构造 base64 数据 URI（doubao 需要公网可访问的图片，飞书链接需要认证，所以用 base64 代替）
    product_image_base64 = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('utf-8')}"

    # Step 2: Kimi 生成场景 Prompt
    print(f"🎨 正在用 AI 生成 {scene_count} 个场景描述...")
    prompts = generate_scene_prompts(product_name, scene_count)
    result["prompts"] = prompts

    if not prompts:
        result["message"] = "❌ 生成场景描述失败"
        return result

    # Step 3 & 4: 用 doubao 生成场景图并上传飞书
    ai_image_tokens = []
    generated_urls = []

    for i, prompt in enumerate(prompts):
        print(f"🖼️  正在生成第 {i+1}/{len(prompts)} 张配图: {prompt[:30]}...")

        # 调用 doubao 图生图（传 base64）
        strict_prompt = (
            f"{prompt}。"
            f"保持衣服颜色和参考图一致。"
            f"人物为中国人特征，面部自然，避免外国人或欧美模特感。"
        )
        gen_url = generate_lifestyle_image(strict_prompt, product_image_base64)
        if gen_url:
            generated_urls.append(gen_url)

            # 上传生成的图片到飞书
            ai_token = upload_image_to_feishu(gen_url, feishu_token, f"lifestyle_{i+1}.png")
            if ai_token:
                ai_image_tokens.append(ai_token)
        else:
            print(f"  ⚠️ 第 {i+1} 张生成失败，跳过")

    result["image_urls"] = generated_urls

    if not generated_urls:
        result["message"] = "❌ 所有场景图生成均失败，请检查 DOUBAO_API_KEY 是否正确"
        return result

    # Step 5: 写入飞书表格
    print("📝 正在写入飞书表格...")
    success = write_to_feishu_table(product_name, product_image_token, ai_image_tokens, feishu_token)

    if success:
        result["status"] = "success"
        result["message"] = f"🎉 成功生成 {len(generated_urls)} 张买家秀配图，已写入飞书表格！"
    else:
        result["status"] = "partial"
        result["message"] = f"⚠️ 生成了 {len(generated_urls)} 张配图，但写入飞书失败"

    return result

