from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "topic-ingestion" / "baserow-topic-candidates.json"
DEFAULT_ENV_FILE = PROJECT_ROOT / "data" / "feishu-bitable" / "feishu-bitable.env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "feishu-bitable"

TOPIC_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "source_id",
    "title",
    "pet_type",
    "topic",
    "source_org",
    "source_url",
    "source_file",
    "translated_path",
    "evidence_level",
    "evidence_excerpt",
    "xhs_angle",
    "hook",
    "summary",
    "risk_level",
    "risk_reason",
    "vet_boundary_required",
    "score",
    "status",
    "content_hash",
    "created_at",
)

FIELD_MAPPING: tuple[tuple[str, str, str], ...] = (
    ("candidate_id", "文本", "稳定选题 ID，用于去重和更新。"),
    ("source_id", "文本", "来源 ID。"),
    ("title", "文本", "资料标题或选题标题。"),
    ("pet_type", "单选", "兔 / 狗 / 猫 / 猫狗通用。"),
    ("topic", "单选", "体重 / 标签 / 营养 / 补剂 / 零食 / 食品加工 / 食品安全。"),
    ("source_org", "文本", "来源机构，例如 FEDIAF。"),
    ("source_url", "文本", "原文 URL。用文本更稳，避免 URL 字段 API 格式差异。"),
    ("source_file", "文本", "本地源文件路径。"),
    ("translated_path", "文本", "本地中文译文路径。"),
    ("evidence_level", "单选", "高 / 中 / 低。"),
    ("evidence_excerpt", "多行文本", "证据摘录。"),
    ("xhs_angle", "多行文本", "小红书角度。"),
    ("hook", "文本", "封面标题钩子。"),
    ("summary", "多行文本", "中文摘要。"),
    ("risk_level", "单选", "低 / 中 / 高。"),
    ("risk_reason", "多行文本", "风险原因。"),
    ("vet_boundary_required", "复选框", "是否需要兽医边界提示。"),
    ("score", "数字", "选题分数。"),
    ("status", "单选", "待评估 / 人工复核 / 可写 / 已写 / 放弃。"),
    ("content_hash", "文本", "译文内容哈希。"),
    ("created_at", "文本", "ISO 时间字符串。用文本避免日期字段格式转换问题。"),
)


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def config_value(env_values: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env_values.get(key) or default


def write_env_template(path: Path) -> None:
    lines = [
        "# Copy this file to feishu-bitable.env and fill real values. Do not commit real app secrets.",
        "FEISHU_BASE_URL=https://open.feishu.cn",
        "FEISHU_APP_ID=",
        "FEISHU_APP_SECRET=",
        "# Option A: paste the full Feishu Bitable URL. Wiki URLs are supported.",
        "FEISHU_BITABLE_URL=",
        "# Option B: fill tokens manually. For /wiki/ URLs, FEISHU_WIKI_NODE_TOKEN is the token after /wiki/.",
        "FEISHU_WIKI_NODE_TOKEN=",
        "FEISHU_BITABLE_APP_TOKEN=",
        "FEISHU_BITABLE_TABLE_ID=",
        "FEISHU_UPSERT_FIELD=candidate_id",
        "FEISHU_PAGE_SIZE=500",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_field_mapping(path: Path) -> None:
    lines = [
        "# Feishu Bitable Topic Candidate Field Mapping",
        "",
        "Create these fields in the Feishu Bitable table before running `--sync`.",
        "Field names must match exactly because the sync script writes by field name.",
        "",
        "| 字段名 | 建议类型 | 说明 |",
        "|---|---|---|",
    ]
    for field, field_type, note in FIELD_MAPPING:
        lines.append(f"| `{field}` | {field_type} | {note} |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Use `candidate_id` as the stable upsert key.",
            "- Keep `status` as a single select field; existing remote status is preserved by default.",
            "- Use text fields for local file paths and source URLs to avoid Feishu URL/date field API format differences.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def read_input_records(path: Path, limit: int | None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON array: {path}")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, dict) and isinstance(item.get("json"), dict):
            item = item["json"]
        if not isinstance(item, dict):
            raise RuntimeError(f"Item {index} is not a JSON object.")
        records.append(normalize_record(item, index))
        if limit is not None and len(records) >= limit:
            break
    return records


def normalize_record(record: dict[str, Any], index: int) -> dict[str, Any]:
    candidate_id = str(record.get("candidate_id", "")).strip()
    if not candidate_id:
        raise RuntimeError(f"Item {index} is missing candidate_id.")

    normalized: dict[str, Any] = {}
    for field in TOPIC_FIELDS:
        value = record.get(field)
        if field == "vet_boundary_required":
            normalized[field] = value is True or str(value).lower() == "true"
        elif field == "score":
            normalized[field] = int(value or 0)
        else:
            normalized[field] = "" if value is None else str(value)
    return normalized


def dedupe_records(records: list[dict[str, Any]], upsert_field: str) -> tuple[list[dict[str, Any]], int]:
    deduped: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for record in records:
        key = str(record.get(upsert_field, "")).strip()
        if not key:
            raise RuntimeError(f"Record is missing upsert field: {upsert_field}")
        if key in deduped:
            duplicates += 1
        deduped[key] = record
    return list(deduped.values()), duplicates


def feishu_api_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Feishu response.")
    code = payload.get("code", 0)
    if code not in (0, "0"):
        message = payload.get("msg") or payload.get("message") or payload
        raise RuntimeError(f"Feishu API error {code}: {message}")
    data = payload.get("data", {})
    return data if isinstance(data, dict) else {}


def raw_request(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu HTTP {error.code}: {response_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Feishu request failed: {error.reason}") from error


def build_url(base_url: str, path: str, query: dict[str, str | int] | None = None) -> str:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def parse_bitable_url(url: str) -> dict[str, str]:
    if not url:
        return {}
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    query = urllib.parse.parse_qs(parsed.query)
    result: dict[str, str] = {}
    for index, part in enumerate(parts):
        if part == "base" and index + 1 < len(parts):
            result["app_token"] = parts[index + 1]
        if part == "wiki" and index + 1 < len(parts):
            result["wiki_node_token"] = parts[index + 1]
    if "table" in query and query["table"]:
        result["table_id"] = query["table"][0]
    return result


def get_tenant_access_token(base_url: str, app_id: str, app_secret: str, timeout: int) -> str:
    url = build_url(base_url, "/open-apis/auth/v3/tenant_access_token/internal")
    response = raw_request(
        "POST",
        url,
        {"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
    )
    feishu_api_result(response)
    token = str(response.get("tenant_access_token", ""))
    if not token:
        raise RuntimeError("Feishu tenant_access_token is missing in response.")
    return token


def resolve_wiki_bitable_app_token(
    base_url: str,
    tenant_token: str,
    wiki_node_token: str,
    timeout: int,
) -> str:
    data = feishu_request(
        "GET",
        base_url,
        tenant_token,
        "/open-apis/wiki/v2/spaces/get_node",
        query={"token": wiki_node_token},
        timeout=timeout,
    )
    node = data.get("node", {})
    if not isinstance(node, dict):
        raise RuntimeError("Unexpected Feishu wiki node response.")
    obj_type = str(node.get("obj_type", ""))
    obj_token = str(node.get("obj_token", ""))
    if obj_type != "bitable":
        raise RuntimeError(f"Wiki node is not a Bitable document. obj_type={obj_type!r}")
    if not obj_token:
        raise RuntimeError("Wiki node response does not contain obj_token.")
    return obj_token


def feishu_request(
    method: str,
    base_url: str,
    tenant_token: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, str | int] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    url = build_url(base_url, path, query)
    response = raw_request(
        method,
        url,
        body=body,
        headers={"Authorization": f"Bearer {tenant_token}"},
        timeout=timeout,
    )
    return feishu_api_result(response)


def fetch_table_fields(
    base_url: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    timeout: int,
) -> set[str]:
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    fields: set[str] = set()
    page_token = ""
    while True:
        query: dict[str, str | int] = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        data = feishu_request("GET", base_url, tenant_token, path, query=query, timeout=timeout)
        items = data.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Unexpected Feishu Bitable fields response.")
        for field in items:
            if not isinstance(field, dict):
                continue
            name = field.get("field_name") or field.get("name")
            if name:
                fields.add(str(name))
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token", ""))
        if not page_token:
            break
    return fields


def fetch_existing_records(
    base_url: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    upsert_field: str,
    page_size: int,
    timeout: int,
) -> tuple[dict[str, dict[str, Any]], int]:
    existing: dict[str, dict[str, Any]] = {}
    duplicates = 0
    page_token = ""
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    while True:
        query: dict[str, str | int] = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token
        body = {"field_names": list(TOPIC_FIELDS)}
        data = feishu_request("POST", base_url, tenant_token, path, body=body, query=query, timeout=timeout)
        items = data.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Unexpected Feishu Bitable records response.")
        for row in items:
            if not isinstance(row, dict):
                continue
            fields = row.get("fields", {})
            if not isinstance(fields, dict):
                fields = {}
            key = cell_text(fields.get(upsert_field)).strip()
            if not key:
                continue
            if key in existing:
                duplicates += 1
                continue
            existing[key] = {
                "record_id": row.get("record_id"),
                "fields": fields,
            }
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token", ""))
        if not page_token:
            break
    return existing, duplicates


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "name", "value", "link"):
            if key in value:
                return "" if value[key] is None else str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text") or ""))
                elif "name" in item:
                    parts.append(str(item.get("name") or ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def values_equal(field: str, left: Any, right: Any) -> bool:
    if field == "vet_boundary_required":
        return (left is True or str(left).lower() == "true") == (
            right is True or str(right).lower() == "true"
        )
    if field == "score":
        try:
            return int(left or 0) == int(right or 0)
        except (TypeError, ValueError):
            return False
    return cell_text(left) == cell_text(right)


def row_changes(
    remote_fields: dict[str, Any],
    local_record: dict[str, Any],
    preserve_status: bool,
) -> tuple[dict[str, Any], list[str], bool]:
    patch: dict[str, Any] = {}
    changed: list[str] = []
    preserved_status = False
    for field in TOPIC_FIELDS:
        if preserve_status and field == "status" and cell_text(remote_fields.get("status")):
            preserved_status = True
            continue
        remote_value = remote_fields.get(field)
        local_value = local_record[field]
        if not values_equal(field, remote_value, local_value):
            patch[field] = local_value
            changed.append(field)
    return patch, changed, preserved_status


def create_record(
    base_url: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    record: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    return feishu_request("POST", base_url, tenant_token, path, body={"fields": record}, timeout=timeout)


def update_record(
    base_url: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    patch: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    return feishu_request("PUT", base_url, tenant_token, path, body={"fields": patch}, timeout=timeout)


def sync_records(
    records: list[dict[str, Any]],
    base_url: str,
    app_id: str,
    app_secret: str,
    app_token: str,
    wiki_node_token: str,
    table_id: str,
    upsert_field: str,
    page_size: int,
    timeout: int,
    preserve_status: bool,
) -> dict[str, Any]:
    tenant_token = get_tenant_access_token(base_url, app_id, app_secret, timeout)
    if not app_token and wiki_node_token:
        app_token = resolve_wiki_bitable_app_token(base_url, tenant_token, wiki_node_token, timeout)
    if not app_token:
        raise RuntimeError("FEISHU_BITABLE_APP_TOKEN is missing and no FEISHU_WIKI_NODE_TOKEN was provided.")

    fields = fetch_table_fields(base_url, tenant_token, app_token, table_id, timeout)
    missing_fields = sorted(set(TOPIC_FIELDS) - fields)
    if missing_fields:
        raise RuntimeError(
            "Feishu Bitable table is missing required fields: " + ", ".join(missing_fields)
        )

    existing, remote_duplicates = fetch_existing_records(
        base_url, tenant_token, app_token, table_id, upsert_field, page_size, timeout
    )
    created = 0
    updated = 0
    unchanged = 0
    preserved_status = 0
    changed_counter: Counter[str] = Counter()

    for record in records:
        key = str(record[upsert_field])
        remote = existing.get(key)
        if remote is None:
            create_record(base_url, tenant_token, app_token, table_id, record, timeout)
            created += 1
            continue

        record_id = remote.get("record_id")
        if not record_id:
            raise RuntimeError(f"Existing row has no record_id for {upsert_field}={key}")
        remote_fields = remote.get("fields", {})
        if not isinstance(remote_fields, dict):
            remote_fields = {}
        patch, changed, status_preserved = row_changes(remote_fields, record, preserve_status)
        if status_preserved:
            preserved_status += 1
        if not patch:
            unchanged += 1
            continue
        update_record(base_url, tenant_token, app_token, table_id, str(record_id), patch, timeout)
        updated += 1
        changed_counter.update(changed)

    return {
        "remote_rows_seen": len(existing),
        "remote_duplicate_upsert_keys": remote_duplicates,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "preserved_status": preserved_status,
        "changed_fields": dict(changed_counter.most_common()),
    }


def dry_run_summary(records: list[dict[str, Any]], configured: bool) -> dict[str, Any]:
    return {
        "remote_rows_seen": None,
        "remote_duplicate_upsert_keys": None,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "preserved_status": 0,
        "changed_fields": {},
        "would_get_tenant_access_token": configured,
        "would_validate_schema": configured,
        "would_sync_records": len(records),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    sync = summary["sync"]
    lines = [
        "# Feishu Bitable Topic Sync Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Mode: {summary['mode']}",
        f"Input: `{summary['input']}`",
        f"Env file: `{summary['env_file']}`",
        f"Configured: {summary['configured']}",
        f"Base URL: `{summary['base_url']}`",
        f"App ID configured: {summary['app_id_configured']}",
        f"App secret configured: {summary['app_secret_configured']}",
        f"Bitable URL configured: {summary['bitable_url_configured']}",
        f"Wiki node token configured: {summary['wiki_node_token_configured']}",
        f"Bitable app token configured: {summary['bitable_app_token_configured']}",
        f"Bitable table ID configured: {summary['bitable_table_id_configured']}",
        f"Upsert field: `{summary['upsert_field']}`",
        "",
        "## Counts",
        "",
        f"- Input records: {counts['input_records']}",
        f"- Unique records: {counts['unique_records']}",
        f"- Duplicate input records skipped: {counts['duplicate_input_records']}",
        f"- Would sync records: {sync.get('would_sync_records', counts['unique_records'])}",
        f"- Created: {sync['created']}",
        f"- Updated: {sync['updated']}",
        f"- Unchanged: {sync['unchanged']}",
        f"- Preserved remote statuses: {sync['preserved_status']}",
    ]
    if sync.get("remote_rows_seen") is not None:
        lines.append(f"- Remote rows seen: {sync['remote_rows_seen']}")
    if sync.get("remote_duplicate_upsert_keys") is not None:
        lines.append(f"- Remote duplicate upsert keys: {sync['remote_duplicate_upsert_keys']}")
    if sync.get("changed_fields"):
        lines.extend(["", "## Changed Fields", ""])
        for field, count in sync["changed_fields"].items():
            lines.append(f"- `{field}`: {count}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync topic candidates to a Feishu Bitable table.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Topic candidates JSON.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Feishu Bitable env file path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--write-env-template", action="store_true", help="Write feishu-bitable.env.example and exit.")
    parser.add_argument("--sync", action="store_true", help="Actually create/update Feishu Bitable records. Default is dry-run.")
    parser.add_argument("--overwrite-status", action="store_true", help="Overwrite remote status field on existing records.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N records.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    output_dir = resolve_workspace_path(args.output_dir)
    if args.write_env_template:
        write_env_template(output_dir / "feishu-bitable.env.example")
        write_field_mapping(output_dir / "field-mapping.md")
        print(str(output_dir / "feishu-bitable.env.example"), flush=True)
        return

    input_path = resolve_workspace_path(args.input)
    env_file = resolve_workspace_path(args.env_file)
    env_values = read_env_file(env_file)
    base_url = config_value(env_values, "FEISHU_BASE_URL", "https://open.feishu.cn")
    app_id = config_value(env_values, "FEISHU_APP_ID")
    app_secret = config_value(env_values, "FEISHU_APP_SECRET")
    bitable_url = config_value(env_values, "FEISHU_BITABLE_URL")
    url_tokens = parse_bitable_url(bitable_url)
    wiki_node_token = config_value(env_values, "FEISHU_WIKI_NODE_TOKEN", url_tokens.get("wiki_node_token", ""))
    app_token = config_value(env_values, "FEISHU_BITABLE_APP_TOKEN", url_tokens.get("app_token", ""))
    table_id = config_value(env_values, "FEISHU_BITABLE_TABLE_ID", url_tokens.get("table_id", ""))
    upsert_field = config_value(env_values, "FEISHU_UPSERT_FIELD", "candidate_id")
    page_size = int(config_value(env_values, "FEISHU_PAGE_SIZE", "500") or "500")
    configured = bool(app_id and app_secret and table_id and (app_token or wiki_node_token))

    records_raw = read_input_records(input_path, args.limit)
    records, duplicates = dedupe_records(records_raw, upsert_field)
    mode = "sync" if args.sync else "dry_run"

    if args.sync:
        if not configured:
            raise RuntimeError(
                f"FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_BITABLE_APP_TOKEN, "
                f"or FEISHU_WIKI_NODE_TOKEN, and FEISHU_BITABLE_TABLE_ID are required in {env_file}"
            )
        sync = sync_records(
            records=records,
            base_url=base_url,
            app_id=app_id,
            app_secret=app_secret,
            app_token=app_token,
            wiki_node_token=wiki_node_token,
            table_id=table_id,
            upsert_field=upsert_field,
            page_size=page_size,
            timeout=args.timeout,
            preserve_status=not args.overwrite_status,
        )
    else:
        sync = dry_run_summary(records, configured)

    summary_path = output_dir / "latest-sync-summary.json"
    markdown_path = output_dir / "latest-sync-summary.md"
    env_template_path = output_dir / "feishu-bitable.env.example"
    field_mapping_path = output_dir / "field-mapping.md"
    write_env_template(env_template_path)
    write_field_mapping(field_mapping_path)
    summary = {
        "generated_at": utc_now(),
        "mode": mode,
        "input": str(input_path),
        "env_file": str(env_file),
        "base_url": base_url,
        "configured": configured,
        "app_id_configured": bool(app_id),
        "app_secret_configured": bool(app_secret),
        "bitable_url_configured": bool(bitable_url),
        "wiki_node_token_configured": bool(wiki_node_token),
        "bitable_app_token_configured": bool(app_token),
        "bitable_table_id_configured": bool(table_id),
        "upsert_field": upsert_field,
        "preserve_status": not args.overwrite_status,
        "counts": {
            "input_records": len(records_raw),
            "unique_records": len(records),
            "duplicate_input_records": duplicates,
        },
        "sync": sync,
        "outputs": {
            "json": str(summary_path),
            "markdown": str(markdown_path),
            "env_template": str(env_template_path),
            "field_mapping": str(field_mapping_path),
        },
    }
    write_json(summary_path, summary)
    write_markdown(markdown_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
