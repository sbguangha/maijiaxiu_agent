"""
买家秀配图生成模块
功能：
  1. 用 qwen3.8-omni-flash 看平铺图，抽出衣服事实
  2. 用 qwen3.7-plus 按槽位写场景句
  3. 用 doubao-seedream-4-5 逐张生图，硬伤最多重做一次
  4. 将生成的图片上传到飞书，并写入多维表格
"""

import json
import time
import base64
import logging
import os
import random
from pathlib import Path

import requests

from config import settings
from feishu_reader import get_tenant_access_token as get_feishu_token
from lark_cli import BASE_DIR, base_batch_create, base_upload_attachment, write_tmp_bytes
from qwen_client import dashscope_configured, qwen_chat
from buyer_show_prompt import (
    GARMENT_SYSTEM,
    JUDGE_SYSTEM,
    ImageChecklist,
    SlotMemory,
    build_borrow_pose_prompt,
    build_product_only_prompt,
    build_reference_scene_prompt,
    build_scene_messages,
    checklist_from_response,
    contains_banned_scene_word,
    failure_hint,
    garment_from_title,
    normalize_garment,
    parse_json_object,
    prefer_candidate,
    sample_slot,
    scene_sentence_from_response,
)

logger = logging.getLogger("image-generator")

PORTRAIT_SIZE = "1728x2304"
SQUARE_SIZE = "2K"


# ========== 2. 调用 doubao 图生图 ==========
def _image_bytes_to_data_uri(image_bytes: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"


def _is_size_error(data: dict) -> bool:
    blob = json.dumps(data, ensure_ascii=False).lower()
    return "size" in blob or "dimension" in blob or "resolution" in blob or "像素" in blob


def _post_doubao_payload(payload: dict, *, allow_size_fallback: bool = True) -> str | None:
    if not settings.llm.doubao_api_key:
        raise RuntimeError("未配置 DOUBAO_API_KEY，无法调用豆包生图")
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

            if (
                allow_size_fallback
                and payload.get("size") not in {None, SQUARE_SIZE}
                and response.status_code == 400
                and _is_size_error(data)
            ):
                logger.info("竖图尺寸被拒绝，改用 %s", SQUARE_SIZE)
                return _post_doubao_payload(
                    {**payload, "size": SQUARE_SIZE},
                    allow_size_fallback=False,
                )

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
        "size": PORTRAIT_SIZE,
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
        "size": PORTRAIT_SIZE,
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
def _cli_relpath(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        candidate = BASE_DIR / path
    if not candidate.is_file():
        return None
    try:
        return candidate.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return None


def upload_image_to_feishu(image_source, token: str, filename: str = "image.png") -> str | None:
    """
    将图片落到本地临时文件，返回可供 lark-cli 上传的相对路径。
    image_source 可以是 URL、本地路径或 bytes。
    """
    del token
    try:
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
        return write_tmp_bytes(image_bytes, filename)
    except Exception as e:
        logger.info(f"准备飞书上传文件失败: {str(e)}")
        return None


def write_to_feishu_table(product_name: str, product_image_token: str | None,
                          ai_image_tokens: list[str], token: str,
                          review_text: str = "") -> bool:
    """
    向飞书结果表写入一行：先创建文本记录，再把本地图片追加到附件字段。
    """
    del token
    app_token = settings.feishu.app_token
    table_id = settings.feishu.table_id
    if not app_token or not table_id:
        logger.info("❌ 飞书结果表未配置")
        return False

    try:
        fields = {"商品名称": product_name}
        if review_text.strip():
            fields["评价内容"] = review_text.strip()
        record_ids = base_batch_create(app_token, table_id, [fields])
        if not record_ids:
            logger.info("❌ 飞书表格写入失败: 未返回 record_id")
            return False
        record_id = record_ids[0]
        product_files = [_cli_relpath(product_image_token)]
        product_files = [p for p in product_files if p]
        if product_files:
            base_upload_attachment(app_token, table_id, record_id, "商品主图", product_files)
        ai_files = [_cli_relpath(path) for path in ai_image_tokens]
        ai_files = [p for p in ai_files if p]
        if ai_files:
            base_upload_attachment(app_token, table_id, record_id, "ai生成的买家秀图片", ai_files)
        logger.info("✅ 飞书表格写入成功")
        return True
    except Exception as e:
        logger.info(f"飞书写入异常: {str(e)}")
        return False


def commit_images_to_feishu(
    product_name: str,
    product_image_source,
    generated_image_sources: list,
    review_text: str = "",
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

    success = write_to_feishu_table(
        product_name, product_image_token, ai_image_tokens, feishu_token, review_text=review_text,
    )
    result["product_image_token"] = product_image_token
    result["ai_image_tokens"] = ai_image_tokens
    if success:
        result["status"] = "success"
        result["message"] = f"🎉 审核通过，已写入 {len(ai_image_tokens)} 张买家秀图到飞书"
    else:
        result["status"] = "partial"
        result["message"] = "⚠️ 图片已上传，但写入飞书表格失败"
    return result


# ========== 4. 看图、场景句、逐张生成 ==========
def _guess_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "image/webp"
    return "image/jpeg"


def _image_content(image_bytes: bytes) -> dict:
    mime = _guess_mime(image_bytes)
    data_uri = _image_bytes_to_data_uri(image_bytes, mime)
    return {"type": "image_url", "image_url": {"url": data_uri}}


def extract_garment_facts(image_bytes: bytes, product_name: str) -> dict:
    """看平铺图抽出衣服事实。没有百炼 Key 或调用失败时，退回洗过的标题。"""
    fallback = garment_from_title(product_name)
    if not dashscope_configured():
        logger.warning("未配置 DASHSCOPE_API_KEY，衣服事实退回标题")
        return fallback
    try:
        text = qwen_chat(
            [
                {"role": "system", "content": GARMENT_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        _image_content(image_bytes),
                        {"type": "text", "text": f"商品标题：{product_name}"},
                    ],
                },
            ],
            model=settings.llm.vision_model,
            temperature=0.2,
        )
        return normalize_garment(parse_json_object(text), product_name)
    except Exception as exc:
        logger.warning("看图抽取衣服事实失败，退回标题: %s", exc)
        return fallback


def write_scene_sentence(
    garment: dict,
    slot,
    *,
    reject_note: str = "",
    failure_hint_text: str = "",
    rng: random.Random | None = None,
) -> str:
    """用 qwen3.7-plus 把已经抽中的槽位写成一条场景句。"""
    messages = build_scene_messages(
        garment,
        slot,
        reject_note=reject_note,
        failure_hint=failure_hint_text,
        rng=rng,
    )
    if not dashscope_configured():
        raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法写场景句")
    text = qwen_chat(messages, model=settings.llm.text_model, temperature=0.8)
    sentence = scene_sentence_from_response(text, garment.get("one_line") or "")
    banned = contains_banned_scene_word(sentence)
    if banned:
        logger.warning("场景句含禁用词「%s」，仍使用该句", banned)
    return sentence


def generate_scene_prompts(product_name: str, scene_count: int = 2) -> list[str]:
    """兼容旧调用：没有平铺图时，只按标题槽位写多条场景句。"""
    garment = garment_from_title(product_name)
    memory = SlotMemory()
    rng = random.Random()
    prompts: list[str] = []
    for _ in range(scene_count):
        slot = sample_slot(garment, memory, rng)
        try:
            sentence = write_scene_sentence(garment, slot, rng=rng)
        except Exception as exc:
            logger.info("场景句生成失败: %s", exc)
            return prompts
        prompts.append(build_product_only_prompt(sentence, garment["one_line"]))
    return prompts


def _download_image(url: str) -> bytes | None:
    try:
        response = requests.get(url, timeout=30)
        if response.status_code != 200 or not response.content:
            logger.info("下载成图失败: HTTP %s", response.status_code)
            return None
        return response.content
    except Exception as exc:
        logger.info("下载成图异常: %s", exc)
        return None


def judge_buyer_show(product_bytes: bytes, generated_url: str) -> ImageChecklist:
    """对照平铺图检查成图。只认衣服错误和四项硬伤，其余放行。"""
    if not dashscope_configured():
        logger.warning("未配置 DASHSCOPE_API_KEY，跳过成图硬伤检查")
        return ImageChecklist(skipped=True)
    generated = _download_image(generated_url)
    if not generated:
        logger.warning("成图下载失败，跳过硬伤检查")
        return ImageChecklist(skipped=True)
    try:
        text = qwen_chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        _image_content(product_bytes),
                        _image_content(generated),
                        {"type": "text", "text": "图1是商品平铺图，图2是生成的买家秀。只输出 JSON。"},
                    ],
                },
            ],
            model=settings.llm.vision_model,
            temperature=0.1,
        )
        return checklist_from_response(text)
    except Exception as exc:
        logger.warning("成图硬伤检查失败，本张放行: %s", exc)
        return ImageChecklist(skipped=True)


def _render_prompt(
    *,
    mode: str,
    garment: dict,
    slot,
    reject_note: str,
    failure_hint_text: str,
    rng: random.Random,
) -> str:
    one_line = garment.get("one_line") or "服装"
    if mode == "use_ref_scene":
        text = build_reference_scene_prompt(one_line, reject_note)
        if failure_hint_text.strip():
            text += f"上一张的问题，这次必须避开：{failure_hint_text.strip()}。"
        return text
    sentence = write_scene_sentence(
        garment,
        slot,
        reject_note=reject_note,
        failure_hint_text=failure_hint_text,
        rng=rng,
    )
    if mode == "borrow_pose":
        return build_borrow_pose_prompt(sentence, one_line)
    return build_product_only_prompt(sentence, one_line)


def _generate_one(
    *,
    mode: str,
    prompt: str,
    product_image_base64: str,
    outfit_bytes: bytes | None,
) -> tuple[str | None, str]:
    if mode == "product_only" or not outfit_bytes:
        return generate_lifestyle_image(prompt, product_image_base64), ""
    outfit_image_base64 = _image_bytes_to_data_uri(outfit_bytes)
    return generate_lifestyle_image_with_references(
        prompt,
        product_image_base64,
        outfit_image_base64,
    )


def run_image_generation(
    image_bytes: bytes,
    product_name: str,
    scene_count: int = 2,
    outfit_image_bytes_list: list[bytes] | None = None,
    commit_to_feishu: bool = True,
    reject_note: str = "",
    only_indexes: list[int] | None = None,
    review_text: str = "",
) -> dict:
    """
    按晒图数量逐张生成。

    有穿搭参考图且张数不超过参考图时，沿用参考图的场景，不另写场景句。
    张数更多时，多出来的只借姿势并新写场景。
    没有参考图时，每张单独抽槽位、单独写场景句。
    衣服硬伤或四项画面硬伤才重做这一张，最多一次；新图没有更好就留旧图。
    """
    outfit_image_bytes_list = outfit_image_bytes_list or []
    result = {
        "status": "error",
        "message": "",
        "prompts": [],
        "image_urls": [],
        "reference_mode": "product_plus_outfit" if outfit_image_bytes_list else "product_only",
        "outfit_reference_count": len(outfit_image_bytes_list),
        "reference_fields": [],
        "committed_to_feishu": False,
        "checks": [],
        "image_indexes": [],
    }

    if only_indexes is None:
        indexes = list(range(scene_count))
    else:
        indexes = []
        for raw in only_indexes:
            idx = int(raw)
            if idx < 0 or idx >= scene_count:
                raise ValueError(f"图片序号超出范围: {idx}")
            if idx not in indexes:
                indexes.append(idx)
        if not indexes:
            result["message"] = "没有要重做的图片"
            return result
    result["image_indexes"] = indexes

    garment = extract_garment_facts(image_bytes, product_name)
    product_image_base64 = _image_bytes_to_data_uri(image_bytes)
    memory = SlotMemory()
    rng = random.Random()
    generated_urls: list[str] = []

    for index in indexes:
        if outfit_image_bytes_list and index < len(outfit_image_bytes_list):
            mode = "use_ref_scene"
            outfit_bytes = outfit_image_bytes_list[index]
            slot = None
        elif outfit_image_bytes_list:
            mode = "borrow_pose"
            outfit_bytes = outfit_image_bytes_list[index % len(outfit_image_bytes_list)]
            slot = sample_slot(garment, memory, rng)
        else:
            mode = "product_only"
            outfit_bytes = None
            slot = sample_slot(garment, memory, rng)

        try:
            prompt = _render_prompt(
                mode=mode,
                garment=garment,
                slot=slot,
                reject_note=reject_note,
                failure_hint_text="",
                rng=rng,
            )
        except Exception as exc:
            logger.info("第 %d 张场景句失败: %s", index + 1, exc)
            continue

        logger.info("正在生成第 %d/%d 张，模式=%s", index + 1, scene_count, mode)
        gen_url, reference_field = _generate_one(
            mode=mode,
            prompt=prompt,
            product_image_base64=product_image_base64,
            outfit_bytes=outfit_bytes,
        )
        if not gen_url:
            logger.info("第 %d 张生成失败，跳过", index + 1)
            continue

        checklist = judge_buyer_show(image_bytes, gen_url)
        chosen_url = gen_url
        chosen_prompt = prompt
        chosen_field = reference_field
        if checklist.needs_retry:
            hint = failure_hint(checklist)
            logger.info("第 %d 张硬伤 %s，重做一次", index + 1, hint)
            retry_slot = sample_slot(garment, memory, rng) if mode != "use_ref_scene" else None
            try:
                retry_prompt = _render_prompt(
                    mode=mode,
                    garment=garment,
                    slot=retry_slot or slot,
                    reject_note=reject_note,
                    failure_hint_text=hint,
                    rng=rng,
                )
            except Exception as exc:
                logger.info("第 %d 张重做场景句失败，留原图: %s", index + 1, exc)
                retry_prompt = ""
            retry_url = None
            retry_field = ""
            if retry_prompt:
                retry_url, retry_field = _generate_one(
                    mode=mode,
                    prompt=retry_prompt,
                    product_image_base64=product_image_base64,
                    outfit_bytes=outfit_bytes,
                )
            if retry_url:
                retry_check = judge_buyer_show(image_bytes, retry_url)
                if prefer_candidate(checklist, retry_check):
                    chosen_url = retry_url
                    chosen_prompt = retry_prompt
                    chosen_field = retry_field
                    checklist = retry_check
                else:
                    logger.info("第 %d 张重做没有更好，留原图", index + 1)

        generated_urls.append(chosen_url)
        result["prompts"].append(chosen_prompt)
        if chosen_field:
            result["reference_fields"].append(chosen_field)
        result["checks"].append({
            "garment_issues": checklist.garment_issues,
            "hard_defects": checklist.hard_defects,
            "skipped": checklist.skipped,
        })

    result["image_urls"] = generated_urls
    short = len(generated_urls) < len(indexes)

    if not generated_urls:
        result["message"] = "❌ 所有场景图生成均失败，请检查 DOUBAO_API_KEY 是否正确"
        return result

    if not commit_to_feishu:
        result["status"] = "partial" if short else "success"
        result["message"] = (
            f"生成了 {len(generated_urls)}/{len(indexes)} 张，缺的张留给人工"
            if short
            else f"✅ 成功生成 {len(generated_urls)} 张待审核买家秀图，尚未写入飞书"
        )
        return result

    commit_result = commit_images_to_feishu(
        product_name, image_bytes, generated_urls, review_text=review_text,
    )

    if commit_result.get("status") == "success":
        result["status"] = "partial" if short else "success"
        result["committed_to_feishu"] = True
        result["message"] = f"🎉 成功生成 {len(generated_urls)} 张买家秀配图，已写入飞书表格！"
    else:
        result["status"] = commit_result.get("status", "partial")
        result["message"] = commit_result.get("message") or f"⚠️ 生成了 {len(generated_urls)} 张配图，但写入飞书失败"

    return result

