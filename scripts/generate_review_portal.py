from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "topic-database" / "topic-candidates.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "review-portal"
DEFAULT_LIMIT = 0

REVIEW_STATUSES = ("待评估", "人工复核")
TOPIC_FIELDS = (
    "candidate_id",
    "title",
    "pet_type",
    "topic",
    "source_org",
    "source_url",
    "source_file",
    "translated_path",
    "evidence_level",
    "evidence_excerpt",
    "xhs_angle",
    "hook",
    "summary",
    "risk_level",
    "risk_reason",
    "vet_boundary_required",
    "score",
    "status",
    "content_hash",
    "created_at",
    "first_seen_at",
    "last_seen_at",
    "import_count",
)


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def connect_database(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RuntimeError(f"Topic database does not exist: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def read_records(conn: sqlite3.Connection, include_all: bool, limit: int) -> list[dict[str, Any]]:
    where = "" if include_all else "where status in ('待评估', '人工复核')"
    limit_clause = "" if limit <= 0 else f"limit {int(limit)}"
    rows = conn.execute(
        f"""
        select {', '.join(TOPIC_FIELDS)}
        from topic_candidates
        {where}
        order by
            case when status = '人工复核' then 0 when status = '待评估' then 1 else 2 end,
            score desc,
            vet_boundary_required desc,
            risk_level desc,
            candidate_id asc
        {limit_clause}
        """
    ).fetchall()
    return [normalize_row(row) for row in rows]


def read_all_counters(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("select topic, risk_level, status, vet_boundary_required from topic_candidates").fetchall()
    return {
        "total": len(rows),
        "topics": dict(Counter(row["topic"] for row in rows).most_common()),
        "risks": dict(Counter(row["risk_level"] for row in rows).most_common()),
        "statuses": dict(Counter(row["status"] for row in rows).most_common()),
        "vet_boundary_required": sum(1 for row in rows if row["vet_boundary_required"] == 1),
    }


def normalize_row(row: sqlite3.Row) -> dict[str, Any]:
    record = {field: row[field] for field in TOPIC_FIELDS}
    record["vet_boundary_required"] = bool(record["vet_boundary_required"])
    record["score"] = int(record["score"])
    record["import_count"] = int(record["import_count"])
    return record


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_actions_template(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=["candidate_id", "current_status", "next_status", "review_note"],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "candidate_id": record["candidate_id"],
                    "current_status": record["status"],
                    "next_status": "",
                    "review_note": "",
                }
            )


def truncate_text(value: str, max_len: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def file_href(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.exists():
        return path.resolve().as_uri()
    return ""


def html_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def pill(value: str, class_name: str = "") -> str:
    return f'<span class="pill {class_name}">{html.escape(value)}</span>'


def record_link(path_value: str, label: str) -> str:
    href = file_href(path_value)
    if href:
        return f'<a href="{html_attr(href)}">{html.escape(label)}</a>'
    if path_value:
        return f"<span>{html.escape(label)}</span>"
    return '<span class="muted">无</span>'


def status_class(value: str) -> str:
    return {
        "待评估": "status-pending",
        "人工复核": "status-review",
        "可写": "status-ready",
        "已写": "status-done",
        "放弃": "status-drop",
    }.get(value, "status-pending")


def risk_class(value: str) -> str:
    return {"高": "risk-high", "中": "risk-mid", "低": "risk-low"}.get(value, "risk-low")


def write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    counts = summary["counts"]
    lines = [
        "# Topic Review Notification Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Review portal: `{summary['outputs']['html']}`",
        f"Review queue JSON: `{summary['outputs']['json']}`",
        "",
        "## Counts",
        "",
        f"- Database total: {counts['database_total']}",
        f"- Queue records: {counts['queue_records']}",
        f"- Veterinary boundary required in queue: {counts['queue_vet_boundary_required']}",
        "",
        "## Queue Statuses",
        "",
    ]
    for status, count in counts["queue_statuses"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Top Review Items", ""])
    for record in records[:12]:
        boundary = "需兽医边界" if record["vet_boundary_required"] else "常规边界"
        lines.append(
            f"- [{record['score']}] {record['hook']} | {record['topic']} | {record['risk_level']} | {boundary} | `{record['candidate_id']}`"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_html(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    record_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    counts = summary["counts"]
    rows = []
    details = []
    for index, record in enumerate(records, start=1):
        boundary = "需兽医边界" if record["vet_boundary_required"] else "常规边界"
        rows.append(
            f"""
            <tr data-topic="{html_attr(record['topic'])}" data-risk="{html_attr(record['risk_level'])}" data-status="{html_attr(record['status'])}" data-index="{index - 1}">
              <td class="rank">{index}</td>
              <td>
                <button class="row-button" type="button" data-index="{index - 1}">
                  <span class="hook">{html.escape(record['hook'])}</span>
                  <span class="meta">{html.escape(record['title'])}</span>
                </button>
              </td>
              <td>{pill(record['topic'])}</td>
              <td>{pill(record['risk_level'], risk_class(record['risk_level']))}</td>
              <td>{pill(record['status'], status_class(record['status']))}</td>
              <td>{record['score']}</td>
              <td>{html.escape(boundary)}</td>
            </tr>
            """
        )
        details.append(detail_panel(index - 1, record, active=index == 1))

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RedBook 选题审核</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --line: #d8dee8;
      --text: #17202a;
      --muted: #617084;
      --accent: #176b87;
      --accent-soft: #e2f3f7;
      --warn: #a45c00;
      --warn-soft: #fff2d8;
      --ok: #2d6a4f;
      --ok-soft: #e3f4eb;
      --danger: #9f2d3b;
      --danger-soft: #fde7ea;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      line-height: 1.5;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      padding: 20px 28px 16px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
      max-width: 1120px;
    }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      padding: 10px 12px;
      min-height: 70px;
    }}
    .stat span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .stat strong {{
      display: block;
      margin-top: 4px;
      font-size: 22px;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.75fr);
      gap: 16px;
      padding: 18px 28px 28px;
      max-width: 1440px;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 150px 130px 130px;
      gap: 10px;
      margin-bottom: 12px;
    }}
    input, select {{
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 10px;
      font: inherit;
    }}
    .panel {{
      min-width: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px;
      vertical-align: middle;
      text-align: left;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      background: #eef2f6;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .rank {{
      width: 48px;
      color: var(--muted);
      text-align: right;
    }}
    .row-button {{
      display: block;
      width: 100%;
      min-height: 48px;
      padding: 0;
      border: 0;
      background: transparent;
      text-align: left;
      color: inherit;
      cursor: pointer;
      font: inherit;
    }}
    .row-button:focus {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
      border-radius: 4px;
    }}
    .hook {{
      display: block;
      font-weight: 700;
      color: var(--text);
    }}
    .meta {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #edf1f5;
      color: #314154;
      font-size: 12px;
      white-space: nowrap;
    }}
    .risk-high {{ background: var(--danger-soft); color: var(--danger); }}
    .risk-mid {{ background: var(--warn-soft); color: var(--warn); }}
    .risk-low {{ background: var(--ok-soft); color: var(--ok); }}
    .status-pending {{ background: var(--accent-soft); color: var(--accent); }}
    .status-review {{ background: var(--warn-soft); color: var(--warn); }}
    .status-ready {{ background: var(--ok-soft); color: var(--ok); }}
    .status-done {{ background: #ece7ff; color: #5840a6; }}
    .status-drop {{ background: #eceff3; color: #526070; }}
    .detail {{
      display: none;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 520px;
    }}
    .detail.active {{ display: block; }}
    .detail h2 {{
      margin: 0 0 8px;
      font-size: 20px;
      line-height: 1.3;
      letter-spacing: 0;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}
    .field {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    .field label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .field p {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .muted {{ color: var(--muted); }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .empty {{
      display: none;
      border: 1px dashed var(--line);
      background: var(--surface);
      color: var(--muted);
      padding: 28px;
      border-radius: 8px;
      text-align: center;
    }}
    @media (max-width: 980px) {{
      main {{
        grid-template-columns: 1fr;
        padding: 14px;
      }}
      header {{ padding: 16px 14px; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      th:nth-child(6), td:nth-child(6),
      th:nth-child(7), td:nth-child(7) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>RedBook 选题审核</h1>
    <section class="stats" aria-label="审核统计">
      <div class="stat"><span>数据库总数</span><strong>{counts['database_total']}</strong></div>
      <div class="stat"><span>审核队列</span><strong>{counts['queue_records']}</strong></div>
      <div class="stat"><span>需兽医边界</span><strong>{counts['queue_vet_boundary_required']}</strong></div>
      <div class="stat"><span>生成时间</span><strong>{html.escape(summary['local_generated_at'])}</strong></div>
    </section>
  </header>
  <main>
    <section class="panel">
      <div class="toolbar">
        <input id="search" type="search" placeholder="搜索标题、钩子、ID、证据">
        <select id="topicFilter" aria-label="主题筛选"><option value="">全部主题</option>{options_for(records, 'topic')}</select>
        <select id="riskFilter" aria-label="风险筛选"><option value="">全部风险</option>{options_for(records, 'risk_level')}</select>
        <select id="statusFilter" aria-label="状态筛选"><option value="">全部状态</option>{options_for(records, 'status')}</select>
      </div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>选题</th>
            <th>主题</th>
            <th>风险</th>
            <th>状态</th>
            <th>分数</th>
            <th>边界</th>
          </tr>
        </thead>
        <tbody id="rows">
          {''.join(rows)}
        </tbody>
      </table>
      <div id="empty" class="empty">没有匹配的候选</div>
    </section>
    <aside id="details">
      {''.join(details)}
    </aside>
  </main>
  <script type="application/json" id="records-json">{record_json}</script>
  <script>
    const records = JSON.parse(document.getElementById('records-json').textContent);
    const search = document.getElementById('search');
    const topicFilter = document.getElementById('topicFilter');
    const riskFilter = document.getElementById('riskFilter');
    const statusFilter = document.getElementById('statusFilter');
    const rows = Array.from(document.querySelectorAll('#rows tr'));
    const panels = Array.from(document.querySelectorAll('.detail'));
    const empty = document.getElementById('empty');

    function activate(index) {{
      panels.forEach((panel) => panel.classList.remove('active'));
      const panel = document.querySelector(`.detail[data-index="${{index}}"]`);
      if (panel) panel.classList.add('active');
    }}

    function filterRows() {{
      const q = search.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach((row) => {{
        const record = records[Number(row.dataset.index)];
        const haystack = [
          record.candidate_id,
          record.title,
          record.hook,
          record.summary,
          record.evidence_excerpt,
          record.risk_reason
        ].join(' ').toLowerCase();
        const matched =
          (!q || haystack.includes(q)) &&
          (!topicFilter.value || row.dataset.topic === topicFilter.value) &&
          (!riskFilter.value || row.dataset.risk === riskFilter.value) &&
          (!statusFilter.value || row.dataset.status === statusFilter.value);
        row.style.display = matched ? '' : 'none';
        if (matched) visible += 1;
      }});
      empty.style.display = visible === 0 ? 'block' : 'none';
    }}

    document.querySelectorAll('.row-button').forEach((button) => {{
      button.addEventListener('click', () => activate(button.dataset.index));
    }});
    [search, topicFilter, riskFilter, statusFilter].forEach((control) => {{
      control.addEventListener('input', filterRows);
    }});
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def detail_panel(index: int, record: dict[str, Any], active: bool) -> str:
    boundary = "需兽医边界提示" if record["vet_boundary_required"] else "常规科普边界"
    class_name = "detail active" if active else "detail"
    return f"""
    <article class="{class_name}" data-index="{index}">
      <h2>{html.escape(record['hook'])}</h2>
      <div>
        {pill(record['topic'])}
        {pill(record['risk_level'], risk_class(record['risk_level']))}
        {pill(record['status'], status_class(record['status']))}
        {pill(boundary)}
      </div>
      <div class="detail-grid">
        <div class="field"><label>Candidate ID</label><p>{html.escape(record['candidate_id'])}</p></div>
        <div class="field"><label>分数</label><p>{record['score']}</p></div>
        <div class="field"><label>来源</label><p><a href="{html_attr(record['source_url'])}">{html.escape(record['source_org'] or record['source_url'])}</a></p></div>
        <div class="field"><label>本地译文</label><p>{record_link(record['translated_path'], '打开译文')}</p></div>
      </div>
      <div class="field"><label>标题</label><p>{html.escape(record['title'])}</p></div>
      <div class="field"><label>小红书角度</label><p>{html.escape(record['xhs_angle'])}</p></div>
      <div class="field"><label>摘要</label><p>{html.escape(record['summary'])}</p></div>
      <div class="field"><label>证据摘录</label><p>{html.escape(record['evidence_excerpt'])}</p></div>
      <div class="field"><label>风险原因</label><p>{html.escape(record['risk_reason'])}</p></div>
      <div class="field"><label>内容哈希</label><p>{html.escape(record['content_hash'])}</p></div>
    </article>
    """


def options_for(records: list[dict[str, Any]], field: str) -> str:
    values = sorted({str(record[field]) for record in records if record.get(field)})
    return "".join(f'<option value="{html_attr(value)}">{html.escape(value)}</option>' for value in values)


def local_time_label() -> str:
    china_tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(timezone.utc).astimezone(china_tz).strftime("%Y-%m-%d %H:%M:%S")


def build_summary(
    db_path: Path,
    output_dir: Path,
    records: list[dict[str, Any]],
    database_counts: dict[str, Any],
    include_all: bool,
    limit: int,
) -> dict[str, Any]:
    queue_statuses = Counter(record["status"] for record in records)
    queue_topics = Counter(record["topic"] for record in records)
    queue_risks = Counter(record["risk_level"] for record in records)
    html_path = output_dir / "index.html"
    json_path = output_dir / "review-queue.json"
    markdown_path = output_dir / "notification-summary.md"
    actions_path = output_dir / "review-actions-template.csv"
    return {
        "generated_at": utc_now(),
        "local_generated_at": local_time_label(),
        "database": str(db_path),
        "mode": "all" if include_all else "review_queue",
        "limit": limit,
        "counts": {
            "database_total": database_counts["total"],
            "queue_records": len(records),
            "queue_vet_boundary_required": sum(1 for record in records if record["vet_boundary_required"]),
            "queue_statuses": dict(queue_statuses.most_common()),
            "queue_topics": dict(queue_topics.most_common()),
            "queue_risks": dict(queue_risks.most_common()),
            "database_statuses": database_counts["statuses"],
            "database_topics": database_counts["topics"],
            "database_risks": database_counts["risks"],
        },
        "outputs": {
            "html": str(html_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "review_actions_template": str(actions_path),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a local topic review portal and notification summary.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite topic database path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for review portal outputs.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum queue records; 0 means all.")
    parser.add_argument("--include-all", action="store_true", help="Include all statuses instead of review statuses only.")
    args = parser.parse_args()

    db_path = resolve_workspace_path(args.db)
    output_dir = resolve_workspace_path(args.output_dir)
    conn = connect_database(db_path)
    try:
        records = read_records(conn, include_all=args.include_all, limit=args.limit)
        database_counts = read_all_counters(conn)
    finally:
        conn.close()

    summary = build_summary(db_path, output_dir, records, database_counts, args.include_all, args.limit)
    write_json(output_dir / "review-queue.json", {"summary": summary, "records": records})
    write_markdown(output_dir / "notification-summary.md", summary, records)
    write_review_actions_template(output_dir / "review-actions-template.csv", records)
    write_html(output_dir / "index.html", summary, records)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
