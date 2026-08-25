from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from translate_fediaf_to_zh import TranslationCache, TranslationRouter, translate_text


DEFAULT_INPUT_DIR = Path("data/petmd")
DEFAULT_OUTPUT_DIR = Path("data/petmd-zh")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def field_value(markdown: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def first_heading(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def section_after(markdown: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
    if not match:
        return ""
    section = markdown[match.end() :].strip()
    next_heading = re.search(r"^##\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()].strip()
    return section


def relative_output_path(source_path: Path, input_dir: Path, out_dir: Path) -> Path:
    try:
        relative = source_path.resolve().relative_to(input_dir.resolve())
    except ValueError:
        relative = Path("pages") / source_path.name
    return out_dir / relative


def translate_pages(input_dir: Path, out_dir: Path, limit: int | None, overwrite: bool) -> list[dict[str, Any]]:
    index = read_json(input_dir / "index.json")
    records: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for record in index:
        if not isinstance(record, dict) or record.get("status") != "crawled" or not record.get("markdown"):
            continue
        dedupe_key = str(record.get("url") or record.get("markdown"))
        if dedupe_key in seen_records:
            continue
        seen_records.add(dedupe_key)
        records.append(record)
    if limit is not None:
        records = records[:limit]

    cache = TranslationCache(out_dir / "translation-cache.sqlite3")
    router = TranslationRouter()
    translated_at = utc_now()
    output_records: list[dict[str, Any]] = []
    try:
        for position, record in enumerate(records, start=1):
            source_path = Path(str(record["markdown"]))
            output_path = relative_output_path(source_path, input_dir, out_dir)
            if output_path.exists() and not overwrite:
                print(f"[page {position}/{len(records)}] skip {output_path}", flush=True)
                output_records.append(
                    {
                        "url": record.get("url"),
                        "source_path": str(source_path),
                        "path": str(output_path),
                        "status": "skipped",
                    }
                )
                continue

            markdown = source_path.read_text(encoding="utf-8", errors="replace")
            title = first_heading(markdown, str(record.get("title") or source_path.stem))
            body = section_after(markdown, "Text")
            translated_title = translate_text(title, router, cache)
            translated_body = translate_text(body, router, cache)
            translated_description = translate_text(str(record.get("description") or field_value(markdown, "Description")), router, cache)

            metadata = {
                "Source": field_value(markdown, "Source") or str(record.get("url", "")),
                "Fetched": field_value(markdown, "Fetched"),
                "Content type": field_value(markdown, "Content type") or str(record.get("content_type", "")),
                "Published": field_value(markdown, "Published") or str(record.get("published_at", "")),
                "Modified": field_value(markdown, "Modified") or str(record.get("modified_at", "")),
                "Author": field_value(markdown, "Author") or str(record.get("author", "")),
                "Reviewed by": field_value(markdown, "Reviewed by") or str(record.get("reviewed_by", "")),
                "Reviewed on": field_value(markdown, "Reviewed on") or str(record.get("reviewed_on", "")),
                "Description": field_value(markdown, "Description") or str(record.get("description", "")),
                "Image": field_value(markdown, "Image") or str(record.get("image_url", "")),
            }

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                "\n\n".join(
                    [
                        f"# {translated_title or title}",
                        f"原文标题: {title}",
                        f"来源: {metadata['Source']}",
                        f"抓取时间: {metadata['Fetched']}",
                        f"翻译时间: {translated_at}",
                        f"内容类型: {metadata['Content type'] or 'unknown'}",
                        f"发布时间: {metadata['Published'] or 'unknown'}",
                        f"修改时间: {metadata['Modified'] or 'unknown'}",
                        f"作者: {metadata['Author'] or 'unknown'}",
                        f"审核人: {metadata['Reviewed by'] or 'unknown'}",
                        f"审核时间: {metadata['Reviewed on'] or 'unknown'}",
                        f"原文描述: {metadata['Description'] or 'unknown'}",
                        f"中文描述: {translated_description or 'unknown'}",
                        f"图片: {metadata['Image'] or 'unknown'}",
                        "## 正文",
                        translated_body,
                    ]
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            print(f"[page {position}/{len(records)}] {title} -> {output_path}", flush=True)
            output_records.append(
                {
                    "url": metadata["Source"],
                    "source_path": str(source_path),
                    "path": str(output_path),
                    "status": "translated",
                    "title": title,
                    "translated_title": translated_title,
                    "content_type": metadata["Content type"],
                    "author": metadata["Author"],
                    "reviewed_by": metadata["Reviewed by"],
                    "reviewed_on": metadata["Reviewed on"],
                    "published_at": metadata["Published"],
                    "modified_at": metadata["Modified"],
                    "description": metadata["Description"],
                    "image_url": metadata["Image"],
                    "source_chars": len(body),
                    "translated_chars": len(translated_body),
                }
            )
    finally:
        cache.close()
    return output_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate crawled PetMD Markdown pages into Chinese.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_DIR), help="PetMD crawl output directory.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help="Chinese translation output directory.")
    parser.add_argument("--limit", type=int, default=None, help="Translate only the first N crawled pages.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing translated files.")
    args = parser.parse_args()

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = translate_pages(input_dir=input_dir, out_dir=out_dir, limit=args.limit, overwrite=args.overwrite)
    summary = {
        "input": str(input_dir),
        "output": str(out_dir),
        "translated_at": utc_now(),
        "pages": records,
        "counts": {
            "translated": sum(1 for record in records if record.get("status") == "translated"),
            "skipped": sum(1 for record in records if record.get("status") == "skipped"),
        },
    }
    write_json(out_dir / "translation-index.json", summary)
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
