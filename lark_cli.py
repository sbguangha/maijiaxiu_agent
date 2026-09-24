"""
通过本机 lark-cli（用户身份）调用飞书 Base，避免在 .env 里存放 APP_SECRET。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("lark-cli")

BASE_DIR = Path(__file__).parent.resolve()
TMP_DIR = BASE_DIR / "data" / "_lark_tmp"


def _find_lark_cli() -> str:
    which = shutil.which("lark-cli")
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        candidates.append(
            Path(appdata) / "npm" / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe"
        )
    if which:
        which_path = Path(which)
        candidates.append(which_path)
        # npm 全局入口旁边的原生二进制
        npm_dir = which_path.parent
        candidates.append(npm_dir / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe")
    for path in candidates:
        if path.is_file() and path.suffix.lower() == ".exe":
            return str(path)
    if which:
        return which
    raise RuntimeError("未找到 lark-cli，请先执行: npx @larksuite/cli@latest install")


def _parse_json_stdout(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("lark-cli 没有输出")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise RuntimeError(f"lark-cli 输出不是 JSON: {text[:300]}")


def run_lark(*args: str, timeout: int = 120) -> dict[str, Any]:
    exe = _find_lark_cli()
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    cmd = [exe, *args]
    last_error: Optional[Exception] = None
    for attempt in range(3):
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
        payload: dict[str, Any] = {}
        combined = (result.stdout or "") + (result.stderr or "")
        try:
            payload = _parse_json_stdout(result.stdout or result.stderr or "")
        except Exception as e:
            last_error = e
            if "unexpected EOF" in combined and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"lark-cli 调用失败: {' '.join(args[:6])} | {combined[:400]}") from e

        if payload.get("ok") is True or result.returncode == 0 and "error" not in payload:
            return payload
        err = payload.get("error") or {}
        message = err.get("message") or combined[:400] or "unknown lark-cli error"
        if "unexpected EOF" in message or message.endswith(": EOF") or "EOF" in message:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                last_error = RuntimeError(message)
                continue
        raise RuntimeError(f"lark-cli 失败: {message}")
    raise RuntimeError(f"lark-cli 重试失败: {last_error}")


def ensure_user_identity() -> None:
    payload = run_lark("whoami", "--as", "user")
    identity = payload.get("identity")
    if not identity and isinstance(payload.get("data"), dict):
        identity = payload["data"].get("identity")
    if identity and identity != "user":
        raise RuntimeError(f"lark-cli 当前身份是 {identity}，请重新执行用户授权登录")


def _relpath(path: Path) -> str:
    return path.resolve().relative_to(BASE_DIR).as_posix()


def _ensure_tmp() -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def write_tmp_json(payload: dict[str, Any], prefix: str = "payload") -> str:
    _ensure_tmp()
    path = TMP_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return _relpath(path)


def write_tmp_bytes(content: bytes, filename: str) -> str:
    _ensure_tmp()
    safe = filename.replace("\\", "_").replace("/", "_").replace(":", "_")
    path = TMP_DIR / f"{uuid.uuid4().hex[:10]}_{safe}"
    path.write_bytes(content)
    return _relpath(path)


def _normalize_record(item: dict[str, Any], id_to_name: dict[str, str] | None = None) -> dict[str, Any]:
    record_id = item.get("record_id") or item.get("id") or item.get("recordId") or ""
    fields = item.get("fields")
    if not isinstance(fields, dict):
        fields = {
            k: v
            for k, v in item.items()
            if k not in {"record_id", "id", "recordId"}
        }
    if id_to_name:
        fields = {id_to_name.get(str(k), str(k)): v for k, v in fields.items()}
    return {"record_id": record_id, "fields": fields}


def _records_from_cli_data(data: Any, id_to_name: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """把 lark-cli +record-list 的返回拆成 {record_id, fields} 列表。"""
    inner = data
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and "record_id_list" in data["data"]:
        inner = data["data"]
    if not isinstance(inner, dict):
        if isinstance(inner, list):
            return [_normalize_record(item, id_to_name) for item in inner if isinstance(item, dict)], False
        return [], False

    record_ids = inner.get("record_id_list") or []
    matrix = inner.get("data")
    raw_fields = inner.get("fields")
    field_ids = inner.get("field_id_list") or []
    has_more = bool(inner.get("has_more"))

    names: list[str] = []
    if isinstance(raw_fields, list) and raw_fields and isinstance(raw_fields[0], str):
        names = [str(x) for x in raw_fields]
    elif isinstance(raw_fields, list) and raw_fields and isinstance(raw_fields[0], dict):
        names = [str(x.get("name") or x.get("id") or "") for x in raw_fields]
    elif field_ids:
        names = [(id_to_name or {}).get(str(fid), str(fid)) for fid in field_ids]

    if isinstance(matrix, list) and record_ids:
        records: list[dict[str, Any]] = []
        for i, rid in enumerate(record_ids):
            row = matrix[i] if i < len(matrix) else []
            fields: dict[str, Any] = {}
            if isinstance(row, list):
                for j, val in enumerate(row):
                    key = names[j] if j < len(names) else (
                        str(field_ids[j]) if j < len(field_ids) else str(j)
                    )
                    fields[key] = val
            records.append({"record_id": str(rid), "fields": fields})
        return records, has_more

    page = inner.get("records") or inner.get("items") or inner.get("items_list") or []
    if isinstance(page, list):
        return [_normalize_record(item, id_to_name) for item in page if isinstance(item, dict)], has_more
    return [], has_more
    record_id = item.get("record_id") or item.get("id") or item.get("recordId") or ""
    fields = item.get("fields")
    if not isinstance(fields, dict):
        fields = {
            k: v
            for k, v in item.items()
            if k not in {"record_id", "id", "recordId"}
        }
    if id_to_name:
        fields = {id_to_name.get(str(k), str(k)): v for k, v in fields.items()}
    return {"record_id": record_id, "fields": fields}


def _field_id_to_name(base_token: str, table_id: str) -> dict[str, str]:
    payload = run_lark(
        "base", "+field-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--as", "user",
        "--json",
    )
    data = payload.get("data") or {}
    fields = data.get("fields") if isinstance(data, dict) else data
    mapping: dict[str, str] = {}
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict) and field.get("id") and field.get("name"):
                mapping[str(field["id"])] = str(field["name"])
    return mapping


def base_record_list_all(
    base_token: str,
    table_id: str,
    field_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    del field_names
    id_to_name = _field_id_to_name(base_token, table_id)
    records: list[dict[str, Any]] = []
    offset = 0
    limit = 200
    while True:
        args = [
            "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--as", "user",
            "--format", "json",
            "--limit", str(limit),
            "--offset", str(offset),
        ]
        payload = run_lark(*args, timeout=180)
        data = payload.get("data") or {}
        page, has_more = _records_from_cli_data(data, id_to_name)
        records.extend(page)
        if not has_more and len(page) < limit:
            break
        if not page:
            break
        offset += len(page)
        if offset > 5000:
            break
    logger.info("lark-cli 读取记录 %d 条 base=%s table=%s", len(records), base_token[:8], table_id)
    return records


def base_download_attachment(
    base_token: str,
    table_id: str,
    record_id: str,
    file_token: str,
) -> bytes:
    if not record_id:
        raise RuntimeError("下载附件需要 record_id")
    _ensure_tmp()
    out = TMP_DIR / f"dl_{uuid.uuid4().hex[:12]}.bin"
    rel = _relpath(out)
    run_lark(
        "base", "+record-download-attachment",
        "--base-token", base_token,
        "--table-id", table_id,
        "--record-id", record_id,
        "--file-token", file_token,
        "--output", rel,
        "--overwrite",
        "--as", "user",
        "--json",
        timeout=180,
    )
    if not out.is_file():
        raise RuntimeError(f"附件下载后文件不存在: {rel}")
    try:
        return out.read_bytes()
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass


def base_batch_create(base_token: str, table_id: str, create_records: list[dict[str, Any]]) -> list[str]:
    rel = write_tmp_json({"create_records": create_records}, prefix="create")
    payload = run_lark(
        "base", "+record-batch-create",
        "--base-token", base_token,
        "--table-id", table_id,
        "--as", "user",
        "--json", f"@{rel}",
        timeout=180,
    )
    data = payload.get("data") or {}
    ids = data.get("record_id_list") or data.get("record_ids") or []
    if not ids and isinstance(data.get("records"), list):
        ids = [r.get("record_id") or r.get("id") for r in data["records"] if isinstance(r, dict)]
    return [str(x) for x in ids if x]


def base_batch_update(base_token: str, table_id: str, update_records: dict[str, dict[str, Any]]) -> None:
    rel = write_tmp_json({"update_records": update_records}, prefix="update")
    run_lark(
        "base", "+record-batch-update",
        "--base-token", base_token,
        "--table-id", table_id,
        "--as", "user",
        "--json", f"@{rel}",
        timeout=180,
    )


def base_upload_attachment(
    base_token: str,
    table_id: str,
    record_id: str,
    field_id: str,
    file_paths: list[str],
) -> None:
    if not record_id or not file_paths:
        return
    args = [
        "base", "+record-upload-attachment",
        "--base-token", base_token,
        "--table-id", table_id,
        "--record-id", record_id,
        "--field-id", field_id,
        "--as", "user",
        "--json",
    ]
    for path in file_paths:
        args.extend(["--file", path.replace("\\", "/")])
    run_lark(*args, timeout=180)
