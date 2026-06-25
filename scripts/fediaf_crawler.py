from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


USER_AGENT = "RedBookAutomationCrawler/1.0 (+local research; respectful crawl)"
DEFAULT_START_URL = "https://www.fediaf.org/"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class Page:
    url: str
    title: str
    text: str
    pdf_links: list[str]


class RateLimiter:
    def __init__(self, delay_seconds: int) -> None:
        self.delay_seconds = max(0, delay_seconds)
        self._last_request_at = 0.0

    def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def mark(self) -> None:
        self._last_request_at = time.monotonic()


class ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._in_title = False
        self._skip_depth = 0
        self._body_started = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value for key, value in attrs if value is not None}

        if tag == "title":
            self._in_title = True

        if tag in {"body", "main", "article"}:
            self._body_started = True

        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}:
            self._skip_depth += 1

        if tag == "a":
            href = attrs_map.get("href")
            if href:
                self.links.append(href)

        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "section"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return

        data = unescape(data).strip()
        if not data:
            return

        if self._in_title:
            self.title_parts.append(data)
            return

        if self._body_started:
            self.text_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_space(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        lines = [normalize_space(line) for line in "\n".join(self.text_parts).splitlines()]
        lines = [line for line in lines if line and not looks_like_menu_noise(line)]
        return "\n".join(dedupe_adjacent(lines))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def dedupe_adjacent(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if not result or result[-1] != line:
            result.append(line)
    return result


def looks_like_menu_noise(line: str) -> bool:
    if len(line) <= 2:
        return True
    lowered = line.lower()
    noisy = {
        "menu",
        "search",
        "read more",
        "skip to content",
        "privacy policy",
        "cookie policy",
    }
    return lowered in noisy


def fetch(url: str, timeout: int = 30) -> tuple[str, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read()
        text = raw.decode(charset, errors="replace")
        return text, response.geturl(), content_type


def fetch_binary(url: str, timeout: int = 90) -> tuple[bytes, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        raw = response.read()
        return raw, response.geturl(), content_type


def get_crawl_delay(site_url: str) -> int:
    parsed = urllib.parse.urlparse(site_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        robots, _, _ = fetch(robots_url)
    except Exception:
        return 10

    for line in robots.splitlines():
        if line.lower().startswith("crawl-delay:"):
            try:
                return max(1, int(float(line.split(":", 1)[1].strip())))
            except ValueError:
                return 10
    return 10


def parse_sitemap_locations(xml_text: str) -> list[str]:
    root = ElementTree.fromstring(xml_text)
    locations: list[str] = []
    for element in root.iter():
        if element.tag.endswith("loc") and element.text:
            locations.append(element.text.strip())
    return locations


def normalize_page_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.path.startswith("/_/"):
        parsed = parsed._replace(path=parsed.path[2:])
    return urllib.parse.urlunparse(parsed)


def collect_urls(start_url: str) -> list[str]:
    _, final_url, _ = fetch(start_url)
    parsed = urllib.parse.urlparse(final_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    sitemap_xml, _, _ = fetch(sitemap_url)

    locations = parse_sitemap_locations(sitemap_xml)
    urls: list[str] = []
    for location in locations:
        if location.endswith(".xml"):
            child_xml, _, _ = fetch(location)
            urls.extend(parse_sitemap_locations(child_xml))
        else:
            urls.append(location)

    allowed_hosts = {urllib.parse.urlparse(final_url).netloc}
    unique_urls = []
    seen = set()
    for url in urls:
        url = normalize_page_url(url)
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.netloc:
            allowed_hosts.add(parsed_url.netloc)
        if parsed_url.netloc not in allowed_hosts:
            continue
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    return unique_urls


def parse_page(url: str, html: str) -> Page:
    parser = ContentParser()
    parser.feed(html)
    pdf_links = []
    for link in parser.links:
        absolute = urllib.parse.urljoin(url, link)
        if urllib.parse.urlparse(absolute).path.lower().endswith(".pdf"):
            pdf_links.append(absolute)
    pdf_links = sorted(set(pdf_links))
    title = parser.title or url.rstrip("/").split("/")[-1] or "Untitled"
    return Page(url=url, title=title, text=parser.text, pdf_links=pdf_links)


def safe_filename(url: str, title: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or "home"
    base = path.replace("/", "__")
    if not base:
        base = title
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-._")
    return (base or "page")[:120]


def write_page(out_dir: Path, page: Page) -> dict[str, object]:
    filename = safe_filename(page.url, page.title)
    markdown_path = out_dir / "pages" / f"{filename}.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    fetched_at = datetime.now(timezone.utc).isoformat()
    pdf_block = "\n".join(f"- {link}" for link in page.pdf_links) if page.pdf_links else "None"
    markdown = (
        f"# {page.title}\n\n"
        f"Source: {page.url}\n\n"
        f"Fetched: {fetched_at}\n\n"
        "## Text\n\n"
        f"{page.text}\n\n"
        "## PDF Links\n\n"
        f"{pdf_block}\n"
    )
    markdown_path.write_text(markdown, encoding="utf-8")

    return {
        "url": page.url,
        "title": page.title,
        "markdown": str(markdown_path),
        "text_chars": len(page.text),
        "pdf_links": page.pdf_links,
        "fetched_at": fetched_at,
    }


def pdf_filename(url: str, used_names: set[str]) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name
    if not name.lower().endswith(".pdf"):
        name = f"{safe_filename(url, 'document')}.pdf"
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-._") or "document.pdf"
    if name not in used_names:
        used_names.add(name)
        return name

    stem = Path(name).stem
    suffix = Path(name).suffix or ".pdf"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    unique_name = f"{stem}-{digest}{suffix}"
    used_names.add(unique_name)
    return unique_name


def pdf_url_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    candidates = [url]

    marker = "/wp-content/uploads/"
    if marker in parsed.path and not parsed.path.startswith(marker):
        path = parsed.path[parsed.path.index(marker) :]
        candidates.append(urllib.parse.urlunparse(parsed._replace(path=path)))

    if parsed.path.startswith("/uploads/"):
        candidates.append(urllib.parse.urlunparse(parsed._replace(path=f"/wp-content{parsed.path}")))

    if parsed.netloc in {"fediaf.org", "www.fediaf.org"} and parsed.path.startswith("/images/"):
        name = Path(urllib.parse.unquote(parsed.path)).name
        for month in ("02", "03"):
            candidates.append(f"https://europeanpetfood.org/wp-content/uploads/2022/{month}/{urllib.parse.quote(name)}")

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)
    return unique_candidates


def download_pdfs(
    out_dir: Path,
    page_records: list[dict[str, object]],
    limiter: RateLimiter,
) -> list[dict[str, object]]:
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    sources_by_pdf: dict[str, list[str]] = {}
    for record in page_records:
        page_url = str(record["url"])
        for pdf_url in record.get("pdf_links", []):
            sources_by_pdf.setdefault(str(pdf_url), []).append(page_url)

    used_names: set[str] = set()
    results: list[dict[str, object]] = []
    total = len(sources_by_pdf)

    for position, (pdf_url, source_pages) in enumerate(sorted(sources_by_pdf.items()), start=1):
        target_name = pdf_filename(pdf_url, used_names)
        target_path = pdf_dir / target_name
        result: dict[str, object] = {
            "url": pdf_url,
            "source_pages": sorted(set(source_pages)),
            "path": str(target_path),
            "status": "pending",
            "bytes": 0,
            "downloaded_at": None,
        }
        try:
            last_error: Exception | None = None
            fetched_url = pdf_url
            for candidate_url in pdf_url_candidates(pdf_url):
                try:
                    limiter.wait()
                    raw, final_url, content_type = fetch_binary(candidate_url)
                    limiter.mark()
                    fetched_url = candidate_url
                    break
                except Exception as exc:
                    limiter.mark()
                    last_error = exc
            else:
                raise last_error or RuntimeError("No PDF URL candidates were available.")

            target_path.write_bytes(raw)
            result.update(
                {
                    "final_url": final_url,
                    "fetched_url": fetched_url,
                    "content_type": content_type,
                    "status": "downloaded",
                    "bytes": len(raw),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(f"[pdf {position}/{total}] {pdf_url} -> {target_path}", flush=True)
        except Exception as exc:
            result.update({"status": "failed", "error": str(exc)})
            print(f"[pdf {position}/{total}] FAILED {pdf_url}: {exc}", flush=True)
        results.append(result)

        pdf_index_path = out_dir / "pdf-index.json"
        pdf_index_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    return results


def crawl(
    start_url: str,
    out_dir: Path,
    limit: int | None,
    delay: int | None,
    contains: list[str] | None,
    download_pdf_files: bool,
) -> list[dict[str, object]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = collect_urls(start_url)
    if contains:
        urls = [url for url in urls if any(fragment in url for fragment in contains)]
    if limit is not None:
        urls = urls[:limit]

    _, final_url, _ = fetch(start_url)
    crawl_delay = delay if delay is not None else get_crawl_delay(final_url)
    limiter = RateLimiter(crawl_delay)
    index: list[dict[str, object]] = []

    for position, url in enumerate(urls, start=1):
        try:
            limiter.wait()
            html, final_page_url, content_type = fetch(url)
            limiter.mark()
            if "text/html" not in content_type.lower():
                continue
            page = parse_page(final_page_url, html)
            record = write_page(out_dir, page)
            record["position"] = position
            record["status"] = "crawled"
            index.append(record)
            print(f"[page {position}/{len(urls)}] {page.title} -> {record['markdown']}", flush=True)
        except Exception as exc:
            record = {
                "url": url,
                "position": position,
                "status": "failed",
                "error": str(exc),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            index.append(record)
            print(f"[page {position}/{len(urls)}] FAILED {url}: {exc}", flush=True)

    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    if download_pdf_files:
        download_pdfs(out_dir, index, limiter)

    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Respectfully crawl FEDIAF / EuropeanPetFood pages.")
    parser.add_argument("--start-url", default=DEFAULT_START_URL)
    parser.add_argument("--out", default="data/fediaf")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", help="Crawl every URL found in the sitemap.")
    parser.add_argument("--delay", type=int, default=None, help="Override robots Crawl-delay in seconds.")
    parser.add_argument("--download-pdfs", action="store_true", help="Download PDFs linked from crawled pages.")
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Only crawl URLs containing this text. Can be used multiple times.",
    )
    args = parser.parse_args()
    limit = None if args.all else args.limit

    records = crawl(
        start_url=args.start_url,
        out_dir=Path(args.out),
        limit=limit,
        delay=args.delay,
        contains=args.contains,
        download_pdf_files=args.download_pdfs,
    )
    print(f"Saved {len(records)} pages to {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
