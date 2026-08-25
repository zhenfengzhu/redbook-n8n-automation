from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_TRANSLATION_INDEX = Path("data/petmd-zh/translation-index.json")
DEFAULT_OUTPUT_DIR = Path("data/petmd-topic-cards")
GENERATOR_VERSION = "petmd-topic-card-heuristic-v1"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


PET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("猫", ("猫", "cat", "kitten")),
    ("狗", ("狗", "犬", "dog", "puppy")),
)

TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("营养饮食", ("营养", "饮食", "食物", "主粮", "猫粮", "狗粮", "diet", "food", "nutrition", "ingredient")),
    ("疾病症状", ("疾病", "症状", "呕吐", "腹泻", "癌", "心脏", "肾", "肝", "皮肤", "disease", "symptom", "cancer", "vomit")),
    ("行为护理", ("行为", "焦虑", "训练", "护理", "洗澡", "behavior", "anxiety", "training", "care")),
    ("用药驱虫", ("药", "驱虫", "跳蚤", "蜱", "heartworm", "flea", "tick", "medication")),
    ("体重管理", ("体重", "肥胖", "减重", "热量", "weight", "obesity", "calorie")),
    ("安全避坑", ("安全", "中毒", "巧克力", "召回", "毒", "safe", "toxicity", "poison", "recall")),
    ("老年幼宠", ("老年", "幼犬", "幼猫", "senior", "puppy", "kitten")),
)

MEDICAL_BOUNDARY_KEYWORDS = (
    "疾病",
    "症状",
    "治疗",
    "药",
    "处方",
    "剂量",
    "呕吐",
    "腹泻",
    "癌",
    "心脏",
    "肾",
    "肝",
    "手术",
    "慢性",
    "幼犬",
    "幼猫",
    "老年",
    "pregnant",
    "treatment",
    "medication",
    "dose",
    "disease",
    "symptom",
    "cancer",
    "surgery",
)

HIGH_RISK_KEYWORDS = (
    "剂量",
    "处方",
    "用药",
    "治疗方案",
    "手术",
    "癌",
    "糖尿病",
    "肾病",
    "心脏病",
    "dose",
    "prescription",
    "surgery",
    "cancer",
    "diabetes",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


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


def flat_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def first_heading(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def field_value(markdown: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def section_after(markdown: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
    if not match:
        return ""
    return markdown[match.end() :].strip()


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
    title_area = text[:800].lower()
    best_topic = "营养饮食"
    best_score = 0
    for topic, keywords in TOPIC_KEYWORDS:
        score = 0
        for keyword in keywords:
            lower_keyword = keyword.lower()
            if lower_keyword in text.lower():
                score += 1
            if lower_keyword in title_area:
                score += 3
        if score > best_score:
            best_topic = topic
            best_score = score
    return best_topic


def risk_assessment(text: str) -> tuple[str, bool, str, str]:
    high_hits = keyword_hits(text, HIGH_RISK_KEYWORDS)
    boundary_hits = keyword_hits(text, MEDICAL_BOUNDARY_KEYWORDS)
    if high_hits:
        hit_text = "、".join(high_hits[:5])
        return "高", True, f"命中高风险医疗词：{hit_text}", "只做症状识别和就医提醒，不给诊断、药名、剂量或治疗方案。"
    if boundary_hits:
        hit_text = "、".join(boundary_hits[:5])
        return "中", True, f"命中健康边界词：{hit_text}", "出现持续、严重、幼宠老年宠或伴随异常症状时，建议尽快咨询兽医。"
    return "低", False, "未命中明显医疗高风险词", "作为一般养宠知识分享，避免绝对化表述。"


def extract_summary(body: str, max_chars: int = 220) -> str:
    text = flat_text(body)
    if len(text) <= max_chars:
        return text
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+", text) if len(part.strip()) > 8]
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence) > max_chars:
            break
        summary = f"{summary}{sentence}" if summary else sentence
    return summary or text[:max_chars].rstrip()


def evidence_excerpt(body: str, topic: str, max_chars: int = 260) -> str:
    text = flat_text(body)
    keywords = dict(TOPIC_KEYWORDS).get(topic, ())
    lower = text.lower()
    for keyword in keywords:
        position = lower.find(keyword.lower())
        if position >= 0:
            start = max(0, position - 70)
            return text[start : start + max_chars].strip()
    return text[:max_chars].strip()


def cover_hook(topic: str, pet_type: str) -> str:
    pet = "猫狗" if pet_type == "猫狗通用" else pet_type
    hooks = {
        "营养饮食": f"{pet}饮食别只看营销词",
        "疾病症状": f"{pet}这些症状别硬扛",
        "行为护理": f"{pet}日常护理先抓重点",
        "用药驱虫": f"{pet}用药驱虫别自己猜",
        "体重管理": f"{pet}体重变化要重视",
        "安全避坑": f"{pet}安全避坑清单",
        "老年幼宠": f"{pet}特殊阶段更要谨慎",
    }
    return hooks.get(topic, f"{pet}健康知识卡")


def xhs_angle(topic: str, pet_type: str) -> str:
    pet = "猫狗" if pet_type == "猫狗通用" else pet_type
    angles = {
        "营养饮食": f"把 PetMD 的兽医审核内容改成养宠人能执行的喂养判断清单，重点讲误区和适用边界。",
        "疾病症状": f"从常见症状切入，做成“哪些情况先观察、哪些情况该就医”的风险分层卡。",
        "行为护理": f"把日常护理问题拆成可观察信号和家庭管理动作，避免夸大效果。",
        "用药驱虫": f"强调不能自行给药，内容聚焦识别风险、记录信息和咨询兽医前准备。",
        "体重管理": f"用体重变化、热量和日常观察做选题，帮助养宠人建立长期管理意识。",
        "安全避坑": f"做成收藏型避坑清单，强调风险来源、误食处理和及时求助。",
        "老年幼宠": f"突出幼宠、老年宠的特殊风险和个体差异，避免通用建议套所有{pet}。",
    }
    return angles.get(topic, "转成普通养宠人能理解的健康判断清单。")


def priority_score(topic: str, risk_level: str, content_chars: int, has_author: bool) -> int:
    base = {
        "安全避坑": 5,
        "疾病症状": 5,
        "营养饮食": 4,
        "体重管理": 4,
        "老年幼宠": 4,
        "行为护理": 3,
        "用药驱虫": 3,
    }.get(topic, 3)
    if risk_level == "高":
        base -= 1
    if content_chars < 1000:
        base -= 1
    if has_author:
        base += 1
    return max(1, min(5, base))


def parse_translated_page(record: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(str(record.get("path", "")))
    if not path.exists():
        return None
    markdown = path.read_text(encoding="utf-8", errors="replace")
    body = section_after(markdown, "正文")
    source_url = field_value(markdown, "来源") or str(record.get("url", ""))
    original_title = field_value(markdown, "原文标题") or str(record.get("title", ""))
    translated_title = first_heading(markdown, original_title or path.stem)
    source_id = "petmd_" + sha1_text(f"{source_url}\0{path}")[:16]
    return {
        "source_id": source_id,
        "source_url": source_url,
        "source_path": str(record.get("source_path", "")),
        "translated_path": str(path),
        "original_title": original_title,
        "translated_title": translated_title,
        "content_type": field_value(markdown, "内容类型") or str(record.get("content_type", "")),
        "published_at": field_value(markdown, "发布时间") or str(record.get("published_at", "")),
        "modified_at": field_value(markdown, "修改时间") or str(record.get("modified_at", "")),
        "author": field_value(markdown, "作者") or str(record.get("author", "")),
        "reviewed_by": field_value(markdown, "审核人") or str(record.get("reviewed_by", "")),
        "reviewed_on": field_value(markdown, "审核时间") or str(record.get("reviewed_on", "")),
        "description": field_value(markdown, "中文描述"),
        "image_url": field_value(markdown, "图片") or str(record.get("image_url", "")),
        "content_chars": len(body),
        "content_hash": sha1_text(body),
        "body": body,
    }


def build_card(source: dict[str, Any], created_at: str) -> dict[str, Any]:
    classification_text = "\n".join(
        [
            str(source.get("translated_title", "")),
            str(source.get("original_title", "")),
            str(source.get("description", "")),
            str(source.get("body", ""))[:5000],
        ]
    )
    pet_type = detect_pet_type(classification_text)
    topic = detect_topic(classification_text)
    risk_level, vet_boundary_required, risk_reason, vet_boundary_note = risk_assessment(classification_text)
    score = priority_score(topic, risk_level, int(source["content_chars"]), bool(source.get("author") and source.get("author") != "unknown"))
    status = "人工复核" if risk_level == "高" else "待评估"
    hook = cover_hook(topic, pet_type)
    card_id = "petmd_card_" + sha1_text(f"{source['source_id']}\0{topic}\0{hook}\0{GENERATOR_VERSION}")[:16]
    return {
        "card_id": card_id,
        "source_id": source["source_id"],
        "source_org": "PetMD",
        "source_url": source["source_url"],
        "source_path": source["source_path"],
        "translated_path": source["translated_path"],
        "original_title": source["original_title"],
        "translated_title": source["translated_title"],
        "pet_type": pet_type,
        "topic": topic,
        "cover_hook": hook,
        "xhs_angle": xhs_angle(topic, pet_type),
        "summary": extract_summary(str(source.get("body", ""))),
        "evidence_excerpt": evidence_excerpt(str(source.get("body", "")), topic),
        "author": source["author"],
        "reviewed_by": source["reviewed_by"],
        "reviewed_on": source["reviewed_on"],
        "published_at": source["published_at"],
        "modified_at": source["modified_at"],
        "image_url": source["image_url"],
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "vet_boundary_required": vet_boundary_required,
        "vet_boundary_note": vet_boundary_note,
        "priority_score": score,
        "status": status,
        "content_hash": source["content_hash"],
        "generator": GENERATOR_VERSION,
        "created_at": created_at,
    }


def markdown_cell(value: object, max_chars: int = 80) -> str:
    text = flat_text(str(value or ""))
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "..."
    return text.replace("|", "\\|")


def write_review_report(path: Path, cards: list[dict[str, Any]], created_at: str, missing_paths: list[str]) -> None:
    lines = [
        "# PetMD 小红书选题卡片报告",
        "",
        f"生成时间: {created_at}",
        f"生成器: {GENERATOR_VERSION}",
        "",
        "## 汇总",
        "",
        f"- 选题卡片: {len(cards)}",
        f"- 人工复核: {sum(1 for card in cards if card['status'] == '人工复核')}",
        f"- 需要兽医边界: {sum(1 for card in cards if card['vet_boundary_required'])}",
        f"- 缺失译文: {len(missing_paths)}",
        "",
        "## 卡片预览",
        "",
        "| 标题 | 宠物 | 主题 | 优先级 | 风险 | 状态 | 封面钩子 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for card in sorted(cards, key=lambda item: (-int(item["priority_score"]), str(item["risk_level"]), str(item["translated_title"]))):
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(card["translated_title"]),
                    markdown_cell(card["pet_type"], 20),
                    markdown_cell(card["topic"], 20),
                    markdown_cell(card["priority_score"], 10),
                    markdown_cell(card["risk_level"], 20),
                    markdown_cell(card["status"], 20),
                    markdown_cell(card["cover_hook"], 40),
                ]
            )
            + " |"
        )
    if missing_paths:
        lines.extend(["", "## 缺失译文", ""])
        lines.extend(f"- `{item}`" for item in missing_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Xiaohongshu-oriented topic cards from translated PetMD pages.")
    parser.add_argument("--translation-index", default=str(DEFAULT_TRANSLATION_INDEX), help="PetMD Chinese translation index.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N translated pages.")
    args = parser.parse_args()

    translation_index_path = Path(args.translation_index)
    output_dir = Path(args.output_dir)
    index = read_json(translation_index_path)
    records = index.get("pages", []) if isinstance(index, dict) else []
    if args.limit is not None:
        records = records[: args.limit]

    created_at = utc_now()
    sources: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    missing_paths: list[str] = []
    seen_sources: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        source = parse_translated_page(record)
        if source is None:
            missing_paths.append(str(record.get("path", "")))
            continue
        dedupe_key = f"{source['source_url']}\0{source['translated_path']}"
        if dedupe_key in seen_sources:
            continue
        seen_sources.add(dedupe_key)
        sources.append({key: value for key, value in source.items() if key != "body"})
        cards.append(build_card(source, created_at))

    source_items_path = output_dir / "source-items.jsonl"
    cards_path = output_dir / "topic-cards.jsonl"
    review_report_path = output_dir / "review-report.md"
    summary_path = output_dir / "summary.json"
    source_count = write_jsonl(source_items_path, sources)
    card_count = write_jsonl(cards_path, cards)
    write_review_report(review_report_path, cards, created_at, missing_paths)
    summary = {
        "generated_at": created_at,
        "generator": GENERATOR_VERSION,
        "translation_index": str(translation_index_path),
        "outputs": {
            "source_items": str(source_items_path),
            "topic_cards": str(cards_path),
            "review_report": str(review_report_path),
            "summary": str(summary_path),
        },
        "counts": {
            "source_items": source_count,
            "topic_cards": card_count,
            "manual_review": sum(1 for card in cards if card["status"] == "人工复核"),
            "vet_boundary_required": sum(1 for card in cards if card["vet_boundary_required"]),
            "missing_paths": len(missing_paths),
        },
        "missing_paths": missing_paths,
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
