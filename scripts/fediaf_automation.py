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
DEFAULT_CRAWL_OUT = Path("data/fediaf-full")
DEFAULT_TRANSLATE_OUT = Path("data/fediaf-full-zh")
DEFAULT_LOG_ROOT = Path("data/fediaf-automation-logs")
TRANSLATION_PACKAGES = (
    ("en", "zh"),
    ("lt", "en"),
    ("pl", "en"),
    ("ro", "en"),
    ("sl", "en"),
    ("hu", "en"),
)


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


def count_files(path: Path, pattern: str, recursive: bool = False) -> int:
    if not path.exists():
        return 0
    iterator = path.rglob(pattern) if recursive else path.glob(pattern)
    return sum(1 for item in iterator if item.is_file())


def summarize_records(records: Any, status_key: str = "status") -> dict[str, int]:
    if not isinstance(records, list):
        return {}
    return dict(Counter(str(record.get(status_key, "unknown")) for record in records if isinstance(record, dict)))


def collect_summary(crawl_out: Path, translate_out: Path) -> dict[str, Any]:
    page_index = read_json(crawl_out / "index.json")
    pdf_index = read_json(crawl_out / "pdf-index.json")
    translation_index = read_json(translate_out / "translation-index.json")
    translated_pages: list[Any] = []
    translated_pdfs: list[Any] = []
    if isinstance(translation_index, dict):
        translated_pages = translation_index.get("pages", [])
        translated_pdfs = translation_index.get("pdfs", [])

    return {
        "crawl_output": str(crawl_out),
        "translation_output": str(translate_out),
        "source_pages_markdown": count_files(crawl_out / "pages", "*.md", recursive=True),
        "source_pdfs": count_files(crawl_out / "pdfs", "*.pdf"),
        "translated_pages_markdown": count_files(translate_out / "pages", "*.md", recursive=True),
        "translated_pdf_markdown": count_files(translate_out / "pdfs", "*.md"),
        "partial_translation_files": count_files(translate_out / "pdfs", "*.partial"),
        "crawl_status": summarize_records(page_index),
        "pdf_download_status": summarize_records(pdf_index),
        "translation_page_status": summarize_records(translated_pages),
        "translation_pdf_status": summarize_records(translated_pdfs),
    }


def has_translation(source_code: str, target_code: str) -> bool:
    import argostranslate.translate

    languages = {language.code: language for language in argostranslate.translate.get_installed_languages()}
    source = languages.get(source_code)
    target = languages.get(target_code)
    if source is None or target is None:
        return False
    try:
        source.get_translation(target)
    except Exception:
        return False
    return True


def install_translation_packages() -> list[str]:
    import argostranslate.package

    installed: list[str] = []
    missing = [(source, target) for source, target in TRANSLATION_PACKAGES if not has_translation(source, target)]
    if not missing:
        return installed

    print("[models] Updating Argos package index.", flush=True)
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()

    for source, target in missing:
        package = next(
            (item for item in available if item.from_code == source and item.to_code == target),
            None,
        )
        if package is None:
            raise RuntimeError(f"Argos package {source}->{target} is not available.")
        print(f"[models] Installing {source}->{target}.", flush=True)
        package.install()
        installed.append(f"{source}->{target}")
    return installed


def build_crawl_command(args: argparse.Namespace, crawl_out: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "fediaf_crawler.py"),
        "--out",
        str(crawl_out),
    ]
    if args.crawl_all:
        command.append("--all")
    else:
        command.extend(["--limit", str(args.limit)])
    if args.delay is not None:
        command.extend(["--delay", str(args.delay)])
    if args.download_pdfs:
        command.append("--download-pdfs")
    for fragment in args.contains:
        command.extend(["--contains", fragment])
    return command


def build_translate_command(
    args: argparse.Namespace,
    crawl_out: Path,
    translate_out: Path,
    translate_pages: bool,
    translate_pdfs: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "translate_fediaf_to_zh.py"),
        "--input",
        str(crawl_out),
        "--out",
        str(translate_out),
    ]
    if translate_pages:
        command.append("--pages")
    if translate_pdfs:
        command.append("--pdfs")
    if args.overwrite_translation:
        command.append("--overwrite")
    return command


def write_summary(log_dir: Path, summary: dict[str, Any]) -> Path:
    summary_path = log_dir / "run-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = log_dir.parent / "latest-run.json"
    latest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FEDIAF crawl and Chinese translation workflow.")
    parser.add_argument("--full", action="store_true", help="Crawl all pages, download PDFs, and translate pages/PDFs.")
    parser.add_argument("--no-crawl", action="store_true", help="Skip crawling and use an existing crawl output folder.")
    parser.add_argument("--no-translate", action="store_true", help="Skip Chinese translation.")
    parser.add_argument("--crawl-out", default=str(DEFAULT_CRAWL_OUT), help="Crawler output directory.")
    parser.add_argument("--translate-out", default=str(DEFAULT_TRANSLATE_OUT), help="Chinese translation output directory.")
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT), help="Automation log root directory.")
    parser.add_argument("--all", dest="crawl_all", action="store_true", help="Crawl every URL found in the sitemap.")
    parser.add_argument("--limit", type=int, default=5, help="Number of pages to crawl when --all is not used.")
    parser.add_argument("--delay", type=int, default=None, help="Override robots Crawl-delay for crawling.")
    parser.add_argument("--download-pdfs", action="store_true", help="Download PDFs found during crawling.")
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Only crawl URLs containing this text. Can be used multiple times.",
    )
    parser.add_argument("--translate-pages", action="store_true", help="Translate crawled Markdown pages.")
    parser.add_argument("--translate-pdfs", action="store_true", help="Translate extracted PDF text.")
    parser.add_argument("--overwrite-translation", action="store_true", help="Overwrite existing translated files.")
    parser.add_argument("--install-models", action="store_true", help="Install required Argos translation models.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and write a summary without running them.")
    args = parser.parse_args()

    if args.full:
        args.crawl_all = True
        args.download_pdfs = True
        args.translate_pages = True
        args.translate_pdfs = True

    if not args.no_translate and not args.translate_pages and not args.translate_pdfs:
        args.translate_pages = True
        args.translate_pdfs = args.download_pdfs

    crawl_out = resolve_workspace_path(args.crawl_out)
    translate_out = resolve_workspace_path(args.translate_out)
    log_root = resolve_workspace_path(args.log_root)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = log_root / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    installed_models: list[str] = []
    started_at = utc_now()

    if args.install_models and not args.no_translate and not args.dry_run:
        installed_models = install_translation_packages()
    elif args.install_models and args.dry_run:
        installed_models = ["dry_run"]

    if not args.no_crawl:
        steps.append(run_command("crawl", build_crawl_command(args, crawl_out), log_dir / "crawl.log", args.dry_run))

    if not args.no_translate:
        steps.append(
            run_command(
                "translate",
                build_translate_command(args, crawl_out, translate_out, args.translate_pages, args.translate_pdfs),
                log_dir / "translate.log",
                args.dry_run,
            )
        )

    finished_at = utc_now()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": args.dry_run,
        "installed_models": installed_models,
        "steps": [asdict(step) for step in steps],
        "outputs": collect_summary(crawl_out, translate_out),
    }
    summary_path = write_summary(log_dir, summary)

    print(f"[summary] {summary_path}", flush=True)
    print(json.dumps(summary["outputs"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
