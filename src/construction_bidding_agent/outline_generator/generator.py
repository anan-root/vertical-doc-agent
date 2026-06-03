"""根据招标文件解析结果和优秀标书索引生成技术标目录树。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "technical_bid_outline_v0.1"
CONFIRMATION_SCHEMA_VERSION = "outline_confirmation_v0.1"
GENERATOR_VERSION = "stage0_rule_based"
DEFAULT_TZ = "Asia/Shanghai"
MAX_TEMPLATE_CHILDREN = 12

DOMAIN_DESIGN_KEYWORDS = [
    "设计方案",
    "方案设计",
    "建筑设计",
    "结构设计",
    "给排水设计",
    "暖通设计",
    "电气设计",
    "消防设计",
    "绿色建筑",
    "限额设计",
    "设计优化",
    "设计管理",
    "设计进度",
    "设计质量",
    "设计服务",
]
DOMAIN_MANAGEMENT_KEYWORDS = [
    "总承包",
    "协调",
    "管理组织",
    "组织管理",
    "项目管理",
    "设计施工",
    "协同",
    "项目理解",
    "招标项目的理解",
]

CATEGORY_RULES = [
    ("技术标完整性说明", ["内容完整性", "内容完整", "章节完整", "响应完整", "完整性响应"]),
    ("项目理解", ["项目理解", "招标项目的理解", "对招标项目的理解"]),
    ("施工总平面", ["总平面", "平面布置", "布置图"]),
    ("施工进度", ["进度", "施工进度表", "网络计划"]),
    ("工期管理", ["工期"]),
    ("技术创新", ["技术创新", "新工艺", "新技术", "新设备", "新材料"]),
    ("信息化与BIM", ["BIM", "信息化", "监控", "数据"]),
    ("质量管理", ["质量"]),
    ("安全管理", ["安全"]),
    ("文明环保", ["文明", "环境保护", "扬尘", "环保"]),
    ("绿色施工", ["绿色施工", "绿色"]),
    ("资源投入", ["资源", "劳动力", "机械", "材料", "投入"]),
    ("风险管理", ["风险", "应急"]),
    ("施工方案", ["施工方案", "技术措施", "施工组织"]),
    ("消防工程", ["消防"]),
    ("地下人防", ["人防"]),
    ("重点难点", ["重点", "难点"]),
    ("资金保障", ["资金", "专款专用"]),
]

TEMPLATE_HINTS = {
    "施工方案": ["总体施工方案", "施工管理", "施工方案与技术措施"],
    "质量管理": ["质量管理"],
    "安全管理": ["安全管理"],
    "文明环保": ["文明施工", "环境保护", "扬尘"],
    "施工进度": ["进度管理"],
    "工期管理": ["进度管理"],
    "施工总平面": ["总平面"],
    "绿色施工": ["绿色施工"],
    "消防工程": ["消防工程"],
    "地下人防": ["地下人防"],
    "重点难点": ["重点难点"],
    "资金保障": ["资金安全", "专款专用"],
}

FALLBACK_CHILDREN = {
    "项目理解": ["项目概况理解", "招标范围与建设目标理解", "项目特点分析", "技术标响应重点", "总体实施思路"],
    "内容完整性": ["技术标响应范围", "评分点逐项响应说明", "章节完整性组织", "响应依据与编制原则", "技术标完整性承诺"],
    "技术标完整性说明": ["技术标响应范围", "评分点逐项响应说明", "章节完整性组织", "响应依据与编制原则", "技术标完整性承诺"],
    "施工方案": ["项目概况", "编制依据", "施工部署", "主要施工方法", "重点难点分析及对策", "绿色施工响应"],
    "质量管理": ["质量管理目标", "质量保证体系", "质量控制措施", "关键工序质量控制", "质量通病防治措施"],
    "安全管理": ["安全管理目标", "安全管理体系", "安全生产责任制", "危险源辨识与控制", "安全防护措施", "应急管理措施"],
    "文明环保": ["文明施工目标", "文明施工管理体系", "现场扬尘治理措施", "环境保护措施", "噪声与污水控制措施"],
    "工期管理": ["工期目标", "施工进度计划管理", "进度保证体系", "关键线路保障措施", "工期纠偏措施"],
    "施工进度": ["施工进度计划说明", "施工进度表编制", "关键线路分析", "阶段节点控制"],
    "施工总平面": ["施工总平面布置原则", "临时设施布置", "施工道路与材料堆场布置", "临水临电布置", "施工总平面图"],
    "资源投入": ["劳动力投入计划", "主要机械设备投入计划", "材料供应计划", "资源保障措施"],
    "技术创新": ["技术创新应用目标", "新技术应用措施", "新工艺应用措施", "新材料新设备应用措施", "BIM 应用措施"],
    "信息化与BIM": ["BIM 应用目标", "信息化监控措施", "施工数据采集与处理", "信息化协同管理"],
    "风险管理": ["风险识别", "风险评估", "风险控制措施", "应急预案", "风险动态管理"],
    "unknown": ["评分标准响应", "实施思路", "主要措施", "复核后完善章节"],
}

PREFER_CATEGORY_CHILDREN = {
    "项目理解",
    "资源投入",
    "施工总平面",
    "施工进度",
    "技术创新",
    "信息化与BIM",
    "风险管理",
}

DESIGN_CHILDREN = [
    "设计目标与原则",
    "设计依据与标准",
    "建筑方案设计响应",
    "结构与机电设计响应",
    "绿色建筑与节能设计措施",
    "设计进度与质量保证",
    "设计施工协同措施",
]

MANAGEMENT_CHILDREN = [
    "管理目标与组织架构",
    "总承包协调管理",
    "设计与施工协同机制",
    "信息沟通与会议管理",
    "计划、质量、安全综合管控",
]


@dataclass(frozen=True, slots=True)
class TemplateSection:
    slice_id: str
    title: str
    level: int
    section_path: list[str]
    heading_index: int | None


def build_outline_from_files(
    parse_result_json: str | Path,
    *,
    excellent_bid_index_json: str | Path | None = None,
    generated_at: str | None = None,
    outline_id: str | None = None,
) -> dict[str, Any]:
    parse_result = _read_json(parse_result_json)
    excellent_bid_index = _read_json(excellent_bid_index_json) if excellent_bid_index_json else None
    return build_outline_tree(
        parse_result,
        excellent_bid_index=excellent_bid_index,
        generated_at=generated_at,
        outline_id=outline_id,
    )


def build_outline_tree(
    parse_result: dict[str, Any],
    *,
    excellent_bid_index: dict[str, Any] | None = None,
    generated_at: str | None = None,
    outline_id: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _now_iso()
    outline_id_value = outline_id or _default_outline_id(parse_result, generated)
    project_type = ((parse_result.get("project_type") or {}).get("value") or "construction").strip()
    score_points = _score_points(parse_result)
    template_sections = _template_sections(excellent_bid_index or {})
    template_top_sections = [section for section in template_sections if section.level == 1]
    nodes = []
    review_items: list[dict[str, Any]] = []

    blocked_reasons = _outline_block_reasons(parse_result, score_points)
    for index, point in enumerate(score_points, start=1):
        raw_title, display_title = _score_point_titles(point)
        title_source = "score_point_raw" if raw_title == display_title else "score_point_normalized"
        classification = _classify_score_point({**point, "catalog_level_1_title": display_title}, project_type)
        template_match = _match_template(classification["category"], display_title, template_top_sections)
        children, child_source, child_review = _children_for_point(
            point,
            classification=classification,
            template_match=template_match,
            template_sections=template_sections,
        )
        requires_review = (
            bool(point.get("review_required"))
            or bool(point.get("blocks_outline_generation"))
            or classification["needs_review"]
            or child_review
        )
        review_reason = _review_reason(point, classification, child_source)
        node = {
            "node_id": f"{outline_id_value}_{index:03d}",
            "level": 1,
            "number": str(index),
            "title": display_title,
            "title_source": title_source,
            "score_point_original_title": raw_title,
            "score_point_display_title": display_title,
            "score_point_id": point.get("score_point_id"),
            "score_point_ref": _first_source_ref(point.get("source_refs")),
            "score": point.get("score_value"),
            "score_rule": point.get("score_rule"),
            "domain": classification["domain"],
            "category": classification["category"],
            "template_source": child_source,
            "template_refs": _template_refs(template_match),
            "children": [
                _child_node(
                    outline_id_value,
                    index,
                    child_index,
                    child,
                    classification=classification,
                    title_source=child_source,
                    template_match=template_match,
                    requires_review=child_review,
                )
                for child_index, child in enumerate(children, start=1)
            ],
            "requires_review": requires_review,
            "review_reason": review_reason if requires_review else None,
            "generation_status": _generation_status(classification["domain"]),
        }
        nodes.append(node)
        if requires_review:
            review_items.append(
                _review_item(
                    len(review_items) + 1,
                    "blocking" if point.get("blocks_outline_generation") else "high",
                    f"复核目录：{display_title}",
                    review_reason,
                    "人工对照评分点原文、评分标准和优秀标书范式确认二三级目录。",
                )
            )

    review_items.extend(_coverage_review_items(len(review_items), blocked_reasons))
    _apply_confirmation_state(nodes)
    checks = _quality_checks(nodes, score_points, blocked_reasons)
    status = "blocked" if blocked_reasons else ("completed_with_warnings" if review_items else "completed")
    confirmation = _confirmation_view(
        nodes,
        review_items,
        blocked_reasons=blocked_reasons,
        status=status,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated,
        "outline_id": outline_id_value,
        "source_parse_job_id": (parse_result.get("parse_job") or {}).get("job_id"),
        "project_type": project_type if project_type in {"construction", "epc"} else "construction",
        "status": status,
        "can_generate_chapters": not blocked_reasons,
        "can_export_construction_only": _can_export_construction_only(nodes),
        "score_point_count": len(score_points),
        "level_1_count": len(nodes),
        "nodes": nodes,
        "quality_checks": checks,
        "review_items": review_items,
        "confirmation": confirmation,
    }


def write_outline_outputs(
    outline: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_outline_report(outline), encoding="utf-8")


def refresh_outline_confirmation(outline: dict[str, Any]) -> dict[str, Any]:
    """在目录节点变更后刷新人工确认视图与基础计数。"""

    nodes = [node for node in outline.get("nodes") or [] if isinstance(node, dict)]
    _apply_confirmation_state(nodes)
    review_items = [item for item in outline.get("review_items") or [] if isinstance(item, dict)]
    blocked_reasons = [
        str(reason)
        for reason in (outline.get("blocked_reasons") or [])
        if str(reason).strip()
    ]
    status = str(outline.get("status") or "completed")
    outline["level_1_count"] = len(nodes)
    outline["confirmation"] = _confirmation_view(
        nodes,
        review_items,
        blocked_reasons=blocked_reasons,
        status=status,
    )
    return outline


def render_outline_report(outline: dict[str, Any]) -> str:
    lines = [
        "# 技术标目录生成报告",
        "",
        f"- 生成时间：{outline.get('generated_at') or ''}",
        f"- 项目类型：{outline.get('project_type') or ''}",
        f"- 状态：{outline.get('status') or ''}",
        f"- 是否可进入章节生成：{'是' if outline.get('can_generate_chapters') else '否'}",
        f"- 技术评分点数量：{outline.get('score_point_count', 0)}",
        f"- 一级目录数量：{outline.get('level_1_count', 0)}",
        "",
        "## 1. 目录树",
        "",
    ]
    for node in outline.get("nodes") or []:
        review = "（需复核）" if node.get("requires_review") else ""
        lines.append(f"{node.get('number')}. {node.get('title')}{review}")
        for child in node.get("children") or []:
            child_review = "（需复核）" if child.get("requires_review") else ""
            lines.append(f"　{child.get('number')} {child.get('title')}{child_review}")
            for grandchild in child.get("children") or []:
                grandchild_review = "（需复核）" if grandchild.get("requires_review") else ""
                lines.append(f"　　{grandchild.get('number')} {grandchild.get('title')}{grandchild_review}")
    lines.extend(["", "## 2. 质量检查", "", "| 检查项 | 状态 | 说明 |", "|---|---|---|"])
    for check in outline.get("quality_checks") or []:
        lines.append(f"| {_cell(check.get('check'))} | {_cell(check.get('status'))} | {_cell(check.get('message'))} |")
    confirmation = outline.get("confirmation") or {}
    summary = confirmation.get("summary") or {}
    lines.extend(
        [
            "",
            "## 3. 人工确认状态",
            "",
            "| 项目 | 数量/状态 |",
            "|---|---|",
            f"| 确认状态 | {_cell(confirmation.get('status'))} |",
            f"| 一级目录锁定数 | {summary.get('locked_level_1_count', 0)} |",
            f"| 待复核节点数 | {summary.get('pending_review_count', 0)} |",
            f"| 阻断节点数 | {summary.get('blocking_count', 0)} |",
            f"| 施工域节点数 | {summary.get('domain_counts', {}).get('construction', 0)} |",
            f"| 设计域节点数 | {summary.get('domain_counts', {}).get('design', 0)} |",
            "",
            "## 4. 人工复核清单",
            "",
            "| 优先级 | 事项 | 原因 | 建议动作 |",
            "|---|---|---|---|",
        ]
    )
    for item in outline.get("review_items") or []:
        lines.append(
            f"| {_cell(item.get('priority'))} | {_cell(item.get('item'))} | "
            f"{_cell(item.get('reason'))} | {_cell(item.get('suggested_action'))} |"
        )
    if not outline.get("review_items"):
        lines.append("| - | 无 | - | - |")
    lines.append("")
    return "\n".join(lines)


def _score_points(parse_result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        point
        for point in parse_result.get("technical_score_points") or []
        if isinstance(point, dict)
    ]


def _score_point_titles(point: dict[str, Any]) -> tuple[str, str]:
    raw_title = str(point.get("original_text") or point.get("catalog_level_1_title") or "").strip()
    display_source = str(point.get("catalog_level_1_title") or raw_title).strip()
    display_title = _clean_pdf_heading_spaces(display_source or raw_title)
    return raw_title or display_title, display_title or raw_title


def _outline_block_reasons(parse_result: dict[str, Any], score_points: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    execution = parse_result.get("execution") or {}
    if execution.get("can_generate_outline") is False:
        reasons.append("招标文件解析结果标记为不可生成目录。")
    if not score_points:
        reasons.append("解析结果中没有技术标评分点。")
    for point in score_points:
        if point.get("blocks_outline_generation"):
            title = point.get("original_text") or point.get("catalog_level_1_title") or point.get("score_point_id")
            reasons.append(f"评分点“{title}”存在阻断问题。")
    return list(dict.fromkeys(reasons))


def _classify_score_point(point: dict[str, Any], project_type: str) -> dict[str, Any]:
    title_text = " ".join(
        str(value or "")
        for value in [
            _clean_pdf_heading_spaces(point.get("catalog_level_1_title") or point.get("original_text")),
            point.get("catalog_level_1_title"),
        ]
    )
    rule_text = str(point.get("score_rule") or "")
    text = " ".join(
        str(value or "")
        for value in [
            title_text,
            point.get("score_rule"),
        ]
    )
    domain = "construction"
    confidence = 0.82
    title_category = _category_from_text(title_text)
    rule_category = _category_from_text(rule_text)
    if title_category == "技术标完整性说明":
        return {
            "domain": "general",
            "category": "技术标完整性说明",
            "confidence": 0.9,
            "needs_review": False,
        }
    if _contains_any(title_text, DOMAIN_DESIGN_KEYWORDS):
        domain = "design"
        confidence = 0.86
    elif _contains_any(title_text, DOMAIN_MANAGEMENT_KEYWORDS) or title_category == "项目理解":
        domain = "management"
        confidence = 0.78
    elif not title_category and _contains_any(rule_text, DOMAIN_DESIGN_KEYWORDS):
        domain = "design"
        confidence = 0.78
    elif project_type == "epc" and _contains_any(rule_text, DOMAIN_MANAGEMENT_KEYWORDS):
        domain = "management"
        confidence = 0.78
    elif _contains_any(rule_text, DOMAIN_MANAGEMENT_KEYWORDS):
        domain = "management"
        confidence = 0.72

    category = title_category or rule_category or "unknown"
    if domain == "design" and category == "unknown":
        category = "设计方案"
    if domain == "management" and category == "unknown":
        category = "综合管理"
    if category == "unknown":
        confidence = min(confidence, 0.68)
    return {
        "domain": domain,
        "category": category,
        "confidence": confidence,
        "needs_review": domain == "design" or category == "unknown" or confidence < 0.75,
    }


def _category_from_text(text: str) -> str | None:
    for candidate, keywords in CATEGORY_RULES:
        if _contains_any(text, keywords):
            return candidate
    return None


def _template_sections(index: dict[str, Any]) -> list[TemplateSection]:
    sections: list[TemplateSection] = []
    for raw in index.get("slices") or []:
        if not isinstance(raw, dict):
            continue
        path = [str(part).strip() for part in raw.get("section_path") or [] if str(part).strip()]
        if not path:
            continue
        sections.append(
            TemplateSection(
                slice_id=str(raw.get("slice_id") or ""),
                title=path[-1],
                level=int(raw.get("level") or len(path)),
                section_path=path,
                heading_index=raw.get("heading_index") if isinstance(raw.get("heading_index"), int) else None,
            )
        )
    return sections


def _match_template(
    category: str,
    raw_title: str,
    top_sections: list[TemplateSection],
) -> TemplateSection | None:
    if not top_sections:
        return None
    normalized_raw = _normalize(raw_title)
    exact = [
        section
        for section in top_sections
        if _normalize(section.title) == normalized_raw
    ]
    if exact:
        return exact[0]
    hints = TEMPLATE_HINTS.get(category) or [category]
    scored: list[tuple[int, TemplateSection]] = []
    for section in top_sections:
        text = section.title
        score = sum(1 for hint in hints if hint and hint in text)
        if category != "unknown" and category in text:
            score += 2
        overlap = len(_keyword_set(raw_title) & _keyword_set(text))
        score += overlap
        if score:
            scored.append((score, section))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].heading_index if item[1].heading_index is not None else 999999))
    return scored[0][1]


def _children_for_point(
    point: dict[str, Any],
    *,
    classification: dict[str, Any],
    template_match: TemplateSection | None,
    template_sections: list[TemplateSection],
) -> tuple[list[str], str, bool]:
    domain = classification["domain"]
    category = classification["category"]
    if category == "技术标完整性说明":
        return FALLBACK_CHILDREN[category], "generated", True
    if category in PREFER_CATEGORY_CHILDREN and category in FALLBACK_CHILDREN:
        return FALLBACK_CHILDREN[category], "generated", True
    if domain == "design":
        return _children_from_score_rule(point, DESCRIPTIVE_LIMIT=7) or DESIGN_CHILDREN, "generated_from_requirement", True
    if domain == "management":
        return _children_from_score_rule(point, DESCRIPTIVE_LIMIT=6) or MANAGEMENT_CHILDREN, "generated_from_requirement", True
    if template_match:
        children = _template_children(template_match, template_sections)
        if children:
            return children, "excellent_bid_template", False
    fallback = _children_from_score_rule(point, DESCRIPTIVE_LIMIT=6)
    if fallback:
        return fallback, "generated_from_requirement", True
    return FALLBACK_CHILDREN.get(category) or FALLBACK_CHILDREN["unknown"], "generated", True


def _children_from_score_rule(point: dict[str, Any], *, DESCRIPTIVE_LIMIT: int) -> list[str]:
    rule = str(point.get("score_rule") or "")
    if not rule.strip():
        return []
    candidates: list[str] = []
    keyword_titles = [
        ("工程特点", "工程特点分析"),
        ("施工重点", "施工重点分析"),
        ("施工难点", "施工难点分析及对策"),
        ("绿色施工", "绿色施工措施"),
        ("施工工艺", "施工工艺选择"),
        ("施工机械", "施工机械配置"),
        ("完整", "评分标准完整性响应"),
        ("针对性", "针对性响应措施"),
        ("先进", "先进适用技术措施"),
        ("合理", "合理化建议"),
        ("质量", "质量保证措施"),
        ("安全", "安全保证措施"),
        ("进度", "进度控制措施"),
        ("设计", "设计方案响应"),
        ("BIM", "BIM 应用措施"),
        ("信息化", "信息化应用措施"),
    ]
    for keyword, title in keyword_titles:
        if keyword in rule and title not in candidates:
            candidates.append(title)
    return candidates[:DESCRIPTIVE_LIMIT]


def _template_children(template_match: TemplateSection, sections: list[TemplateSection]) -> list[str]:
    parent_path = template_match.section_path
    parent_level = template_match.level
    children: list[str] = []
    for section in sections:
        if section.level != parent_level + 1:
            continue
        if section.section_path[: len(parent_path)] != parent_path:
            continue
        title = _clean_template_title(section.title)
        if title and title not in children:
            children.append(title)
        if len(children) >= MAX_TEMPLATE_CHILDREN:
            break
    return children


def _child_node(
    outline_id: str,
    parent_index: int,
    child_index: int,
    title: str,
    *,
    classification: dict[str, Any],
    title_source: str,
    template_match: TemplateSection | None,
    requires_review: bool,
) -> dict[str, Any]:
    return {
        "node_id": f"{outline_id}_{parent_index:03d}_{child_index:03d}",
        "level": 2,
        "number": f"{parent_index}.{child_index}",
        "title": title,
        "title_source": title_source,
        "domain": classification["domain"],
        "category": classification["category"],
        "template_refs": _template_refs(template_match) if title_source == "excellent_bid_template" else [],
        "children": [],
        "requires_review": requires_review,
        "review_reason": "二级目录由系统根据评分标准生成，需人工确认。" if requires_review else None,
        "generation_status": _generation_status(classification["domain"]),
    }


def _apply_confirmation_state(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        _apply_confirmation_state_to_node(node)


def _apply_confirmation_state_to_node(node: dict[str, Any]) -> None:
    node["confirmation_state"] = _node_confirmation_state(node)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            _apply_confirmation_state_to_node(child)


def _confirmation_view(
    nodes: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
    *,
    blocked_reasons: list[str],
    status: str,
) -> dict[str, Any]:
    flat_nodes = _confirmation_flat_nodes(nodes)
    blocking_nodes = [
        item
        for item in flat_nodes
        if item.get("confirmation_state", {}).get("risk_level") == "blocking"
    ]
    pending_nodes = [
        item
        for item in flat_nodes
        if item.get("confirmation_state", {}).get("review_status") == "pending_review"
    ]
    locked_level_1 = [
        item
        for item in flat_nodes
        if item.get("level") == 1 and item.get("confirmation_state", {}).get("title_locked")
    ]
    return {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "status": _confirmation_status(status, pending_nodes, blocking_nodes),
        "rules": {
            "level_1_title_locked": True,
            "level_1_order_locked": True,
            "level_1_delete_forbidden": True,
            "level_1_source_required": True,
            "level_2_title_editable": True,
            "level_2_can_add_delete_reorder": True,
        },
        "summary": {
            "node_count": len(flat_nodes),
            "level_1_count": sum(1 for item in flat_nodes if item.get("level") == 1),
            "locked_level_1_count": len(locked_level_1),
            "pending_review_count": len(pending_nodes),
            "blocking_count": len(blocking_nodes) + len(blocked_reasons),
            "domain_counts": _domain_counts(flat_nodes),
        },
        "domain_groups": _domain_groups(nodes),
        "review_queue": _confirmation_review_queue(review_items, flat_nodes),
        "flat_nodes": flat_nodes,
    }


def _node_confirmation_state(node: dict[str, Any]) -> dict[str, Any]:
    level = int(node.get("level") or 0)
    requires_review = bool(node.get("requires_review"))
    risk_level = "none"
    if node.get("blocks_outline_generation"):
        risk_level = "blocking"
    elif requires_review:
        risk_level = "high" if level == 1 else "medium"
    review_status = "pending_review" if requires_review else "auto_checked"
    if risk_level == "blocking":
        review_status = "blocked"
    title_locked = level == 1
    order_locked = level == 1
    delete_forbidden = level == 1
    allowed_actions = ["view_source", "confirm"]
    if level == 1:
        allowed_actions.extend(["add_child", "reorder_children"])
    else:
        allowed_actions.extend(["edit_title", "delete", "move", "mark_not_applicable"])
    if review_status == "blocked":
        allowed_actions.append("resolve_blocking_issue")
    return {
        "review_status": review_status,
        "risk_level": risk_level,
        "title_locked": title_locked,
        "order_locked": order_locked,
        "delete_forbidden": delete_forbidden,
        "editable_fields": [] if title_locked else ["title", "enabled", "notes"],
        "allowed_actions": allowed_actions,
        "lock_reason": "一级目录来自招标文件技术评分点原文，禁止改写。" if title_locked else None,
        "source_label": _source_label(node.get("title_source")),
        "review_reason": node.get("review_reason"),
    }


def _confirmation_status(
    outline_status: str,
    pending_nodes: list[dict[str, Any]],
    blocking_nodes: list[dict[str, Any]],
) -> str:
    if outline_status == "blocked" or blocking_nodes:
        return "blocked"
    if pending_nodes:
        return "pending_review"
    return "ready"


def _confirmation_flat_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        _append_confirmation_flat_node(result, node, parent_node_id=None)
    return result


def _append_confirmation_flat_node(
    result: list[dict[str, Any]],
    node: dict[str, Any],
    *,
    parent_node_id: str | None,
) -> None:
    result.append(_confirmation_node_item(node, parent_node_id=parent_node_id))
    for child in node.get("children") or []:
        if isinstance(child, dict):
            _append_confirmation_flat_node(result, child, parent_node_id=node.get("node_id"))


def _confirmation_node_item(node: dict[str, Any], *, parent_node_id: str | None) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "parent_node_id": parent_node_id,
        "level": node.get("level"),
        "number": node.get("number"),
        "title": node.get("title"),
        "domain": node.get("domain"),
        "category": node.get("category"),
        "title_source": node.get("title_source"),
        "template_source": node.get("template_source"),
        "score_point_id": node.get("score_point_id"),
        "confirmation_state": node.get("confirmation_state") or {},
    }


def _domain_counts(flat_nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in flat_nodes:
        domain = str(item.get("domain") or "unknown")
        counts[domain] = counts.get(domain, 0) + 1
    return counts


def _domain_groups(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for node in nodes:
        domain = str(node.get("domain") or "unknown")
        group = groups.setdefault(
            domain,
            {
                "domain": domain,
                "label": _domain_label(domain),
                "level_1_node_ids": [],
                "can_generate_chapters": True,
                "can_export_word": domain == "construction",
            },
        )
        group["level_1_node_ids"].append(node.get("node_id"))
        if node.get("generation_status") == "design_pending":
            group["can_generate_chapters"] = False
    return list(groups.values())


def _confirmation_review_queue(
    review_items: list[dict[str, Any]],
    flat_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queue_by_node_id: dict[str, dict[str, Any]] = {}
    queue: list[dict[str, Any]] = []
    for item in review_items:
        target_node_id = _review_item_target_node_id(item, flat_nodes)
        if not target_node_id:
            continue
        node = next((candidate for candidate in flat_nodes if candidate.get("node_id") == target_node_id), None)
        if not node or node.get("confirmation_state", {}).get("review_status") != "pending_review":
            continue
        queue_by_node_id[target_node_id] = {
            "review_id": item.get("review_id"),
            "target_node_id": target_node_id,
            "priority": item.get("priority"),
            "item": item.get("item"),
            "reason": item.get("reason"),
            "suggested_action": item.get("suggested_action"),
            "status": "pending",
        }
    for node in flat_nodes:
        state = node.get("confirmation_state") or {}
        if state.get("review_status") != "pending_review":
            continue
        node_id = str(node.get("node_id") or "")
        if node_id in queue_by_node_id:
            continue
        queue_by_node_id[node_id] = {
            "review_id": f"OR{len(queue_by_node_id) + 1:03d}",
            "target_node_id": node_id,
            "priority": state.get("risk_level") or "medium",
            "item": f"复核目录：{node.get('title')}",
            "reason": state.get("review_reason") or "目录需要人工确认。",
            "suggested_action": "人工对照评分点原文、评分标准和优秀标书范式确认目录。",
            "status": "pending",
        }
    queue.extend(queue_by_node_id.values())
    return queue


def _review_item_target_node_id(item: dict[str, Any], flat_nodes: list[dict[str, Any]]) -> str | None:
    target_node_id = item.get("target_node_id")
    if isinstance(target_node_id, str) and target_node_id:
        return target_node_id
    title = str(item.get("item") or "").replace("复核目录：", "", 1)
    node = next((candidate for candidate in flat_nodes if str(candidate.get("title") or "") == title), None)
    node_id = node.get("node_id") if node else None
    return str(node_id) if node_id else None


def _source_label(title_source: Any) -> str:
    labels = {
        "score_point_raw": "评分点原文",
        "score_point_normalized": "评分点原文（去版式空格）",
        "excellent_bid_template": "优秀标书范式",
        "generated_from_requirement": "评分标准生成",
        "generated": "系统规则生成",
        "manual": "人工维护",
    }
    return labels.get(str(title_source or ""), str(title_source or "未知来源"))


def _domain_label(domain: str) -> str:
    labels = {
        "construction": "施工方案",
        "design": "设计方案",
        "general": "技术标响应说明",
        "management": "综合管理",
        "unknown": "待确认",
    }
    return labels.get(domain, domain)


def _quality_checks(nodes: list[dict[str, Any]], score_points: list[dict[str, Any]], blocked_reasons: list[str]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "评分点覆盖",
            len(nodes) == len(score_points) and bool(score_points),
            f"评分点 {len(score_points)} 个，一级目录 {len(nodes)} 个。",
        )
    )
    title_mismatches = []
    for node, point in zip(nodes, score_points, strict=False):
        raw_title, display_title = _score_point_titles(point)
        if _normalize(node.get("title")) != _normalize(raw_title) or node.get("title") != display_title:
            title_mismatches.append(str(node.get("number")))
    checks.append(
        _check(
            "一级目录原文一致性",
            not title_mismatches,
            "全部一级目录均与评分点原文等价，已去除 PDF 版式空格。" if not title_mismatches else f"不一致目录：{', '.join(title_mismatches)}。",
        )
    )
    missing_refs = [node.get("number") for node in nodes if not node.get("score_point_ref")]
    checks.append(
        _check(
            "评分点来源引用",
            not missing_refs,
            "全部一级目录均保留来源。" if not missing_refs else f"缺少来源目录：{', '.join(map(str, missing_refs))}。",
        )
    )
    design_nodes = [node for node in nodes if node.get("domain") == "design"]
    misused_design = [
        node.get("number")
        for node in design_nodes
        if node.get("template_source") == "excellent_bid_template"
    ]
    checks.append(
        _check(
            "设计施工分流",
            not misused_design,
            "设计类评分点未套用施工优秀标书范式。" if not misused_design else f"疑似误套施工范式：{', '.join(map(str, misused_design))}。",
        )
    )
    checks.append(
        _check(
            "目录生成阻断",
            not blocked_reasons,
            "无阻断问题。" if not blocked_reasons else "；".join(blocked_reasons),
        )
    )
    return checks


def _coverage_review_items(offset: int, blocked_reasons: list[str]) -> list[dict[str, Any]]:
    items = []
    for reason in blocked_reasons:
        items.append(
            _review_item(
                offset + len(items) + 1,
                "blocking",
                "处理目录生成阻断问题",
                reason,
                "先修复招标文件解析中的评分点质检问题，再重新生成目录。",
            )
        )
    return items


def _check(name: str, passed: bool, message: str) -> dict[str, str]:
    return {
        "check": name,
        "status": "passed" if passed else "failed",
        "message": message,
    }


def _review_item(
    index: int,
    priority: str,
    item: str,
    reason: str | None,
    suggested_action: str,
) -> dict[str, Any]:
    return {
        "review_id": f"OR{index:03d}",
        "priority": priority,
        "item": item,
        "reason": reason or "目录需要人工确认。",
        "suggested_action": suggested_action,
    }


def _review_reason(point: dict[str, Any], classification: dict[str, Any], child_source: str) -> str:
    reasons: list[str] = []
    if point.get("blocks_outline_generation"):
        reasons.append("评分点质检存在阻断问题")
    if point.get("review_required"):
        reasons.append("评分点解析结果需要人工复核")
    if classification["domain"] == "design":
        reasons.append("设计类评分点暂未接入设计优秀标书范式")
    if classification["category"] == "unknown":
        reasons.append("评分点分类置信度不足")
    if child_source in {"generated", "generated_from_requirement"}:
        reasons.append("二级目录由系统生成，未完全来自优秀标书范式")
    return "；".join(dict.fromkeys(reasons)) or "目录需要人工确认。"


def _generation_status(domain: str) -> str:
    if domain == "design":
        return "design_pending"
    if domain == "construction":
        return "construction_ready"
    if domain == "general":
        return "general_ready"
    return "management_ready"


def _can_export_construction_only(nodes: list[dict[str, Any]]) -> bool:
    return any(node.get("domain") == "construction" for node in nodes)


def _template_refs(template_match: TemplateSection | None) -> list[dict[str, Any]]:
    if not template_match:
        return []
    return [
        {
            "source_bid_id": "excellent_bid_001",
            "slice_id": template_match.slice_id,
            "section_title": template_match.title,
            "section_path": template_match.section_path,
        }
    ]


def _first_source_ref(source_refs: Any) -> dict[str, Any] | None:
    if isinstance(source_refs, list) and source_refs and isinstance(source_refs[0], dict):
        return source_refs[0]
    return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _clean_pdf_heading_spaces(text: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not _contains_cjk(cleaned):
        return cleaned
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", "", cleaned)
    cleaned = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"(?<=[、，,；;：:（）()])\s+(?=[A-Za-z0-9\u4e00-\u9fff])", "", cleaned)
    return cleaned.strip()


def _keyword_set(text: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or ""))
    useful = set()
    for token in tokens:
        if len(token) >= 2:
            useful.add(token)
    for _, keywords in CATEGORY_RULES:
        useful.update(keyword for keyword in keywords if keyword in text)
    return useful


def _clean_template_title(title: str) -> str:
    cleaned = re.sub(r"^\s*\d+[\s.、．-]+", "", title).strip()
    return cleaned or title.strip()


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")


def _default_outline_id(parse_result: dict[str, Any], generated_at: str) -> str:
    job_id = ((parse_result.get("parse_job") or {}).get("job_id") or "outline").strip()
    digits = re.sub(r"\D+", "", generated_at)[:14]
    safe_job = re.sub(r"[^\w\u4e00-\u9fff]+", "_", job_id).strip("_")
    return f"{safe_job}_{digits}"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
