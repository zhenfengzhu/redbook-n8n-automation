from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path("data/topic-pipeline/eligible-topic-candidates.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/topic-ingestion")

BASEROW_FIELDS: tuple[str, ...] = (
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
    ("candidate_id", "Text", "Stable candidate identifier. Use this for upsert/deduplication."),
    ("source_id", "Text", "Stable source identifier."),
    ("title", "Text", "Topic/source title."),
    ("pet_type", "Single select", "猫 / 狗 / 猫狗通用 / 兔."),
    ("topic", "Single select", "营养 / 食品安全 / 标签 / 体重 / 老年宠物 / 零食 / 补剂 / 食品加工."),
    ("source_org", "Text", "Source organization, such as FEDIAF."),
    ("source_url", "URL", "Original source URL."),
    ("source_file", "Text", "Local source PDF or Markdown path when available."),
    ("translated_path", "Text", "Local Chinese translation Markdown path."),
    ("evidence_level", "Single select", "高 / 中 / 低."),
    ("evidence_excerpt", "Long text", "Short source excerpt used as evidence."),
    ("xhs_angle", "Long text", "Xiaohongshu angle."),
    ("hook", "Text", "Cover/title hook candidate."),
    ("summary", "Long text", "Chinese summary."),
    ("risk_level", "Single select", "低 / 中 / 高."),
    ("risk_reason", "Long text", "Why this risk level was assigned."),
    ("vet_boundary_required", "Boolean", "Whether veterinary consultation boundary is required."),
    ("score", "Number", "Composite topic score."),
    ("status", "Single select", "Initial status, usually 待评估."),
    ("content_hash", "Text", "Hash of translated source body for change detection."),
    ("created_at", "Date", "Candidate creation time from topic generation."),
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"Line {line_number} is not a JSON object: {path}")
            records.append(value)
    return records


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def baserow_record(candidate: dict[str, Any]) -> dict[str, Any]:
    return {field: candidate.get(field, "") for field in BASEROW_FIELDS}


def csv_record(candidate: dict[str, Any]) -> dict[str, str]:
    return {field: text_value(candidate.get(field, "")) for field in BASEROW_FIELDS}


def dedupe_by_candidate_id(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        candidate_id = str(record.get("candidate_id", ""))
        if not candidate_id:
            raise RuntimeError("Missing required candidate_id.")
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(record)
    return deduped


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=BASEROW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(csv_record(record))


def write_field_mapping(path: Path) -> None:
    lines = [
        "# Topic Candidate Field Mapping",
        "",
        "Use `candidate_id` as the primary upsert/deduplication key. In Baserow, create these fields before importing CSV or writing via API.",
        "",
        "| Field | Suggested type | Notes |",
        "|---|---|---|",
    ]
    for field, field_type, notes in FIELD_MAPPING:
        lines.append(f"| `{field}` | {field_type} | {notes} |")
    lines.extend(
        [
            "",
            "## n8n Usage",
            "",
            "Read `n8n-items.json`. It is an array of n8n-compatible items shaped as `{ \"json\": { ...candidate fields... } }`.",
            "",
            "Suggested flow:",
            "",
            "```text",
            "Read File",
            "  -> Parse JSON",
            "  -> Split Out items",
            "  -> Baserow/HTTP upsert by candidate_id",
            "  -> Notification summary",
            "```",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export eligible topic candidates for Baserow and n8n ingestion.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Eligible topic candidates JSONL.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for ingestion outputs.")
    parser.add_argument("--limit", type=int, default=None, help="Export only the first N records.")
    args = parser.parse_args()

    input_path = resolve_workspace_path(args.input)
    output_dir = resolve_workspace_path(args.output_dir)
    records = dedupe_by_candidate_id(read_jsonl(input_path))
    if args.limit is not None:
        records = records[: args.limit]

    baserow_records = [baserow_record(record) for record in records]
    n8n_items = [{"json": record} for record in baserow_records]

    csv_path = output_dir / "baserow-topic-candidates.csv"
    json_path = output_dir / "baserow-topic-candidates.json"
    n8n_path = output_dir / "n8n-items.json"
    mapping_path = output_dir / "field-mapping.md"
    summary_path = output_dir / "summary.json"

    write_csv(csv_path, baserow_records)
    write_json(json_path, baserow_records)
    write_json(n8n_path, n8n_items)
    write_field_mapping(mapping_path)

    summary = {
        "generated_at": utc_now(),
        "input": str(input_path),
        "outputs": {
            "baserow_csv": str(csv_path),
            "baserow_json": str(json_path),
            "n8n_items": str(n8n_path),
            "field_mapping": str(mapping_path),
            "summary": str(summary_path),
        },
        "counts": {
            "records": len(records),
            "fields": len(BASEROW_FIELDS),
        },
        "dedupe_key": "candidate_id",
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
