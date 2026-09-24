"""
统一配置管理（Pydantic BaseSettings）

所有环境变量集中定义，自动从 .env 文件读取，支持类型校验和默认值。
使用方式：
    from config import settings
    print(settings.llm.text_model)
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).parent.resolve()


class LLMSettings(BaseSettings):
    """大模型 API 配置。"""

    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
    )

    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = Field(
        default="https://maas.qianwenaiapi.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )
    text_model: str = Field(default="qwen3.7-plus", alias="QWEN_TEXT_MODEL")
    vision_model: str = Field(default="qwen3.8-omni-flash", alias="QWEN_VISION_MODEL")

    doubao_api_key: str = Field(default="", alias="DOUBAO_API_KEY")
    doubao_api_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3/images/generations",
        alias="DOUBAO_API_URL",
    )

    @property
    def doubao_enabled(self) -> bool:
        return bool(self.doubao_api_key)


class FeishuSettings(BaseSettings):
    """飞书开放平台配置。"""

    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
    )

    app_id: str = Field(default="", alias="FEISHU_APP_ID")
    app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    app_token: str = Field(default="", alias="FEISHU_APP_TOKEN")
    table_id: str = Field(default="", alias="FEISHU_TABLE_ID")
    source_app_token: str = Field(default="", alias="FEISHU_SOURCE_APP_TOKEN")
    source_table_id: str = Field(default="", alias="FEISHU_SOURCE_TABLE_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret) or bool(self.source_app_token and self.source_table_id) or bool(
            self.source_app_token and self.source_table_id
        )

    @property
    def source_enabled(self) -> bool:
        return bool(self.source_app_token and self.source_table_id)


class OutboxSettings(BaseSettings):
    """审核记录与本地任务库配置。"""

    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
    )

    db_path: str = Field(
        default=str(BASE_DIR / "data" / "outbox.db"),
        alias="OUTBOX_DB_PATH",
    )
    require_confirmation: bool = Field(default=False, alias="REQUIRE_DELIVERY_CONFIRMATION")
    default_target_contacts: str = Field(default="", alias="WECHAT_TARGET_CONTACTS")

    @property
    def target_contacts_list(self) -> List[str]:
        if not self.default_target_contacts:
            return []
        return [
            item.strip()
            for item in self.default_target_contacts.replace("，", ",").replace("\n", ",").split(",")
            if item.strip()
        ]


class PathSettings(BaseSettings):
    """文件路径配置。"""

    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
    )

    generated_image_dir: str = Field(
        default=str(BASE_DIR / "data" / "generated_images"),
        alias="GENERATED_IMAGE_DIR",
    )
    checkpoint_db: str = Field(
        default=str(BASE_DIR / "data" / "checkpoints.db"),
        alias="LANGGRAPH_CHECKPOINT_DB",
    )
    batch_checkpoint_db: str = Field(
        default=str(BASE_DIR / "data" / "batch_checkpoints.db"),
        alias="BATCH_CHECKPOINT_DB",
    )


class AppSettings(BaseSettings):
    """全局应用配置。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")

    llm: LLMSettings = Field(default_factory=LLMSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    outbox: OutboxSettings = Field(default_factory=OutboxSettings)
    paths: PathSettings = Field(default_factory=PathSettings)


# 全局单例
settings = AppSettings()


# ============================================================
# 共享 LLM 工厂（所有模块统一入口）
# ============================================================

@lru_cache(maxsize=4)
def create_text_llm(temperature: float = 0.7, max_retries: int = 3):
    """创建百炼文本模型（默认 qwen3.7-plus）。评价和场景句都走这里。"""
    from langchain_openai import ChatOpenAI  # pylint: disable=import-outside-toplevel
    return ChatOpenAI(
        api_key=settings.llm.dashscope_api_key or "unset",
        base_url=settings.llm.dashscope_base_url,
        model=settings.llm.text_model,
        temperature=temperature,
        max_retries=max_retries,
        extra_body={"enable_thinking": False},
    )
