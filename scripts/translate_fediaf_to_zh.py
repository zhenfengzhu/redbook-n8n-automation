from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import argostranslate.translate
import fitz


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_INPUT_DIR = Path("data/fediaf-full")
DEFAULT_OUTPUT_DIR = Path("data/fediaf-full-zh")
MAX_CHUNK_CHARS = 5000


@dataclass
class TranslationCache:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                source_hash TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def get(self, text: str, namespace: str = "") -> str | None:
        digest = text_hash(cache_input(text, namespace))
        row = self.connection.execute(
            "SELECT translated_text FROM translations WHERE source_hash = ?",
            (digest,),
        ).fetchone()
        return str(row[0]) if row else None

    def put(self, text: str, translated: str, namespace: str = "") -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO translations (source_hash, source_text, translated_text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (text_hash(cache_input(text, namespace)), text, translated, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def cache_input(text: str, namespace: str = "") -> str:
    return f"{namespace}\0{text}" if namespace else text


def has_latin_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def split_long_piece(piece: str, max_chars: int) -> Iterable[str]:
    current: list[str] = []
    current_len = 0
    for token in re.split(r"(\s+)", piece):
        if not token:
            continue
        if current and current_len + len(token) > max_chars:
            yield "".join(current).strip()
            current = []
            current_len = 0
        current.append(token)
        current_len += len(token)
    if current:
        yield "".join(current).strip()


def split_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    pieces = re.split(r"(\n\s*\n)", text)
    for piece in pieces:
        if not piece:
            continue
        if len(piece) > max_chars:
            if current:
                chunks.append("".join(current).strip())
                current = []
                current_len = 0
            chunks.extend(split_long_piece(piece, max_chars))
            continue

        if current and current_len + len(piece) > max_chars:
            chunks.append("".join(current).strip())
            current = []
            current_len = 0

        current.append(piece)
        current_len += len(piece)

    if current:
        chunks.append("".join(current).strip())

    return [chunk for chunk in chunks if chunk]


class TranslationRouter:
    def __init__(self) -> None:
        languages = argostranslate.translate.get_installed_languages()
        self.languages = {language.code: language for language in languages}
        if "en" not in self.languages or "zh" not in self.languages:
            raise RuntimeError("Argos English->Chinese model is not installed. Install package en -> zh first.")
        self._translations: dict[tuple[str, str], argostranslate.translate.ITranslation] = {}

    def get_translation(self, source_code: str, target_code: str) -> argostranslate.translate.ITranslation:
        key = (source_code, target_code)
        if key not in self._translations:
            source = self.languages.get(source_code)
            target = self.languages.get(target_code)
            if source is None or target is None:
                raise RuntimeError(f"Argos translation model {source_code}->{target_code} is not installed.")
            self._translations[key] = source.get_translation(target)
        return self._translations[key]

    def translate_chunk(self, chunk: str, source_code: str) -> str:
        if source_code == "en":
            return self.get_translation("en", "zh").translate(chunk)

        to_english = self.get_translation(source_code, "en").translate(chunk)
        return self.get_translation("en", "zh").translate(to_english)


def translate_text(text: str, router: TranslationRouter, cache: TranslationCache, source_code: str = "en") -> str:
    text = normalize_text(text)
    if not text:
        return ""
    if not has_latin_letters(text):
        return text

    if source_code not in router.languages:
        print(f"[warn] source language {source_code!r} is not installed; falling back to English.", flush=True)
        source_code = "en"

    cache_namespace = "" if source_code == "en" else f"{source_code}->en->zh"
    translated_chunks: list[str] = []
    for chunk in split_chunks(text):
        cached = cache.get(chunk, cache_namespace)
        if cached is None:
            cached = router.translate_chunk(chunk, source_code)
            cache.put(chunk, cached, cache_namespace)
        translated_chunks.append(cached.strip())
    return "\n\n".join(translated_chunks).strip()


def read_json(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def markdown_output_path(record: dict[str, object], input_dir: Path, out_dir: Path) -> Path:
    source_path = Path(str(record["markdown"]))
    try:
        relative = source_path.resolve().relative_to(input_dir.resolve())
    except ValueError:
        relative = Path("pages") / source_path.name
    return out_dir / relative


def parse_crawled_markdown(markdown: str) -> tuple[str, str, str, str, str]:
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Untitled"

    source_match = re.search(r"^Source:\s*(.+)$", markdown, re.MULTILINE)
    source = source_match.group(1).strip() if source_match else ""

    fetched_match = re.search(r"^Fetched:\s*(.+)$", markdown, re.MULTILINE)
    fetched = fetched_match.group(1).strip() if fetched_match else ""

    text_match = re.search(r"## Text\s*(.*?)\s*## PDF Links", markdown, re.DOTALL)
    body = text_match.group(1).strip() if text_match else markdown

    pdf_match = re.search(r"## PDF Links\s*(.*)$", markdown, re.DOTALL)
    pdf_links = pdf_match.group(1).strip() if pdf_match else "None"

    return title, source, fetched, body, pdf_links


def translate_pages(
    input_dir: Path,
    out_dir: Path,
    router: TranslationRouter,
    cache: TranslationCache,
    limit: int | None,
    overwrite: bool,
) -> list[dict[str, object]]:
    page_index = read_json(input_dir / "index.json")
    records = [record for record in page_index if record.get("status") == "crawled" and record.get("markdown")]
    if limit is not None:
        records = records[:limit]

    output_records: list[dict[str, object]] = []
    for position, record in enumerate(records, start=1):
        input_path = Path(str(record["markdown"]))
        output_path = markdown_output_path(record, input_dir, out_dir)
        if output_path.exists() and not overwrite:
            print(f"[page {position}/{len(records)}] skip {output_path}", flush=True)
            output_records.append({"url": record.get("url"), "path": str(output_path), "status": "skipped"})
            continue

        markdown = input_path.read_text(encoding="utf-8")
        title, source, fetched, body, pdf_links = parse_crawled_markdown(markdown)
        translated_title = translate_text(title, router, cache)
        translated_body = translate_text(body, router, cache)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n\n".join(
                [
                    f"# {translated_title or title}",
                    f"原文标题: {title}",
                    f"来源: {source}",
                    f"抓取时间: {fetched}",
                    f"翻译时间: {datetime.now(timezone.utc).isoformat()}",
                    "## 正文",
                    translated_body,
                    "## PDF 链接",
                    pdf_links,
                ]
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        print(f"[page {position}/{len(records)}] {title} -> {output_path}", flush=True)
        output_records.append(
            {
                "url": record.get("url"),
                "source_path": str(input_path),
                "path": str(output_path),
                "status": "translated",
                "source_chars": len(body),
                "translated_chars": len(translated_body),
            }
        )
    return output_records


def pdf_text_by_page(path: Path) -> list[str]:
    with fitz.open(path) as document:
        return [normalize_text(page.get_text("text", sort=True)) for page in document]


def safe_pdf_markdown_name(path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", path.stem).strip("-._") + ".md"


PDF_LANGUAGE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hu", ("_hun", "-hun", "hungarian")),
    ("pl", ("polish", "fascynujace", "psy_i_male", "psy-i-male")),
    ("ro", ("romana", "romanian")),
    ("sl", ("slovenian",)),
    ("lt", ("bipa", "brosiura", "kates", "sunys")),
)


def guess_pdf_source_language(path: Path) -> str:
    name = path.name.lower()
    for code, hints in PDF_LANGUAGE_HINTS:
        if any(hint in name for hint in hints):
            return code
    return "en"


def matches_pdf_patterns(path: Path, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    name = path.name.lower()
    return any(pattern.lower() in name for pattern in patterns)


def translate_pdfs(
    input_dir: Path,
    out_dir: Path,
    router: TranslationRouter,
    cache: TranslationCache,
    limit: int | None,
    overwrite: bool,
    pdf_patterns: list[str] | None,
) -> list[dict[str, object]]:
    pdf_index = read_json(input_dir / "pdf-index.json")
    records = [record for record in pdf_index if record.get("status") == "downloaded" and record.get("path")]
    records = [record for record in records if matches_pdf_patterns(Path(str(record["path"])), pdf_patterns)]
    if limit is not None:
        records = records[:limit]

    output_records: list[dict[str, object]] = []
    pdf_out_dir = out_dir / "pdfs"
    for position, record in enumerate(records, start=1):
        input_path = Path(str(record["path"]))
        source_language = guess_pdf_source_language(input_path)
        output_path = pdf_out_dir / safe_pdf_markdown_name(input_path)
        partial_path = output_path.with_suffix(output_path.suffix + ".partial")
        if output_path.exists() and not overwrite:
            print(f"[pdf {position}/{len(records)}] skip {output_path}", flush=True)
            output_records.append(
                {
                    "url": record.get("url"),
                    "source_path": str(input_path),
                    "path": str(output_path),
                    "status": "skipped",
                    "source_language": source_language,
                }
            )
            continue

        pages = pdf_text_by_page(input_path)
        if partial_path.exists():
            partial_path.unlink()

        source_chars = 0
        translated_chars = 0
        source_pages = record.get("source_pages") or []
        if isinstance(source_pages, list):
            source_pages_block = "\n".join(f"- {item}" for item in source_pages) or "None"
        else:
            source_pages_block = str(source_pages)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text(
            "\n\n".join(
                [
                    f"# {input_path.name}",
                    f"PDF 来源: {record.get('url', '')}",
                    f"本地 PDF: {input_path}",
                    f"识别源语言: {source_language}",
                    f"翻译时间: {datetime.now(timezone.utc).isoformat()}",
                    "## 来源页面",
                    source_pages_block,
                ]
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        with partial_path.open("a", encoding="utf-8") as output_file:
            for page_number, page_text in enumerate(pages, start=1):
                source_chars += len(page_text)
                print(
                    f"[pdf {position}/{len(records)} page {page_number}/{len(pages)}] "
                    f"{input_path.name} lang={source_language} chars={len(page_text)}",
                    flush=True,
                )
                if page_text:
                    translated_text = translate_text(page_text, router, cache, source_language)
                    translated_chars += len(translated_text)
                    output_file.write(f"\n\n## 第 {page_number} 页\n\n{translated_text}\n")
                else:
                    output_file.write(f"\n\n## 第 {page_number} 页\n\n未抽取到可翻译文本。\n")

        if output_path.exists():
            output_path.unlink()
        partial_path.rename(output_path)
        print(f"[pdf {position}/{len(records)}] {input_path.name} -> {output_path}", flush=True)
        output_records.append(
            {
                "url": record.get("url"),
                "source_path": str(input_path),
                "path": str(output_path),
                "status": "translated",
                "source_language": source_language,
                "pages": len(pages),
                "source_chars": source_chars,
                "translated_chars": translated_chars,
            }
        )
    return output_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate crawled FEDIAF pages and PDF text into Chinese.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_DIR), help="Input crawl directory.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help="Output translation directory.")
    parser.add_argument("--pages", action="store_true", help="Translate crawled Markdown pages.")
    parser.add_argument("--pdfs", action="store_true", help="Extract and translate downloaded PDF text.")
    parser.add_argument("--limit-pages", type=int, default=None, help="Translate only the first N pages.")
    parser.add_argument("--limit-pdfs", type=int, default=None, help="Translate only the first N PDFs.")
    parser.add_argument(
        "--pdf-pattern",
        action="append",
        default=[],
        help="Translate only PDFs whose file name contains this text. Can be provided multiple times.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing translated files.")
    args = parser.parse_args()

    translate_pages_enabled = args.pages or not args.pdfs
    translate_pdfs_enabled = args.pdfs or not args.pages

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = TranslationCache(out_dir / "translation-cache.sqlite3")

    router = TranslationRouter()
    summary_path = out_dir / "translation-index.json"
    if summary_path.exists():
        existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary: dict[str, object] = existing_summary if isinstance(existing_summary, dict) else {}
    else:
        summary = {}
    summary.update(
        {
            "input": str(input_dir),
            "output": str(out_dir),
            "translated_at": datetime.now(timezone.utc).isoformat(),
            "pages": summary.get("pages", []),
            "pdfs": summary.get("pdfs", []),
        }
    )
    try:
        if translate_pages_enabled:
            summary["pages"] = translate_pages(
                input_dir=input_dir,
                out_dir=out_dir,
                router=router,
                cache=cache,
                limit=args.limit_pages,
                overwrite=args.overwrite,
            )
        if translate_pdfs_enabled:
            summary["pdfs"] = translate_pdfs(
                input_dir=input_dir,
                out_dir=out_dir,
                router=router,
                cache=cache,
                limit=args.limit_pdfs,
                overwrite=args.overwrite,
                pdf_patterns=args.pdf_pattern,
            )
        write_json(summary_path, summary)
    finally:
        cache.close()


if __name__ == "__main__":
    main()
