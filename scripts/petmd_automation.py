from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CRAWL_OUT = Path("data/petmd")
DEFAULT_TRANSLATE_OUT = Path("data/petmd-zh")
DEFAULT_CARDS_OUT = Path("data/petmd-topic-cards")
DEFAULT_LOG_ROOT = Path("data/petmd-automation-logs")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class StepResult:
    name: str
    command: list[str]
    log_path: str
    status: str
    returncode: int | None
    started_at: str
    finished_at: str
    duration_seconds: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def display_command(command: list[str]) -> str:
    if hasattr(subprocess, "list2cmdline"):
        return subprocess.list2cmdline(command)
    return " ".join(command)


def run_command(name: str, command: list[str], log_path: Path, dry_run: bool) -> StepResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    started_at = utc_now()
    command_text = display_command(command)
    print(f"[{name}] {command_text}", flush=True)

    if dry_run:
        log_path.write_text(f"DRY RUN\n{command_text}\n", encoding="utf-8")
        finished_at = utc_now()
        return StepResult(
            name=name,
            command=command,
            log_path=str(log_path),
            status="dry_run",
            returncode=None,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(time.monotonic() - started, 3),
        )

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"Started: {started_at}\n")
        log_file.write(f"Command: {command_text}\n\n")
        log_file.flush()
        process = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        finished_at = utc_now()
        duration = round(time.monotonic() - started, 3)
        log_file.write(f"\nFinished: {finished_at}\n")
        log_file.write(f"Duration seconds: {duration}\n")
        log_file.write(f"Return code: {process.returncode}\n")

    status = "completed" if process.returncode == 0 else "failed"
    result = StepResult(
        name=name,
        command=command,
        log_path=str(log_path),
        status=status,
        returncode=process.returncode,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
    )
    if process.returncode != 0:
        raise RuntimeError(f"Step {name!r} failed with exit code {process.returncode}. See {log_path}")
    return result


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob(pattern) if item.is_file())


def summarize_records(records: Any, status_key: str = "status") -> dict[str, int]:
    if not isinstance(records, list):
        return {}
    return dict(Counter(str(record.get(status_key, "unknown")) for record in records if isinstance(record, dict)))


def collect_summary(crawl_out: Path, translate_out: Path, cards_out: Path) -> dict[str, Any]:
    crawl_index = read_json(crawl_out / "index.json")
    crawl_summary = read_json(crawl_out / "summary.json")
    translation_index = read_json(translate_out / "translation-index.json")
    cards_summary = read_json(cards_out / "summary.json")
    translated_pages: list[Any] = []
    if isinstance(translation_index, dict):
        translated_pages = translation_index.get("pages", [])

    return {
        "crawl_output": str(crawl_out),
        "translation_output": str(translate_out),
        "cards_output": str(cards_out),
        "source_pages_markdown": count_files(crawl_out / "pages", "*.md"),
        "translated_pages_markdown": count_files(translate_out / "pages", "*.md"),
        "topic_cards_jsonl_exists": (cards_out / "topic-cards.jsonl").exists(),
        "crawl_status": summarize_records(crawl_index),
        "translation_status": summarize_records(translated_pages),
        "crawl_counts": crawl_summary if isinstance(crawl_summary, dict) else {},
        "card_counts": cards_summary.get("counts", {}) if isinstance(cards_summary, dict) else {},
    }


def build_crawl_command(args: argparse.Namespace, crawl_out: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "petmd_crawler.py"),
        "--out",
        str(crawl_out),
        "--delay",
        str(args.delay),
        "--min-chars",
        str(args.min_chars),
        "--max-discovery",
        str(args.max_discovery),
    ]
    if args.crawl_all:
        command.append("--all")
    else:
        command.extend(["--limit", str(args.limit)])
    if args.include_index_pages:
        command.append("--include-index-pages")
    if args.no_saved_page_discovery:
        command.append("--no-saved-page-discovery")
    for url in args.start_url:
        command.extend(["--start-url", url])
    for fragment in args.contains:
        command.extend(["--contains", fragment])
    return command


def build_translate_command(args: argparse.Namespace, crawl_out: Path, translate_out: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "translate_petmd_to_zh.py"),
        "--input",
        str(crawl_out),
        "--out",
        str(translate_out),
    ]
    if args.overwrite_translation:
        command.append("--overwrite")
    return command


def build_cards_command(translate_out: Path, cards_out: Path) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "generate_petmd_topic_cards.py"),
        "--translation-index",
        str(translate_out / "translation-index.json"),
        "--output-dir",
        str(cards_out),
    ]


def write_summary(log_dir: Path, summary: dict[str, Any]) -> Path:
    summary_path = log_dir / "run-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = log_dir.parent / "latest-run.json"
    latest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PetMD crawl, Chinese translation, and topic-card workflow.")
    parser.add_argument("--no-crawl", action="store_true", help="Skip crawling and use an existing crawl output folder.")
    parser.add_argument("--no-translate", action="store_true", help="Skip Chinese translation.")
    parser.add_argument("--no-cards", action="store_true", help="Skip topic-card generation.")
    parser.add_argument("--crawl-out", default=str(DEFAULT_CRAWL_OUT), help="PetMD crawl output directory.")
    parser.add_argument("--translate-out", default=str(DEFAULT_TRANSLATE_OUT), help="Chinese translation output directory.")
    parser.add_argument("--cards-out", default=str(DEFAULT_CARDS_OUT), help="Topic-card output directory.")
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT), help="Automation log root directory.")
    parser.add_argument("--start-url", action="append", default=[], help="Seed URL for PetMD crawl. Can be used multiple times.")
    parser.add_argument("--contains", action="append", default=[], help="Prioritize and save URLs containing this text.")
    parser.add_argument("--limit", type=int, default=10, help="Number of pages to save when --all is not used.")
    parser.add_argument("--all", dest="crawl_all", action="store_true", help="Crawl until discovery is exhausted or capped.")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay between crawl requests in seconds.")
    parser.add_argument("--include-index-pages", action="store_true", help="Save PetMD category/index pages too.")
    parser.add_argument(
        "--no-saved-page-discovery",
        action="store_true",
        help="Do not discover more links from pages saved as articles.",
    )
    parser.add_argument("--min-chars", type=int, default=700, help="Minimum extracted text characters required for saving.")
    parser.add_argument("--max-discovery", type=int, default=1000, help="Maximum visited URLs in one crawl run.")
    parser.add_argument("--overwrite-translation", action="store_true", help="Overwrite existing translated PetMD files.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and write a summary without running them.")
    args = parser.parse_args()

    crawl_out = resolve_workspace_path(args.crawl_out)
    translate_out = resolve_workspace_path(args.translate_out)
    cards_out = resolve_workspace_path(args.cards_out)
    log_root = resolve_workspace_path(args.log_root)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = log_root / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    started_at = utc_now()

    if not args.no_crawl:
        steps.append(run_command("crawl", build_crawl_command(args, crawl_out), log_dir / "crawl.log", args.dry_run))

    if not args.no_translate:
        steps.append(
            run_command(
                "translate",
                build_translate_command(args, crawl_out, translate_out),
                log_dir / "translate.log",
                args.dry_run,
            )
        )

    if not args.no_cards:
        steps.append(
            run_command(
                "cards",
                build_cards_command(translate_out, cards_out),
                log_dir / "cards.log",
                args.dry_run,
            )
        )

    finished_at = utc_now()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": args.dry_run,
        "steps": [asdict(step) for step in steps],
        "outputs": collect_summary(crawl_out, translate_out, cards_out),
    }
    summary_path = write_summary(log_dir, summary)
    print(f"[summary] {summary_path}", flush=True)
    print(json.dumps(summary["outputs"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
