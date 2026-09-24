"""
买家秀配图生成模块
功能：
  1. 用 Kimi 根据商品名称生成多个生活场景的 Prompt
  2. 用 doubao-seedream-4-5 图生图 API 生成买家秀配图
  3. 将生成的图片上传到飞书，并写入多维表格
"""

import re
import json
import time
import base64
import logging
import os
import requests
import tempfile
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import settings, create_moonshot_llm
from feishu_reader import get_tenant_access_token as get_feishu_token
from buyer_show_review import (
    ImageReview,
    build_retry_prompt,
    pick_attempt,
    prompt_diff,
    retry_constraints,
    review_buyer_show,
)
from utils import guess_image_ext, save_generated_bytes

logger = logging.getLogger("image-generator")

# Kimi LLM（用于生成场景 Prompt）
_llm = create_moonshot_llm(temperature=0.8, max_retries=3)


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
                logger.info(f"  ⚠️ Kimi API 过载，{wait_time} 秒后自动重试（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(wait_time)
            else:
                logger.info(f"  ❌ Kimi 生成 Prompt 失败: {str(e)}")
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
def _image_bytes_to_data_uri(image_bytes: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"


def _post_doubao_payload(payload: dict) -> str | None:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.llm.doubao_api_key}"
    }

    try:
        # 自动重试（应对 429 过载错误）
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.post(settings.llm.doubao_api_url, headers=headers, json=payload, timeout=120)
            data = response.json()

            # 检查是否 429 过载
            if response.status_code == 429 or data.get("error", {}).get("type") == "engine_overloaded_error":
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                    logger.info(f"  ⚠️ doubao API 过载，{wait_time} 秒后自动重试（第 {attempt+1}/{max_retries} 次）...")
                    time.sleep(wait_time)
                    continue

            if "data" in data and len(data["data"]) > 0:
                return data["data"][0].get("url")

            logger.info(f"doubao 生图失败: {json.dumps(data, ensure_ascii=False)}")
            return None

        logger.info("doubao API 多次重试后仍失败")
        return None
    except Exception as e:
        logger.info(f"doubao API 调用异常: {str(e)}")
        return None


def generate_lifestyle_image(prompt: str, image_base64: str) -> str | None:
    """
    调用 doubao-seedream-4-5 API，传入白底图的 base64 数据和场景 Prompt，
    返回生成的生活场景图片 URL。
    """
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

    return _post_doubao_payload(payload)


def generate_lifestyle_image_with_references(
    prompt: str,
    product_image_base64: str,
    outfit_image_base64: str,
) -> tuple[str | None, str]:
    """
    使用商品平铺图 + 单张穿搭参考图进行多图参考生成。
    当前火山方舟接口的单图生图使用 image 字段，这里用同一字段传入两张图数组。
    如果接口不支持双图数组，直接失败，避免未知字段被忽略后变成纯提示词生图。
    """
    base_payload = {
        "model": "doubao-seedream-4-5-251128",
        "prompt": prompt,
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": False
    }

    reference_images = [product_image_base64, outfit_image_base64]
    field_name = "image"
    payload = {**base_payload, field_name: reference_images}
    logger.info(f"  使用多图字段 {field_name} 进行双图参考生成，输入图片数={len(reference_images)}...")
    url = _post_doubao_payload(payload)
    if url:
        logger.info(f"  ✅ 双图参考生成成功，使用字段: {field_name}")
        return url, field_name

    logger.info("  ⚠️ 双图参考生成失败，未回退到单商品图生图")
    return None, ""


# ========== 飞书图片上传 ==========
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
            if os.path.isfile(image_source):
                with open(image_source, "rb") as f:
                    image_bytes = f.read()
            else:
                img_resp = requests.get(image_source, timeout=30)
                if img_resp.status_code != 200:
                    logger.info(f"下载图片失败: HTTP {img_resp.status_code}")
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
                    "parent_node": settings.feishu.app_token,
                    "size": str(len(image_bytes))
                }
                resp = requests.post(upload_url, headers=headers, data=data, files=files, timeout=30)
                result = resp.json()

            if result.get("code") == 0:
                return result["data"]["file_token"]
            else:
                logger.info(f"飞书上传失败: {json.dumps(result, ensure_ascii=False)}")
                return None
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.info(f"上传飞书异常: {str(e)}")
        return None


def write_to_feishu_table(product_name: str, product_image_token: str | None,
                          ai_image_tokens: list[str], token: str) -> bool:
    """
    向飞书多维表格写入一行数据：
    - 商品名称：文本
    - 商品主图：用户上传的白底图（附件）
    - ai生成的买家秀图片：doubao 生成的多张场景图（附件列表）
    """
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.feishu.app_token}/tables/{settings.feishu.table_id}/records"
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
            logger.info("✅ 飞书表格写入成功")
            return True
        else:
            logger.info(f"❌ 飞书写入失败: {json.dumps(result, ensure_ascii=False)}")
            return False
    except Exception as e:
        logger.info(f"飞书写入异常: {str(e)}")
        return False


def commit_images_to_feishu(
    product_name: str,
    product_image_source,
    generated_image_sources: list,
) -> dict:
    """审核通过后，把商品图和候选买家秀图写回飞书结果表。"""
    result = {
        "status": "error",
        "message": "",
        "product_image_token": None,
        "ai_image_tokens": [],
    }

    try:
        feishu_token = get_feishu_token()
    except RuntimeError as e:
        result["message"] = f"❌ 获取飞书权限失败：{e}"
        return result

    logger.info("📤 审核通过，正在上传商品图到飞书...")
    product_image_token = upload_image_to_feishu(product_image_source, feishu_token, "product_main.png")
    if not product_image_token:
        result["message"] = "❌ 商品图上传飞书失败"
        return result

    ai_image_tokens = []
    for idx, image_source in enumerate(generated_image_sources, start=1):
        token = upload_image_to_feishu(image_source, feishu_token, f"lifestyle_{idx}.png")
        if token:
            ai_image_tokens.append(token)

    if not ai_image_tokens:
        result["message"] = "❌ 候选买家秀图上传飞书失败"
        return result

    success = write_to_feishu_table(product_name, product_image_token, ai_image_tokens, feishu_token)
    result["product_image_token"] = product_image_token
    result["ai_image_tokens"] = ai_image_tokens
    if success:
        result["status"] = "success"
        result["message"] = f"🎉 审核通过，已写入 {len(ai_image_tokens)} 张买家秀图到飞书"
    else:
        result["status"] = "partial"
        result["message"] = "⚠️ 图片已上传，但写入飞书表格失败"
    return result


# ========== 4. 主流程编排 ==========
def _build_outfit_reference_prompt(product_name: str, index: int, total: int) -> str:
    return (
        f"请基于两张参考图生成一张真实买家秀照片。商品名称：{product_name}。"
        f"第一张图是商品平铺图，必须严格保留商品的颜色、版型、图案、领型、袖型、长度和材质质感。"
        f"第二张图是穿搭参考图，只参考其中的人物姿势、拍摄角度、构图、光线、场景氛围和手机拍摄质感，"
        f"不要保留第二张图里原有衣服的款式、颜色、图案。"
        f"将第一张图中的商品自然穿到第二张图人物身上。不要改变商品颜色，不要改变款式，"
        f"不要增加不存在的图案。保持真实普通人买家秀质感，避免商业广告、棚拍、过度磨皮、AI 精修感。"
        f"人物为自然中国人特征，背景、构图和拍摄方式参考第二张图。"
        f"这是第 {index}/{total} 张图，请保持自然、真实、像手机随手拍。"
    )


def _product_only_prompt(scene_prompt: str) -> str:
    return (
        f"{scene_prompt}。"
        f"保持衣服颜色和参考图一致。"
        f"人物为中国人特征，面部自然，避免外国人或欧美模特感。"
    )


def _download_image(url: str) -> tuple[bytes | None, str]:
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200 or not resp.content:
            logger.info("下载成图失败: HTTP %s", resp.status_code)
            return None, "png"
        return resp.content, guess_image_ext(resp.headers.get("Content-Type", ""), url)
    except Exception as exc:
        logger.info("下载成图异常: %s", exc)
        return None, "png"


def _generate_once(
    prompt: str,
    product_image_base64: str,
    outfit_bytes: bytes | None,
) -> tuple[str | None, str]:
    if outfit_bytes:
        return generate_lifestyle_image_with_references(
            prompt,
            product_image_base64,
            _image_bytes_to_data_uri(outfit_bytes),
        )
    return generate_lifestyle_image(prompt, product_image_base64), ""


def _run_attempt(
    *,
    round_no: int,
    prompt: str,
    previous_prompt: str,
    added_constraints: list[str],
    image_bytes: bytes,
    product_image_base64: str,
    outfit_bytes: bytes | None,
) -> dict:
    """生成一张、落到本地、交给视觉模型评审。"""
    gen_url, reference_field = _generate_once(prompt, product_image_base64, outfit_bytes)
    attempt = {
        "round": round_no,
        "prompt": prompt,
        "added_constraints": added_constraints,
        "diff": prompt_diff(previous_prompt, prompt) if previous_prompt else [],
        "image_url": gen_url or "",
        "image_path": "",
        "reference_field": reference_field,
        "review": ImageReview(skipped=True, error="生图失败").to_dict(),
    }
    if not gen_url:
        return attempt
    content, ext = _download_image(gen_url)
    if content:
        attempt["image_path"] = save_generated_bytes(content, prefix=f"ai_r{round_no}", ext=ext)
    attempt["review"] = review_buyer_show(image_bytes, content).to_dict()
    return attempt


def _generate_with_review(
    *,
    index: int,
    first_prompt: str,
    image_bytes: bytes,
    product_image_base64: str,
    outfit_bytes: bytes | None,
    prior_attempt: dict | None,
    note: str,
) -> dict:
    """一张图最多生成两次：第2次 = 第1次原文 + 评审给出的约束。"""
    attempts: list[dict] = []
    previous_prompt = ""
    added: list[str] = []
    if prior_attempt and prior_attempt.get("prompt"):
        prior = {**prior_attempt, "from_previous_round": True}
        attempts.append(prior)
        previous_prompt = prior["prompt"]
        added = retry_constraints(ImageReview.from_dict(prior.get("review")), note)
        first_prompt = build_retry_prompt(previous_prompt, added)

    first = _run_attempt(
        round_no=len(attempts) + 1,
        prompt=first_prompt,
        previous_prompt=previous_prompt,
        added_constraints=added,
        image_bytes=image_bytes,
        product_image_base64=product_image_base64,
        outfit_bytes=outfit_bytes,
    )
    attempts.append(first)

    first_review = ImageReview.from_dict(first.get("review"))
    if first.get("image_url") and not first_review.passed and first_review.constraints:
        constraints = retry_constraints(first_review)
        logger.info("第 %d 张评审未通过，追加 %d 条约束重做", index + 1, len(constraints))
        attempts.append(_run_attempt(
            round_no=len(attempts) + 1,
            prompt=build_retry_prompt(first_prompt, constraints),
            previous_prompt=first_prompt,
            added_constraints=constraints,
            image_bytes=image_bytes,
            product_image_base64=product_image_base64,
            outfit_bytes=outfit_bytes,
        ))

    start = 1 if prior_attempt and prior_attempt.get("prompt") else 0
    picked = pick_attempt(attempts[start:])
    chosen = picked + start if picked >= 0 else -1
    return {"index": index, "chosen": chosen, "attempts": attempts}


def run_image_generation(
    image_bytes: bytes,
    product_name: str,
    scene_count: int = 2,
    outfit_image_bytes_list: list[bytes] | None = None,
    commit_to_feishu: bool = True,
    prior_attempts: list[dict] | None = None,
    note: str = "",
) -> dict:
    """
    完整的图片生成主流程：
    1. 如果存在穿搭参考图，则使用商品图 + 单张穿搭图双图参考生成
       否则保留旧的 Kimi 场景 Prompt + 单图生图逻辑
    2. doubao 逐张生成，视觉模型评审；不通过就在原提示词后追加约束再生成一次
    3. commit_to_feishu=True 时写入飞书；False 时只返回候选图 URL

    prior_attempts 是人工驳回时上一轮每张最终采用的尝试。
    这时不重写场景，直接在上一轮提示词后追加人工备注和上一轮评审约束。
    """
    outfit_image_bytes_list = outfit_image_bytes_list or []
    prior_attempts = prior_attempts or []
    result = {
        "status": "error",
        "message": "",
        "prompts": [],
        "image_urls": [],
        "local_image_paths": [],
        "attempts": [],
        "reference_mode": "product_plus_outfit" if outfit_image_bytes_list else "product_only",
        "outfit_reference_count": len(outfit_image_bytes_list),
        "reference_fields": [],
        "committed_to_feishu": False,
    }

    # 构造 base64 数据 URI（飞书链接需要认证，所以用 base64 代替）
    product_image_base64 = _image_bytes_to_data_uri(image_bytes)

    # Step 2: 生成提示词。存在穿搭参考图时使用固定强约束，减少模型自由发挥。
    if prior_attempts:
        prompts = [str(item.get("prompt") or "") for item in prior_attempts][:scene_count]
    elif outfit_image_bytes_list:
        logger.info(f"🎨 检测到 {len(outfit_image_bytes_list)} 张穿搭参考图，使用双图参考生成...")
        prompts = [
            _build_outfit_reference_prompt(product_name, i + 1, scene_count)
            for i in range(scene_count)
        ]
    else:
        logger.info(f"🎨 正在用 AI 生成 {scene_count} 个场景描述...")
        prompts = [_product_only_prompt(p) for p in generate_scene_prompts(product_name, scene_count)]

    if not prompts:
        result["message"] = "❌ 生成场景描述失败"
        return result

    # Step 3: 逐张生成并评审。是否写飞书由后续 commit_to_feishu 决定。
    generated_urls = []

    for i, prompt in enumerate(prompts):
        logger.info(f"🖼️  正在生成第 {i+1}/{len(prompts)} 张配图: {prompt[:30]}...")
        outfit_bytes = (
            outfit_image_bytes_list[i % len(outfit_image_bytes_list)]
            if outfit_image_bytes_list
            else None
        )
        group = _generate_with_review(
            index=i,
            first_prompt=prompt,
            image_bytes=image_bytes,
            product_image_base64=product_image_base64,
            outfit_bytes=outfit_bytes,
            prior_attempt=prior_attempts[i] if i < len(prior_attempts) else None,
            note=note,
        )
        if group["chosen"] < 0:
            logger.info(f"  ⚠️ 第 {i+1} 张生成失败，跳过")
            continue
        chosen = group["attempts"][group["chosen"]]
        result["attempts"].append(group)
        result["prompts"].append(chosen["prompt"])
        generated_urls.append(chosen["image_url"])
        result["local_image_paths"].append(chosen.get("image_path") or "")
        if chosen.get("reference_field"):
            result["reference_fields"].append(chosen["reference_field"])

    result["image_urls"] = generated_urls

    if not generated_urls:
        result["message"] = "❌ 所有场景图生成均失败，请检查 DOUBAO_API_KEY 是否正确"
        return result

    if not commit_to_feishu:
        result["status"] = "success"
        result["message"] = f"✅ 成功生成 {len(generated_urls)} 张待审核买家秀图，尚未写入飞书"
        return result

    commit_result = commit_images_to_feishu(product_name, image_bytes, generated_urls)

    if commit_result.get("status") == "success":
        result["status"] = "success"
        result["committed_to_feishu"] = True
        result["message"] = f"🎉 成功生成 {len(generated_urls)} 张买家秀配图，已写入飞书表格！"
    else:
        result["status"] = commit_result.get("status", "partial")
        result["message"] = commit_result.get("message") or f"⚠️ 生成了 {len(generated_urls)} 张配图，但写入飞书失败"

    return result

