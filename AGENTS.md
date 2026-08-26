# AGENTS.md

This file gives AI coding agents enough context to work safely in this repository.

## Project Summary

This repository is a local automation workspace for Xiaohongshu/RED pet health content research.

Current working implementation:

- Python scripts crawl FEDIAF / EuropeanPetFood pages and PDFs.
- Python scripts translate retained material into Chinese Markdown.
- FEDIAF data has already been cleaned to keep material useful for a pet health creator.
- n8n is installed locally, but no active n8n workflow currently orchestrates the project.

Read `PROJECT_MEMORY.md` before making significant changes.
Read `ACCOUNT_MEMORY.md` before account positioning, content strategy, product selection, monetization, supplier evaluation, or customer-service work.

## CodeGraph

If a `.codegraph\` directory exists at the repository root, use CodeGraph before grep/find or manual source reading when locating or understanding code.

If no `.codegraph\` directory exists, skip CodeGraph entirely. Do not create or initialize CodeGraph unless the user explicitly asks.

## Important Paths

```text
D:\AUnityProject\RedBook
+-- scripts\
|   +-- fediaf_crawler.py
|   +-- translate_fediaf_to_zh.py
|   +-- fediaf_automation.py
+-- data\
|   +-- fediaf-full\
|   +-- fediaf-full-zh\
|   +-- fediaf-cleanup-report.json
+-- n8n-runtime\
+-- n8n\
+-- tools\
+-- 小红书图文管理\
+-- README-local-n8n.md
+-- 小红书宠物健康选题自动化实施方案.md
+-- PROJECT_MEMORY.md
+-- ACCOUNT_MEMORY.md
+-- AGENTS.md
```

## Current Responsibilities

Use Python for:

- Crawling.
- PDF download and text extraction.
- Translation.
- Data cleanup and indexing.

Use n8n later for:

- Scheduling.
- Running Python scripts.
- AI screening.
- Writing to Baserow/Notion.
- Notifications.
- Manual approval steps.

Do not rewrite the crawler in n8n unless the user explicitly asks.

## Common Commands

Run a full FEDIAF crawl, PDF download, and translation:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --full --install-models
```

Translate existing crawl only:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --no-crawl --translate-pages --translate-pdfs
```

Preview automation commands:

```powershell
cd D:\AUnityProject\RedBook
python scripts\fediaf_automation.py --full --dry-run
```

Start n8n:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
.\start-n8n.ps1
```

Stop n8n:

```powershell
cd D:\AUnityProject\RedBook\n8n-runtime
.\stop-n8n.ps1
```

## Data State

Current retained FEDIAF material:

- 42 source Markdown page records.
- 35 source PDF records.
- 42 Chinese translated page records.
- 35 Chinese translated PDF Markdown records.

Primary indexes:

```text
data\fediaf-full\index.json
data\fediaf-full\pdf-index.json
data\fediaf-full-zh\translation-index.json
```

Cleanup report:

```text
data\fediaf-cleanup-report.json
```

`data\` is ignored by Git.

## Safety Rules

- Never delete or overwrite `小红书图文管理\` unless explicitly requested.
- Never delete `scripts\`, `n8n-runtime\`, `n8n\`, or `tools\` unless explicitly requested.
- Do not assume `n8n` is active in the current automation flow.
- Do not recrawl/retranslate full FEDIAF data unless the user asks.
- Do not revert user changes in the Git working tree.
- Treat `data\` as local generated research data, not source code.
- Keep generated changes scoped and explain verification.

## Pet Health Content Rules

When generating, reviewing, or transforming content for Xiaohongshu pet health:

- Treat confirmed account facts and unvalidated product recommendations in `ACCOUNT_MEMORY.md` as different states; do not turn a candidate product into a final user decision.
- For Xiaohongshu post creation, write the plain text content first and wait for the user's approval before generating HTML, carousel pages, image assets, or exports.
- Prefer evidence from FEDIAF, WSAVA, AAHA, AVMA, FDA, AAFCO, veterinary universities, and peer-reviewed sources.
- Preserve source URLs and local evidence paths.
- Avoid absolute medical claims.
- Do not present diagnosis, treatment, medication, dosage, or supplement protocols as final advice.
- Add a veterinary consultation boundary for symptoms, disease, abnormal appetite/stool/weight changes, pregnancy, puppies/kittens, senior pets, chronic conditions, and medical supplements.
- Do not imply an authority body certifies a specific product unless verified.

## Git Notes

Known current work may include:

- Modified `README-local-n8n.md`.
- Untracked `scripts\fediaf_automation.py`.
- Untracked `小红书图文管理\`.
- Untracked `小红书宠物健康选题自动化实施方案.md`.
- Added `PROJECT_MEMORY.md`.
- Added `AGENTS.md`.

Do not clean, reset, or delete these unless the user explicitly requests it.
