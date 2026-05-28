"""
飞书需求表格读取模块

从《评价晒图agent_需求表格》读取商品信息和附件图片，
供 /batch-generate 端点消费。
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import List, Optional

import requests
from config import settings

logger = logging.getLogger("feishu-reader")

_BASE = "https://open.feishu.cn/open-apis"


@dataclass
class SourceRow:
    """需求表格中的一行"""
    record_id: str
    product_title: str
    image_file_tokens: list[str]
    outfit_image_file_tokens: list[str]
    review_count: int
    image_count: int
    wechat_contacts: list[str]
    processing_status: str
    should_process: bool


def get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token，失败时抛异常。"""
    app_id = settings.feishu.app_id
    app_secret = settings.feishu.app_secret
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
    resp = requests.post(
        f"{_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败: {resp}")
    return resp["tenant_access_token"]


def list_source_records(
    token: str,
    app_token: str,
    table_id: str,
    page_size: int = 100,
) -> list[dict]:
    """
    分页读取需求表所有记录，返回原始 record 列表。
    """
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    all_records: list[dict] = []
    page_token: Optional[str] = None

    while True:
        params: dict = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=30).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"读取飞书表格失败: {resp}")

        data = resp.get("data", {})
        items = data.get("items", [])
        all_records.extend(items)
        logger.info("已读取 %d 条记录（本页 %d 条）", len(all_records), len(items))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    return all_records


def parse_source_row(record: dict) -> SourceRow | None:
    """
    将飞书原始 record 解析为 SourceRow。
    字段名必须与表格列名完全一致。
    """
    fields = record.get("fields", {})
    record_id = record.get("record_id", "")

    product_title = ""
    raw_title = fields.get("商品标题")
    if isinstance(raw_title, str):
        product_title = raw_title.strip()
    elif isinstance(raw_title, list):
        product_title = "".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg)
            for seg in raw_title
        ).strip()

    if not product_title:
        logger.warning("record %s 缺少商品标题，跳过", record_id)
        return None

    image_file_tokens: list[str] = []
    raw_images = fields.get("商品平铺图")
    if isinstance(raw_images, list):
        for att in raw_images:
            if isinstance(att, dict) and att.get("file_token"):
                image_file_tokens.append(att["file_token"])

    outfit_image_file_tokens: list[str] = []
    raw_outfit_images = fields.get("穿搭参考图")
    if isinstance(raw_outfit_images, list):
        for att in raw_outfit_images:
            if isinstance(att, dict) and att.get("file_token"):
                outfit_image_file_tokens.append(att["file_token"])

    review_count = _parse_int(fields.get("评价数量"), default=5)
    image_count = _parse_int(fields.get("晒图数量"), default=2)
    wechat_contacts = _parse_wechat_contacts(fields.get("微信联系人"))
    processing_status = _parse_status_text(fields.get("处理状态"))
    should_process = _is_todo_status(processing_status)

    return SourceRow(
        record_id=record_id,
        product_title=product_title,
        image_file_tokens=image_file_tokens,
        outfit_image_file_tokens=outfit_image_file_tokens,
        review_count=review_count,
        image_count=image_count,
        wechat_contacts=wechat_contacts,
        processing_status=processing_status,
        should_process=should_process,
    )


def download_attachment(token: str, file_token: str) -> bytes:
    """
    通过飞书 Drive API 下载附件，返回文件内容 bytes。
    """
    url = f"{_BASE}/drive/v1/medias/{file_token}/download"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"下载附件 {file_token} 失败: HTTP {resp.status_code} {resp.text[:200]}"
        )
    return resp.content


def fetch_all_source_rows(
    app_token: str | None = None,
    table_id: str | None = None,
) -> tuple[str, list[SourceRow]]:
    """
    一站式入口：获取 token -> 读取所有行 -> 解析。
    返回 (token, rows)，token 后续下载附件用。
    """
    app_token = app_token or settings.feishu.source_app_token
    table_id = table_id or settings.feishu.source_table_id
    if not app_token or not table_id:
        raise RuntimeError("FEISHU_SOURCE_APP_TOKEN / FEISHU_SOURCE_TABLE_ID 未配置")

    token = get_tenant_access_token()
    raw_records = list_source_records(token, app_token, table_id)

    rows: list[SourceRow] = []
    for rec in raw_records:
        row = parse_source_row(rec)
        if row:
            rows.append(row)

    logger.info("共解析出 %d 条有效需求行（原始 %d 条）", len(rows), len(raw_records))
    return token, rows


def update_source_row_status(
    token: str,
    record_id: str,
    status_value: str = "已处理",
    app_token: str | None = None,
    table_id: str | None = None,
) -> None:
    """
    更新需求表某行的“处理状态”字段。
    """
    app_token = app_token or settings.feishu.source_app_token
    table_id = table_id or settings.feishu.source_table_id
    if not app_token or not table_id:
        raise RuntimeError("FEISHU_SOURCE_APP_TOKEN / FEISHU_SOURCE_TABLE_ID 未配置")
    if not record_id:
        raise RuntimeError("record_id 不能为空")

    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    # 兼容文本字段与单选字段两种常见建模方式。
    candidates = [
        {"fields": {"处理状态": status_value}},
        {"fields": {"处理状态": [{"text": status_value}]}},
    ]
    last_resp = None
    for payload in candidates:
        resp = requests.put(url, headers=headers, json=payload, timeout=20)
        data = resp.json()
        last_resp = data
        if data.get("code") == 0:
            return
    raise RuntimeError(f"更新处理状态失败: {last_resp}")


def _parse_int(value, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default
    return default


def _parse_status_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            val = value.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                for key in ("text", "name", "value"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val.strip())
                        break
        return " ".join(parts).strip()
    return str(value).strip()


def _is_todo_status(status_text: str) -> bool:
    normalized = (status_text or "").strip().replace(" ", "")
    if not normalized:
        return True
    if normalized in {"已处理", "完成", "待审核", "审核中", "completed", "done"}:
        return False
    if normalized in {"待处理", "未处理", "pending", "todo"}:
        return True
    # 未知状态默认纳入处理，避免误漏单
    return True


def _parse_wechat_contacts(value) -> list[str]:
    """
    解析需求表中的“微信联系人”字段，支持：
    - 文本：张三,李四
    - 多行文本：每行一个
    - 列表/对象：提取 text/name/value
    """
    raw_parts: list[str] = []

    if value is None:
        return []

    if isinstance(value, str):
        raw_parts.append(value)
    elif isinstance(value, dict):
        for key in ("text", "name", "value"):
            val = value.get(key)
            if isinstance(val, str) and val.strip():
                raw_parts.append(val)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                raw_parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "name", "value"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        raw_parts.append(val)
                        break

    contacts: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        normalized = (
            part.replace("，", ",")
            .replace("\n", ",")
            .replace(";", ",")
            .replace("；", ",")
        )
        for name in normalized.split(","):
            name = name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            contacts.append(name)
    return contacts
