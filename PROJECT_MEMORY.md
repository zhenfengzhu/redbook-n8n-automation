# Project Memory

Last updated: 2026-08-26

## Project Purpose

This repository is a local automation workspace for building a Xiaohongshu/RED pet health content research and topic pipeline.

The current focus is:

- Collect authoritative pet nutrition and pet health materials.
- Translate useful English source material into Chinese.
- Keep only materials useful for a Xiaohongshu pet health creator.
- Use n8n for scheduling, smoke verification, AI screening, topic generation, and later writing selected topics into a topic database.

The user is working toward becoming or supporting a Xiaohongshu pet health creator. Practical content value matters more than mirroring entire websites.

## Account Strategy Memory

The account's confirmed business constraints, positioning, product-selection priorities, current product hypothesis, compliance boundaries, and unresolved decisions are stored separately in:

```text
ACCOUNT_MEMORY.md
```

Read `ACCOUNT_MEMORY.md` before account positioning, content strategy, product selection, supplier evaluation, monetization, or customer-service work. Keep confirmed user decisions separate from recommendations that still require validation.

## Current Reality

Important: n8n is installed locally and has six imported smoke workflows, one published daily digest schedule workflow that now sends Feishu notifications, and one inactive standalone Feishu digest workflow for manual sends. It is not yet connected to the full scraping workflow or to production Feishu Bitable/Baserow/Notion writes.

Current working flow:

```text
Python scripts do the crawling, PDF download, translation, and local reports.
n8n currently verifies that it can call local topic-ingestion, topic-database upsert, review-portal, review-actions, Feishu Bitable sync dry-run, and Baserow sync dry-run smoke scripts.
n8n also has one active schedule that generates a daily digest file and sends it to Feishu at 09:00 Asia/Shanghai.
n8n also has one inactive standalone Feishu digest workflow that can be run manually and uses send_feishu_digest.py --send.
```

Do not describe this project as a fully active n8n automation project yet. It is currently a Python automation project with a local n8n runtime, six smoke workflows, one active digest-plus-Feishu notification schedule, and one inactive standalone Feishu notification workflow.

## Workspace

Root path:

```text
D:\AUnityProject\RedBook
```

Known sibling workspace:

```text
D:\AUnityProject\PushPetNews
```

## Important Directories

```text
D:\AUnityProject\RedBook
+-- .agents\
+-- data\
+-- n8n\
+-- n8n-runtime\
+-- runtime_failed_partial\
+-- scripts\
+-- tools\
+-- 小红书图文管理\
+-- README-local-n8n.md
+-- 小红书宠物健康选题自动化实施方案.md
+-- PROJECT_MEMORY.md
+-- AGENTS.md
```

Directory meanings:

- `.agents\`: currently available for AI-agent support files.
- `data\`: ignored by Git; stores crawled FEDIAF material, translations, cleanup reports, and automation logs.
- `n8n\`: cloned n8n source repository. It is large and should not be touched unless explicitly requested.
- `n8n-runtime\`: local runnable n8n installation based on the npm package.
- `runtime_failed_partial\`: old partial runtime output. Do not delete unless the user explicitly asks.
- `scripts\`: active Python automation scripts.
- `tools\`: portable local tools, including Node used for n8n.
- `小红书图文管理\`: user/content work area. Do not delete or refactor unless explicitly requested.

## Xiaohongshu Post Creation Workflow

For Xiaohongshu/RED pet health posts:

1. Draft the plain text content first, including the proposed title, page-by-page copy, caption, evidence notes, and veterinary boundary.
2. Send the text draft for user review and wait for explicit approval or revision feedback.
3. Only after the user is satisfied with the text should the project generate HTML carousel pages, image assets, PNG exports, or other visual deliverables.
4. Do not skip directly from a topic idea to HTML generation unless the user explicitly asks to bypass text review for that specific post.

## Active Scripts

### `scripts\fediaf_crawler.py`

Purpose:

- Crawl FEDIAF / EuropeanPetFood pages.
- Read `sitemap.xml`.
- Respect crawl delay from `robots.txt` when available.
- Parse HTML into Markdown.
- Extract PDF links.
- Optionally download PDFs.
- Write `index.json` and `pdf-index.json`.

Common command:

```powershell
python scripts\fediaf_crawler.py --all --download-pdfs --out data\fediaf-full
```

Limitations:

- Static HTML crawler.
- Depends on sitemap-style discovery.
- Does not render JavaScript.
- Does not handle login.
- Does not bypass anti-bot systems.
- Only partially interprets robots behavior through crawl delay.

### `scripts\translate_fediaf_to_zh.py`

Purpose:

- Translate crawled Markdown pages into Chinese.
- Extract text from downloaded PDFs and translate extracted text to Chinese Markdown.
- Uses Argos Translate and local translation cache.

Common command:

```powershell
python scripts\translate_fediaf_to_zh.py --pages --pdfs --out data\fediaf-full-zh
```

Notes:

- Machine translation must be reviewed before publication.
- PDF layout is not recreated. Output is Markdown generated from extracted text.
- Image-only or scanned PDFs may have no extractable text.

### `scripts\fediaf_automation.py`

Purpose:

- Run the crawl and translation workflow from one command.
- Optionally install required Argos translation models.
- Write logs and run summaries.

Common commands:

```powershell
python scripts\fediaf_automation.py --full --install-models
python scripts\fediaf_automation.py --no-crawl --translate-pages --translate-pdfs
python scripts\fediaf_automation.py --full --dry-run
```

Logs:

```text
data\fediaf-automation-logs\<run-id>\crawl.log
data\fediaf-automation-logs\<run-id>\translate.log
data\fediaf-automation-logs\<run-id>\run-summary.json
data\fediaf-automation-logs\latest-run.json
```

### `scripts\petmd_crawler.py`

Purpose:

- Crawl PetMD pages for local pet-health content research.
- Read the PetMD HTML sitemap page and same-site links.
- Respect `robots.txt` blocks for search, login, admin, and similar paths.
- Extract article-like metadata from JSON-LD / Next.js page data when available.
- Write Markdown pages, `index.json`, and `summary.json` under `data\`.

Common commands:

```powershell
python scripts\petmd_crawler.py --limit 10 --out data\petmd
python scripts\petmd_crawler.py --start-url https://www.petmd.com/dog/conditions --contains /dog/conditions --limit 3 --out data\petmd-smoke
python scripts\petmd_crawler.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 20 --out data\petmd-cat
```

Notes:

- Default delay is 3 seconds between requests.
- Default mode prefers article-like pages and skips short/index-only pages.
- PetMD material should be treated as local research/reference input, not text to republish verbatim.
- Any Xiaohongshu transformation should keep source attribution and veterinary-scope boundaries.

### `scripts\translate_petmd_to_zh.py`

Purpose:

- Translate PetMD crawler Markdown into Chinese Markdown.
- Reuse the existing Argos translation router and SQLite translation cache from `translate_fediaf_to_zh.py`.
- Preserve PetMD source URL, author, publish/update dates, review metadata, description, image URL, and original title.
- Write `translation-index.json` under the selected output directory.

Common command:

```powershell
python scripts\translate_petmd_to_zh.py --input data\petmd --out data\petmd-zh
```

### `scripts\generate_petmd_topic_cards.py`

Purpose:

- Generate Xiaohongshu-oriented internal topic cards from translated PetMD pages.
- Write normalized `source-items.jsonl`, creator-facing `topic-cards.jsonl`, `review-report.md`, and `summary.json`.
- Include source attribution, evidence excerpt, cover hook, content angle, risk level, and veterinary-boundary note.
- Mark high-risk medical or prescription-related records as `人工复核`.

Common command:

```powershell
python scripts\generate_petmd_topic_cards.py --translation-index data\petmd-zh\translation-index.json --output-dir data\petmd-topic-cards
```

### `scripts\petmd_automation.py`

Purpose:

- Run PetMD crawl, Chinese translation, and topic-card generation from one command.
- Write per-step logs and `run-summary.json` under `data\petmd-automation-logs\` by default.
- Support `--dry-run`, `--no-crawl`, `--no-translate`, and `--no-cards` for staged operation.

Common command:

```powershell
python scripts\petmd_automation.py --start-url https://www.petmd.com/cat/centers/nutrition --contains /cat/ --limit 20 --crawl-out data\petmd-cat --translate-out data\petmd-cat-zh --cards-out data\petmd-cat-topic-cards
```

Verified smoke on 2026-07-11:

- Command used `--limit 1 --delay 1 --max-discovery 80`.
- Output had 1 crawled page, 1 translated page, and 1 topic card.
- Card result had `manual_review=1`, `vet_boundary_required=1`, and `missing_paths=0`.

Large PetMD crawl state verified on 2026-07-11:

- `data\petmd-health-focused` contains 2,226 indexed Markdown pages after stopping stale background crawlers and reconciling files to `index.json`.
- `data\petmd-health-focused\final-crawl-summary.md` and `.json` summarize the health-focused crawl.
- The health-focused run used category/path filtering for dog/cat conditions, symptoms, nutrition, general health, care, parasites, allergies, emergency, medications, PetMD medication pages, recalls, healthy weight, and veterinary terms.
- `data\petmd-health-all` contains 3,453 indexed Markdown pages from a broader PetMD crawl; it is useful as a fallback source library but includes broader site content.
- For future large PetMD work, prefer `--no-saved-page-discovery` so category pages discover articles, but article pages do not recursively expand the crawl graph.
- Do not translate all PetMD pages at once by default; translate and generate cards by topic/path batches.

## n8n Status

n8n runtime path:

```text
D:\AUnityProject\RedBook\n8n-runtime
```

Start:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
.\start-n8n.ps1
```

Stop:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
.\stop-n8n.ps1
```

Current status:

- n8n local runtime exists.
- A minimal smoke workflow has been imported into local n8n: `RedBookTopicIngestionSmoke`.
- The smoke workflow calls `scripts\n8n_topic_ingestion_smoke.py` through Execute Command and reads `data\topic-ingestion\n8n-items.json`.
- CLI execution succeeded on 2026-07-08 with `status = success`; output files were updated under `data\n8n-topic-smoke\`.
- A second smoke workflow has been imported into local n8n: `RedBookTopicDatabaseUpsertSmoke`.
- The upsert workflow calls `scripts\upsert_topic_database.py` and writes to `data\topic-database\topic-candidates.sqlite`.
- The local topic database currently contains 58 candidates; repeat upsert validation returned `inserted=0`, `updated=0`, `unchanged=58`.
- A third smoke workflow has been imported into local n8n: `RedBookTopicReviewPortalSmoke`.
- The review workflow calls `scripts\generate_review_portal.py` and writes `data\review-portal\index.html`, `review-queue.json`, `notification-summary.md`, and `review-actions-template.csv`.
- The local review queue currently contains 58 candidates; 34 require a veterinary boundary.
- A fourth smoke workflow has been imported into local n8n: `RedBookTopicReviewActionsSmoke`.
- The review-actions workflow calls `scripts\apply_review_actions.py --dry-run`, validating `data\review-portal\review-actions-template.csv` without changing the real SQLite database.
- Real review action applies are supported by `scripts\apply_review_actions.py`; smoke validation used a copied SQLite database and applied 3 status changes without touching the real database.
- A fifth smoke workflow has been imported into local n8n: `RedBookBaserowTopicSyncSmoke`.
- The Baserow sync smoke workflow calls `scripts\sync_baserow_topics.py` in dry-run mode.
- Baserow dry-run reads `data\topic-ingestion\baserow-topic-candidates.json`; latest validation read 58 records, kept 58 unique records, skipped 0 duplicate inputs, and wrote summaries under `data\baserow\` at local time 2026-07-09 11:18:09.
- Baserow credentials are not configured yet. `data\baserow\baserow.env.example` exists, but no real `data\baserow\baserow.env` should be assumed.
- A sixth smoke workflow has been imported into local n8n: `RedBookFeishuBitableTopicSyncSmoke`.
- The Feishu Bitable sync smoke workflow calls `scripts\sync_feishu_bitable_topics.py` in dry-run mode.
- Feishu Bitable is now the preferred external topic table path; Baserow remains an optional fallback.
- Feishu Bitable dry-run reads `data\topic-ingestion\baserow-topic-candidates.json`; latest validation read 58 records, kept 58 unique records, skipped 0 duplicate inputs, and wrote summaries under `data\feishu-bitable\` at local time 2026-07-10 16:32:22.
- Feishu Bitable app credentials and the target Bitable URL are configured locally in `data\feishu-bitable\feishu-bitable.env`; do not print, commit, or move that file into source-controlled docs.
- Feishu Bitable field setup now has a helper script: `scripts\setup_feishu_bitable_fields.py`.
- Latest Feishu Bitable field setup verification can resolve the app token and table id, and can list the target table's fields.
- After the Bitable document was shared to the Feishu group that contains the custom app, `scripts\setup_feishu_bitable_fields.py --create` created all 21 required fields.
- A first real Feishu Bitable sync test created 3 records with `python scripts\sync_feishu_bitable_topics.py --sync --limit 3`.
- A repeat 3-record sync returned `created=0`, `updated=0`, and `unchanged=3`, confirming `candidate_id` upsert behavior.
- Full Feishu Bitable sync has been run. The first full run created the remaining 55 records after the 3-record smoke test.
- A repeat full sync returned `created=0`, `updated=0`, and `unchanged=58`, confirming full-table idempotency.
- A daily digest workflow has been imported, published, and activated in local n8n: `RedBookDailyReviewDigestSchedule`.
- The daily digest workflow runs at 09:00 Asia/Shanghai and calls `generate_review_portal.py`, `apply_review_actions.py --dry-run`, `generate_daily_digest.py`, and `send_feishu_digest.py --send`.
- Daily digest outputs are written under `data\daily-digest\`; latest verified output was `latest-digest.md` / `latest-digest.json` at local time 2026-07-09 10:54:22.
- A Feishu digest notification workflow has been imported into local n8n: `RedBookFeishuDigestNotification`.
- The standalone Feishu workflow calls `generate_daily_digest.py`, then `send_feishu_digest.py --send`.
- The standalone Feishu workflow is inactive (`active=0`) and is intended for manual sends or troubleshooting.
- Feishu credentials are stored locally in `data\feishu\feishu.env`; do not print, commit, or move that file into source-controlled docs.
- Feishu outputs are written under `data\feishu\`; latest verified output was `latest-send-summary.json` / `latest-send-summary.md` at local time 2026-07-09 10:54:23.
- Real Feishu sending has been verified from the standalone script, standalone n8n workflow, and active daily schedule workflow. Latest response: `result=sent`, `http_status=200`, `StatusMessage=success`.
- No n8n workflow currently calls `fediaf_automation.py`.
- No n8n workflow currently writes to Feishu Bitable, Baserow, or Notion. Feishu Bitable script-based real sync has completed all 58 records, but the n8n workflow remains a dry-run smoke workflow.
- n8n now sends the daily digest to Feishu through the active daily schedule.
- n8n 2.x disables Execute Command by default; `n8n-runtime\start-n8n.ps1` sets `NODES_EXCLUDE=[]` for this local trusted runtime.

Planned role for n8n:

```text
Schedule trigger
  -> Execute Python scripts
  -> Read results
  -> AI summarize and score topics
  -> Write selected topics into Baserow or Notion
  -> Notify the user
```

Do not rewrite the Python crawler in n8n. Use n8n for orchestration, scheduling, credentials, notifications, AI screening, and database writes.

## FEDIAF Data State

The FEDIAF corpus was previously full-crawled, translated, and then cleaned for Xiaohongshu pet health usefulness.

Current retained data:

- `data\fediaf-full\index.json`: 42 source page records.
- `data\fediaf-full\pdf-index.json`: 35 retained PDF records.
- `data\fediaf-full-zh\translation-index.json`: 42 translated pages and 35 translated PDF Markdown records.

Cleanup report:

```text
data\fediaf-cleanup-report.json
```

Cleanup result:

- Deleted 98 low-value PDF records.
- Deleted 151 low-value page URLs.
- Deleted 5 old or test FEDIAF data directories.
- Deleted 12 old FEDIAF logs.
- Kept 35 useful PDF records and 42 useful page URLs.

Deleted categories included:

- Annual reports.
- Congress/event brochures.
- Press releases.
- Jobs and leadership appointments.
- Packaging and environmental policy.
- Fuel and renewable energy policy.
- Human-benefit reports.
- Non-English duplicate brochures.
- Hash-suffixed duplicates.
- Old guideline versions when newer versions were retained.

Kept categories included:

- FEDIAF nutritional guidelines.
- Rabbit nutritional guidelines.
- Pet food safety.
- Pet food labeling.
- Healthy weight.
- Senior dog nutrition.
- Water, carbohydrates, protein, additives.
- Homemade diets, vegetarian diets, grain-free diets.
- Prepared pet food benefits.
- Dry/wet pet food manufacturing.
- Body condition scoring.
- Useful animal welfare or responsible ownership materials.

## Important Documents

### `README-local-n8n.md`

Documents:

- Local n8n setup.
- Starting/stopping n8n.
- FEDIAF crawl commands.
- Translation commands.
- Automation runner commands.

### `小红书宠物健康选题自动化实施方案.md`

Detailed implementation plan for:

- Using n8n as an orchestrator.
- Building a pet health topic database.
- AI topic screening.
- Baserow/Notion integration.
- Notifications and manual review.

## Git and Ignore Rules

Relevant `.gitignore` behavior:

- `data\` is ignored.
- `n8n\` is ignored.
- `n8n-runtime\node_modules\` is ignored.
- `n8n-runtime\data\` is ignored.
- `n8n-runtime\*.log` is ignored.
- `tools\` is ignored.
- Python caches are ignored.

This means crawled data and local runtime artifacts are not intended to be committed.

Current known untracked or modified project files from recent work:

- `README-local-n8n.md` modified.
- `scripts\fediaf_automation.py` untracked.
- `小红书图文管理\` untracked.
- `小红书宠物健康选题自动化实施方案.md` untracked.
- `PROJECT_MEMORY.md` added.

Do not revert or delete user-created work.

## Safety Rules for Future Agents

- Do not delete `小红书图文管理\`.
- Do not delete `n8n\`, `n8n-runtime\`, `tools\`, or `scripts\` unless explicitly requested.
- Do not assume n8n is actively orchestrating the crawler.
- Do not recrawl or retranslate the full FEDIAF site unless explicitly requested.
- Do not publish pet health claims without evidence and veterinary-scope boundaries.
- Do not claim FEDIAF, AAFCO, WSAVA, FDA, or similar bodies certify a specific pet food unless verified.
- Keep source URLs and local paths attached to any generated topic.
- For medical symptoms, disease, dosage, supplements for illness, pregnancy, senior pets, puppies/kittens, chronic conditions, or abnormal appetite/stool/weight changes, add a veterinary consultation boundary.

## Recommended Next Steps

1. Inspect the 58 synced records in Feishu Bitable and adjust column order, views, filters, and manual review fields for daily use.

2. If Feishu Bitable looks correct, update or add an n8n workflow that runs Feishu Bitable sync intentionally, instead of the current dry-run smoke workflow.

3. Add AI screening:

```text
Read translated Markdown
  -> summarize
  -> score Xiaohongshu usefulness
  -> output JSON
```

4. Write high-score topics to the topic database.

5. Add manual review before using any AI-generated content for publishing.

6. Later, connect `fediaf_automation.py` to n8n only when a recrawl/retranslation cadence is explicitly needed.
