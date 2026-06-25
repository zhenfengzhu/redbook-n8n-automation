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

Outputs are written under `data/`, which is ignored by Git.

## Notes

- Docker is not installed on this machine, so this setup uses Node/npm.
- This workspace uses portable Node `v22.23.0` from `tools/` because n8n requires Node `>=22.22`.
- `N8N_SECURE_COOKIE=false` is set because this local instance runs on plain HTTP.
- Keep credentials and workflow data inside `n8n-runtime/data`; do not commit that folder.
