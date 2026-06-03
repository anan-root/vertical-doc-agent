"""生成图片题注治理预览，不覆盖原始素材库。"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY = ROOT / "outputs" / "json" / "excellent_bid_material_library_with_zhenggui_yunting_full.json"
DEFAULT_OUTPUT_LIBRARY = ROOT / "outputs" / "json" / "excellent_bid_material_library_with_zhenggui_yunting_full_caption_governance_preview.json"
DEFAULT_REPORT = ROOT / "outputs" / "reports" / "image_caption_governance_preview_zhenggui_full.md"
DEFAULT_JSON = ROOT / "outputs" / "json" / "image_caption_governance_preview_zhenggui_full.json"

STRONG_TERMS = [
    "图",
    "示意",
    "做法",
    "流程",
    "节点",
    "大样",
    "详图",
    "照片",
    "布置",
    "体系",
    "组织机构",
    "控制网",
    "平面",
    "立面",
    "剖面",
    "成型",
    "绑扎",
    "支设",
    "浇筑",
    "防水",
    "脚手架",
    "防护",
    "样板",
]

PROJECT_SPECIFIC_TERMS = [
    "施工总平面",
    "总平面布置",
    "平面布置图",
    "施工进度",
    "进度计划",
    "网络图",
    "横道图",
    "现场踏勘",
    "踏勘",
    "现状照片",
    "现场照片",
    "实景图",
    "航拍",
    "救援路线",
    "医院",
]

GENERIC_REUSABLE_TERMS = [
    "优秀做法",
    "标准化做法",
    "标准化防护",
    "成品保护",
    "样板",
    "工艺",
    "做法",
    "防护",
    "材料堆放",
    "堆放整齐",
    "分类摆放",
    "标识标牌",
    "安全文明",
    "绿色施工",
    "环境保护",
    "扬尘",
    "喷淋",
    "洗车",
    "围挡",
    "宣传长廊",
]

TABLE_HEADER_TERMS = {
    "序号",
    "编号",
    "项目",
    "内容",
    "方法",
    "措施",
    "要求",
    "标准",
    "图片",
    "图示",
    "说明",
    "备注",
    "名称",
    "部位",
    "工序",
    "做法",
}

GENERIC_CAPTIONS = {
    "图",
    "图片",
    "图示",
    "施工图示",
    "示意",
    "示意图",
    "照片",
    "现场图",
    "效果图",
    "施工方法",
    "主要施工方法",
    "控制要点",
    "工艺控制要点",
}

CAPTION_SUFFIXES = [
    "流程示意图",
    "做法示意图",
    "控制示意图",
    "施工示意图",
    "布置示意图",
    "节点示意图",
    "构造示意图",
    "组砌示意图",
    "绑扎示意图",
    "安装示意图",
    "支设示意图",
    "加固示意图",
    "防护示意图",
    "示意图",
    "流程图",
    "布置图",
    "效果图",
    "详图",
    "大样图",
    "节点图",
    "系统图",
    "平面图",
    "立面图",
    "剖面图",
    "构造图",
    "做法图",
    "控制网",
    "照片",
]

LOW_VALUE_SOURCES = {"section_heading", "section_path", "section_leaf", "section_parent"}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成图片题注治理预览，不覆盖原始素材库。")
    parser.add_argument("--library-json", default=str(DEFAULT_LIBRARY), help="原始优秀标书素材库 JSON。")
    parser.add_argument("--output-library-json", default=str(DEFAULT_OUTPUT_LIBRARY), help="治理预览素材库 JSON。")
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT), help="Markdown 报告输出路径。")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON), help="治理建议 JSON 输出路径。")
    args = parser.parse_args()

    library_path = Path(args.library_json)
    library = json.loads(library_path.read_text(encoding="utf-8"))
    preview_library = copy.deepcopy(library)
    group_map = {group.get("image_group_id"): group for group in preview_library.get("image_groups") or []}

    decisions = []
    for image in preview_library.get("image_assets") or []:
        decision = build_caption_decision(image, group_map.get(image.get("image_group_id")))
        image["caption_governance"] = decision
        if decision["action"] != "keep":
            image["caption_governance_suggested"] = decision["suggested_caption"]
        decisions.append(decision)

    summary = build_summary(preview_library, decisions)
    report = render_report(library_path, summary)

    output_library = Path(args.output_library_json)
    report_path = Path(args.report_output)
    json_path = Path(args.json_output)
    for path in [output_library, report_path, json_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
    output_library.write_text(json.dumps(preview_library, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Preview library: {output_library.resolve()}")
    print(f"Report: {report_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    print(
        "Counts: "
        f"images={summary['image_count']}, "
        f"rewrite={summary['action_counts'].get('rewrite', 0)}, "
        f"manual_review={summary['action_counts'].get('manual_review', 0)}, "
        f"keep={summary['action_counts'].get('keep', 0)}"
    )
    return 0


def build_caption_decision(image: dict[str, Any], group: dict[str, Any] | None) -> dict[str, Any]:
    original = _clean(image.get("caption_actual"))
    candidates = candidate_captions(image, group)
    best, source, confidence = choose_caption(original, candidates)
    issue = caption_issue(image, original)
    action = "keep"
    reason = "原题注可用"

    if _is_high_risk_project_specific_image(image, group):
        action = "manual_review"
        reason = "疑似项目专属或高风险图片，不自动重写"
        best = original or best
        confidence = min(confidence, 0.4)
    elif not original:
        action = "rewrite" if best else "manual_review"
        reason = "原题注缺失，使用语义候选重写" if best else "原题注缺失且无可靠候选"
    elif is_weak_caption(original):
        action = "rewrite" if best and best != original else "manual_review"
        reason = "原题注偏弱，使用套图/图片语义重写" if action == "rewrite" else "原题注偏弱且无更好候选"
    elif image.get("review_required") and best and best != original and confidence >= 0.75:
        action = "rewrite"
        reason = "图片需复核，但存在高置信语义候选"

    suggested = best or original
    if action == "rewrite" and not suggested:
        action = "manual_review"
        reason = "缺少可用建议题注"

    return {
        "image_asset_id": image.get("image_asset_id"),
        "source_id": image.get("source_id"),
        "image_group_id": image.get("image_group_id"),
        "original_caption": original,
        "suggested_caption": suggested,
        "action": action,
        "reason": reason,
        "issue": issue,
        "candidate_source": source,
        "confidence": round(confidence, 3),
        "must_keep_with_group": bool(image.get("must_keep_with_group") or (group and group.get("must_keep_together"))),
        "section_path": image.get("section_path") or [],
        "reuse_level": image.get("reuse_level"),
        "project_specific_risk": image.get("project_specific_risk"),
    }


def candidate_captions(image: dict[str, Any], group: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(text: Any, source: str, confidence: float) -> None:
        value = normalize_caption(text)
        if not value:
            return
        if any(item["text"] == value for item in candidates):
            return
        candidates.append({"text": value, "source": source, "confidence": confidence})

    if group:
        add(group.get("group_title"), "group_title", 0.92)
        add(group.get("semantic_text"), "group_semantic_text", 0.9)
        for caption in group.get("captions") or []:
            add(caption, "group_caption", 0.76)

    add(image.get("group_title"), "image_group_title", 0.9)
    add(image.get("group_semantic_text"), "image_group_semantic_text", 0.88)
    add(image.get("semantic_text"), "image_semantic_text", float(image.get("semantic_confidence") or 0.7))

    for item in image.get("semantic_sources") or []:
        add(
            item.get("text"),
            str(item.get("source_type") or item.get("source") or item.get("type") or "semantic_source"),
            float(item.get("confidence") or 0.65),
        )

    for caption in image.get("caption_candidates") or []:
        add(caption, "caption_candidate", 0.72)

    for key, source, confidence in [
        ("below_cell_text", "below_cell", 0.82),
        ("cell_text", "cell_text", 0.78),
        ("above_cell_text", "above_cell", 0.72),
        ("previous_non_empty_cell_text", "previous_cell", 0.66),
        ("left_cell_text", "left_cell", 0.64),
        ("right_cell_text", "right_cell", 0.62),
    ]:
        add(image.get(key), source, confidence)

    section_path = image.get("section_path") or []
    if section_path:
        add(section_path[-1], "section_leaf", 0.42)
        if len(section_path) >= 2:
            add(section_path[-2], "section_parent", 0.36)
    return sorted(candidates, key=lambda item: score_candidate(item["text"], item["confidence"]), reverse=True)


def choose_caption(original: str, candidates: list[dict[str, Any]]) -> tuple[str, str, float]:
    strong_candidates = [item for item in candidates if is_better_caption(item, original)]
    if not strong_candidates:
        return original, "original", 0.5 if original else 0.0
    best = max(strong_candidates, key=lambda item: score_candidate(item["text"], item["confidence"]))
    return best["text"], best["source"], float(best["confidence"])


def score_candidate(text: str, confidence: float) -> float:
    score = float(confidence or 0)
    if any(term in text for term in STRONG_TERMS):
        score += 0.18
    if is_weak_caption(text):
        score -= 0.35
    if (
        is_generic_caption(text)
        or is_sentence_like_caption(text)
        or is_table_header_like(text)
        or looks_like_table_row(text)
        or looks_like_section_path(text)
    ):
        score -= 0.8
    if len(text) > 36:
        score -= 0.08
    if len(text) > 60:
        score -= 0.2
    if "；" in text or "|" in text:
        score -= 0.08
    return score


def is_better_caption(candidate: dict[str, Any], original: str) -> bool:
    value = _clean(candidate.get("text"))
    if not value:
        return False
    if _canonical(value) == _canonical(original):
        return False
    if (
        is_generic_caption(value)
        or is_sentence_like_caption(value)
        or looks_like_section_path(value)
        or is_table_header_like(value)
        or looks_like_table_row(value)
    ):
        return False
    if len(value) > 64:
        return False
    source = str(candidate.get("source") or "")
    confidence = float(candidate.get("confidence") or 0)
    if source in LOW_VALUE_SOURCES and confidence < 0.65:
        return False
    if confidence < 0.62 and not any(term in value for term in STRONG_TERMS):
        return False
    if is_weak_caption(value) and not has_strong_caption_signal(value):
        return False
    if original and not is_weak_caption(original) and score_caption(value) <= score_caption(original):
        return False
    return True


def score_caption(text: str) -> float:
    if not text:
        return 0
    score = 0.5
    if has_strong_caption_signal(text):
        score += 0.3
    if is_weak_caption(text):
        score -= 0.4
    if is_generic_caption(text) or is_sentence_like_caption(text) or is_table_header_like(text) or looks_like_table_row(text) or looks_like_section_path(text):
        score -= 0.8
    if 6 <= len(text) <= 36:
        score += 0.1
    if len(text) > 60:
        score -= 0.2
    return score


def caption_issue(image: dict[str, Any], original: str) -> str:
    if _is_high_risk_project_specific_image(image, None):
        return "project_specific"
    if not original:
        return "missing_caption"
    if is_weak_caption(original):
        return "weak_caption"
    if image.get("review_required"):
        return "review_required"
    return "none"


def build_summary(library: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(item["action"] for item in decisions)
    issue_counts = Counter(item["issue"] for item in decisions)
    source_counts: dict[str, Counter[str]] = {}
    for item in decisions:
        source_counts.setdefault(str(item.get("source_id")), Counter())[item["action"]] += 1
    group_rewrite = [
        item
        for item in decisions
        if item["action"] == "rewrite" and item.get("image_group_id") and item.get("must_keep_with_group")
    ]
    manual = [item for item in decisions if item["action"] == "manual_review"]
    rewrite = [item for item in decisions if item["action"] == "rewrite"]
    return {
        "schema_version": "image_caption_governance_preview_v1",
        "library_id": library.get("library_id"),
        "image_count": len(decisions),
        "action_counts": dict(sorted(action_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "source_action_counts": {key: dict(sorted(value.items())) for key, value in sorted(source_counts.items())},
        "group_rewrite_count": len(group_rewrite),
        "rewrite_examples": rewrite[:80],
        "manual_review_examples": manual[:80],
    }


def render_report(library_path: Path, summary: dict[str, Any]) -> str:
    lines = [
        "# 图片题注治理预览报告",
        "",
        "## 一、报告对象",
        "",
        f"- 原始素材库：`{library_path}`",
        f"- 素材库 ID：`{summary['library_id']}`",
        f"- 图片总数：{summary['image_count']}",
        "",
        "## 二、治理动作统计",
        "",
        f"- 动作分布：{format_counter(summary['action_counts'])}",
        f"- 问题分布：{format_counter(summary['issue_counts'])}",
        f"- 涉及套图整组语义重写图片：{summary['group_rewrite_count']}",
        "",
        "## 三、按来源统计",
        "",
        "| 来源 | keep | rewrite | manual_review |",
        "|---|---:|---:|---:|",
    ]
    for source_id, counts in summary["source_action_counts"].items():
        lines.append(
            f"| {source_id} | {counts.get('keep', 0)} | {counts.get('rewrite', 0)} | {counts.get('manual_review', 0)} |"
        )

    lines.extend(["", "## 四、自动重写示例", ""])
    lines.extend(caption_table(summary["rewrite_examples"][:40]))
    lines.extend(["", "## 五、需人工确认示例", ""])
    lines.extend(caption_table(summary["manual_review_examples"][:40]))
    lines.extend(
        [
            "",
            "## 六、使用建议",
            "",
            "- 本报告只生成治理预览，不覆盖原始题注。",
            "- `rewrite` 可作为后续自动题注候选，但正式启用前建议抽查前 40 条。",
            "- `manual_review` 不应自动用于生成，尤其是总平面图、进度图、现场照片、项目专属标牌等。",
            "- 对套图内图片，应优先使用套图组名和组内单图题注共同生成，避免把套图拆散。",
            "",
        ]
    )
    return "\n".join(lines)


def caption_table(items: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 图片 | 来源 | 原题注 | 建议题注 | 动作 | 置信度 | 来源 | 原因 | 章节 |",
        "|---|---|---|---|---|---:|---|---|---|",
    ]
    if not items:
        lines.append("| - | - | - | - | - | - | - | - | - |")
        return lines
    for item in items:
        path = " > ".join(item.get("section_path") or [])
        lines.append(
            f"| {item.get('image_asset_id')} | {item.get('source_id')} | {escape(item.get('original_caption'))} | "
            f"{escape(item.get('suggested_caption'))} | {item.get('action')} | {item.get('confidence')} | "
            f"{escape(item.get('candidate_source'))} | {escape(item.get('reason'))} | {escape(path)} |"
        )
    return lines


def normalize_caption(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = re.sub(r"^[|；;、，,\s]+|[|；;、，,\s]+$", "", text)
    text = re.sub(r"\s+", " ", text)

    extracted = extract_caption_phrase(text)
    if extracted and (len(text) > 48 or looks_like_table_row(text)):
        text = extracted

    if looks_like_section_path(text):
        return ""
    if is_table_header_like(text) or looks_like_table_row(text):
        return ""
    if len(text) > 64:
        return ""
    if re.fullmatch(r"\d+", text):
        return ""
    return text


def extract_caption_phrase(text: str) -> str:
    value = _clean(text)
    if not value:
        return ""
    matches: list[str] = []
    for suffix in CAPTION_SUFFIXES:
        pattern = rf"([\u4e00-\u9fffA-Za-z0-9（）()、/\-]{{2,30}}{re.escape(suffix)})"
        matches.extend(match.group(1) for match in re.finditer(pattern, value))
    for match in reversed(matches):
        caption = re.sub(r"^[\d\s|；;、，,：:]+", "", match).strip()
        if caption and not any(bad in caption for bad in ["如下图", "见下图", "如图所示", "下图所示"]):
            return caption
    return ""


def is_weak_caption(value: Any) -> bool:
    text = _clean(value)
    compact = _canonical(text)
    if not compact:
        return False
    if compact in {"图片", "图示", "示意图", "照片", "现场图", "效果图", "主要施工方法", "施工方法", "控制要点"}:
        return True
    if re.fullmatch(r"第?[一二三四五六七八九十百\d]+步", compact):
        return True
    if looks_like_heading_caption(text):
        return True
    parts = [part for part in re.split(r"[|、，,/\s]+", text) if part.strip()]
    return bool(parts) and all(re.fullmatch(r"第?[一二三四五六七八九十百\d]+步", _canonical(part)) for part in parts)


def looks_like_heading_caption(text: str) -> bool:
    value = _clean(text)
    if not value:
        return False
    if has_strong_caption_signal(value):
        return False
    numbered = re.match(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fffA-Za-z].*$", value)
    if numbered and len(value) <= 28:
        return True
    if len(value) <= 8 and any(term in value for term in ["工程", "措施", "方法", "方案", "考评"]):
        return True
    return False


def has_strong_caption_signal(text: str) -> bool:
    return any(term in text for term in STRONG_TERMS) or bool(extract_caption_phrase(text))


def is_generic_caption(text: str) -> bool:
    compact = _canonical(text)
    return compact in {_canonical(item) for item in GENERIC_CAPTIONS}


def is_sentence_like_caption(text: str) -> bool:
    value = _clean(text)
    if not value:
        return False
    compact = _canonical(value)
    if re.match(r"^[（(]?\d+[）)]", value):
        return True
    if any(term in value for term in ["如下图", "见下图", "下图所示", "如下表", "下表", "如图", "具体的做法"]):
        return True
    modal_terms = ["应", "必须", "不得", "严禁", "需", "需要", "采用", "进行", "根据", "符合", "达到", "允许", "不得"]
    if len(value) >= 12 and any(term in compact for term in modal_terms):
        return True
    if "：" in value or ":" in value:
        return True
    return False


def looks_like_section_path(text: str) -> bool:
    return ">" in _clean(text)


def is_table_header_like(text: str) -> bool:
    value = _clean(text)
    compact = _canonical(value)
    if compact in {"序号内容", "序号方法", "序号项目", "序号措施", "项目内容", "项目措施", "名称图片"}:
        return True
    parts = [part.strip() for part in re.split(r"[|；;、，,\s]+", value) if part.strip()]
    if len(parts) >= 2 and all(part in TABLE_HEADER_TERMS for part in parts):
        return True
    return False


def looks_like_table_row(text: str) -> bool:
    value = _clean(text)
    if "|" in value:
        return True
    if re.match(r"^\d+\s*[；;]\s*", value):
        return True
    if len(value) > 48 and re.search(r"[，,。；;：:]", value):
        return True
    return False


def _contains_project_specific(text: str) -> bool:
    compact = _canonical(text)
    return any(_canonical(term) in compact for term in PROJECT_SPECIFIC_TERMS)


def _is_high_risk_project_specific_image(image: dict[str, Any], group: dict[str, Any] | None) -> bool:
    text = image_text(image)
    if group:
        text = " ".join([text, _clean(group.get("group_title")), _clean(group.get("semantic_text"))])
    risk = str(image.get("project_specific_risk") or "")
    if _is_generic_reusable_text(text) and not _has_hard_project_specific_text(text):
        return False
    return risk == "high" or _contains_project_specific(text)


def _is_generic_reusable_text(text: str) -> bool:
    compact = _canonical(text)
    return any(_canonical(term) in compact for term in GENERIC_REUSABLE_TERMS)


def _has_hard_project_specific_text(text: str) -> bool:
    compact = _canonical(text)
    hard_terms = [
        "施工总平面",
        "总平面布置",
        "平面布置图",
        "施工进度计划",
        "进度计划",
        "网络图",
        "横道图",
        "踏勘",
        "现状",
        "周边环境",
        "周边道路",
        "航拍",
        "救援路线",
        "交通组织",
    ]
    return any(_canonical(term) in compact for term in hard_terms)


def image_text(image: dict[str, Any]) -> str:
    parts = [
        image.get("caption_actual"),
        image.get("semantic_text"),
        image.get("nearby_text"),
        image.get("group_title"),
        image.get("group_semantic_text"),
        " ".join(image.get("section_path") or []),
    ]
    return " ".join(_clean(part) for part in parts)


def format_counter(counter: dict[str, Any]) -> str:
    if not counter:
        return "-"
    return "，".join(f"{key}={value}" for key, value in sorted(counter.items()))


def escape(value: Any) -> str:
    return _clean(value).replace("|", "\\|")[:120]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical(value: Any) -> str:
    return re.sub(r"[\s。．.、，,；;：:（）()\[\]【】_\-]+", "", str(value or "")).lower()


if __name__ == "__main__":
    raise SystemExit(main())
