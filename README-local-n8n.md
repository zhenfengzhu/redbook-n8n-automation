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

`start-n8n.ps1` sets `NODES_EXCLUDE=[]` because n8n 2.x disables the Execute Command node by default. The local smoke workflow needs that node to call Python scripts.

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

## FEDIAF Automation

Use the automation runner when you want one command to crawl, translate, and write a run summary with logs.

Quick test run with 5 pages and Chinese page translation:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --limit 5
```

Full crawl, PDF download, and Chinese translation:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --full --install-models
```

Translate an existing crawl without crawling again:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --no-crawl --translate-pages --translate-pdfs
```

Preview the commands without running them:

```powershell
python scripts\fediaf_automation.py --full --dry-run
```

Automation logs and summaries are written under `data/fediaf-automation-logs/`:

- `<run-id>/crawl.log`: crawler output for that run.
- `<run-id>/translate.log`: translation output for that run.
- `<run-id>/run-summary.json`: command, timing, status, and output counts.
- `latest-run.json`: copy of the latest run summary.

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

## Crawl PetMD

The PetMD crawler is separate from the FEDIAF crawler. It reads PetMD's HTML sitemap page and same-site links, respects `robots.txt` blocks for search/login/admin-style paths, and saves extracted article text as Markdown under `data/`.

Small smoke run:

```powershell
cd D:\AUnityProject\RedBook
python scripts\petmd_crawler.py --start-url https://www.petmd.com/dog/conditions --contains /dog/conditions --limit 3 --out data\petmd-smoke
```

Default local research run:

```powershell
python scripts\petmd_crawler.py --limit 10 --out data\petmd
```

Topic-focused examples:

```powershell
python scripts\petmd_crawler.py --start-url https://www.petmd.com/dog/centers/nutrition --contains /dog/ --limit 20 --out data\petmd-dog
python scripts\petmd_crawler.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 20 --out data\petmd-cat
```

Useful options:

- `--delay 3`: delay between requests in seconds; default is 3.
- `--contains TEXT`: only save/continue priority for URLs containing this text; can be repeated.
- `--include-index-pages`: save category/index pages too; by default the script prefers article-like pages.
- `--no-saved-page-discovery`: discover links from category pages, but stop expanding from pages already saved as articles.
- `--max-discovery 1000`: stop after this many visited URLs.
- `--all`: continue until discovery is exhausted or `--max-discovery` is reached.

Outputs:

- `pages/`: one Markdown file per saved page, with source URL, timestamps, author/review metadata when available, description, image URL, and extracted text.
- `index.json`: crawl records, skipped/discovered pages, metadata, and Markdown paths.
- `summary.json`: run summary.

Use PetMD content as local research/reference material. Do not republish copied article text verbatim; convert it into original Xiaohongshu scripts with source attribution and veterinary-scope boundaries.

## Translate PetMD and Generate Topic Cards

Translate a PetMD crawl into Chinese:

```powershell
cd D:\AUnityProject\RedBook
python scripts\translate_petmd_to_zh.py --input data\petmd --out data\petmd-zh
```

Generate Xiaohongshu-oriented internal topic cards from the Chinese translations:

```powershell
python scripts\generate_petmd_topic_cards.py --translation-index data\petmd-zh\translation-index.json --output-dir data\petmd-topic-cards
```

Smoke example using the cat nutrition crawl:

```powershell
python scripts\petmd_crawler.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 1 --out data\petmd-cat-smoke
python scripts\translate_petmd_to_zh.py --input data\petmd-cat-smoke --out data\petmd-cat-smoke-zh --overwrite
python scripts\generate_petmd_topic_cards.py --translation-index data\petmd-cat-smoke-zh\translation-index.json --output-dir data\petmd-cat-topic-smoke
```

Topic-card outputs:

- `source-items.jsonl`: normalized PetMD source records.
- `topic-cards.jsonl`: creator-facing topic cards with source URL, translated title, topic, cover hook, Xiaohongshu angle, evidence excerpt, risk level, and veterinary-boundary note.
- `review-report.md`: quick human-readable preview.
- `summary.json`: counts and output paths.

PetMD content often includes medical symptoms, treatments, prescription diets, or veterinary recommendations. The card generator intentionally marks those records for manual review and adds a veterinary-boundary note.

## PetMD Automation Runner

Use the PetMD automation runner when you want one command to crawl, translate, generate topic cards, and write run logs:

```powershell
cd D:\AUnityProject\RedBook
python scripts\petmd_automation.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 20 --crawl-out data\petmd-cat --translate-out data\petmd-cat-zh --cards-out data\petmd-cat-topic-cards
```

Smoke run:

```powershell
python scripts\petmd_automation.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 1 --delay 1 --max-discovery 80 --crawl-out data\petmd-auto-smoke --translate-out data\petmd-auto-smoke-zh --cards-out data\petmd-auto-smoke-cards --log-root data\petmd-auto-smoke-logs --overwrite-translation
```

Preview commands without running:

```powershell
python scripts\petmd_automation.py --start-url https://www.petmd.com/dog/centers/nutrition --contains /dog/ --limit 20 --dry-run
```

Logs and summaries:

- `data\petmd-automation-logs\<run-id>\crawl.log`
- `data\petmd-automation-logs\<run-id>\translate.log`
- `data\petmd-automation-logs\<run-id>\cards.log`
- `data\petmd-automation-logs\<run-id>\run-summary.json`
- `data\petmd-automation-logs\latest-run.json`

Large PetMD source crawls verified on 2026-07-11:

- `data\petmd-health-focused`: 2,226 crawled health-focused pages from dog/cat conditions, symptoms, nutrition, care, parasites, medication, recalls, healthy weight, and veterinary terms paths. See `data\petmd-health-focused\final-crawl-summary.md`.
- `data\petmd-health-all`: 3,453 broader PetMD pages from a less-filtered crawl. This includes health pages plus broader site content. See `data\petmd-health-all\final-crawl-summary.md`.

For large batches, crawl first and translate later in smaller topic groups. Translating thousands of PetMD pages in one run is slow and produces machine-translation text that still needs editorial review.

## Generate Topic Candidates

Generate stable JSONL records from the existing Chinese translations before wiring n8n or Baserow:

```powershell
cd D:\AUnityProject\RedBook
python scripts\generate_topic_candidates.py
```

Useful test run:

```powershell
python scripts\generate_topic_candidates.py --limit 5 --output-dir data\topic-pipeline-smoke
```

Outputs are written under `data/topic-pipeline/`:

- `source-items.jsonl`: one normalized source record per translated page or PDF.
- `topic-candidates.jsonl`: first-pass topic candidates with score, risk, source paths, evidence excerpt, and review status.
- `eligible-topic-candidates.jsonl`: only candidates that passed the local score/risk gate and are suitable for n8n/Baserow ingestion.
- `review-report.md`: human-readable review summary for quick manual inspection.
- `summary.json`: generation counts and missing translated paths.

For n8n, read `eligible-topic-candidates.jsonl`. Records with `status = 人工复核` stay in `topic-candidates.jsonl` and should not be auto-written into a publish queue.

## Export Topic Ingestion Package

Convert eligible candidates into Baserow and n8n ingestion files:

```powershell
cd D:\AUnityProject\RedBook
python scripts\export_topic_ingestion.py
```

Outputs are written under `data/topic-ingestion/`:

- `baserow-topic-candidates.csv`: CSV import file for Baserow.
- `baserow-topic-candidates.json`: JSON array with the same fields.
- `n8n-items.json`: n8n-compatible array shaped as `{ "json": { ... } }`.
- `field-mapping.md`: suggested Baserow field types and n8n flow notes.
- `summary.json`: export counts and output paths.

Use `candidate_id` as the upsert/deduplication key.

## Import n8n Smoke Workflow

The first n8n workflow does not write to Baserow. It only verifies that n8n can call the local pipeline and read the prepared topic ingestion file.

Workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\topic-ingestion-smoke.workflow.json
```

Import it in the n8n UI, then run it manually. The workflow executes:

```powershell
python D:\AUnityProject\RedBook\scripts\n8n_topic_ingestion_smoke.py
```

CLI import and execution commands:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\topic-ingestion-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookTopicIngestionSmoke' --rawOutput
```

Use `N8N_RUNNERS_BROKER_PORT=5689` when the local n8n server is already running on ports `5678` and `5679`.

Outputs are written under `data/n8n-topic-smoke/`:

- `latest-summary.json`: machine-readable run summary.
- `latest-summary.md`: human-readable run summary.

Expected current result: 58 records read from `data/topic-ingestion/n8n-items.json`.

Verified on 2026-07-08:

- workflow imported as `RedBookTopicIngestionSmoke`;
- CLI execution succeeded with `status = success`;
- `latest-summary.json` and `latest-summary.md` were updated at local time `2026-07-08 18:07:24`;
- output summary: 58 records, 34 records requiring a veterinary boundary, risk split `中=34`, `低=24`.

## Upsert Local Topic Database

Before connecting real Baserow credentials, use the local SQLite topic database to verify upsert behavior and manual-review state handling.

Run directly:

```powershell
cd D:\AUnityProject\RedBook
python scripts\upsert_topic_database.py
```

Outputs are written under `data/topic-database/`:

- `topic-candidates.sqlite`: local topic database.
- `latest-upsert-summary.json`: machine-readable upsert summary.
- `latest-upsert-summary.md`: human-readable upsert summary.
- `baserow.env.example`: placeholder environment variables for a later Baserow API connection.

The table uses `candidate_id` as the primary key. Re-running the script updates changed records, skips unchanged records, and preserves existing manual `status` values unless `--overwrite-status` is passed.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\topic-database-upsert-smoke.workflow.json
```

CLI import and execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\topic-database-upsert-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookTopicDatabaseUpsertSmoke' --rawOutput
```

Verified on 2026-07-08:

- workflow imported as `RedBookTopicDatabaseUpsertSmoke`;
- CLI execution succeeded with `status = success`;
- local database contains 58 topic candidates;
- repeat run result: `inserted=0`, `updated=0`, `unchanged=58`;
- `latest-upsert-summary.json` and `latest-upsert-summary.md` were updated at local time `2026-07-08 18:18:32`.

## Sync Feishu Bitable Topic Table

Feishu Bitable is now the preferred external topic table for this workspace. The sync layer reads the exported topic candidates and can create or update Feishu Bitable records by `candidate_id`. It is dry-run by default and only writes to Feishu when `--sync` is passed.

Feishu official API references:

- Tenant access token: https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
- Bitable records: https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create
- Bitable record search: https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/search

Prepare the local env template and field mapping:

```powershell
cd D:\AUnityProject\RedBook
python scripts\sync_feishu_bitable_topics.py --write-env-template
```

Copy `data\feishu-bitable\feishu-bitable.env.example` to `data\feishu-bitable\feishu-bitable.env`, then fill:

```text
FEISHU_BASE_URL=https://open.feishu.cn
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BITABLE_APP_TOKEN=
FEISHU_BITABLE_TABLE_ID=
FEISHU_UPSERT_FIELD=candidate_id
FEISHU_PAGE_SIZE=500
```

Before real sync, create the Feishu Bitable table fields from:

```text
D:\AUnityProject\RedBook\data\feishu-bitable\field-mapping.md
```

Or let the local setup script create the required fields:

```powershell
cd D:\AUnityProject\RedBook
python scripts\setup_feishu_bitable_fields.py
python scripts\setup_feishu_bitable_fields.py --create
```

The field setup script writes:

- `data\feishu-bitable\latest-field-setup-summary.json`
- `data\feishu-bitable\latest-field-setup-summary.md`

Dry-run locally:

```powershell
cd D:\AUnityProject\RedBook
python scripts\sync_feishu_bitable_topics.py
```

Real sync after configuring Feishu Bitable:

```powershell
python scripts\sync_feishu_bitable_topics.py --sync
```

Useful first real test:

```powershell
python scripts\sync_feishu_bitable_topics.py --sync --limit 3
```

Outputs are written under `data/feishu-bitable/`:

- `latest-sync-summary.json`: machine-readable sync result.
- `latest-sync-summary.md`: human-readable sync result.
- `feishu-bitable.env.example`: local configuration template.
- `field-mapping.md`: Feishu Bitable field setup guide.

Existing remote `status` values are preserved by default on updates. Pass `--overwrite-status` only when you intentionally want local candidate status to overwrite Feishu review status.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\feishu-bitable-topic-sync-smoke.workflow.json
```

The current workflow is a dry-run smoke test:

```text
Manual Trigger
  -> Feishu Bitable Sync Dry Run
```

CLI import and execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\feishu-bitable-topic-sync-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookFeishuBitableTopicSyncSmoke' --rawOutput
```

Verified on 2026-07-10:

- script passed `python -m py_compile`;
- local dry-run read 58 records and skipped 0 duplicate input records;
- workflow imported as `RedBookFeishuBitableTopicSyncSmoke`;
- workflow remains inactive, `active=0`;
- CLI execution succeeded with `status = success`;
- latest dry-run output was written at local time `2026-07-10 16:32:22`;
- real Feishu app credentials and the Bitable URL are configured locally in `data\feishu-bitable\feishu-bitable.env`; do not print or commit that file;
- the field setup script resolves the target app/table and can list fields;
- after sharing the Bitable document to the Feishu group that contains the custom app, `python scripts\setup_feishu_bitable_fields.py --create` created all 21 required fields;
- a first real sync test with `python scripts\sync_feishu_bitable_topics.py --sync --limit 3` created 3 Feishu Bitable records;
- a repeat sync with the same command returned `created=0`, `updated=0`, and `unchanged=3`, confirming `candidate_id` upsert behavior;
- full sync with `python scripts\sync_feishu_bitable_topics.py --sync` created the remaining 55 records;
- a repeat full sync returned `created=0`, `updated=0`, and `unchanged=58`, confirming full-table idempotency.

## Sync Baserow Topic Table

Baserow is now an optional fallback path. Prefer Feishu Bitable unless there is a specific need for Baserow Cloud or self-hosted Baserow.

The Baserow sync layer reads the exported topic candidates and can create or update rows in a Baserow table by `candidate_id`. It is dry-run by default and only writes to Baserow when `--sync` is passed.

Baserow official API guide: https://baserow.io/user-docs/database-api

Prepare the local env template:

```powershell
cd D:\AUnityProject\RedBook
python scripts\sync_baserow_topics.py --write-env-template
```

Copy `data\baserow\baserow.env.example` to `data\baserow\baserow.env`, then fill:

```text
BASEROW_API_URL=https://api.baserow.io
BASEROW_TOKEN=
BASEROW_TABLE_ID=
BASEROW_UPSERT_FIELD=candidate_id
BASEROW_READ_PAGE_SIZE=200
```

Before real sync, create the Baserow table fields from:

```text
D:\AUnityProject\RedBook\data\topic-ingestion\field-mapping.md
```

Dry-run locally:

```powershell
cd D:\AUnityProject\RedBook
python scripts\sync_baserow_topics.py
```

Real sync after configuring Baserow:

```powershell
python scripts\sync_baserow_topics.py --sync
```

Useful first real test:

```powershell
python scripts\sync_baserow_topics.py --sync --limit 3
```

Outputs are written under `data/baserow/`:

- `latest-sync-summary.json`: machine-readable sync result.
- `latest-sync-summary.md`: human-readable sync result.
- `baserow.env.example`: local configuration template.

Existing remote `status` values are preserved by default on updates. Pass `--overwrite-status` only when you intentionally want local candidate status to overwrite Baserow review status.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\baserow-topic-sync-smoke.workflow.json
```

The current workflow is a dry-run smoke test:

```text
Manual Trigger
  -> Baserow Sync Dry Run
```

CLI import and execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\baserow-topic-sync-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookBaserowTopicSyncSmoke' --rawOutput
```

Verified on 2026-07-09:

- script passed `python -m py_compile`;
- local dry-run read 58 records and skipped 0 duplicate input records;
- workflow imported as `RedBookBaserowTopicSyncSmoke`;
- workflow remains inactive, `active=0`;
- CLI execution succeeded with `status = success`;
- latest dry-run output was written at local time `2026-07-09 11:18:09`;
- no `BASEROW_TOKEN` or `BASEROW_TABLE_ID` is configured yet, so no external Baserow rows were written.

## Generate Local Review Portal

Before a real Baserow/Notion review table is connected, generate a local static review portal and notification summary from the SQLite topic database.

Run directly:

```powershell
cd D:\AUnityProject\RedBook
python scripts\generate_review_portal.py
```

Outputs are written under `data/review-portal/`:

- `index.html`: local static topic review page.
- `review-queue.json`: structured review queue.
- `notification-summary.md`: notification-ready summary.
- `review-actions-template.csv`: status review action template for later manual processing.

Open the review portal directly in a browser:

```text
D:\AUnityProject\RedBook\data\review-portal\index.html
```

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\topic-review-portal-smoke.workflow.json
```

CLI import and execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\topic-review-portal-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookTopicReviewPortalSmoke' --rawOutput
```

Verified on 2026-07-08:

- workflow imported as `RedBookTopicReviewPortalSmoke`;
- CLI execution succeeded with `status = success`;
- review portal contains 58 queue records;
- 34 records require a veterinary boundary;
- review portal outputs were updated at local time `2026-07-08 18:24:12`.

## Apply Local Review Actions

The review portal exports `review-actions-template.csv`. Fill `next_status` for rows you want to change, then validate or apply those actions to the local SQLite database.

Allowed statuses:

```text
待评估
人工复核
可写
已写
放弃
```

Dry-run against the real local database:

```powershell
cd D:\AUnityProject\RedBook
python scripts\apply_review_actions.py --dry-run
```

Apply real actions after reviewing the CSV:

```powershell
python scripts\apply_review_actions.py
```

Outputs are written under `data/review-actions/`:

- `latest-apply-summary.json`: structured validation/apply result.
- `latest-apply-summary.md`: human-readable validation/apply result.

The script checks `candidate_id`, validates `next_status`, and rejects stale rows when `current_status` no longer matches the database. Real applies update `topic_candidates.status` and write an audit row to `topic_review_actions`. Dry-run does not change the database.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\topic-review-actions-smoke.workflow.json
```

CLI import and execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\topic-review-actions-smoke.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookTopicReviewActionsSmoke' --rawOutput
```

Verified on 2026-07-08:

- workflow imported as `RedBookTopicReviewActionsSmoke`;
- CLI execution succeeded with `status = success`;
- real database dry-run result: `action_rows=58`, `skipped_blank_next_status=58`;
- real database remained `待评估=58`;
- smoke copy test applied 3 status changes and logged 3 review actions without touching the real database.

## Generate Daily Review Digest

The daily digest refreshes the local review portal, validates review actions in dry-run mode, and writes a notification-ready summary.

Run directly:

```powershell
cd D:\AUnityProject\RedBook
python scripts\generate_review_portal.py
python scripts\apply_review_actions.py --dry-run
python scripts\generate_daily_digest.py
```

Outputs are written under `data/daily-digest/`:

- `latest-digest.md`: latest notification-ready Markdown digest.
- `latest-digest.json`: latest machine-readable digest.
- `<YYYY-MM-DD>-digest.md`: date-stamped archive copy.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\daily-review-digest-schedule.workflow.json
```

The workflow contains:

```text
Manual Trigger
Daily 09:00 Schedule
  -> Refresh Review Portal
  -> Validate Review Actions
  -> Generate Daily Digest
  -> Send Feishu Digest
```

CLI import, test execution, and publish:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\daily-review-digest-schedule.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookDailyReviewDigestSchedule' --rawOutput
.\node_modules\.bin\n8n.cmd publish:workflow --id 'RedBookDailyReviewDigestSchedule'
```

Restart n8n after publishing so the schedule is activated.

Verified on 2026-07-09:

- workflow imported as `RedBookDailyReviewDigestSchedule`;
- CLI execution succeeded with `status = success`;
- `latest-digest.md` and `latest-digest.json` were updated at local time `2026-07-09 10:54:22`;
- workflow was published and activated after n8n restart;
- n8n reports `active=1` for `RedBookDailyReviewDigestSchedule`;
- current schedule: daily at `09:00` Asia/Shanghai;
- this workflow now sends the daily digest to Feishu with `send_feishu_digest.py --send`;
- latest scheduled-flow Feishu verification returned `result=sent`, `http_status=200`, and Feishu `StatusMessage=success` at local time `2026-07-09 10:54:23`.

## Feishu Digest Notification

The Feishu integration sends the latest local daily digest to a Feishu custom bot webhook. The standalone script is dry-run by default, but the active daily n8n schedule now calls it with `--send`.

Feishu official guide: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

Configuration template:

```powershell
cd D:\AUnityProject\RedBook
python scripts\send_feishu_digest.py --write-env-template
```

Copy `data\feishu\feishu.env.example` to `data\feishu\feishu.env`, then fill the real bot values:

```text
FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=
FEISHU_KEYWORD=RedBook
```

- `FEISHU_WEBHOOK_URL` is required for real sending.
- `FEISHU_WEBHOOK_SECRET` is optional and only needed when the Feishu bot enables signed requests.
- `FEISHU_KEYWORD` is optional; keep `RedBook` if the bot uses keyword safety checks.

Dry-run locally:

```powershell
cd D:\AUnityProject\RedBook
python scripts\send_feishu_digest.py
```

Send for real after configuring `data\feishu\feishu.env`:

```powershell
cd D:\AUnityProject\RedBook
python scripts\send_feishu_digest.py --send
```

Outputs are written under `data/feishu/`:

- `latest-payload.json`: Feishu text message payload.
- `latest-send-summary.json`: machine-readable send summary.
- `latest-send-summary.md`: human-readable send summary.
- `feishu.env.example`: local configuration template.

n8n workflow file:

```text
D:\AUnityProject\RedBook\n8n-workflows\feishu-digest-notification.workflow.json
```

The workflow contains:

```text
Manual Trigger
  -> Generate Daily Digest
  -> Send Feishu Digest
```

CLI import and test execution:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
$env:PATH='D:\AUnityProject\RedBook\tools\node-v22.23.0-win-x64;' + $env:PATH
$env:N8N_USER_FOLDER='D:\AUnityProject\RedBook\n8n-runtime\data'
$env:N8N_SECURE_COOKIE='false'
$env:NODES_EXCLUDE='[]'
.\node_modules\.bin\n8n.cmd import:workflow --input 'D:\AUnityProject\RedBook\n8n-workflows\feishu-digest-notification.workflow.json'

$env:N8N_RUNNERS_BROKER_PORT='5689'
.\node_modules\.bin\n8n.cmd execute --id 'RedBookFeishuDigestNotification' --rawOutput
```

Verified on 2026-07-09:

- workflow imported as `RedBookFeishuDigestNotification`;
- workflow remains inactive, `active=0`;
- CLI execution succeeded with `status = success`;
- Feishu standalone script sent successfully at local time `2026-07-09 10:50:37`;
- Feishu n8n workflow sent successfully at local time `2026-07-09 10:52:39`;
- latest daily scheduled-flow Feishu step sent successfully at local time `2026-07-09 10:54:23`;
- `data\feishu\feishu.env` exists locally and contains the real webhook configuration; do not commit or print it.

The active daily workflow uses this command:

```text
python "D:\AUnityProject\RedBook\scripts\send_feishu_digest.py" --send
```

## Notes

- Docker is not installed on this machine, so this setup uses Node/npm.
- This workspace uses portable Node `v22.23.0` from `tools/` because n8n requires Node `>=22.22`.
- `N8N_SECURE_COOKIE=false` is set because this local instance runs on plain HTTP.
- Keep credentials and workflow data inside `n8n-runtime/data`; do not commit that folder.
