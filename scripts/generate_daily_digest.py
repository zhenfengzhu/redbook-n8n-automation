from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "topic-database" / "topic-candidates.sqlite"
DEFAULT_REVIEW_QUEUE = PROJECT_ROOT / "data" / "review-portal" / "review-queue.json"
DEFAULT_REVIEW_ACTIONS = PROJECT_ROOT / "data" / "review-actions" / "latest-apply-summary.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "daily-digest"
TOP_REVIEW_LIMIT = 8


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def china_now() -> datetime:
    china_tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(timezone.utc).astimezone(china_tz)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def connect_database(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RuntimeError(f"Topic database does not exist: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def database_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        select
            candidate_id,
            title,
            topic,
            hook,
            risk_level,
            status,
            vet_boundary_required,
            score,
            source_url,
            translated_path
        from topic_candidates
        order by score desc, vet_boundary_required desc, candidate_id asc
        """
    ).fetchall()
    records = [dict(row) for row in rows]
    return {
        "total": len(records),
        "statuses": dict(Counter(record["status"] for record in records).most_common()),
        "topics": dict(Counter(record["topic"] for record in records).most_common()),
        "risks": dict(Counter(record["risk_level"] for record in records).most_common()),
        "vet_boundary_required": sum(1 for record in records if record["vet_boundary_required"] == 1),
        "top_review_items": [
            {
                **record,
                "vet_boundary_required": bool(record["vet_boundary_required"]),
            }
            for record in records
            if record["status"] in {"待评估", "人工复核", "可写"}
        ][:TOP_REVIEW_LIMIT],
    }


def build_summary(
    db_path: Path,
    review_queue_path: Path,
    review_actions_path: Path,
    output_dir: Path,
    snapshot: dict[str, Any],
    review_queue: dict[str, Any],
    review_actions: dict[str, Any],
) -> dict[str, Any]:
    now = china_now()
    date_key = now.strftime("%Y-%m-%d")
    latest_md = output_dir / "latest-digest.md"
    latest_json = output_dir / "latest-digest.json"
    archive_md = output_dir / f"{date_key}-digest.md"
    review_counts = review_queue.get("summary", {}).get("counts", {})
    action_counts = review_actions.get("counts", {})
    return {
        "generated_at": utc_now(),
        "local_generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": date_key,
        "database_path": str(db_path),
        "review_queue": str(review_queue_path),
        "review_actions": str(review_actions_path),
        "counts": {
            "database_total": snapshot["total"],
            "review_queue_records": review_counts.get("queue_records", 0),
            "review_queue_vet_boundary_required": review_counts.get("queue_vet_boundary_required", 0),
            "review_actions_rows": action_counts.get("action_rows", 0),
            "review_actions_pending_blanks": action_counts.get("skipped_blank_next_status", 0),
        },
        "database": {
            "statuses": snapshot["statuses"],
            "topics": snapshot["topics"],
            "risks": snapshot["risks"],
            "vet_boundary_required": snapshot["vet_boundary_required"],
        },
        "top_review_items": snapshot["top_review_items"],
        "outputs": {
            "latest_markdown": str(latest_md),
            "latest_json": str(latest_json),
            "archive_markdown": str(archive_md),
        },
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    database = summary["database"]
    lines = [
        f"# RedBook 审核日报 {summary['date']}",
        "",
        f"生成时间：{summary['local_generated_at']}",
        "",
        "## 今日概览",
        "",
        f"- 选题库总数：{counts['database_total']}",
        f"- 审核队列：{counts['review_queue_records']}",
        f"- 需兽医边界提示：{counts['review_queue_vet_boundary_required']}",
        f"- 审核动作表行数：{counts['review_actions_rows']}",
        f"- 未填写 next_status：{counts['review_actions_pending_blanks']}",
        "",
        "## 状态分布",
        "",
    ]
    for status, count in database["statuses"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## 主题分布", ""])
    for topic, count in database["topics"].items():
        lines.append(f"- {topic}: {count}")
    lines.extend(["", "## 风险分布", ""])
    for risk, count in database["risks"].items():
        lines.append(f"- {risk}: {count}")
    lines.extend(["", "## 优先审核候选", ""])
    for item in summary["top_review_items"]:
        boundary = "需兽医边界" if item["vet_boundary_required"] else "常规边界"
        lines.append(
            f"- [{item['score']}] {item['hook']} | {item['topic']} | {item['risk_level']} | {item['status']} | {boundary} | `{item['candidate_id']}`"
        )
    lines.extend(
        [
            "",
            "## 本地入口",
            "",
            "- 审核页面：`D:\\AUnityProject\\RedBook\\data\\review-portal\\index.html`",
            "- 审核动作 CSV：`D:\\AUnityProject\\RedBook\\data\\review-portal\\review-actions-template.csv`",
            "- 状态回写 dry-run：`python scripts\\apply_review_actions.py --dry-run`",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a local daily digest for RedBook topic review.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite topic database path.")
    parser.add_argument("--review-queue", default=str(DEFAULT_REVIEW_QUEUE), help="Review queue JSON path.")
    parser.add_argument("--review-actions", default=str(DEFAULT_REVIEW_ACTIONS), help="Review actions summary JSON path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for digest outputs.")
    args = parser.parse_args()

    db_path = resolve_workspace_path(args.db)
    review_queue_path = resolve_workspace_path(args.review_queue)
    review_actions_path = resolve_workspace_path(args.review_actions)
    output_dir = resolve_workspace_path(args.output_dir)
    conn = connect_database(db_path)
    try:
        snapshot = database_snapshot(conn)
    finally:
        conn.close()

    summary = build_summary(
        db_path=db_path,
        review_queue_path=review_queue_path,
        review_actions_path=review_actions_path,
        output_dir=output_dir,
        snapshot=snapshot,
        review_queue=read_json(review_queue_path),
        review_actions=read_json(review_actions_path),
    )
    write_json(output_dir / "latest-digest.json", summary)
    write_markdown(output_dir / "latest-digest.md", summary)
    write_markdown(output_dir / f"{summary['date']}-digest.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
