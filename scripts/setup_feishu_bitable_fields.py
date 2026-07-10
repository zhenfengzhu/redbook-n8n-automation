from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sync_feishu_bitable_topics import (
    DEFAULT_ENV_FILE,
    DEFAULT_OUTPUT_DIR,
    TOPIC_FIELDS,
    build_url,
    config_value,
    feishu_request,
    fetch_table_fields,
    get_tenant_access_token,
    parse_bitable_url,
    read_env_file,
    resolve_wiki_bitable_app_token,
    resolve_workspace_path,
)


FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {
    field: {"field_name": field, "type": 1, "ui_type": "Text"} for field in TOPIC_FIELDS
}
FIELD_DEFINITIONS["score"] = {"field_name": "score", "type": 2, "ui_type": "Number"}
FIELD_DEFINITIONS["vet_boundary_required"] = {
    "field_name": "vet_boundary_required",
    "type": 7,
    "ui_type": "Checkbox",
}


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_field(
    base_url: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    field_definition: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    request_body = {key: value for key, value in field_definition.items() if key != "ui_type"}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return feishu_request(
                "POST",
                base_url,
                tenant_token,
                path,
                body=request_body,
                timeout=timeout,
            )
        except RuntimeError as error:
            last_error = error
            text = str(error)
            if "1254291" not in text and "TooManyRequest" not in text and "Write conflict" not in text:
                raise
            time.sleep(attempt * 1.5)
    raise RuntimeError(f"Failed to create field after retries: {field_definition['field_name']}") from last_error


def error_hints(error_text: str) -> list[str]:
    if "91403" in error_text or "Forbidden" in error_text:
        return [
            "The Feishu app can reach this Bitable, but the current access identity does not have document edit/manage permission for field creation.",
            "In the Bitable page, use the top-right menu to add this custom app as a document app/collaborator and grant manage permission.",
            "If advanced permissions are enabled for the Bitable, grant the app manage permission for the target table/base.",
            "In Feishu Open Platform, keep Bitable read/write API permissions enabled for the app, then publish or make the permission change effective.",
        ]
    if "99991672" in error_text or "permission" in error_text.lower():
        return [
            "The Feishu app is missing an API scope required by this endpoint.",
            "Open Feishu Open Platform permission management, add the scope named in the API error, then publish or make the permission change effective.",
        ]
    return []


def resolve_config(env_file: Path, timeout: int) -> dict[str, Any]:
    env_values = read_env_file(env_file)
    base_url = config_value(env_values, "FEISHU_BASE_URL", "https://open.feishu.cn")
    app_id = config_value(env_values, "FEISHU_APP_ID")
    app_secret = config_value(env_values, "FEISHU_APP_SECRET")
    bitable_url = config_value(env_values, "FEISHU_BITABLE_URL")
    url_tokens = parse_bitable_url(bitable_url)
    wiki_node_token = config_value(env_values, "FEISHU_WIKI_NODE_TOKEN", url_tokens.get("wiki_node_token", ""))
    app_token = config_value(env_values, "FEISHU_BITABLE_APP_TOKEN", url_tokens.get("app_token", ""))
    table_id = config_value(env_values, "FEISHU_BITABLE_TABLE_ID", url_tokens.get("table_id", ""))

    configured = bool(app_id and app_secret and table_id and (app_token or wiki_node_token))
    if not configured:
        raise RuntimeError(
            f"FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_BITABLE_URL or token fields are required in {env_file}"
        )

    tenant_token = get_tenant_access_token(base_url, app_id, app_secret, timeout)
    if not app_token and wiki_node_token:
        app_token = resolve_wiki_bitable_app_token(base_url, tenant_token, wiki_node_token, timeout)
    if not app_token:
        raise RuntimeError("FEISHU_BITABLE_APP_TOKEN is missing and no FEISHU_WIKI_NODE_TOKEN was provided.")

    return {
        "base_url": base_url,
        "tenant_token": tenant_token,
        "app_token": app_token,
        "table_id": table_id,
        "app_id_configured": bool(app_id),
        "app_secret_configured": bool(app_secret),
        "bitable_url_configured": bool(bitable_url),
        "wiki_node_token_configured": bool(wiki_node_token),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    lines = [
        "# Feishu Bitable Field Setup Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Mode: {summary['mode']}",
        f"Env file: `{summary['env_file']}`",
        f"App token resolved: {summary['app_token_resolved']}",
        f"Table ID: `{summary['table_id']}`",
        "",
        "## Counts",
        "",
        f"- Existing fields before: {counts['existing_fields_before']}",
        f"- Required fields: {counts['required_fields']}",
        f"- Missing fields before: {counts['missing_fields_before']}",
        f"- Created fields: {counts['created_fields']}",
        f"- Failed fields: {counts['failed_fields']}",
        f"- Existing fields after: {counts['existing_fields_after']}",
        f"- Missing fields after: {counts['missing_fields_after']}",
    ]
    if summary["missing_fields_after"]:
        lines.extend(["", "## Missing Fields", ""])
        for field in summary["missing_fields_after"]:
            lines.append(f"- `{field}`")
    if summary["created_fields"]:
        lines.extend(["", "## Created Fields", ""])
        for field in summary["created_fields"]:
            lines.append(f"- `{field}`")
    if summary["field_errors"]:
        lines.extend(["", "## Field Errors", ""])
        for error in summary["field_errors"]:
            lines.append(f"- `{error['field']}`: {error['error']}")
            for hint in error.get("hints", []):
                lines.append(f"  - {hint}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create required Feishu Bitable topic candidate fields.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Feishu Bitable env file path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--create", action="store_true", help="Actually create missing fields. Default is dry-run.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    env_file = resolve_workspace_path(args.env_file)
    output_dir = resolve_workspace_path(args.output_dir)
    config = resolve_config(env_file, args.timeout)

    existing_before = fetch_table_fields(
        config["base_url"],
        config["tenant_token"],
        config["app_token"],
        config["table_id"],
        args.timeout,
    )
    missing_before = [field for field in TOPIC_FIELDS if field not in existing_before]
    created_fields: list[str] = []
    field_errors: list[dict[str, Any]] = []

    if args.create:
        for field in missing_before:
            try:
                create_field(
                    config["base_url"],
                    config["tenant_token"],
                    config["app_token"],
                    config["table_id"],
                    FIELD_DEFINITIONS[field],
                    args.timeout,
                )
                created_fields.append(field)
            except RuntimeError as error:
                error_text = str(error)
                field_errors.append(
                    {
                        "field": field,
                        "error": error_text,
                        "hints": error_hints(error_text),
                    }
                )
                break
            time.sleep(0.3)

    existing_after = fetch_table_fields(
        config["base_url"],
        config["tenant_token"],
        config["app_token"],
        config["table_id"],
        args.timeout,
    )
    missing_after = [field for field in TOPIC_FIELDS if field not in existing_after]

    summary_path = output_dir / "latest-field-setup-summary.json"
    markdown_path = output_dir / "latest-field-setup-summary.md"
    summary = {
        "generated_at": utc_now(),
        "mode": "create" if args.create else "dry_run",
        "env_file": str(env_file),
        "base_url": config["base_url"],
        "app_token_resolved": bool(config["app_token"]),
        "table_id": config["table_id"],
        "counts": {
            "existing_fields_before": len(existing_before),
            "required_fields": len(TOPIC_FIELDS),
            "missing_fields_before": len(missing_before),
            "created_fields": len(created_fields),
            "failed_fields": len(field_errors),
            "existing_fields_after": len(existing_after),
            "missing_fields_after": len(missing_after),
        },
        "existing_fields_before": sorted(existing_before),
        "missing_fields_before": missing_before,
        "created_fields": created_fields,
        "field_errors": field_errors,
        "existing_fields_after": sorted(existing_after),
        "missing_fields_after": missing_after,
        "outputs": {
            "json": str(summary_path),
            "markdown": str(markdown_path),
        },
    }
    write_json(summary_path, summary)
    write_markdown(markdown_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if field_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
