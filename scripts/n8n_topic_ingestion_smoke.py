from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "topic-ingestion" / "n8n-items.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "n8n-topic-smoke"


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON array: {path}")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("json"), dict):
            raise RuntimeError(f"Item {index} is not shaped as {{\"json\": object}}.")
        records.append(item["json"])
    return records


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# n8n Topic Ingestion Smoke Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Input: `{summary['input']}`",
        "",
        "## Counts",
        "",
        f"- Records: {summary['counts']['records']}",
        f"- Veterinary boundary required: {summary['counts']['vet_boundary_required']}",
        "",
        "## Topics",
        "",
    ]
    for topic, count in summary["topics"].items():
        lines.append(f"- {topic}: {count}")
    lines.extend(["", "## Risks", ""])
    for risk, count in summary["risks"].items():
        lines.append(f"- {risk}: {count}")
    lines.extend(["", "## Preview", ""])
    for record in summary["preview"]:
        lines.append(f"- [{record['score']}] {record['title']} / {record['topic']} / {record['risk_level']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_summary(input_path: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    topics = Counter(str(record.get("topic", "unknown")) for record in records)
    risks = Counter(str(record.get("risk_level", "unknown")) for record in records)
    preview_records = sorted(records, key=lambda record: int(record.get("score", 0)), reverse=True)[:10]
    return {
        "generated_at": utc_now(),
        "input": str(input_path),
        "counts": {
            "records": len(records),
            "vet_boundary_required": sum(1 for record in records if record.get("vet_boundary_required") is True),
        },
        "topics": dict(topics.most_common()),
        "risks": dict(risks.most_common()),
        "preview": [
            {
                "candidate_id": record.get("candidate_id", ""),
                "title": record.get("title", ""),
                "topic": record.get("topic", ""),
                "risk_level": record.get("risk_level", ""),
                "score": record.get("score", 0),
                "hook": record.get("hook", ""),
            }
            for record in preview_records
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test n8n topic ingestion input and write a local summary.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="n8n-items.json path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for smoke-test summary outputs.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    records = read_items(input_path)
    summary = build_summary(input_path, records)
    summary["outputs"] = {
        "json": str(output_dir / "latest-summary.json"),
        "markdown": str(output_dir / "latest-summary.md"),
    }
    write_json(output_dir / "latest-summary.json", summary)
    write_markdown(output_dir / "latest-summary.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
