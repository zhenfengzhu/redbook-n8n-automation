from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


USER_AGENT = "RedBookAutomationCrawler/1.0 (+local research; respectful crawl)"
DEFAULT_START_URL = "https://www.petmd.com/"
DEFAULT_SITEMAP_URL = "https://www.petmd.com/sitemap"
DEFAULT_OUT = "data/petmd"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class PageContent:
    url: str
    title: str
    text: str
    description: str
    author: str
    reviewed_by: str
    reviewed_on: str
    published_at: str
    modified_at: str
    image_url: str
    content_type: str
    links: list[str]


class RateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
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


class RobotsRules:
    def __init__(self, rules: list[tuple[str, str]]) -> None:
        self.rules = rules

    def can_fetch(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or "/"
        best_rule: tuple[int, str] | None = None
        for action, pattern in self.rules:
            if not pattern:
                continue
            normalized = pattern.rstrip("$")
            if pattern.endswith("$"):
                matched = path == normalized
            else:
                matched = path.startswith(normalized)
            if not matched:
                continue
            score = len(normalized)
            if best_rule is None or score > best_rule[0]:
                best_rule = (score, action)
        if best_rule is None:
            return True
        return best_rule[1] == "allow"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_map = {key.lower(): value for key, value in attrs if value is not None}
        href = attrs_map.get("href")
        if href:
            self.links.append(href)


class MainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._in_title = False
        self._in_main = False
        self._main_depth = 0
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value for key, value in attrs if value is not None}

        if tag == "title":
            self._in_title = True
        if tag == "main":
            self._in_main = True
            self._main_depth = 1
        elif self._in_main:
            self._main_depth += 1

        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button"}:
            self._skip_depth += 1

        if tag == "a":
            href = attrs_map.get("href")
            if href:
                self.links.append(href)

        if self._in_main and tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "section"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button"} and self._skip_depth:
            self._skip_depth -= 1
        if self._in_main and tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")
        if self._in_main:
            self._main_depth -= 1
            if self._main_depth <= 0:
                self._in_main = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        data = unescape(data).strip()
        if not data:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._in_main:
            self.text_parts.append(data)

    @property
    def title(self) -> str:
        title = normalize_space(" ".join(self.title_parts))
        return title.removesuffix("| PetMD").strip()

    @property
    def text(self) -> str:
        lines = [normalize_space(line) for line in "\n".join(self.text_parts).splitlines()]
        lines = [line for line in lines if line and not looks_like_navigation_noise(line)]
        return "\n".join(dedupe_adjacent(lines))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def dedupe_adjacent(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if not result or result[-1] != line:
            result.append(line)
    return result


def looks_like_navigation_noise(line: str) -> bool:
    lowered = line.lower()
    if len(line) <= 2:
        return True
    if lowered in {
        "menu",
        "search",
        "read more",
        "skip to content",
        "skip to main content",
        "privacy policy",
        "legal notices",
        "subscribe",
        "email address",
    }:
        return True
    return lowered.startswith("image:") or lowered.startswith("sponsored by")


def strip_tags(html: str) -> str:
    parser = MainTextParser()
    parser.feed(f"<main>{html}</main>")
    return parser.text


def fetch(url: str, timeout: int = 30) -> tuple[str, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read()
        return raw.decode(charset, errors="replace"), response.geturl(), content_type


def build_robot_rules(start_url: str) -> RobotsRules:
    parsed = urllib.parse.urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        robots_text, _, _ = fetch(robots_url)
    except Exception:
        return RobotsRules([])

    rules: list[tuple[str, str]] = []
    active = False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            active = value == "*"
            continue
        if not active or key not in {"allow", "disallow"}:
            continue
        if value:
            rules.append((key, value))
    return RobotsRules(rules)


def normalize_url(base_url: str, href: str) -> str | None:
    if not href or href.startswith(("mailto:", "tel:", "javascript:")):
        return None
    absolute = urllib.parse.urljoin(base_url, href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    cleaned = parsed._replace(fragment="", query="")
    path = re.sub(r"/{2,}", "/", cleaned.path)
    cleaned = cleaned._replace(path=path)
    url = urllib.parse.urlunparse(cleaned)
    return url.rstrip("/") or url


def is_same_site(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc.lower() in allowed_hosts


def is_probably_content_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return True
    blocked_prefixes = (
        "admin",
        "comment/reply",
        "contact",
        "filter/tips",
        "images",
        "index.php",
        "newsletter",
        "preview",
        "search",
        "user",
    )
    if path.startswith(blocked_prefixes):
        return False
    if any(part in {"privacy-policy", "legal-notices", "write-for-us"} for part in path.split("/")):
        return False
    return "." not in Path(path).name


def collect_links(base_url: str, html: str, allowed_hosts: set[str]) -> list[str]:
    parser = LinkParser()
    parser.feed(html)
    links: list[str] = []
    seen: set[str] = set()
    for href in parser.links:
        url = normalize_url(base_url, href)
        if not url or url in seen:
            continue
        if is_same_site(url, allowed_hosts) and is_probably_content_url(url):
            seen.add(url)
            links.append(url)
    return links


def extract_json_script(html: str, marker: str) -> str | None:
    start = html.find(marker)
    if start < 0:
        return None
    start = html.find(">", start)
    if start < 0:
        return None
    start += 1
    end = html.find("</script>", start)
    if end < 0:
        return None
    return html[start:end]


def extract_json_ld(html: str) -> list[Any]:
    blocks: list[Any] = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        raw = unescape(match.group(1)).strip()
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def iter_json_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_json_objects(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_json_objects(child))
    return found


def first_text(value: Any) -> str:
    if isinstance(value, str):
        return normalize_space(value)
    if isinstance(value, list):
        return ", ".join(first_text(item) for item in value if first_text(item))
    if isinstance(value, dict):
        for key in ("name", "headline", "url"):
            if key in value:
                return first_text(value[key])
    return ""


def extract_next_data(html: str) -> dict[str, Any]:
    raw = extract_json_script(html, 'id="__NEXT_DATA__"')
    if not raw:
        raw = extract_json_script(html, "id='__NEXT_DATA__'")
    if not raw:
        return {}
    try:
        data = json.loads(unescape(raw))
    except json.JSONDecodeError:
        return {}
    page_props = data.get("props", {}).get("pageProps", {})
    page_data = page_props.get("data", {})
    return page_data if isinstance(page_data, dict) else {}


def content_from_next_data(page_data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in page_data.get("content", []):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", ""))
        if block_type in {"text", "html"}:
            text = strip_tags(str(block.get("content", "")))
        elif "title" in block:
            text = first_text(block.get("title"))
        else:
            text = ""
        if text:
            parts.append(text)
    return "\n\n".join(dedupe_adjacent(parts))


def parse_page(url: str, html: str) -> PageContent:
    fallback_parser = MainTextParser()
    fallback_parser.feed(html)
    json_ld_blocks = extract_json_ld(html)
    json_ld_objects = [obj for block in json_ld_blocks for obj in iter_json_objects(block)]
    article = next(
        (
            obj
            for obj in json_ld_objects
            if str(obj.get("@type", "")).lower() in {"article", "newsarticle", "blogposting"}
        ),
        {},
    )
    page_data = extract_next_data(html)

    title = first_text(article.get("headline")) or first_text(page_data.get("title")) or fallback_parser.title
    description = first_text(article.get("description")) or first_text(page_data.get("seo", {}).get("description"))
    author = first_text(article.get("author")) or first_text(page_data.get("author"))
    reviewed = page_data.get("reviewed") if isinstance(page_data.get("reviewed"), dict) else {}
    reviewed_by = first_text(reviewed.get("by")) if isinstance(reviewed, dict) else ""
    reviewed_on = first_text(reviewed.get("on")) if isinstance(reviewed, dict) else ""
    published_at = first_text(article.get("datePublished")) or first_text(page_data.get("created"))
    modified_at = first_text(article.get("dateModified")) or first_text(page_data.get("changed"))
    image = article.get("image")
    image_url = first_text(image) or first_text(article.get("thumbnailUrl")) or first_text(page_data.get("image_new", {}).get("path"))
    content_type = first_text(page_data.get("type")) or first_text(article.get("@type"))
    article_body = first_text(article.get("articleBody"))
    text = content_from_next_data(page_data) or article_body or fallback_parser.text
    links = collect_links(url, html, {urllib.parse.urlparse(url).netloc.lower()})

    return PageContent(
        url=url,
        title=title or url.rstrip("/").split("/")[-1],
        text=text,
        description=description,
        author=author,
        reviewed_by=reviewed_by,
        reviewed_on=reviewed_on,
        published_at=published_at,
        modified_at=modified_at,
        image_url=image_url,
        content_type=content_type,
        links=links,
    )


def safe_filename(url: str, title: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or "home"
    base = path.replace("/", "__") or title
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-._")
    return (base or "page")[:160]


def write_page(out_dir: Path, page: PageContent) -> dict[str, Any]:
    filename = safe_filename(page.url, page.title)
    markdown_path = out_dir / "pages" / f"{filename}.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    metadata_lines = [
        f"Source: {page.url}",
        f"Fetched: {fetched_at}",
        f"Content type: {page.content_type or 'unknown'}",
        f"Published: {page.published_at or 'unknown'}",
        f"Modified: {page.modified_at or 'unknown'}",
        f"Author: {page.author or 'unknown'}",
        f"Reviewed by: {page.reviewed_by or 'unknown'}",
        f"Reviewed on: {page.reviewed_on or 'unknown'}",
        f"Description: {page.description or 'unknown'}",
        f"Image: {page.image_url or 'unknown'}",
    ]
    markdown = (
        f"# {page.title}\n\n"
        + "\n".join(metadata_lines)
        + "\n\n"
        + "## Text\n\n"
        + page.text
        + "\n"
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "url": page.url,
        "title": page.title,
        "markdown": str(markdown_path),
        "text_chars": len(page.text),
        "content_type": page.content_type,
        "author": page.author,
        "reviewed_by": page.reviewed_by,
        "reviewed_on": page.reviewed_on,
        "published_at": page.published_at,
        "modified_at": page.modified_at,
        "description": page.description,
        "image_url": page.image_url,
        "link_count": len(page.links),
        "fetched_at": fetched_at,
        "status": "crawled",
    }


def seed_urls(sitemap_url: str, start_urls: list[str], allowed_hosts: set[str], robots: RobotsRules) -> list[str]:
    seeds: list[str] = []
    seen: set[str] = set()
    for url in start_urls:
        normalized = normalize_url(url, url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            seeds.append(normalized)
    try:
        html, final_url, content_type = fetch(sitemap_url)
        if "text/html" in content_type.lower():
            for url in collect_links(final_url, html, allowed_hosts):
                if url not in seen and robots.can_fetch(url):
                    seen.add(url)
                    seeds.append(url)
    except Exception as exc:
        print(f"[seed] Failed to read sitemap page {sitemap_url}: {exc}", flush=True)
    return seeds


def should_save_page(page: PageContent, min_chars: int, article_only: bool) -> bool:
    if len(page.text) < min_chars:
        return False
    if not article_only:
        return True
    return page.content_type.lower() in {"article", "condition", "breed", "petcontent", "nutrition", "medication"} or bool(
        page.published_at or page.author
    )


def crawl(
    start_urls: list[str],
    sitemap_url: str,
    out_dir: Path,
    limit: int | None,
    delay: float,
    contains: list[str],
    article_only: bool,
    min_chars: int,
    max_discovery: int,
    discover_from_saved_pages: bool,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    primary_start = start_urls[0] if start_urls else DEFAULT_START_URL
    allowed_hosts = {urllib.parse.urlparse(primary_start).netloc.lower()}
    robots = build_robot_rules(primary_start)
    limiter = RateLimiter(delay)
    queue = deque(seed_urls(sitemap_url, start_urls, allowed_hosts, robots))
    discovered: set[str] = set(queue)
    seen_final_urls: set[str] = set()
    saved = 0
    visited = 0
    index: list[dict[str, Any]] = []

    while queue and (limit is None or saved < limit) and visited < max_discovery:
        url = queue.popleft()
        visited += 1
        if contains and not any(fragment in url for fragment in contains):
            continue
        if not robots.can_fetch(url):
            index.append({"url": url, "status": "blocked_by_robots"})
            continue
        try:
            limiter.wait()
            html, final_url, content_type = fetch(url)
            limiter.mark()
            if "text/html" not in content_type.lower():
                index.append({"url": url, "final_url": final_url, "status": "skipped_non_html", "content_type": content_type})
                continue
            normalized_final_url = normalize_url(final_url, final_url) or final_url
            if normalized_final_url in seen_final_urls:
                index.append({"url": url, "final_url": final_url, "status": "duplicate_final_url"})
                continue
            seen_final_urls.add(normalized_final_url)
            page = parse_page(final_url, html)
            save_page = should_save_page(page, min_chars, article_only)
            if discover_from_saved_pages or not save_page:
                priority_links: list[str] = []
                regular_links: list[str] = []
                for link in page.links:
                    if link not in discovered and robots.can_fetch(link):
                        discovered.add(link)
                        if contains and any(fragment in link for fragment in contains):
                            priority_links.append(link)
                        else:
                            regular_links.append(link)
                for link in reversed(priority_links):
                    queue.appendleft(link)
                queue.extend(regular_links)
            if save_page:
                record = write_page(out_dir, page)
                record["position"] = saved + 1
                index.append(record)
                saved += 1
                print(f"[page {saved}] {page.title} -> {record['markdown']}", flush=True)
            else:
                index.append(
                    {
                        "url": final_url,
                        "title": page.title,
                        "status": "discovered",
                        "text_chars": len(page.text),
                        "content_type": page.content_type,
                        "link_count": len(page.links),
                    }
                )
        except Exception as exc:
            limiter.mark()
            index.append({"url": url, "status": "failed", "error": str(exc)})
            print(f"[discover {visited}] FAILED {url}: {exc}", flush=True)
        finally:
            (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_urls": start_urls,
        "sitemap_url": sitemap_url,
        "saved_pages": saved,
        "visited_pages": visited,
        "discovered_urls": len(discovered),
        "limit": limit,
        "delay_seconds": delay,
        "contains": contains,
        "article_only": article_only,
        "min_chars": min_chars,
        "max_discovery": max_discovery,
        "discover_from_saved_pages": discover_from_saved_pages,
        "outputs": {
            "pages": str(out_dir / "pages"),
            "index": str(out_dir / "index.json"),
            "summary": str(out_dir / "summary.json"),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Respectfully crawl PetMD pages for local research.")
    parser.add_argument("--start-url", action="append", default=None, help="Seed URL. Can be used multiple times.")
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP_URL, help="HTML sitemap page used for seed discovery.")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=10, help="Number of pages to save.")
    parser.add_argument("--all", action="store_true", help="Save pages until discovery is exhausted or --max-discovery is reached.")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay between HTTP requests in seconds.")
    parser.add_argument("--contains", action="append", default=[], help="Prioritize and save URLs containing this text. Can be used multiple times.")
    parser.add_argument("--include-index-pages", action="store_true", help="Save category/index pages too.")
    parser.add_argument("--min-chars", type=int, default=700, help="Minimum extracted text characters required for saving.")
    parser.add_argument("--max-discovery", type=int, default=1000, help="Maximum number of URLs to visit/discover in one run.")
    parser.add_argument(
        "--no-saved-page-discovery",
        action="store_true",
        help="Do not continue discovering links from pages that are saved as articles.",
    )
    args = parser.parse_args()

    limit = None if args.all else args.limit
    start_urls = args.start_url or [DEFAULT_START_URL]
    records = crawl(
        start_urls=start_urls,
        sitemap_url=args.sitemap_url,
        out_dir=Path(args.out),
        limit=limit,
        delay=args.delay,
        contains=args.contains,
        article_only=not args.include_index_pages,
        min_chars=args.min_chars,
        max_discovery=args.max_discovery,
        discover_from_saved_pages=not args.no_saved_page_discovery,
    )
    saved = sum(1 for record in records if record.get("status") == "crawled")
    print(f"Saved {saved} pages to {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
