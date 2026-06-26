# Local n8n Setup

This workspace contains two parts:

- `n8n/`: cloned n8n source repository.
- `n8n-runtime/`: local runnable n8n installation based on the published npm package.
- `tools/node-v22.23.0-win-x64/`: portable Node.js used by the runtime.

## Start

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
..\tools\node-v22.23.0-win-x64\npm.cmd install
.\start-n8n.ps1
```

Then open:

```text
http://localhost:5678
```

Runtime data is stored in:

```text
D:\AUnityProject\RedBook\n8n-runtime\data
```

## Stop

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
.\stop-n8n.ps1
```

## Crawl FEDIAF

The crawler reads FEDIAF / EuropeanPetFood sitemap pages, respects the site's `Crawl-delay`, and writes Markdown plus a JSON index.

Fetch the first 5 pages:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_crawler.py --limit 5
```

Fetch core pet food and self-regulation pages:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_crawler.py --contains self-regulation --contains pet-food-facts --limit 12 --out data\fediaf-core
```

Fetch the full site and download PDFs linked from crawled pages:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_crawler.py --all --download-pdfs --out data\fediaf-full
```

Outputs are written under `data/`, which is ignored by Git:

- `pages/`: Markdown copy of each crawled page.
- `index.json`: page crawl index, including source URL, output path, fetch time, and PDF links found on each page.
- `pdfs/`: downloaded PDF files.
- `pdf-index.json`: PDF download index, including source pages, output path, status, and file size.

## Translate FEDIAF to Chinese

Install the local translation dependencies:

```powershell
python -m pip install --user argostranslate pymupdf
```

Install the Argos translation packages needed for this crawl. English pages/PDFs use `en -> zh`; known non-English PDFs use `lt/pl/ro/sl/hu -> en -> zh`:

```powershell
@'
import argostranslate.package

wanted = [
    ("en", "zh"),
    ("lt", "en"),
    ("pl", "en"),
    ("ro", "en"),
    ("sl", "en"),
    ("hu", "en"),
]

argostranslate.package.update_package_index()
packages = argostranslate.package.get_available_packages()
for source, target in wanted:
    package = next((item for item in packages if item.from_code == source and item.to_code == target), None)
    if package is None:
        raise SystemExit(f"Missing package {source}->{target}")
    package.install()
'@ | python -
```

Translate crawled pages and extracted PDF text:

```powershell
cd D:\AUnityProject\RedBook
python scripts\translate_fediaf_to_zh.py --pages --pdfs --out data\fediaf-full-zh
```

Useful resume commands:

```powershell
python scripts\translate_fediaf_to_zh.py --pages --out data\fediaf-full-zh
python scripts\translate_fediaf_to_zh.py --pdfs --out data\fediaf-full-zh
```

Outputs are written under `data/fediaf-full-zh/`:

- `pages/`: translated Markdown pages.
- `pdfs/`: translated Markdown generated from text extracted out of downloaded PDFs.
- `translation-index.json`: translation run index.
- `translation-cache.sqlite3`: local translation cache used for resume and duplicate PDF content.

PDF notes:

- Output is machine translation and should be reviewed before publication, especially nutrition terms and non-English PDFs translated through English.
- The script creates translated Markdown from extracted PDF text; it does not edit or recreate the original PDF layout.
- Image-only or scanned PDF pages can have no extractable text. Those pages are recorded as `未抽取到可翻译文本。`; add OCR if full image-page translation is required.

## Notes

- Docker is not installed on this machine, so this setup uses Node/npm.
- This workspace uses portable Node `v22.23.0` from `tools/` because n8n requires Node `>=22.22`.
- `N8N_SECURE_COOKIE=false` is set because this local instance runs on plain HTTP.
- Keep credentials and workflow data inside `n8n-runtime/data`; do not commit that folder.
