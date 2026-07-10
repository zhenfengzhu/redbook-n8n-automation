from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "topic-database" / "topic-candidates.sqlite"
DEFAULT_ACTIONS = PROJECT_ROOT / "data" / "review-portal" / "review-actions-template.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "review-actions"
VALID_STATUSES = ("待评估", "人工复核", "可写", "已写", "放弃")


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


def connect_database(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RuntimeError(f"Topic database does not exist: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists topic_review_actions (
            id integer primary key autoincrement,
            candidate_id text not null,
            previous_status text not null,
            next_status text not null,
            review_note text not null default '',
            action_source text not null,
            applied_at text not null,
            foreign key (candidate_id) references topic_candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "create index if not exists idx_topic_review_actions_candidate on topic_review_actions(candidate_id)"
    )


def read_actions(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        required = {"candidate_id", "current_status", "next_status", "review_note"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"Missing required columns in {path}: {', '.join(sorted(missing))}")
        actions: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            actions.append(
                {
                    "row_number": str(row_number),
                    "candidate_id": str(row.get("candidate_id", "")).strip(),
                    "current_status": str(row.get("current_status", "")).strip(),
                    "next_status": str(row.get("next_status", "")).strip(),
                    "review_note": str(row.get("review_note", "")).strip(),
                }
            )
    return actions


def database_statuses(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("select status, count(*) as count from topic_candidates group by status").fetchall()
    return {row["status"]: row["count"] for row in rows}


def candidate_status(conn: sqlite3.Connection, candidate_id: str) -> str | None:
    row = conn.execute(
        "select status from topic_candidates where candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return None if row is None else str(row["status"])


def update_status(
    conn: sqlite3.Connection,
    candidate_id: str,
    previous_status: str,
    next_status: str,
    review_note: str,
    action_source: str,
    now: str,
) -> None:
    conn.execute(
        """
        update topic_candidates
        set status = ?, updated_at = ?
        where candidate_id = ?
        """,
        (next_status, now, candidate_id),
    )
    conn.execute(
        """
        insert into topic_review_actions (
            candidate_id,
            previous_status,
            next_status,
            review_note,
            action_source,
            applied_at
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (candidate_id, previous_status, next_status, review_note, action_source, now),
    )


def apply_actions(
    conn: sqlite3.Connection,
    actions: list[dict[str, str]],
    action_source: Path,
    dry_run: bool,
    allow_stale_current_status: bool,
) -> dict[str, Any]:
    now = utc_now()
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    with conn:
        if not dry_run:
            ensure_schema(conn)
        before_statuses = database_statuses(conn)
        for action in actions:
            row_result: dict[str, Any] = {
                "row_number": int(action["row_number"]),
                "candidate_id": action["candidate_id"],
                "current_status": action["current_status"],
                "next_status": action["next_status"],
                "review_note": action["review_note"],
            }
            candidate_id = action["candidate_id"]
            next_status = action["next_status"]
            expected_status = action["current_status"]

            if not candidate_id:
                row_result["result"] = "skipped_missing_candidate_id"
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            if not next_status:
                row_result["result"] = "skipped_blank_next_status"
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            if next_status not in VALID_STATUSES:
                row_result["result"] = "skipped_invalid_next_status"
                row_result["valid_statuses"] = list(VALID_STATUSES)
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            actual_status = candidate_status(conn, candidate_id)
            row_result["database_status"] = actual_status
            if actual_status is None:
                row_result["result"] = "skipped_missing_candidate"
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            if expected_status and expected_status != actual_status and not allow_stale_current_status:
                row_result["result"] = "skipped_stale_current_status"
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            if next_status == actual_status:
                row_result["result"] = "unchanged_same_status"
                counts[row_result["result"]] += 1
                results.append(row_result)
                continue

            row_result["previous_status"] = actual_status
            if dry_run:
                row_result["result"] = "would_apply"
                counts[row_result["result"]] += 1
            else:
                update_status(
                    conn,
                    candidate_id=candidate_id,
                    previous_status=actual_status,
                    next_status=next_status,
                    review_note=action["review_note"],
                    action_source=str(action_source),
                    now=now,
                )
                row_result["result"] = "applied"
                row_result["applied_at"] = now
                counts[row_result["result"]] += 1
            results.append(row_result)

        after_statuses = database_statuses(conn)
        if dry_run:
            conn.rollback()

    return {
        "generated_at": utc_now(),
        "dry_run": dry_run,
        "valid_statuses": list(VALID_STATUSES),
        "counts": {
            "action_rows": len(actions),
            **dict(counts),
        },
        "database_statuses_before": before_statuses,
        "database_statuses_after": after_statuses if not dry_run else before_statuses,
        "results": results,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    lines = [
        "# Review Actions Apply Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Dry run: {summary['dry_run']}",
        f"Database: `{summary['database']}`",
        f"Actions: `{summary['actions']}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Statuses Before", ""])
    for status, count in summary["database_statuses_before"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Statuses After", ""])
    for status, count in summary["database_statuses_after"].items():
        lines.append(f"- {status}: {count}")

    notable = [
        item
        for item in summary["results"]
        if item["result"] not in {"skipped_blank_next_status"}
    ][:20]
    if notable:
        lines.extend(["", "## Non-Blank Results", ""])
        for item in notable:
            lines.append(
                f"- row {item['row_number']}: `{item['candidate_id']}` -> {item['next_status']} ({item['result']})"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply local review action CSV rows to the SQLite topic database.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite topic database path.")
    parser.add_argument("--actions", default=str(DEFAULT_ACTIONS), help="Review actions CSV path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for apply summary outputs.")
    parser.add_argument("--dry-run", action="store_true", help="Validate actions without updating the database.")
    parser.add_argument(
        "--allow-stale-current-status",
        action="store_true",
        help="Apply actions even when CSV current_status differs from the database status.",
    )
    args = parser.parse_args()

    db_path = resolve_workspace_path(args.db)
    actions_path = resolve_workspace_path(args.actions)
    output_dir = resolve_workspace_path(args.output_dir)
    actions = read_actions(actions_path)

    conn = connect_database(db_path)
    try:
        summary = apply_actions(
            conn,
            actions=actions,
            action_source=actions_path,
            dry_run=args.dry_run,
            allow_stale_current_status=args.allow_stale_current_status,
        )
    finally:
        conn.close()

    summary.update(
        {
            "database": str(db_path),
            "actions": str(actions_path),
            "outputs": {
                "json": str(output_dir / "latest-apply-summary.json"),
                "markdown": str(output_dir / "latest-apply-summary.md"),
            },
        }
    )
    write_json(output_dir / "latest-apply-summary.json", summary)
    write_markdown(output_dir / "latest-apply-summary.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
