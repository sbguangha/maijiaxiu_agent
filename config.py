"""
统一配置管理（Pydantic BaseSettings）

所有环境变量集中定义，自动从 .env 文件读取，支持类型校验和默认值。
使用方式：
    from config import settings
    print(settings.llm.moonshot_api_key)
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

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    moonshot_api_key: str = Field(default="", alias="MOONSHOT_API_KEY")
    moonshot_base_url: str = Field(default="https://api.moonshot.cn/v1", alias="MOONSHOT_BASE_URL")
    moonshot_model: str = Field(default="moonshot-v1-8k", alias="MOONSHOT_MODEL")

    doubao_api_key: str = Field(default="", alias="DOUBAO_API_KEY")
    doubao_api_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3/images/generations",
        alias="DOUBAO_API_URL",
    )

    @property
    def moonshot_enabled(self) -> bool:
        return bool(self.moonshot_api_key)

    @property
    def doubao_enabled(self) -> bool:
        return bool(self.doubao_api_key)


class FeishuSettings(BaseSettings):
    """飞书开放平台配置。"""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    app_id: str = Field(default="", alias="FEISHU_APP_ID")
    app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    app_token: str = Field(default="", alias="FEISHU_APP_TOKEN")
    table_id: str = Field(default="", alias="FEISHU_TABLE_ID")
    source_app_token: str = Field(default="", alias="FEISHU_SOURCE_APP_TOKEN")
    source_table_id: str = Field(default="", alias="FEISHU_SOURCE_TABLE_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def source_enabled(self) -> bool:
        return bool(self.source_app_token and self.source_table_id)


class OutboxSettings(BaseSettings):
    """影刀发送队列配置。"""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    db_path: str = Field(
        default=str(BASE_DIR / "data" / "outbox.db"),
        alias="OUTBOX_DB_PATH",
    )
    file_dir: str = Field(
        default=str(BASE_DIR / "data" / "outbox_files"),
        alias="OUTBOX_FILE_DIR",
    )
    heartbeat_file: str = Field(
        default=str(BASE_DIR / "logs" / "outbox_heartbeat.jsonl"),
        alias="OUTBOX_HEARTBEAT_FILE",
    )
    processing_timeout_sec: int = Field(default=120, alias="OUTBOX_PROCESSING_TIMEOUT_SEC")
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

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

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
def create_moonshot_llm(temperature: float = 0.7, max_retries: int = 3):
    """创建 Kimi/Moonshot LLM 实例（带缓存，同参数复用同一实例）。"""
    from langchain_openai import ChatOpenAI  # pylint: disable=import-outside-toplevel
    return ChatOpenAI(
        api_key=settings.llm.moonshot_api_key,
        base_url=settings.llm.moonshot_base_url,
        model=settings.llm.moonshot_model,
        temperature=temperature,
        max_retries=max_retries,
    )
