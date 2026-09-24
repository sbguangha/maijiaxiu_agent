"""
买家秀项目重构诊断脚本。

默认安全模式：
- 不调用 Kimi
- 不调用豆包
- 不调用飞书接口
- 不写回飞书结果表

用途：
- 检查 8000 端口占用
- 检查核心模块导入
- 检查 LangGraph V2 导入链
- 检查飞书 `穿搭参考图` 字段解析
- 检查双图参考生图 payload 是否真正包含 商品图 + 穿搭图
- 检查 batch_graph 单行处理是否把穿搭参考图传入 agent

可选：
    python diagnose_pipeline.py --live-http
只检查本地 HTTP 服务基础端点，不触发批量生成。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import py_compile
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class Diagnostics:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name=name, ok=ok, detail=detail))
        prefix = "PASS" if ok else "FAIL"
        print(f"[{prefix}] {name}")
        if detail:
            print(f"       {detail}")

    def run(self, name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn() or ""
            self.add(name, True, detail)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            tb = traceback.format_exc(limit=5)
            self.add(name, False, f"{exc}\n{tb}")

    def exit_code(self) -> int:
        return 0 if all(r.ok for r in self.results) else 1

    def summary(self) -> None:
        total = len(self.results)
        failed = [r for r in self.results if not r.ok]
        print()
        print("=" * 72)
        print(f"诊断完成：{total - len(failed)}/{total} 通过")
        if failed:
            print("失败项：")
            for item in failed:
                print(f"- {item.name}: {item.detail.splitlines()[0] if item.detail else ''}")
        print("=" * 72)


def _ensure_project_on_path() -> None:
    os.chdir(BASE_DIR)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))


def check_python_compile() -> str:
    files = [
        "app.py",
        "agent_state.py",
        "agent_nodes.py",
        "agent_graph_v2.py",
        "batch_graph.py",
        "config.py",
        "feishu_reader.py",
        "image_generator.py",
        "buyer_show_prompt.py",
        "qwen_client.py",
        "agent_tools.py",
        "outbox.py",
        "utils.py",
    ]
    for name in files:
        py_compile.compile(str(BASE_DIR / name), doraise=True)
    return f"compiled {len(files)} files"


def check_port_8000() -> str:
    host = "127.0.0.1"
    port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        in_use = sock.connect_ex((host, port)) == 0

    if not in_use:
        return "127.0.0.1:8000 当前未被占用，可以启动 app.py"

    detail = "127.0.0.1:8000 已被占用。"
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            lines = [
                line.strip()
                for line in proc.stdout.splitlines()
                if ":8000" in line and "LISTENING" in line.upper()
            ]
            if lines:
                detail += " 占用信息: " + " | ".join(lines[:3])
        except Exception:
            pass
    return detail


def check_required_env() -> str:
    from config import settings

    missing: list[str] = []
    if not settings.llm.dashscope_api_key:
        missing.append("DASHSCOPE_API_KEY")
    if not settings.llm.doubao_api_key:
        missing.append("DOUBAO_API_KEY")
    if not settings.feishu.source_app_token:
        missing.append("FEISHU_SOURCE_APP_TOKEN")
    if not settings.feishu.source_table_id:
        missing.append("FEISHU_SOURCE_TABLE_ID")

    if missing:
        raise RuntimeError("缺少环境变量: " + ", ".join(missing))
    return "核心环境变量存在"


def check_core_imports() -> str:
    modules = [
        "config",
        "utils",
        "outbox",
        "feishu_reader",
        "image_generator",
        "agent_state",
        "agent_nodes",
        "agent_graph_v2",
        "batch_graph",
        "app",
    ]
    for module in modules:
        importlib.import_module(module)
    return "imported: " + ", ".join(modules)


def check_route_compatibility() -> str:
    import agent_nodes

    required = [
        "route_after_generate_reviews",
        "route_after_generate_images",
        "route_after_human_approval",
    ]
    missing = [name for name in required if not hasattr(agent_nodes, name)]
    if missing:
        raise RuntimeError("agent_nodes 缺少路由函数: " + ", ".join(missing))
    return "路由函数完整"


def check_feishu_row_parser() -> str:
    from feishu_reader import parse_source_row

    row = parse_source_row({
        "record_id": "rec_test",
        "fields": {
            "商品标题": "测试卫衣",
            "商品平铺图": [{"file_token": "product-token"}],
            "穿搭参考图": [{"file_token": "outfit-1"}, {"file_token": "outfit-2"}],
            "评价数量": 3,
            "晒图数量": 2,
            "微信联系人": "张三,李四",
            "处理状态": "待处理",
        },
    })
    if row is None:
        raise RuntimeError("parse_source_row 返回 None")
    assert row.product_title == "测试卫衣"
    assert row.image_file_tokens == ["product-token"]
    assert row.outfit_image_file_tokens == ["outfit-1", "outfit-2"]
    assert row.review_count == 3
    assert row.image_count == 2
    assert row.wechat_contacts == ["张三", "李四"]
    assert row.should_process is True
    return "穿搭参考图 file_token 解析正常"


@contextlib.contextmanager
def patched_attr(module: Any, name: str, value: Any):
    old = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, old)


def check_image_reference_payload() -> str:
    import image_generator

    captured: list[dict] = []

    def fake_post(payload: dict) -> str:
        captured.append(payload)
        return "https://example.com/generated.jpg"

    with patched_attr(image_generator, "_post_doubao_payload", fake_post):
        url, field = image_generator.generate_lifestyle_image_with_references(
            prompt="测试双图参考",
            product_image_base64="data:image/png;base64,PRODUCT",
            outfit_image_base64="data:image/png;base64,OUTFIT",
        )

    if not url:
        raise RuntimeError("双图参考生成函数未返回 URL")
    if field != "image":
        raise RuntimeError(f"双图字段应为 image，实际为 {field}")
    if not captured:
        raise RuntimeError("未捕获到 payload")

    payload = captured[0]
    images = payload.get("image")
    if not isinstance(images, list) or len(images) != 2:
        raise RuntimeError(f"payload.image 不是两张图数组: {json.dumps(payload, ensure_ascii=False)[:500]}")
    if "image_input" in payload or "image_urls" in payload:
        raise RuntimeError("payload 包含可能被忽略的备用字段 image_input/image_urls")
    return "payload.image 为 [商品图, 穿搭图]"


def check_run_image_generation_safe_path() -> str:
    import image_generator

    calls: dict[str, Any] = {
        "prompts_called": False,
        "reference_calls": [],
        "uploaded": [],
    }

    def fake_token() -> str:
        return "feishu-token"

    def fake_upload(source: Any, token: str, filename: str = "image.png") -> str:
        calls["uploaded"].append(filename)
        return f"token-{filename}"

    def fake_write(product_name: str, product_image_token: str, ai_image_tokens: list[str], token: str) -> bool:
        calls["written"] = {
            "product_name": product_name,
            "product_image_token": product_image_token,
            "ai_image_tokens": ai_image_tokens,
        }
        return True

    def fake_scene_prompts(*args, **kwargs) -> list[str]:
        calls["prompts_called"] = True
        return ["不应调用场景提示词"]

    def fake_extract(*args, **kwargs):
        return {"one_line": "测试卫衣", "season": "", "occasion": ""}

    def fake_judge(*args, **kwargs):
        from buyer_show_prompt import ImageChecklist
        return ImageChecklist()

    def fake_reference(prompt: str, product_image_base64: str, outfit_image_base64: str):
        calls["reference_calls"].append({
            "prompt": prompt,
            "product_has_data_uri": product_image_base64.startswith("data:image/"),
            "outfit_has_data_uri": outfit_image_base64.startswith("data:image/"),
        })
        return "https://example.com/generated.jpg", "image"

    patches = [
        ("get_feishu_token", fake_token),
        ("upload_image_to_feishu", fake_upload),
        ("write_to_feishu_table", fake_write),
        ("generate_scene_prompts", fake_scene_prompts),
        ("extract_garment_facts", fake_extract),
        ("judge_buyer_show", fake_judge),
        ("generate_lifestyle_image_with_references", fake_reference),
    ]

    managers = []
    try:
        for name, value in patches:
            manager = patched_attr(image_generator, name, value)
            managers.append(manager)
            manager.__enter__()

        result = image_generator.run_image_generation(
            image_bytes=b"product-image-bytes" * 20,
            product_name="测试卫衣",
            scene_count=2,
            outfit_image_bytes_list=[b"outfit-a" * 30, b"outfit-b" * 30],
        )
    finally:
        for manager in reversed(managers):
            manager.__exit__(None, None, None)

    if result.get("status") != "success":
        raise RuntimeError(f"run_image_generation 未成功: {result}")
    if result.get("reference_mode") != "product_plus_outfit":
        raise RuntimeError(f"reference_mode 错误: {result.get('reference_mode')}")
    if calls["prompts_called"]:
        raise RuntimeError("有穿搭参考图且张数不超过参考图时不应另写场景提示词")
    if len(calls["reference_calls"]) != 2:
        raise RuntimeError(f"双图参考调用次数错误: {len(calls['reference_calls'])}")
    if result.get("reference_fields") != ["image", "image"]:
        raise RuntimeError(f"reference_fields 错误: {result.get('reference_fields')}")
    return "有穿搭图时走双图参考，且不另写场景提示词"


def check_agent_generate_images_node_safe_path() -> str:
    import agent_nodes

    captured: dict[str, Any] = {}

    def fake_run_image_generation(
        image_bytes: bytes,
        product_name: str,
        scene_count: int,
        outfit_image_bytes_list=None,
        commit_to_feishu: bool = True,
    ):
        captured["image_bytes_len"] = len(image_bytes)
        captured["product_name"] = product_name
        captured["scene_count"] = scene_count
        captured["outfit_count"] = len(outfit_image_bytes_list or [])
        captured["commit_to_feishu"] = commit_to_feishu
        return {
            "status": "success",
            "image_urls": ["https://example.com/a.jpg"],
            "reference_mode": "product_plus_outfit",
        }

    def fake_materialize(result: dict) -> list[str]:
        return [str(BASE_DIR / "data" / "generated_images" / "fake.jpg")]

    with patched_attr(agent_nodes, "run_image_generation", fake_run_image_generation):
        with patched_attr(agent_nodes, "materialize_generated_images", fake_materialize):
            result = agent_nodes.generate_images_node({
                "skip_image_generation": False,
                "product_name": "测试卫衣",
                "image_count": 1,
                "product_image_bytes": b"product",
                "outfit_image_bytes_list": [b"outfit"],
                "defer_feishu_commit": True,
            })

    if result.get("error_message"):
        raise RuntimeError(result["error_message"])
    if captured.get("outfit_count") != 1:
        raise RuntimeError(f"generate_images_node 未传递穿搭图: {captured}")
    if not result.get("local_image_paths"):
        raise RuntimeError("local_image_paths 为空")
    if captured.get("commit_to_feishu") is not False:
        raise RuntimeError(f"defer_feishu_commit 未生效: {captured}")
    return "agent_nodes.generate_images_node 正确传递 outfit_image_bytes_list 且支持候选图模式"


def check_batch_process_row_safe_path() -> str:
    import batch_graph

    captured: dict[str, Any] = {}

    row = SimpleNamespace(
        record_id="rec_test",
        product_title="测试卫衣",
        image_file_tokens=["product-token"],
        outfit_image_file_tokens=["outfit-1", "outfit-2"],
        wechat_contacts=["张三"],
        review_count=2,
        image_count=2,
    )

    def fake_download(token: str, file_token: str) -> bytes:
        return f"bytes-{file_token}".encode("utf-8") * 20

    def fake_update(token: str, record_id: str, status: str) -> None:
        captured["updated"] = (record_id, status)

    def fake_run_agent_v2(**kwargs):
        captured["agent_kwargs"] = kwargs
        return {
            "reviews_formatted": "评价1\n评价2",
            "image_result": {"image_urls": ["https://example.com/a.jpg"]},
            "task_id": "task_test",
        }

    with patched_attr(batch_graph, "download_attachment", fake_download):
        with patched_attr(batch_graph, "update_source_row_status", fake_update):
            with patched_attr(batch_graph, "run_agent_v2", fake_run_agent_v2):
                result = batch_graph.process_row_node({
                    "batch_id": "batch_test",
                    "row": row,
                    "feishu_token": "feishu-token",
                    "default_contacts": [],
                    "require_confirmation": False,
                })

    row_results = result.get("row_results", [])
    if not row_results or row_results[0].get("status") != "success":
        raise RuntimeError(f"process_row_node 未成功: {result}")
    agent_kwargs = captured.get("agent_kwargs", {})
    if len(agent_kwargs.get("outfit_image_bytes_list") or []) != 2:
        raise RuntimeError(f"batch_graph 未传递 2 张穿搭参考图: {agent_kwargs}")
    if captured.get("updated") != ("rec_test", "已处理"):
        raise RuntimeError(f"未回写处理状态: {captured.get('updated')}")
    return "batch_graph.process_row_node 正确下载并传递穿搭图"


def check_live_http() -> str:
    import requests

    base = "http://127.0.0.1:8000"
    root = requests.get(base + "/", timeout=5)
    if root.status_code != 200:
        raise RuntimeError(f"GET / HTTP {root.status_code}")

    # 只查审核列表，避免触发飞书外部接口。
    approvals = requests.get(base + "/delivery-approvals?limit=5", timeout=5)
    if approvals.status_code != 200:
        raise RuntimeError(f"GET /delivery-approvals HTTP {approvals.status_code}: {approvals.text[:200]}")
    return "HTTP / 与 /delivery-approvals 可访问"


def main() -> int:
    parser = argparse.ArgumentParser(description="买家秀项目安全诊断脚本")
    parser.add_argument("--live-http", action="store_true", help="检查本地运行中的 HTTP 基础端点")
    args = parser.parse_args()

    _ensure_project_on_path()
    diagnostics = Diagnostics()

    diagnostics.run("Python 语法编译", check_python_compile)
    diagnostics.run("8000 端口检查", check_port_8000)
    diagnostics.run("核心环境变量检查", check_required_env)
    diagnostics.run("核心模块导入", check_core_imports)
    diagnostics.run("LangGraph 路由兼容性", check_route_compatibility)
    diagnostics.run("飞书 SourceRow 解析穿搭参考图", check_feishu_row_parser)
    diagnostics.run("双图参考 payload 检查", check_image_reference_payload)
    diagnostics.run("run_image_generation 安全路径检查", check_run_image_generation_safe_path)
    diagnostics.run("agent_nodes 生图节点检查", check_agent_generate_images_node_safe_path)
    diagnostics.run("batch_graph 单行处理检查", check_batch_process_row_safe_path)

    if args.live_http:
        diagnostics.run("本地 HTTP 服务检查", check_live_http)

    diagnostics.summary()
    return diagnostics.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
