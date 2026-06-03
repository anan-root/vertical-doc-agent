"""构建和校验二三级目录 LLM 补强输入输出。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


INPUT_SCHEMA_VERSION = "outline_refinement_input_v1"
OUTPUT_SCHEMA_VERSION = "outline_refinement_v1"
THIN_OUTLINE_THRESHOLD = 3
ALLOWED_TITLE_SOURCES = {
    "excellent_bid_template",
    "score_rule",
    "technical_requirement",
    "generated",
    "manual_review_required",
}
CORE_LEVEL_3_CATEGORIES = {
    "施工方案",
    "质量管理",
    "安全管理",
    "文明环保",
    "工期管理",
    "风险管理",
    "重点难点",
    "绿色施工",
    "消防工程",
    "地下人防",
}
CONSTRUCTION_METHOD_CATEGORIES = {"施工方案"}
MANAGEMENT_CATEGORIES = {"质量管理", "安全管理", "文明环保", "工期管理", "绿色施工"}
PLAN_TABLE_CATEGORIES = {"施工进度", "施工总平面", "资源投入"}
INNOVATION_RISK_CATEGORIES = {"技术创新", "信息化与BIM", "风险管理", "重点难点", "项目理解", "设计方案"}
CONSTRUCTION_METHOD_TITLE_KEYWORDS = {
    "主要施工方案",
    "施工方案与技术措施",
    "施工技术措施",
    "施工方法与技术措施",
}
GENERAL_COMPLETENESS_FORBIDDEN_TITLES = {
    "项目概况",
    "工程概况",
    "编制依据",
    "施工部署",
    "施工组织部署",
    "主要施工方法",
    "主要施工方案",
    "施工方案总体安排",
    "施工总安排",
}
FORBIDDEN_RESIDUE_KEYWORDS = [
    "历史项目名称",
    "历史建设单位",
    "历史地址",
    "医院路线",
    "人员姓名电话",
]
RESIDUE_PATTERNS = [
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"\d{3,4}-\d{7,8}"),
    re.compile(r"医院"),
    re.compile(r"救援路线"),
]


def build_outline_refinement_inputs(
    outline: dict[str, Any],
    parse_result: dict[str, Any],
    *,
    excellent_bid_index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    packages = []
    for node in outline.get("nodes") or []:
        if not isinstance(node, dict) or not _should_refine_node(node):
            continue
        packages.append(
            {
                "schema_version": INPUT_SCHEMA_VERSION,
                "task": "outline_refinement",
                "project_info": _project_info(parse_result),
                "target_outline_node": _target_outline_node(node),
                "granularity_rule": _granularity_rule(node),
                "technical_requirements": _technical_requirements(parse_result, node),
                "excellent_bid_candidates": _excellent_bid_candidates(outline, node, excellent_bid_index or {}),
                "forbidden_content": FORBIDDEN_RESIDUE_KEYWORDS,
                "trigger_reasons": _refinement_reasons(node),
            }
        )
    return packages


def validate_outline_refinement_output(
    output: dict[str, Any],
    input_package: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    target = input_package.get("target_outline_node") or {}
    target_node_id = target.get("node_id")
    level_1_title = target.get("level_1_title")
    rule = input_package.get("granularity_rule") or {}

    _normalize_outline_refinement_envelope(output, target_node_id, level_1_title)
    normalize_outline_refinement_output(output)
    issues.extend(_enforce_granularity_limits(output, rule))
    children = output.get("refined_children") if isinstance(output.get("refined_children"), list) else []

    if output.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        issues.append(_issue("blocking", "schema_version", "输出 schema_version 不正确。"))
    if output.get("target_node_id") != target_node_id:
        issues.append(_issue("blocking", "target_node_id", "输出 target_node_id 与输入目标节点不一致。"))
    output_level_1_title = output.get("level_1_title")
    if _normalize(output_level_1_title) != _normalize(level_1_title):
        issues.append(_issue("blocking", "level_1_modified", "LLM 输出疑似修改了一级目录。"))
    elif output_level_1_title != level_1_title:
        issues.append(_issue("warning", "level_1_whitespace_normalized", "LLM 输出一级目录仅存在空白差异，已按招标文件原文回写。"))
        output["level_1_title"] = level_1_title
    if output.get("level_1_title_unchanged") is not True:
        issues.append(_issue("blocking", "level_1_modified", "LLM 未确认一级目录保持不变。"))
    if not children:
        issues.append(_issue("blocking", "children_empty", "LLM 未输出二级目录。"))

    level_2_titles: list[str] = []
    has_level_3 = False
    level_3_count = 0
    max_level_3_per_level_2 = 0
    for index, child in enumerate(children, start=1):
        if not isinstance(child, dict):
            issues.append(_issue("blocking", "child_not_object", f"第 {index} 个二级目录不是对象。"))
            continue
        title = _clean_title(child.get("title"))
        if not title:
            issues.append(_issue("blocking", "child_title_empty", f"第 {index} 个二级目录标题为空。"))
        if _looks_numbered(title):
            issues.append(_issue("warning", "child_title_numbered", f"第 {index} 个二级目录标题疑似带编号。"))
        if child.get("level") != 2:
            issues.append(_issue("blocking", "child_level_invalid", f"第 {index} 个目录 level 必须为 2。"))
        if child.get("title_source") not in ALLOWED_TITLE_SOURCES:
            issues.append(_issue("warning", "title_source_invalid", f"第 {index} 个二级目录来源类型不在允许范围。"))
        if title:
            level_2_titles.append(_normalize(title))
        grandchildren = child.get("children") or []
        if isinstance(grandchildren, list):
            max_level_3_per_level_2 = max(max_level_3_per_level_2, len(grandchildren))
            level_3_count += sum(1 for item in grandchildren if isinstance(item, dict))
        for sub_index, grandchild in enumerate(grandchildren, start=1):
            if not isinstance(grandchild, dict):
                issues.append(_issue("blocking", "grandchild_not_object", f"第 {index}.{sub_index} 个三级目录不是对象。"))
                continue
            has_level_3 = True
            grandchild_title = _clean_title(grandchild.get("title"))
            if not grandchild_title:
                issues.append(_issue("blocking", "grandchild_title_empty", f"第 {index}.{sub_index} 个三级目录标题为空。"))
            if _looks_numbered(grandchild_title):
                issues.append(_issue("warning", "grandchild_title_numbered", f"第 {index}.{sub_index} 个三级目录标题疑似带编号。"))
            if grandchild.get("level") != 3:
                issues.append(_issue("blocking", "grandchild_level_invalid", f"第 {index}.{sub_index} 个目录 level 必须为 3。"))
            if grandchild.get("title_source") not in ALLOWED_TITLE_SOURCES:
                issues.append(_issue("warning", "grandchild_title_source_invalid", f"第 {index}.{sub_index} 个三级目录来源类型不在允许范围。"))

    duplicates = sorted(title for title in set(level_2_titles) if level_2_titles.count(title) > 1)
    if duplicates:
        issues.append(_issue("warning", "duplicate_level_2_title", "存在重复二级目录标题。"))

    min_level_2 = int(rule.get("min_level_2_count") or THIN_OUTLINE_THRESHOLD)
    if len(children) < min_level_2:
        issues.append(_issue("warning", "level_2_count_below_min", f"二级目录数量 {len(children)} 少于要求 {min_level_2}。"))
    max_level_2 = _optional_int(rule.get("max_level_2_count"))
    if max_level_2 is not None and len(children) > max_level_2:
        issues.append(_issue("blocking", "level_2_count_above_max", f"二级目录数量 {len(children)} 超过上限 {max_level_2}。"))
    max_total_level_3 = _optional_int(rule.get("max_total_level_3_count"))
    if max_total_level_3 is not None and level_3_count > max_total_level_3:
        issues.append(_issue("blocking", "level_3_count_above_max", f"三级目录总数 {level_3_count} 超过上限 {max_total_level_3}。"))
    max_level_3_per_l2 = _optional_int(rule.get("max_level_3_per_level_2"))
    if max_level_3_per_l2 is not None and max_level_3_per_level_2 > max_level_3_per_l2:
        issues.append(
            _issue(
                "blocking",
                "level_3_per_level_2_above_max",
                f"单个二级目录下三级目录数量 {max_level_3_per_level_2} 超过上限 {max_level_3_per_l2}。",
            )
        )
    if rule.get("level_3_allowed") is False and has_level_3:
        issues.append(_issue("blocking", "level_3_forbidden", "该章节类型不允许生成三级目录。"))
    if rule.get("level_3_required") is True and not has_level_3:
        issues.append(_issue("warning", "level_3_missing", "核心章节缺少三级目录。"))
    if str(target.get("category") or "") == "技术标完整性说明" or level_1_title == "内容完整性":
        for title in level_2_titles:
            if any(_normalize(forbidden) == title for forbidden in GENERAL_COMPLETENESS_FORBIDDEN_TITLES):
                issues.append(_issue("blocking", "completeness_misclassified_as_construction", "内容完整性章节误用了施工方案类目录。"))
                break

    residue_hits = _residue_hits(output)
    for hit in residue_hits:
        issues.append(_issue("blocking", "possible_residue", f"疑似历史项目残留：{hit}"))

    blocking_count = sum(1 for issue in issues if issue["severity"] == "blocking")
    warning_count = len(issues) - blocking_count
    return {
        "valid": blocking_count == 0,
        "blocking": blocking_count > 0,
        "issue_count": len(issues),
        "blocking_issue_count": blocking_count,
        "warning_issue_count": warning_count,
        "level_2_count": len(children),
        "level_3_count": level_3_count,
        "max_level_3_per_level_2": max_level_3_per_level_2,
        "chapter_type": rule.get("chapter_type"),
        "has_level_3": has_level_3,
        "issues": issues,
    }


def normalize_outline_refinement_output(output: dict[str, Any]) -> dict[str, Any]:
    """补齐 LLM 容易省略的机械字段。

    标题内容、一级目录一致性和历史残留仍由校验规则判断；这里仅补齐
    level/title_source 这类可由系统确定的结构字段。
    """

    children = output.get("refined_children")
    if not isinstance(children, list):
        return output
    normalized_children: list[Any] = []
    for child in children:
        if isinstance(child, str):
            child = {"title": child}
        if not isinstance(child, dict):
            normalized_children.append(child)
            continue
        _normalize_title_aliases(child, "level_2_title", "level2_title", "name", "heading", "title_text")
        child.setdefault("level", 2)
        if child.get("title_source") not in ALLOWED_TITLE_SOURCES:
            child["title_source"] = "generated"
        _normalize_children_aliases(
            child,
            "level_3_titles",
            "level3_titles",
            "level_3_children",
            "level3_children",
            "grandchildren",
            "subsections",
            "items",
        )
        grandchildren = child.get("children")
        if not isinstance(grandchildren, list):
            child["children"] = []
            normalized_children.append(child)
            continue
        normalized_grandchildren: list[Any] = []
        for grandchild in grandchildren:
            if isinstance(grandchild, str):
                grandchild = {"title": grandchild}
            if not isinstance(grandchild, dict):
                normalized_grandchildren.append(grandchild)
                continue
            _normalize_title_aliases(grandchild, "level_3_title", "level3_title", "name", "heading", "title_text")
            grandchild.setdefault("level", 3)
            if grandchild.get("title_source") not in ALLOWED_TITLE_SOURCES:
                grandchild["title_source"] = "generated"
            normalized_grandchildren.append(grandchild)
        child["children"] = normalized_grandchildren
        normalized_children.append(child)
    output["refined_children"] = normalized_children
    return output


def _normalize_outline_refinement_envelope(
    output: dict[str, Any],
    target_node_id: Any,
    level_1_title: Any,
) -> None:
    """补齐单节点任务中可由系统确定的外层机械字段。"""

    if isinstance(output.get("refined_children"), list):
        output.setdefault("schema_version", OUTPUT_SCHEMA_VERSION)
        output.setdefault("target_node_id", target_node_id)
        output.setdefault("level_1_title", level_1_title)
    if (
        "level_1_title_unchanged" not in output
        and _normalize(output.get("level_1_title")) == _normalize(level_1_title)
    ):
        output["level_1_title_unchanged"] = True


def _normalize_title_aliases(node: dict[str, Any], *aliases: str) -> None:
    if _clean_title(node.get("title")):
        return
    for alias in aliases:
        title = _clean_title(node.get(alias))
        if title:
            node["title"] = title
            return


def _normalize_children_aliases(node: dict[str, Any], *aliases: str) -> None:
    if isinstance(node.get("children"), list):
        return
    for alias in aliases:
        value = node.get(alias)
        if isinstance(value, list):
            node["children"] = value
            return


def write_refinement_inputs(packages: list[dict[str, Any]], json_path: str | Path) -> None:
    target = Path(json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"packages": packages}, ensure_ascii=False, indent=2), encoding="utf-8")


def _should_refine_node(node: dict[str, Any]) -> bool:
    reasons = _refinement_reasons(node)
    return bool(reasons)


def _refinement_reasons(node: dict[str, Any]) -> list[str]:
    category = str(node.get("category") or "")
    domain = str(node.get("domain") or "")
    children = [child for child in node.get("children") or [] if isinstance(child, dict)]
    reasons: list[str] = []
    if category == "技术标完整性说明" or node.get("title") == "内容完整性":
        reasons.append("技术标完整性说明需按评分点专门生成目录")
    if len(children) < THIN_OUTLINE_THRESHOLD:
        reasons.append("目录过薄")
    if _needs_level_3(category, children):
        reasons.append("核心章节缺少三级目录")
    if domain == "design":
        reasons.append("设计类章节缺少设计优秀标书范式")
    if node.get("template_source") in {"generated", "generated_from_requirement", "llm_required", "rule_skeleton_for_llm"}:
        reasons.append("未完全匹配优秀标书范式")
    return list(dict.fromkeys(reasons))


def _needs_level_3(category: str, children: list[dict[str, Any]]) -> bool:
    if category not in CORE_LEVEL_3_CATEGORIES:
        return False
    return not any(child.get("children") for child in children)


def _project_info(parse_result: dict[str, Any]) -> dict[str, str]:
    project_info = parse_result.get("project_info") or {}
    project_type = (parse_result.get("project_type") or {}).get("value") or "construction"
    return {
        "project_name": _field_value(project_info.get("project_name")),
        "project_type": project_type,
        "location": _field_value(project_info.get("construction_location")),
        "scale": _field_value(project_info.get("construction_scale")),
        "scope": _field_value(project_info.get("tender_scope")),
        "duration": _field_value(project_info.get("duration_requirement")),
        "quality": _field_value(project_info.get("quality_requirement")),
        "safety_civilized": _field_value(project_info.get("safety_civilization_requirement")),
    }


def _target_outline_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "level_1_title": node.get("title"),
        "level_1_title_locked": True,
        "domain": node.get("domain"),
        "category": node.get("category"),
        "score_rule": node.get("score_rule"),
        "existing_children": [
            {
                "level": child.get("level"),
                "title": child.get("title"),
                "children": [
                    {"level": grandchild.get("level"), "title": grandchild.get("title")}
                    for grandchild in child.get("children") or []
                    if isinstance(grandchild, dict)
                ],
            }
            for child in node.get("children") or []
            if isinstance(child, dict)
        ],
    }


def _granularity_rule(node: dict[str, Any]) -> dict[str, Any]:
    category = str(node.get("category") or "")
    profile = _granularity_profile(node)
    min_count = int(profile["min_level_2_count"])
    level_3_required = bool(profile["level_3_required"])
    return {
        "recommended_depth": "3级" if level_3_required else "2-3级",
        "chapter_type": profile["chapter_type"],
        "min_level_2_count": min_count,
        "max_level_2_count": profile["max_level_2_count"],
        "level_3_required": level_3_required,
        "level_3_allowed": profile["level_3_allowed"],
        "max_level_3_per_level_2": profile["max_level_3_per_level_2"],
        "max_total_level_3_count": profile["max_total_level_3_count"],
        "thin_outline_threshold": THIN_OUTLINE_THRESHOLD,
        "notes": profile["notes"],
    }


def _granularity_profile(node: dict[str, Any]) -> dict[str, Any]:
    category = str(node.get("category") or "")
    title = str(node.get("title") or "")
    if category == "技术标完整性说明" or title == "内容完整性":
        return {
            "chapter_type": "technical_bid_completeness_statement",
            "min_level_2_count": 3,
            "max_level_2_count": 5,
            "level_3_required": False,
            "level_3_allowed": False,
            "max_level_3_per_level_2": 0,
            "max_total_level_3_count": 0,
            "notes": [
                "本类章节只做技术标完整性说明，不展开施工方案。",
                "二级目录围绕响应范围、章节完整性、逐项响应说明、复核承诺展开。",
            ],
        }
    if category in CONSTRUCTION_METHOD_CATEGORIES or any(keyword in title for keyword in CONSTRUCTION_METHOD_TITLE_KEYWORDS):
        return {
            "chapter_type": "construction_method_and_technical_measures",
            "min_level_2_count": 8,
            "max_level_2_count": 12,
            "level_3_required": True,
            "level_3_allowed": True,
            "max_level_3_per_level_2": 8,
            "max_total_level_3_count": 70,
            "notes": [
                "本类章节是主要施工方案与技术措施类，结构要完整但不能无限展开。",
                "钢筋、模板、混凝土、防水、砌体、脚手架等工艺可作为三级目录；其下细分步骤写入正文，不继续变成目录。",
            ],
        }
    if category in MANAGEMENT_CATEGORIES:
        min_count = 5 if category == "工期管理" else 6
        return {
            "chapter_type": "management_measures",
            "min_level_2_count": min_count,
            "max_level_2_count": 8,
            "level_3_required": True,
            "level_3_allowed": True,
            "max_level_3_per_level_2": 4,
            "max_total_level_3_count": 24,
            "notes": [
                "管理类章节以目标、体系、责任、过程控制、检查改进和保障措施为主。",
                "三级目录用于必要展开，避免把制度条文逐条目录化。",
            ],
        }
    if category in PLAN_TABLE_CATEGORIES:
        min_count = 3 if category == "施工进度" else 4
        return {
            "chapter_type": "plan_or_table_statement",
            "min_level_2_count": min_count,
            "max_level_2_count": 6,
            "level_3_required": False,
            "level_3_allowed": False,
            "max_level_3_per_level_2": 0,
            "max_total_level_3_count": 0,
            "notes": [
                "图表计划类章节围绕编制说明、图表内容、控制要点和保障措施展开。",
                "原则上不生成三级目录，避免把图表说明拆碎。",
            ],
        }
    if category in INNOVATION_RISK_CATEGORIES:
        return {
            "chapter_type": "innovation_bim_risk_or_understanding",
            "min_level_2_count": 4,
            "max_level_2_count": 6,
            "level_3_required": category in {"风险管理", "重点难点"},
            "level_3_allowed": True,
            "max_level_3_per_level_2": 3,
            "max_total_level_3_count": 15,
            "notes": [
                "创新、BIM、信息化、风险和项目理解类章节重在目标、内容、实施路径、保障和成效。",
                "三级目录只保留关键展开项。",
            ],
        }
    level_3_required = category in CORE_LEVEL_3_CATEGORIES
    return {
        "chapter_type": "general_technical_bid_section",
        "min_level_2_count": THIN_OUTLINE_THRESHOLD,
        "max_level_2_count": 8,
        "level_3_required": level_3_required,
        "level_3_allowed": True,
        "max_level_3_per_level_2": 4,
        "max_total_level_3_count": 24,
        "notes": ["通用技术标章节按评分点要求适度展开，避免过度拆分。"],
    }


def _enforce_granularity_limits(output: dict[str, Any], rule: dict[str, Any]) -> list[dict[str, str]]:
    """按章节类型硬约束裁剪 LLM 输出，并返回超限问题。

    裁剪是为了防止失控目录继续污染后续正文生成；已被系统裁剪到可用范围的
    问题标记为 warning，后续通过 requires_review 提醒人工复核。
    """

    issues: list[dict[str, str]] = []
    children = output.get("refined_children")
    if not isinstance(children, list):
        return issues

    max_level_2 = _optional_int(rule.get("max_level_2_count"))
    if max_level_2 is not None and len(children) > max_level_2:
        del children[max_level_2:]
        issues.append(_issue("warning", "level_2_count_above_max", f"二级目录超过上限 {max_level_2}，已自动裁剪。"))

    level_3_allowed = rule.get("level_3_allowed")
    max_per_level_2 = _optional_int(rule.get("max_level_3_per_level_2"))
    max_total_level_3 = _optional_int(rule.get("max_total_level_3_count"))
    total_level_3 = 0
    for child in children:
        if not isinstance(child, dict):
            continue
        grandchildren = child.get("children")
        if not isinstance(grandchildren, list):
            continue
        if level_3_allowed is False and grandchildren:
            child["children"] = []
            issues.append(_issue("warning", "level_3_forbidden", "该章节类型不允许三级目录，已自动移除。"))
            continue
        if max_per_level_2 is not None and len(grandchildren) > max_per_level_2:
            child["children"] = grandchildren[:max_per_level_2]
            grandchildren = child["children"]
            issues.append(
                _issue(
                    "warning",
                    "level_3_per_level_2_above_max",
                    f"单个二级目录下三级目录超过上限 {max_per_level_2}，已自动裁剪。",
                )
            )
        if max_total_level_3 is not None:
            remaining = max(0, max_total_level_3 - total_level_3)
            if len(grandchildren) > remaining:
                child["children"] = grandchildren[:remaining]
                issues.append(_issue("warning", "level_3_count_above_max", f"三级目录总数超过上限 {max_total_level_3}，已自动裁剪。"))
            total_level_3 += len(child.get("children") or [])
    return _dedupe_issues(issues)


def _technical_requirements(parse_result: dict[str, Any], node: dict[str, Any]) -> list[str]:
    category = str(node.get("category") or "")
    domain = str(node.get("domain") or "")
    if category == "技术标完整性说明":
        return [
            "本章节用于说明技术标内容完整、章节齐全，并逐项响应招标文件技术标评分点。",
            "不得展开为施工方案、项目概况、编制依据或施工部署类内容。",
        ]
    result: list[str] = []
    for collection_name in ["technical_bid_requirements", "technical_standards"]:
        for item in parse_result.get(collection_name) or []:
            text = str(item.get("content") or item.get("summary") or item.get("original_excerpt") or "")
            if not text:
                continue
            hint = str(item.get("category") or item.get("generation_impact") or "")
            if category and category in text + hint:
                result.append(text)
            elif domain == "design" and "设计" in text + hint:
                result.append(text)
            elif domain == "construction" and any(keyword in text + hint for keyword in ["施工", "质量", "安全", "进度"]):
                result.append(text)
    return result[:8]


def _excellent_bid_candidates(
    outline: dict[str, Any],
    node: dict[str, Any],
    excellent_bid_index: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = []
    template_refs = node.get("template_refs") or []
    for ref in template_refs[:2]:
        section_path = ref.get("section_path") or []
        if not section_path:
            continue
        candidates.append(
            {
                "source_bid_id": ref.get("source_bid_id") or "excellent_bid_001",
                "matched_section_title": ref.get("section_title") or section_path[-1],
                "outline_excerpt": _template_outline_excerpt(excellent_bid_index, section_path),
            }
        )
    if candidates:
        return candidates
    return []


def _template_outline_excerpt(index: dict[str, Any], parent_path: list[str]) -> list[dict[str, Any]]:
    level_2_items: dict[str, dict[str, Any]] = {}
    for raw in index.get("slices") or []:
        path = [str(part).strip() for part in raw.get("section_path") or [] if str(part).strip()]
        if len(path) <= len(parent_path) or path[: len(parent_path)] != parent_path:
            continue
        relative = path[len(parent_path) :]
        if not relative:
            continue
        l2_title = _strip_number(relative[0])
        item = level_2_items.setdefault(
            l2_title,
            {"level": 2, "title": l2_title, "children": []},
        )
        if len(relative) >= 2:
            l3_title = _strip_number(relative[1])
            if l3_title and all(child["title"] != l3_title for child in item["children"]):
                item["children"].append({"level": 3, "title": l3_title})
    return list(level_2_items.values())[:12]


def _field_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return ""


def _clean_title(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _looks_numbered(title: str) -> bool:
    return bool(re.match(r"^\s*\d+(\.\d+)*[、.．\s-]+", title or ""))


def _strip_number(title: str) -> str:
    return re.sub(r"^\s*\d+(\.\d+)*[、.．\s-]+", "", title).strip()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.get("severity", ""), issue.get("type", ""), issue.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _residue_hits(value: Any) -> list[str]:
    text = json.dumps(value, ensure_ascii=False)
    hits = [keyword for keyword in FORBIDDEN_RESIDUE_KEYWORDS if keyword in text]
    for pattern in RESIDUE_PATTERNS:
        hits.extend(match.group(0) for match in pattern.finditer(text))
    return sorted(set(hits))


def _issue(severity: str, issue_type: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "type": issue_type,
        "message": message,
    }
