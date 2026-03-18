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
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("feishu-reader")

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

_BASE = "https://open.feishu.cn/open-apis"


@dataclass
class SourceRow:
    """需求表格中的一行"""
    record_id: str
    product_title: str
    image_file_tokens: list[str]
    review_count: int
    image_count: int


def get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token，失败时抛异常。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
    resp = requests.post(
        f"{_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
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

    review_count = _parse_int(fields.get("评价数量"), default=5)
    image_count = _parse_int(fields.get("晒图数量"), default=2)

    return SourceRow(
        record_id=record_id,
        product_title=product_title,
        image_file_tokens=image_file_tokens,
        review_count=review_count,
        image_count=image_count,
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
    app_token = app_token or os.getenv("FEISHU_SOURCE_APP_TOKEN", "")
    table_id = table_id or os.getenv("FEISHU_SOURCE_TABLE_ID", "")
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
