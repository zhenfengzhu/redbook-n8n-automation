from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "topic-ingestion" / "n8n-items.json"
DEFAULT_DB = PROJECT_ROOT / "data" / "topic-database" / "topic-candidates.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "topic-database"

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

TEXT_FIELDS = tuple(field for field in TOPIC_FIELDS if field not in {"vet_boundary_required", "score"})


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


def read_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON array: {path}")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, dict) and isinstance(item.get("json"), dict):
            record = item["json"]
        elif isinstance(item, dict):
            record = item
        else:
            raise RuntimeError(f"Item {index} is not a JSON object.")
        records.append(normalize_record(record, index))
    return records


def normalize_record(record: dict[str, Any], index: int) -> dict[str, Any]:
    candidate_id = str(record.get("candidate_id", "")).strip()
    if not candidate_id:
        raise RuntimeError(f"Item {index} is missing candidate_id.")

    normalized: dict[str, Any] = {}
    for field in TOPIC_FIELDS:
        value = record.get(field)
        if field == "vet_boundary_required":
            normalized[field] = 1 if value is True or str(value).lower() == "true" else 0
        elif field == "score":
            normalized[field] = int(value or 0)
        else:
            normalized[field] = "" if value is None else str(value)
    return normalized


def dedupe_by_candidate_id(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for record in records:
        candidate_id = record["candidate_id"]
        if candidate_id in deduped:
            duplicates += 1
        deduped[candidate_id] = record
    return list(deduped.values()), duplicates


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma foreign_keys = on")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    text_columns = ",\n        ".join(f"{field} text not null default ''" for field in TEXT_FIELDS)
    conn.execute(
        f"""
        create table if not exists topic_candidates (
            {text_columns},
            vet_boundary_required integer not null default 0,
            score integer not null default 0,
            raw_json text not null,
            first_seen_at text not null,
            last_seen_at text not null,
            imported_at text not null,
            updated_at text not null,
            import_count integer not null default 1,
            primary key (candidate_id)
        )
        """
    )
    conn.execute("create index if not exists idx_topic_candidates_status on topic_candidates(status)")
    conn.execute("create index if not exists idx_topic_candidates_topic on topic_candidates(topic)")
    conn.execute("create index if not exists idx_topic_candidates_risk on topic_candidates(risk_level)")
    conn.execute("create index if not exists idx_topic_candidates_score on topic_candidates(score)")


def row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {field: row[field] for field in TOPIC_FIELDS}


def changed_fields(existing: sqlite3.Row, incoming: dict[str, Any]) -> list[str]:
    current = row_payload(existing)
    return [field for field in TOPIC_FIELDS if current[field] != incoming[field]]


def insert_record(conn: sqlite3.Connection, record: dict[str, Any], now: str) -> None:
    values = dict(record)
    values.update(
        {
            "raw_json": json.dumps(record, ensure_ascii=False, sort_keys=True),
            "first_seen_at": now,
            "last_seen_at": now,
            "imported_at": now,
            "updated_at": now,
            "import_count": 1,
        }
    )
    columns = list(TOPIC_FIELDS) + [
        "raw_json",
        "first_seen_at",
        "last_seen_at",
        "imported_at",
        "updated_at",
        "import_count",
    ]
    placeholders = ", ".join(":" + column for column in columns)
    conn.execute(
        f"insert into topic_candidates ({', '.join(columns)}) values ({placeholders})",
        values,
    )


def update_record(conn: sqlite3.Connection, record: dict[str, Any], now: str, has_content_changes: bool) -> None:
    values = dict(record)
    values.update(
        {
            "raw_json": json.dumps(record, ensure_ascii=False, sort_keys=True),
            "last_seen_at": now,
            "updated_at": now,
        }
    )
    assignments = [f"{field} = :{field}" for field in TOPIC_FIELDS]
    assignments.extend(
        [
            "raw_json = :raw_json",
            "last_seen_at = :last_seen_at",
            "import_count = import_count + 1",
        ]
    )
    if has_content_changes:
        assignments.append("updated_at = :updated_at")
    conn.execute(
        f"update topic_candidates set {', '.join(assignments)} where candidate_id = :candidate_id",
        values,
    )


def upsert_records(conn: sqlite3.Connection, records: list[dict[str, Any]], preserve_status: bool) -> dict[str, Any]:
    now = utc_now()
    inserted = 0
    updated = 0
    unchanged = 0
    preserved_status = 0
    changed_counter: Counter[str] = Counter()

    with conn:
        for record in records:
            existing = conn.execute(
                "select * from topic_candidates where candidate_id = ?",
                (record["candidate_id"],),
            ).fetchone()
            if existing is None:
                insert_record(conn, record, now)
                inserted += 1
                continue

            if preserve_status and existing["status"] and existing["status"] != record["status"]:
                record = dict(record)
                record["status"] = existing["status"]
                preserved_status += 1

            changes = changed_fields(existing, record)
            for field in changes:
                changed_counter[field] += 1

            if changes:
                updated += 1
            else:
                unchanged += 1
            update_record(conn, record, now, bool(changes))

    total = conn.execute("select count(*) from topic_candidates").fetchone()[0]
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "preserved_status": preserved_status,
        "changed_fields": dict(changed_counter.most_common()),
        "db_total": total,
    }


def database_counters(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "select topic, risk_level, status, vet_boundary_required from topic_candidates"
    ).fetchall()
    return {
        "topics": dict(Counter(row["topic"] for row in rows).most_common()),
        "risks": dict(Counter(row["risk_level"] for row in rows).most_common()),
        "statuses": dict(Counter(row["status"] for row in rows).most_common()),
        "vet_boundary_required": sum(1 for row in rows if row["vet_boundary_required"] == 1),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    database = summary["database"]
    lines = [
        "# Topic Database Upsert Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Input: `{summary['input']}`",
        f"Database: `{database['path']}`",
        "",
        "## Upsert",
        "",
        f"- Input records: {counts['input_records']}",
        f"- Unique records: {counts['unique_records']}",
        f"- Duplicate input records skipped: {counts['duplicate_input_records']}",
        f"- Inserted: {counts['inserted']}",
        f"- Updated: {counts['updated']}",
        f"- Unchanged: {counts['unchanged']}",
        f"- Preserved manual statuses: {counts['preserved_status']}",
        f"- Database total: {counts['db_total']}",
        "",
        "## Database Counters",
        "",
        f"- Veterinary boundary required: {database['vet_boundary_required']}",
        "",
        "### Statuses",
        "",
    ]
    for status, count in database["statuses"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "### Topics", ""])
    for topic, count in database["topics"].items():
        lines.append(f"- {topic}: {count}")
    lines.extend(["", "### Risks", ""])
    for risk, count in database["risks"].items():
        lines.append(f"- {risk}: {count}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_baserow_env_template(path: Path) -> None:
    lines = [
        "# Copy to a private .env file when Baserow is ready. Do not commit real tokens.",
        "BASEROW_API_URL=https://api.baserow.io",
        "BASEROW_TOKEN=",
        "BASEROW_TABLE_ID=",
        "BASEROW_UPSERT_FIELD=candidate_id",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upsert topic candidates into a local SQLite topic database.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="n8n-items.json or JSON array of topic records.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for upsert summary outputs.")
    parser.add_argument(
        "--overwrite-status",
        action="store_true",
        help="Overwrite existing status values. By default, manual status changes are preserved.",
    )
    args = parser.parse_args()

    input_path = resolve_workspace_path(args.input)
    db_path = resolve_workspace_path(args.db)
    output_dir = resolve_workspace_path(args.output_dir)

    input_records = read_records(input_path)
    records, duplicates = dedupe_by_candidate_id(input_records)
    conn = connect_database(db_path)
    try:
        ensure_schema(conn)
        upsert_counts = upsert_records(conn, records, preserve_status=not args.overwrite_status)
        database = database_counters(conn)
    finally:
        conn.close()

    summary_path = output_dir / "latest-upsert-summary.json"
    markdown_path = output_dir / "latest-upsert-summary.md"
    baserow_env_path = output_dir / "baserow.env.example"
    summary = {
        "generated_at": utc_now(),
        "input": str(input_path),
        "mode": "sqlite",
        "dedupe_key": "candidate_id",
        "counts": {
            "input_records": len(input_records),
            "unique_records": len(records),
            "duplicate_input_records": duplicates,
            **upsert_counts,
        },
        "database": {
            "path": str(db_path),
            **database,
        },
        "baserow": {
            "env_template": str(baserow_env_path),
            "upsert_field": "candidate_id",
            "status": "not_configured",
        },
        "outputs": {
            "json": str(summary_path),
            "markdown": str(markdown_path),
            "baserow_env_template": str(baserow_env_path),
        },
    }
    write_json(summary_path, summary)
    write_markdown(markdown_path, summary)
    write_baserow_env_template(baserow_env_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
