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
DEFAULT_ENV_FILE = PROJECT_ROOT / "data" / "baserow" / "baserow.env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "baserow"

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
    return os.environ.get(key) or env_values.get(key, default)


def write_env_template(path: Path) -> None:
    lines = [
        "# Copy this file to baserow.env and fill real values. Do not commit real tokens.",
        "BASEROW_API_URL=https://api.baserow.io",
        "BASEROW_TOKEN=",
        "BASEROW_TABLE_ID=",
        "BASEROW_UPSERT_FIELD=candidate_id",
        "BASEROW_READ_PAGE_SIZE=200",
    ]
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


def baserow_request(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Baserow HTTP {error.code}: {response_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Baserow request failed: {error.reason}") from error


def build_url(api_url: str, path: str, query: dict[str, str | int] | None = None) -> str:
    base = api_url.rstrip("/")
    url = f"{base}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def fetch_table_fields(api_url: str, token: str, table_id: str, timeout: int) -> set[str]:
    url = build_url(api_url, f"/api/database/fields/table/{table_id}/")
    data = baserow_request("GET", url, token, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Baserow field response.")
    return {str(field.get("name", "")) for field in data if isinstance(field, dict)}


def fetch_existing_rows(
    api_url: str,
    token: str,
    table_id: str,
    upsert_field: str,
    page_size: int,
    timeout: int,
) -> tuple[dict[str, dict[str, Any]], int]:
    existing: dict[str, dict[str, Any]] = {}
    duplicates = 0
    page = 1
    while True:
        url = build_url(
            api_url,
            f"/api/database/rows/table/{table_id}/",
            {"user_field_names": "true", "size": page_size, "page": page},
        )
        data = baserow_request("GET", url, token, timeout=timeout)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise RuntimeError("Unexpected Baserow rows response.")
        for row in data["results"]:
            if not isinstance(row, dict):
                continue
            key = cell_text(row.get(upsert_field)).strip()
            if not key:
                continue
            if key in existing:
                duplicates += 1
                continue
            existing[key] = row
        if not data.get("next"):
            break
        page += 1
    return existing, duplicates


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "name"):
            if key in value:
                return "" if value[key] is None else str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
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
    remote_row: dict[str, Any],
    local_record: dict[str, Any],
    preserve_status: bool,
) -> tuple[dict[str, Any], list[str], bool]:
    patch: dict[str, Any] = {}
    changed: list[str] = []
    preserved_status = False
    for field in TOPIC_FIELDS:
        if preserve_status and field == "status" and cell_text(remote_row.get("status")):
            preserved_status = True
            continue
        remote_value = remote_row.get(field)
        local_value = local_record[field]
        if not values_equal(field, remote_value, local_value):
            patch[field] = local_value
            changed.append(field)
    return patch, changed, preserved_status


def create_row(api_url: str, token: str, table_id: str, record: dict[str, Any], timeout: int) -> Any:
    url = build_url(api_url, f"/api/database/rows/table/{table_id}/", {"user_field_names": "true"})
    return baserow_request("POST", url, token, record, timeout=timeout)


def update_row(
    api_url: str,
    token: str,
    table_id: str,
    row_id: int | str,
    patch: dict[str, Any],
    timeout: int,
) -> Any:
    url = build_url(
        api_url,
        f"/api/database/rows/table/{table_id}/{row_id}/",
        {"user_field_names": "true"},
    )
    return baserow_request("PATCH", url, token, patch, timeout=timeout)


def sync_records(
    records: list[dict[str, Any]],
    api_url: str,
    token: str,
    table_id: str,
    upsert_field: str,
    page_size: int,
    timeout: int,
    preserve_status: bool,
) -> dict[str, Any]:
    fields = fetch_table_fields(api_url, token, table_id, timeout)
    missing_fields = sorted(set(TOPIC_FIELDS) - fields)
    if missing_fields:
        raise RuntimeError(
            "Baserow table is missing required fields: " + ", ".join(missing_fields)
        )

    existing, remote_duplicates = fetch_existing_rows(
        api_url, token, table_id, upsert_field, page_size, timeout
    )
    created = 0
    updated = 0
    unchanged = 0
    preserved_status = 0
    changed_counter: Counter[str] = Counter()

    for record in records:
        key = str(record[upsert_field])
        remote_row = existing.get(key)
        if remote_row is None:
            create_row(api_url, token, table_id, record, timeout)
            created += 1
            continue

        row_id = remote_row.get("id")
        if row_id is None:
            raise RuntimeError(f"Existing row has no id for {upsert_field}={key}")
        patch, changed, status_preserved = row_changes(remote_row, record, preserve_status)
        if status_preserved:
            preserved_status += 1
        if not patch:
            unchanged += 1
            continue
        update_row(api_url, token, table_id, row_id, patch, timeout)
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
        "# Baserow Topic Sync Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Mode: {summary['mode']}",
        f"Input: `{summary['input']}`",
        f"Env file: `{summary['env_file']}`",
        f"Configured: {summary['configured']}",
        f"API URL: `{summary['api_url']}`",
        f"Table ID configured: {summary['table_id_configured']}",
        f"Token configured: {summary['token_configured']}",
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
    parser = argparse.ArgumentParser(description="Sync topic candidates to a Baserow table.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Baserow topic candidates JSON.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Baserow env file path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--write-env-template", action="store_true", help="Write baserow.env.example and exit.")
    parser.add_argument("--sync", action="store_true", help="Actually create/update Baserow rows. Default is dry-run.")
    parser.add_argument("--overwrite-status", action="store_true", help="Overwrite remote status field on existing rows.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N records.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    output_dir = resolve_workspace_path(args.output_dir)
    if args.write_env_template:
        write_env_template(output_dir / "baserow.env.example")
        print(str(output_dir / "baserow.env.example"), flush=True)
        return

    input_path = resolve_workspace_path(args.input)
    env_file = resolve_workspace_path(args.env_file)
    env_values = read_env_file(env_file)
    api_url = config_value(env_values, "BASEROW_API_URL", "https://api.baserow.io")
    token = config_value(env_values, "BASEROW_TOKEN")
    table_id = config_value(env_values, "BASEROW_TABLE_ID")
    upsert_field = config_value(env_values, "BASEROW_UPSERT_FIELD", "candidate_id")
    page_size = int(config_value(env_values, "BASEROW_READ_PAGE_SIZE", "200") or "200")
    configured = bool(token and table_id)

    records_raw = read_input_records(input_path, args.limit)
    records, duplicates = dedupe_records(records_raw, upsert_field)
    mode = "sync" if args.sync else "dry_run"

    if args.sync:
        if not configured:
            raise RuntimeError(f"BASEROW_TOKEN and BASEROW_TABLE_ID are required in {env_file}")
        sync = sync_records(
            records=records,
            api_url=api_url,
            token=token,
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
    write_env_template(output_dir / "baserow.env.example")
    summary = {
        "generated_at": utc_now(),
        "mode": mode,
        "input": str(input_path),
        "env_file": str(env_file),
        "api_url": api_url,
        "configured": configured,
        "token_configured": bool(token),
        "table_id_configured": bool(table_id),
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
            "env_template": str(output_dir / "baserow.env.example"),
        },
    }
    write_json(summary_path, summary)
    write_markdown(markdown_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
