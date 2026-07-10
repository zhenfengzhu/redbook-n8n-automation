from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRANSLATION_INDEX = Path("data/fediaf-full-zh/translation-index.json")
DEFAULT_OUTPUT_DIR = Path("data/topic-pipeline")
GENERATOR_VERSION = "heuristic-v1"
DEFAULT_MIN_CONTENT_CHARS = 700


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("体重", ("体重", "超重", "肥胖", "减重", "身体状况", "body condition", "weight", "obesity", "bcs")),
    ("标签", ("标签", "label", "labelling", "labeling", "成分表", "声称")),
    ("食品安全", ("安全", "污染", "召回", "霉菌", "细菌", "沙门氏菌", "safety", "recall", "contamination")),
    ("老年宠物", ("老年", "年长", "senior", "older dog", "older cat")),
    ("零食", ("零食", "咀嚼", "奖励", "treat", "chew")),
    ("补剂", ("补剂", "添加剂", "维生素", "矿物质", "牛磺酸", "supplement", "additive", "vitamin", "mineral")),
    ("食品加工", ("干粮", "湿粮", "制造", "加工", "dry pet food", "wet pet food", "manufacturing")),
    ("营养", ("营养", "蛋白", "碳水", "脂肪", "水", "自制", "素食", "谷物", "nutrition", "protein", "carbohydrate", "homemade", "vegetarian", "grain-free")),
)

PET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("猫", ("猫", "cat", "cats", "kitten", "kittens")),
    ("狗", ("狗", "犬", "dog", "dogs", "puppy", "puppies")),
    ("兔", ("兔", "rabbit", "rabbits")),
)

MEDICAL_BOUNDARY_KEYWORDS = (
    "疾病",
    "症状",
    "糖尿病",
    "尿道",
    "肾",
    "肝",
    "关节",
    "麻醉",
    "手术",
    "治疗",
    "处方",
    "剂量",
    "减重计划",
    "幼猫",
    "幼犬",
    "老年",
    "孕",
    "慢性",
    "异常",
    "supplement",
    "dosage",
    "disease",
    "diabetes",
    "urinary",
    "surgery",
    "anesthesia",
    "treatment",
    "senior",
    "puppy",
    "kitten",
    "pregnant",
    "chronic",
)

HIGH_RISK_KEYWORDS = (
    "处方",
    "剂量",
    "用药",
    "糖尿病",
    "尿道疾病",
    "麻醉",
    "手术",
    "dosage",
    "dose",
    "medication",
    "medicine",
    "prescription",
    "diabetes",
    "urinary disease",
    "anesthesia",
    "surgery",
)

TOPIC_TIEBREAK_PRIORITY = {
    "体重": 8,
    "食品安全": 7,
    "标签": 6,
    "营养": 5,
    "补剂": 4,
    "老年宠物": 3,
    "零食": 2,
    "食品加工": 1,
}

TITLE_TOPIC_OVERRIDES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("标签", ("labelling", "labeling", "labels", "label", "标签")),
    ("体重", ("healthy-weight", "healthy weight", "body condition", "weight", "体重", "身体状况")),
    ("食品安全", ("safety", "recall", "食品安全", "安全")),
    ("零食", ("treat", "treats", "chew", "chews", "零食", "咀嚼")),
    ("老年宠物", ("senior", "older", "老年")),
    ("补剂", ("additive", "additives", "supplement", "vitamin", "mineral", "添加剂", "补剂", "维生素")),
    ("食品加工", ("dry-pet-food", "wet-pet-food", "dry pet food", "wet pet food", "processing", "manufacturing")),
    ("营养", ("nutritional", "nutrition", "营养")),
)

LOW_VALUE_EXACT_PATHS = {
    "/",
    "/about/",
    "/contact/",
    "/resources/",
    "/self-regulation/",
    "/pet-food-facts/",
}

LOW_VALUE_TITLE_KEYWORDS = (
    "annual congress",
    "contact",
    "resources",
    "privacy",
    "the voice of the european pet food industry",
    "年度大会",
    "联系人",
    "资源",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def compact_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def flat_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def first_heading(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def field_value(markdown: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def section_after(markdown: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
    if not match:
        return ""
    return markdown[match.end() :].strip()


def pdf_body(markdown: str) -> str:
    first_page = re.search(r"^##\s+第\s+\d+\s+页\s*$", markdown, re.MULTILINE)
    if first_page:
        return markdown[first_page.start() :].strip()
    return section_after(markdown, "来源页面") or markdown


def page_body(markdown: str) -> str:
    return section_after(markdown, "正文") or markdown


def source_pages(markdown: str) -> list[str]:
    section = section_after(markdown, "来源页面")
    if not section:
        return []
    section = re.split(r"^##\s+", section, maxsplit=1, flags=re.MULTILINE)[0]
    return [line[1:].strip() for line in section.splitlines() if line.strip().startswith("-")]


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    haystack = text.lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    haystack = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in haystack]


def detect_pet_type(text: str) -> str:
    hits = {pet_type for pet_type, keywords in PET_KEYWORDS if contains_any(text, keywords)}
    if "猫" in hits and "狗" in hits:
        return "猫狗通用"
    if hits:
        return sorted(hits)[0]
    return "猫狗通用"


def detect_topic(text: str) -> str:
    lower_text = text.lower()
    header_text = "\n".join(lower_text.splitlines()[:3])
    leading_text = lower_text[:700]
    for topic, keywords in TITLE_TOPIC_OVERRIDES:
        if any(keyword in header_text for keyword in keywords):
            return topic

    best_topic = "营养"
    best_score = 0
    for topic, keywords in TOPIC_KEYWORDS:
        score = 0
        for keyword in keywords:
            lower_keyword = keyword.lower()
            if lower_keyword in lower_text:
                score += 1
            if lower_keyword in header_text:
                score += 4
            if lower_keyword in leading_text:
                score += 2
        if (score, TOPIC_TIEBREAK_PRIORITY.get(topic, 0)) > (
            best_score,
            TOPIC_TIEBREAK_PRIORITY.get(best_topic, 0),
        ):
            best_topic = topic
            best_score = score
    return best_topic


def extract_summary(body: str, max_chars: int = 260) -> str:
    text = flat_text(re.sub(r"^#+\s+.*$", " ", body, flags=re.MULTILINE))
    if len(text) <= max_chars:
        return text

    parts = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+", text) if len(part.strip()) > 8]
    summary = ""
    for part in parts:
        if len(summary) + len(part) > max_chars:
            break
        summary = f"{summary}{part}" if summary else part
    return summary or text[:max_chars].rstrip()


def evidence_excerpt(body: str, topic: str, max_chars: int = 320) -> str:
    topic_keywords = dict(TOPIC_KEYWORDS).get(topic, ())
    text = flat_text(body)
    lower_text = text.lower()
    for keyword in topic_keywords:
        position = lower_text.find(keyword.lower())
        if position >= 0:
            start = max(0, position - 80)
            return text[start : start + max_chars].strip()
    return text[:max_chars].strip()


def evidence_level(item: dict[str, Any], topic: str) -> str:
    title_and_url = f"{item['title']} {item.get('original_title', '')} {item.get('source_url', '')}".lower()
    if item["content_type"] == "PDF" and any(word in title_and_url for word in ("guideline", "指南", "safety", "labelling", "labeling")):
        return "高"
    if topic in {"营养", "食品安全", "标签", "体重"}:
        return "高" if item["source_org"] == "FEDIAF" else "中"
    return "中"


def risk_assessment(text: str) -> tuple[str, bool, str, int]:
    boundary_hits = keyword_hits(text, MEDICAL_BOUNDARY_KEYWORDS)
    high_risk_hits = keyword_hits(text, HIGH_RISK_KEYWORDS)
    needs_boundary = bool(boundary_hits)
    if high_risk_hits:
        hit_text = "、".join(high_risk_hits[:5])
        return "高", True, f"命中高风险健康词：{hit_text}。必须人工复核。", 2
    if needs_boundary:
        hit_text = "、".join(boundary_hits[:5])
        return "中", True, f"命中需边界提示词：{hit_text}。需要加入兽医咨询边界。", 3
    return "低", False, "主要是一般营养或食品知识，风险相对可控。", 5


def low_value_source_reason(item: dict[str, Any]) -> str:
    parsed = urllib.parse.urlparse(str(item.get("source_url", "")))
    path = parsed.path or "/"
    if path in LOW_VALUE_EXACT_PATHS:
        return "站点入口、栏目页或联系页信息密度低，不适合作为独立选题来源。"

    title_text = f"{item.get('title', '')} {item.get('original_title', '')}".lower()
    if any(keyword in title_text for keyword in LOW_VALUE_TITLE_KEYWORDS):
        return "标题显示为会议、联系、资源或站点介绍类内容，先留档不入库。"
    return ""


def score_candidate(item: dict[str, Any], topic: str, risk_control_score: int) -> dict[str, int]:
    text = f"{item['title']} {item.get('original_title', '')} {item.get('source_url', '')} {item.get('body', '')[:5000]}"
    pet_hits = sum(1 for _, keywords in PET_KEYWORDS if contains_any(text, keywords))
    topic_hits = keyword_hits(text, dict(TOPIC_KEYWORDS).get(topic, ()))

    health_relevance = min(5, 2 + pet_hits + min(2, len(topic_hits)))
    xhs_fit = 5 if topic in {"体重", "标签", "食品安全", "营养", "零食", "补剂"} else 4
    authority = 5 if item["source_org"] == "FEDIAF" and item["content_type"] == "PDF" else 4
    readability = 4
    if item["content_chars"] < 300:
        readability = 2
    elif item["content_chars"] > 30000:
        readability = 3

    return {
        "health_relevance": health_relevance,
        "xhs_fit": xhs_fit,
        "authority": authority,
        "readability": readability,
        "risk_controllable": risk_control_score,
    }


def hook_for(topic: str, pet_type: str) -> str:
    pet = "猫狗" if pet_type == "猫狗通用" else pet_type
    hooks = {
        "体重": f"{pet}体重超标？先看这几个信号",
        "标签": f"{pet}食品标签看不懂？先抓这几项",
        "食品安全": f"{pet}食品安全吗？这些点先检查",
        "老年宠物": f"老年{pet}怎么吃更稳妥？",
        "零食": f"{pet}零食别乱喂，先看这条线",
        "补剂": f"{pet}要不要补剂？先别急着买",
        "食品加工": f"干粮湿粮怎么做出来的？养宠人要知道",
        "营养": f"{pet}营养焦虑？先分清这几件事",
    }
    return hooks.get(topic, f"{pet}健康喂养，先看这几个判断点")


def xhs_angle_for(topic: str, pet_type: str) -> str:
    pet = "猫狗" if pet_type == "猫狗通用" else pet_type
    angles = {
        "体重": f"用普通养宠人能执行的方式，讲清如何观察{pet}体重和身体状况，以及什么时候该咨询兽医。",
        "标签": f"把专业标签术语拆成购买前能快速检查的清单，帮助养宠人少被营销词带偏。",
        "食品安全": f"围绕安全风险、储存和选择标准做科普，避免制造恐慌。",
        "老年宠物": f"讲清老年{pet}营养关注点和个体差异，强调体检和兽医建议。",
        "零食": f"用热量、频率和适用场景解释零食怎么喂更克制。",
        "补剂": f"解释补剂不是默认必需，先看主粮完整性、健康状态和兽医意见。",
        "食品加工": f"把干粮、湿粮或加工方式讲成养宠人能理解的食品知识。",
        "营养": f"从一个常见喂养误区切入，转成可收藏的判断清单。",
    }
    return angles.get(topic, "把权威资料转成普通养宠人能理解、能执行、不过度医疗化的判断清单。")


def source_id_for(item: dict[str, Any]) -> str:
    key = "\0".join(
        [
            str(item.get("source_org", "")),
            str(item.get("content_type", "")),
            str(item.get("source_url", "")),
            str(item.get("translated_path", "")),
        ]
    )
    return f"src_{sha1_text(key)[:16]}"


def candidate_id_for(source_id: str, topic: str, hook: str) -> str:
    key = "\0".join([source_id, topic, hook, GENERATOR_VERSION])
    return f"topic_{sha1_text(key)[:16]}"


def source_org_for(url: str, translated_path: Path) -> str:
    text = f"{url} {translated_path}".lower()
    if "fediaf" in text or "europeanpetfood" in text:
        return "FEDIAF"
    return "Unknown"


def build_source_item(content_type: str, record: dict[str, Any]) -> dict[str, Any] | None:
    translated_path = Path(str(record.get("path", "")))
    if not translated_path.exists():
        return None

    markdown = translated_path.read_text(encoding="utf-8", errors="replace")
    if content_type == "PDF":
        title = first_heading(markdown, translated_path.stem)
        source_url = field_value(markdown, "PDF 来源")
        source_file = field_value(markdown, "本地 PDF") or str(record.get("source_path", ""))
        original_title = translated_path.stem
        body = compact_text(pdf_body(markdown))
        metadata = {"source_pages": source_pages(markdown), "source_language": field_value(markdown, "识别源语言")}
    else:
        title = first_heading(markdown, translated_path.stem)
        source_url = field_value(markdown, "来源") or str(record.get("url", ""))
        source_file = str(record.get("source_path", ""))
        original_title = field_value(markdown, "原文标题") or title
        body = compact_text(page_body(markdown))
        metadata = {"fetched_at": field_value(markdown, "抓取时间")}

    content_hash = sha1_text(body)
    item: dict[str, Any] = {
        "source_org": source_org_for(source_url, translated_path),
        "content_type": "PDF" if content_type == "PDF" else "网页",
        "title": title,
        "original_title": original_title,
        "source_url": source_url,
        "source_file": source_file,
        "translated_path": str(translated_path),
        "translation_status": record.get("status", ""),
        "content_hash": content_hash,
        "content_chars": len(body),
        "body": body,
        "metadata": metadata,
    }
    item["source_id"] = source_id_for(item)
    return item


def build_candidate(item: dict[str, Any], min_score: int, min_content_chars: int, created_at: str) -> dict[str, Any]:
    classification_text = "\n".join(
        [
            str(item["title"]),
            str(item.get("original_title", "")),
            str(item.get("source_url", "")),
            str(item.get("body", ""))[:5000],
        ]
    )
    pet_type = detect_pet_type(classification_text)
    topic = detect_topic(classification_text)
    risk_level, vet_boundary_required, risk_reason, risk_control_score = risk_assessment(classification_text)
    scores = score_candidate(item, topic, risk_control_score)
    score = sum(scores.values())
    hook = hook_for(topic, pet_type)
    source_id = str(item["source_id"])
    evidence = evidence_level(item, topic)
    low_value_reason = low_value_source_reason(item)
    passes = (
        score >= min_score
        and scores["health_relevance"] >= 3
        and scores["xhs_fit"] >= 3
        and risk_level != "高"
        and item["content_chars"] >= min_content_chars
        and not low_value_reason
    )

    if risk_level == "高":
        status = "人工复核"
    elif passes:
        status = "待评估"
    else:
        status = "低分留档"

    return {
        "candidate_id": candidate_id_for(source_id, topic, hook),
        "source_id": source_id,
        "title": item.get("original_title") or item["title"],
        "pet_type": pet_type,
        "topic": topic,
        "source_org": item["source_org"],
        "source_url": item["source_url"],
        "source_file": item["source_file"],
        "translated_path": item["translated_path"],
        "content_hash": item["content_hash"],
        "evidence_level": evidence,
        "evidence_excerpt": evidence_excerpt(str(item.get("body", "")), topic),
        "xhs_angle": xhs_angle_for(topic, pet_type),
        "hook": hook,
        "summary": extract_summary(str(item.get("body", ""))),
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "vet_boundary_required": vet_boundary_required,
        "score": score,
        "score_breakdown": scores,
        "passes_score_threshold": passes,
        "status": status,
        "reason": low_value_reason or f"规则识别为{topic}主题，综合评分 {score}，证据等级 {evidence}，风险 {risk_level}。",
        "generator": GENERATOR_VERSION,
        "created_at": created_at,
    }


def iter_translation_records(index: dict[str, Any], source_type: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if source_type in {"all", "pages"}:
        for record in index.get("pages", []):
            if isinstance(record, dict) and record.get("path"):
                yield "page", record
    if source_type in {"all", "pdfs"}:
        for record in index.get("pdfs", []):
            if isinstance(record, dict) and record.get("path"):
                yield "PDF", record


def strip_body(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result.pop("body", None)
    return result


def markdown_cell(value: object, max_chars: int = 80) -> str:
    text = flat_text(str(value or ""))
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text.replace("|", "\\|")


def candidate_table(candidates: list[dict[str, Any]], limit: int) -> list[str]:
    lines = [
        "| 标题 | 主题 | 宠物 | 分数 | 风险 | 状态 | 封面候选 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for candidate in candidates[:limit]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(candidate.get("title")),
                    markdown_cell(candidate.get("topic"), 20),
                    markdown_cell(candidate.get("pet_type"), 20),
                    markdown_cell(candidate.get("score"), 10),
                    markdown_cell(candidate.get("risk_level"), 20),
                    markdown_cell(candidate.get("status"), 20),
                    markdown_cell(candidate.get("hook")),
                ]
            )
            + " |"
        )
    if len(candidates) > limit:
        lines.append(f"| ... | ... | ... | ... | ... | ... | 另有 {len(candidates) - limit} 条未显示 |")
    return lines


def write_review_report(
    path: Path,
    created_at: str,
    candidates: list[dict[str, Any]],
    eligible_candidates: list[dict[str, Any]],
    missing_paths: list[str],
) -> None:
    manual_review = [candidate for candidate in candidates if candidate["status"] == "人工复核"]
    vet_boundary = [candidate for candidate in candidates if candidate["vet_boundary_required"]]
    low_score = [candidate for candidate in candidates if candidate["status"] == "低分留档"]

    lines = [
        "# 宠物健康选题候选审核报告",
        "",
        f"生成时间: {created_at}",
        f"生成器: {GENERATOR_VERSION}",
        "",
        "## 汇总",
        "",
        f"- 总候选: {len(candidates)}",
        f"- 可入库候选: {len(eligible_candidates)}",
        f"- 人工复核: {len(manual_review)}",
        f"- 需要兽医边界提示: {len(vet_boundary)}",
        f"- 低分留档: {len(low_score)}",
        f"- 缺失译文路径: {len(missing_paths)}",
        "",
        "## 可入库候选预览",
        "",
        *candidate_table(eligible_candidates, 25),
        "",
        "## 人工复核",
        "",
        *candidate_table(manual_review, 25),
        "",
        "## 缺失译文路径",
        "",
    ]

    if missing_paths:
        lines.extend(f"- `{path_item}`" for path_item in missing_paths)
    else:
        lines.append("- 无")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate stable source items and first-pass topic candidates.")
    parser.add_argument("--translation-index", default=str(DEFAULT_TRANSLATION_INDEX), help="Chinese translation index.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSONL outputs.")
    parser.add_argument("--source-type", choices=("all", "pages", "pdfs"), default="all", help="Records to process.")
    parser.add_argument("--min-score", type=int, default=16, help="Score threshold for database eligibility.")
    parser.add_argument(
        "--min-content-chars",
        type=int,
        default=DEFAULT_MIN_CONTENT_CHARS,
        help="Minimum translated content length for database eligibility.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N translated records.")
    args = parser.parse_args()

    translation_index_path = resolve_workspace_path(args.translation_index)
    output_dir = resolve_workspace_path(args.output_dir)
    index = read_json(translation_index_path)
    if not isinstance(index, dict):
        raise RuntimeError(f"Translation index must be a JSON object: {translation_index_path}")

    created_at = utc_now()
    source_items: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    missing_paths: list[str] = []

    for position, (content_type, record) in enumerate(iter_translation_records(index, args.source_type), start=1):
        if args.limit is not None and position > args.limit:
            break
        item = build_source_item(content_type, record)
        if item is None:
            missing_paths.append(str(record.get("path", "")))
            continue
        source_items.append(item)
        candidates.append(build_candidate(item, args.min_score, args.min_content_chars, created_at))

    source_items_path = output_dir / "source-items.jsonl"
    candidates_path = output_dir / "topic-candidates.jsonl"
    eligible_candidates_path = output_dir / "eligible-topic-candidates.jsonl"
    review_report_path = output_dir / "review-report.md"
    summary_path = output_dir / "summary.json"
    eligible_candidates = [candidate for candidate in candidates if candidate["passes_score_threshold"]]
    source_items_count = write_jsonl(source_items_path, (strip_body(item) for item in source_items))
    candidates_count = write_jsonl(candidates_path, candidates)
    eligible_candidates_count = write_jsonl(eligible_candidates_path, eligible_candidates)
    write_review_report(review_report_path, created_at, candidates, eligible_candidates, missing_paths)

    summary = {
        "generated_at": created_at,
        "generator": GENERATOR_VERSION,
        "translation_index": str(translation_index_path),
        "source_type": args.source_type,
        "min_score": args.min_score,
        "min_content_chars": args.min_content_chars,
        "outputs": {
            "source_items": str(source_items_path),
            "topic_candidates": str(candidates_path),
            "eligible_topic_candidates": str(eligible_candidates_path),
            "review_report": str(review_report_path),
            "summary": str(summary_path),
        },
        "counts": {
            "source_items": source_items_count,
            "topic_candidates": candidates_count,
            "eligible_for_database": eligible_candidates_count,
            "manual_review": sum(1 for candidate in candidates if candidate["status"] == "人工复核"),
            "vet_boundary_required": sum(1 for candidate in candidates if candidate["vet_boundary_required"]),
            "missing_paths": len(missing_paths),
        },
        "missing_paths": missing_paths[:50],
    }
    write_json(summary_path, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
