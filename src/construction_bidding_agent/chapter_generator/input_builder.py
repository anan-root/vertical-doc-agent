"""构建技术标章节正文生成输入包。

本模块只做正文生成前的数据组织，不调用 LLM。它把已确认或已补强的
目录树、招标文件解析结果和优秀标书素材索引整理为单章节生成任务。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


INPUT_SCHEMA_VERSION = "chapter_generation_input_v1"
INPUT_INDEX_SCHEMA_VERSION = "chapter_generation_input_index_v1"

CORE_SPLIT_CATEGORIES = {
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
CONTENT_COMPLETENESS_TITLES = {"内容完整性"}
CONTENT_COMPLETENESS_CATEGORY = "技术标完整性说明"
CONTENT_COMPLETENESS_SECTION_TYPE = "technical_bid_response_statement"
LEGACY_REUSE_LEVEL_MAP = {
    "light_rewrite": "rewrite_reuse",
    "rewrite": "rewrite_reuse",
    "review_required": "manual_review",
}
REUSE_LEVELS = {"direct_reuse", "rewrite_reuse", "parameterized_reuse", "manual_review"}
PROCESS_KEYWORDS = {
    "施工方案",
    "土建",
    "钢筋",
    "模板",
    "混凝土",
    "防水",
    "砌体",
    "脚手架",
    "土方",
    "基坑",
    "消防",
    "人防",
}
LARGE_CHAPTER_SPLIT_TITLE_KEYWORDS = {
    "土建施工方案",
    "土建工程施工方案",
    "主体结构施工方案",
    "主体工程施工方案",
    "主要施工方案",
    "施工方案与技术措施",
}
LARGE_CHAPTER_SPLIT_MIN_CHILDREN = 4
LARGE_CHAPTER_SPLIT_MAX_UNITS = 8
LARGE_CHAPTER_SPLIT_CHILD_KEYWORDS = {
    "测量",
    "土方",
    "基坑",
    "桩基",
    "钢筋",
    "模板",
    "混凝土",
    "防水",
    "砌体",
    "脚手架",
    "装饰",
    "装修",
    "机电",
    "安装",
    "消防",
    "人防",
    "屋面",
    "幕墙",
}
MANAGEMENT_KEYWORDS = {
    "质量",
    "安全",
    "文明",
    "环境",
    "扬尘",
    "绿色",
    "成品保护",
    "应急",
    "风险",
    "工期保证",
}
PROJECT_SPECIFIC_KEYWORDS = {
    "施工总平面",
    "总平面布置",
    "施工进度表",
    "进度网络图",
    "横道图",
    "项目概况",
    "工程概况",
}
DIRECT_REUSE_CHAPTER_KEYWORDS = {
    "质量管理体系",
    "质量保证体系",
    "安全管理体系",
    "安全保证体系",
    "文明施工",
    "环境保护",
    "扬尘治理",
    "绿色施工",
    "成品保护",
    "应急预案",
    "应急响应",
    "技术创新",
    "BIM",
    "信息化",
    "智慧工地",
    "管理制度",
}
PARAMETERIZED_REUSE_CHAPTER_KEYWORDS = {
    "工期保证",
    "进度保证",
    "资源配备",
    "机械设备",
    "劳动力",
    "施工部署",
    "流水段",
}
NO_DIRECT_REUSE_CHAPTER_KEYWORDS = {
    *PROJECT_SPECIFIC_KEYWORDS,
    "施工进度计划",
    "计划开竣工",
    "计划开、竣工",
    "现场踏勘",
    "现场现状",
    "周边环境",
    "交通组织",
}
PROJECT_FACT_IMAGE_TERMS = ["总平面图", "平面布置图", "进度计划", "网络计划", "横道图", "交通组织", "踏勘", "现状", "周边环境", "周边道路"]
GENERIC_PRACTICE_IMAGE_TERMS = ["成品保护", "标准化防护", "优秀做法", "标准化做法", "工艺", "做法", "防护", "样板", "扬尘", "喷淋", "洗车", "围挡", "材料堆放", "标识标牌"]
FORBIDDEN_CONTENT = [
    "编造人员姓名",
    "编造证书编号",
    "编造企业资质等级",
    "编造获奖情况",
    "编造类似业绩",
    "编造机械设备型号",
    "历史项目名称",
    "历史建设单位",
    "历史工程地点",
    "历史楼栋号",
    "特殊地址",
]
HISTORY_TRACE_SCAN_LABELS = [
    "项目名称",
    "工程名称",
    "建设单位",
    "发包人",
    "建设地点",
    "施工地址",
    "工程地点",
    "楼栋号",
]
HISTORY_TRACE_GENERIC_TERMS = {
    "本工程",
    "本项目",
    "项目名称",
    "工程名称",
    "项目概况",
    "工程概况",
    "建设单位",
    "建设地点",
    "施工方案",
    "技术措施",
    "质量管理",
    "安全管理",
    "文明施工",
    "环境保护",
}
HISTORY_TRACE_FALLBACK_TERMS = [
    "历史项目名称",
    "历史建设单位",
    "历史工程地点",
    "历史地址",
    "历史楼栋号",
    "特殊地址",
]
PROJECT_INFO_FIELD_MAP = {
    "project_name": "project_name",
    "project_type": "project_type",
    "location": "construction_location",
    "scale": "construction_scale",
    "scope": "tender_scope",
    "duration": "duration_requirement",
    "quality": "quality_requirement",
    "safety_civilized": "safety_civilization_requirement",
}


def build_chapter_generation_inputs_from_files(
    outline_json: str | Path,
    parse_result_json: str | Path,
    *,
    excellent_bid_index_json: str | Path | None = None,
    material_retrieval_inputs_json: str | Path | None = None,
    include_domains: set[str] | list[str] | tuple[str, ...] | None = None,
    split_core_level2: bool = True,
    max_packages: int | None = None,
) -> list[dict[str, Any]]:
    """从文件构建章节正文生成输入包。"""

    outline = _read_json(outline_json)
    parse_result = _read_json(parse_result_json)
    excellent_bid_index = _read_json(excellent_bid_index_json) if excellent_bid_index_json else None
    material_retrieval_inputs = _read_json(material_retrieval_inputs_json) if material_retrieval_inputs_json else None
    return build_chapter_generation_inputs(
        outline,
        parse_result,
        excellent_bid_index=excellent_bid_index,
        material_retrieval_inputs=material_retrieval_inputs,
        include_domains=include_domains,
        split_core_level2=split_core_level2,
        max_packages=max_packages,
    )


def build_chapter_generation_inputs(
    outline: dict[str, Any],
    parse_result: dict[str, Any],
    *,
    excellent_bid_index: dict[str, Any] | None = None,
    material_retrieval_inputs: dict[str, Any] | None = None,
    include_domains: set[str] | list[str] | tuple[str, ...] | None = None,
    split_core_level2: bool = True,
    max_packages: int | None = None,
) -> list[dict[str, Any]]:
    """根据目录树构建章节正文生成输入包列表。

    核心长章节默认按二级目录拆分生成；三级目录只作为当前生成单元的
    内部结构，不单独调度。
    """

    allowed_domains = set(include_domains) if include_domains else None
    score_points = _score_points_by_id(parse_result)
    retrieval_by_node, retrieval_by_path = _material_retrieval_maps(material_retrieval_inputs or {})
    packages: list[dict[str, Any]] = []
    for level_1_node in outline.get("nodes") or []:
        if not isinstance(level_1_node, dict):
            continue
        domain = str(level_1_node.get("domain") or "construction")
        if allowed_domains is not None and domain not in allowed_domains:
            continue
        units = _generation_units(level_1_node, split_core_level2=split_core_level2)
        for unit_node, unit_type in units:
            package = _build_package(
                outline,
                parse_result,
                level_1_node,
                unit_node,
                unit_type,
                score_points=score_points,
                excellent_bid_index=excellent_bid_index or {},
                material_retrieval_package=_find_material_retrieval_package(
                    retrieval_by_node,
                    retrieval_by_path,
                    level_1_node,
                    unit_node,
                ),
            )
            packages.append(package)
            if max_packages is not None and len(packages) >= max_packages:
                return packages
    return packages


def write_chapter_generation_inputs(
    packages: list[dict[str, Any]],
    json_path: str | Path,
    report_path: str | Path | None = None,
) -> None:
    """写入章节生成输入包 JSON，并可选写入 Markdown 摘要。"""

    target = Path(json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": INPUT_INDEX_SCHEMA_VERSION,
        "package_count": len(packages),
        "packages": packages,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path:
        report_target = Path(report_path)
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(render_chapter_generation_input_report(packages), encoding="utf-8")


def render_chapter_generation_input_report(packages: list[dict[str, Any]]) -> str:
    """渲染章节生成输入包的轻量检查报告。"""

    domain_counts: dict[str, int] = {}
    unit_type_counts: dict[str, int] = {}
    for package in packages:
        unit = package.get("generation_unit") or {}
        domain = str(unit.get("domain") or "unknown")
        unit_type = str(unit.get("unit_type") or "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        unit_type_counts[unit_type] = unit_type_counts.get(unit_type, 0) + 1

    lines = [
        "# 技术标章节正文生成输入包报告",
        "",
        f"- 输入包数量：{len(packages)}",
        "- 领域分布：" + _format_counts(domain_counts),
        "- 调度颗粒度分布：" + _format_counts(unit_type_counts),
        "",
        "## 输入包清单",
        "",
        "| 序号 | unit_id | 领域 | 颗粒度 | 章节路径 | 技术要求 | 优秀标书参考 | 图文块候选 | 表格模板 | 图片候选 |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, package in enumerate(packages, start=1):
        unit = package.get("generation_unit") or {}
        chapter_path = " > ".join(unit.get("chapter_path") or [])
        lines.append(
            f"| {index} | {_cell(unit.get('unit_id'))} | {_cell(unit.get('domain'))} | "
            f"{_cell(unit.get('unit_type'))} | {_cell(chapter_path)} | "
            f"{len(package.get('technical_requirements') or [])} | "
            f"{len(package.get('excellent_bid_references') or [])} | "
            f"{len(package.get('text_image_block_candidates') or [])} | "
            f"{len(package.get('table_references') or [])} | "
            f"{len(package.get('image_candidates') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_package(
    outline: dict[str, Any],
    parse_result: dict[str, Any],
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
    unit_type: str,
    *,
    score_points: dict[str, dict[str, Any]],
    excellent_bid_index: dict[str, Any],
    material_retrieval_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score_point = _score_point_for_node(level_1_node, score_points)
    is_content_completeness = _is_content_completeness_unit(level_1_node, unit_node)
    relevant_slices = (
        []
        if is_content_completeness
        else _relevant_slices(excellent_bid_index, level_1_node, unit_node)
    )
    if is_content_completeness:
        parameter_conflict_scan = {"enabled": False, "blocking_on_output": True, "hard_constraints": [], "conflicts": []}
        excellent_bid_references = []
        text_image_block_candidates = []
        table_references = []
        row_examples = []
        image_candidates = []
        image_candidate_pool = []
        image_group_candidates = []
        image_group_candidate_pool = []
        text_image_block_reuse_candidates = []
        reuse_warnings = []
        material_retrieval_summary = None
    elif material_retrieval_package:
        parameter_conflict_scan = _parameter_conflict_scan_from_retrieval(material_retrieval_package)
        excellent_bid_references = _excellent_bid_references_from_retrieval(material_retrieval_package)
        text_image_block_candidates = _text_image_block_candidates_from_retrieval(material_retrieval_package)
        table_references = _table_references_from_retrieval(material_retrieval_package)
        row_examples = []
        image_candidates = _image_candidates_from_retrieval(material_retrieval_package)
        image_candidate_pool = _image_candidate_pool_from_retrieval(material_retrieval_package)
        image_group_candidates = _image_group_candidates_from_retrieval(material_retrieval_package)
        image_group_candidate_pool = _image_group_candidate_pool_from_retrieval(material_retrieval_package)
        text_image_block_reuse_candidates = _text_image_block_reuse_candidates_from_retrieval(material_retrieval_package)
        reuse_warnings = list(material_retrieval_package.get("reuse_warnings") or [])
        material_retrieval_summary = _material_retrieval_summary(material_retrieval_package)
    else:
        parameter_conflict_scan = {"enabled": False, "blocking_on_output": True, "hard_constraints": [], "conflicts": []}
        text_image_block_candidates = []
        table_references = _table_references(relevant_slices)
        row_examples = _row_examples(table_references)
        image_candidates = _image_candidates(relevant_slices)
        image_candidate_pool = image_candidates
        image_group_candidates = []
        image_group_candidate_pool = []
        text_image_block_reuse_candidates = []
        excellent_bid_references = _excellent_bid_references(relevant_slices)
        reuse_warnings = []
        material_retrieval_summary = None
    current_project_info = _project_info(parse_result)
    expanded_policy = _expanded_generation_policy(
        level_1_node,
        unit_node,
        unit_type,
        excellent_bid_references=excellent_bid_references,
        table_references=table_references,
        image_candidates=image_candidates,
    )
    chapter_reuse_profile = _chapter_reuse_profile(
        level_1_node,
        unit_node,
        excellent_bid_references=excellent_bid_references,
        table_references=table_references,
        image_candidates=image_candidates,
        image_group_candidates=image_group_candidates,
    )
    history_trace_scan = _history_trace_scan(
        current_project_info,
        excellent_bid_references=excellent_bid_references,
        table_references=table_references,
    )
    return {
        "task_type": "generate_technical_bid_chapter",
        "schema_version": INPUT_SCHEMA_VERSION,
        "project_info": current_project_info,
        "generation_unit": _generation_unit(level_1_node, unit_node, unit_type),
        "score_point": score_point,
        "technical_requirements": _technical_requirements(parse_result, level_1_node, unit_node),
        "excellent_bid_references": excellent_bid_references,
        "text_image_block_candidates": text_image_block_candidates,
        "table_references": table_references,
        "row_examples": row_examples,
        "previous_chapter_summaries": [],
        "image_candidates": image_candidates,
        "image_candidate_pool": image_candidate_pool,
        "image_group_candidates": image_group_candidates,
        "image_group_candidate_pool": image_group_candidate_pool,
        "text_image_block_reuse_candidates": text_image_block_reuse_candidates,
        "auto_image_reuse_policy": _auto_image_reuse_policy(expanded_policy, image_candidate_pool),
        "material_retrieval_summary": material_retrieval_summary,
        "reuse_warnings": reuse_warnings,
        "expanded_generation_policy": expanded_policy,
        "chapter_reuse_profile": chapter_reuse_profile,
        "generation_constraints": {
            "generation_mode": "expanded",
            "style": "technical_bid_formal",
            "must_keep_level1_heading_raw": True,
            "allow_generic_measures_when_missing_detail": True,
            "forbidden_content": FORBIDDEN_CONTENT,
            "domain_generation_independent": True,
            "source_outline_id": outline.get("outline_id"),
            "expanded_targets": expanded_policy["targets"],
            "reuse_level_policy": expanded_policy["reuse_level_policy"],
            "chapter_reuse_profile": chapter_reuse_profile,
            "history_trace_scan": history_trace_scan,
            "parameter_conflict_scan": parameter_conflict_scan,
            "text_image_block_policy": {
                "enabled": bool(text_image_block_candidates),
                "llm_selects_block_id_only": True,
                "complete_block_kept_outside_llm_input": True,
                "single_images_are_fallback": True,
                "missing_suitable_block_behavior": "silent_skip",
            },
        },
    }


def _generation_units(level_1_node: dict[str, Any], *, split_core_level2: bool) -> list[tuple[dict[str, Any], str]]:
    children = [child for child in level_1_node.get("children") or [] if isinstance(child, dict)]
    category = str(level_1_node.get("category") or "")
    title = str(level_1_node.get("title") or "")
    should_split = (
        split_core_level2
        and bool(children)
        and category in CORE_SPLIT_CATEGORIES
        and title not in CONTENT_COMPLETENESS_TITLES
    )
    if not should_split:
        return [(level_1_node, "level1_chapter")]
    units: list[tuple[dict[str, Any], str]] = []
    for child in children:
        if _should_split_large_level2_child(level_1_node, child):
            for grandchild in _bounded_large_level2_children(child):
                if isinstance(grandchild, dict):
                    units.append((_large_chapter_subunit_node(level_1_node, child, grandchild), "level3_subsection_unit"))
            continue
        units.append((child, "level2_section_group"))
    return units


def _should_split_large_level2_child(level_1_node: dict[str, Any], child: dict[str, Any]) -> bool:
    child_nodes = [item for item in child.get("children") or [] if isinstance(item, dict)]
    if len(child_nodes) < LARGE_CHAPTER_SPLIT_MIN_CHILDREN:
        return False
    if str(level_1_node.get("category") or "") != "施工方案":
        return False
    text = f"{child.get('title') or ''} {child.get('category') or ''}"
    return any(keyword in text for keyword in LARGE_CHAPTER_SPLIT_TITLE_KEYWORDS)


def _bounded_large_level2_children(child: dict[str, Any]) -> list[dict[str, Any]]:
    child_nodes = [item for item in child.get("children") or [] if isinstance(item, dict)]
    if len(child_nodes) <= LARGE_CHAPTER_SPLIT_MAX_UNITS:
        return child_nodes
    prioritized = [
        item
        for item in child_nodes
        if any(keyword in str(item.get("title") or "") for keyword in LARGE_CHAPTER_SPLIT_CHILD_KEYWORDS)
    ]
    remainder = [item for item in child_nodes if item not in prioritized]
    return (prioritized + remainder)[:LARGE_CHAPTER_SPLIT_MAX_UNITS]


def _large_chapter_subunit_node(
    level_1_node: dict[str, Any],
    level_2_node: dict[str, Any],
    level_3_node: dict[str, Any],
) -> dict[str, Any]:
    node = dict(level_3_node)
    node["node_id"] = level_3_node.get("node_id") or f"{level_2_node.get('node_id')}_{_normalize(level_3_node.get('title'))}"
    node["domain"] = level_3_node.get("domain") or level_2_node.get("domain") or level_1_node.get("domain")
    node["category"] = level_3_node.get("category") or level_2_node.get("category") or level_1_node.get("category")
    node["children"] = []
    node["_split_parent_level_2"] = {
        "node_id": level_2_node.get("node_id"),
        "title": level_2_node.get("title"),
        "number": level_2_node.get("number"),
    }
    node["_split_root_level_1"] = {
        "node_id": level_1_node.get("node_id"),
        "title": level_1_node.get("title"),
        "number": level_1_node.get("number"),
    }
    return node


def _is_content_completeness_unit(level_1_node: dict[str, Any], unit_node: dict[str, Any]) -> bool:
    """内容完整性是技术标总览说明，不套用施工方案表格素材。"""

    titles = {str(level_1_node.get("title") or ""), str(unit_node.get("title") or "")}
    category = str(level_1_node.get("category") or unit_node.get("category") or "")
    return bool(titles & CONTENT_COMPLETENESS_TITLES) or category == CONTENT_COMPLETENESS_CATEGORY


def _generation_unit(
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
    unit_type: str,
) -> dict[str, Any]:
    path = _chapter_path(level_1_node, unit_node)
    child_headings = [
        str(child.get("title") or "")
        for child in unit_node.get("children") or []
        if isinstance(child, dict) and str(child.get("title") or "").strip()
    ]
    is_content_completeness = _is_content_completeness_unit(level_1_node, unit_node)
    return {
        "unit_id": f"GU-{unit_node.get('node_id')}",
        "target_node_id": unit_node.get("node_id"),
        "parent_level_1_node_id": level_1_node.get("node_id"),
        "parent_level_2_node_id": (unit_node.get("_split_parent_level_2") or {}).get("node_id"),
        "parent_level_2_title": (unit_node.get("_split_parent_level_2") or {}).get("title"),
        "split_from_unit_type": "level2_section_group" if unit_type == "level3_subsection_unit" else None,
        "unit_type": unit_type,
        "domain": "general" if is_content_completeness else unit_node.get("domain") or level_1_node.get("domain") or "construction",
        "category": CONTENT_COMPLETENESS_CATEGORY if is_content_completeness else unit_node.get("category") or level_1_node.get("category"),
        "chapter_path": path,
        "child_headings": child_headings,
        "expected_depth": "detailed" if child_headings else "standard",
    }


def _expanded_generation_policy(
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
    unit_type: str,
    *,
    excellent_bid_references: list[dict[str, Any]],
    table_references: list[dict[str, Any]],
    image_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """为 expanded 详稿模式生成章节体量和素材复用要求。"""

    chapter_text = _chapter_policy_text(level_1_node, unit_node)
    child_headings = [
        str(child.get("title") or "")
        for child in unit_node.get("children") or []
        if isinstance(child, dict) and str(child.get("title") or "").strip()
    ]
    direct_count = sum(1 for item in excellent_bid_references if item.get("reuse_level") == "direct_reuse")
    parameterized_count = sum(1 for item in excellent_bid_references if item.get("reuse_level") == "parameterized_reuse")
    manual_count = sum(1 for item in excellent_bid_references if item.get("reuse_level") == "manual_review")
    reusable_image_count = sum(1 for item in image_candidates if _image_auto_reuse_allowed(item))
    section_type = (
        CONTENT_COMPLETENESS_SECTION_TYPE
        if _is_content_completeness_unit(level_1_node, unit_node)
        else _expanded_section_type(chapter_text)
    )
    targets = _expanded_targets(
        section_type,
        child_heading_count=len(child_headings),
        table_reference_count=len(table_references),
        reusable_image_count=reusable_image_count,
    )
    return {
        "mode": "expanded",
        "section_type": section_type,
        "targets": targets,
        "preferred_section_headings": child_headings,
        "reuse_profile": {
            "direct_reuse_count": direct_count,
            "rewrite_reuse_count": sum(1 for item in excellent_bid_references if item.get("reuse_level") == "rewrite_reuse"),
            "parameterized_reuse_count": parameterized_count,
            "manual_review_count": manual_count,
            "table_reference_count": len(table_references),
            "reusable_image_count": reusable_image_count,
        },
        "reuse_level_policy": {
            "direct_reuse": "可作为正文主素材，允许吸收成熟表达和表格结构，但必须替换项目名称、工期、质量、安全文明目标等当前项目字段。",
            "rewrite_reuse": "只参考章节结构、措施点和表格列结构，正文必须重新组织语言。",
            "parameterized_reuse": "可参考施工工艺流程、控制点和表格模板，必须结合当前项目参数改写；参数缺失时输出复核项，不得编造。",
            "manual_review": "不得作为正文主素材自动写入，只能生成占位、候选说明或人工复核项。",
        },
        "writing_requirements": _expanded_writing_requirements(section_type, bool(child_headings), direct_count, parameterized_count, manual_count, reusable_image_count),
    }


def _chapter_reuse_profile(
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
    *,
    excellent_bid_references: list[dict[str, Any]],
    table_references: list[dict[str, Any]],
    image_candidates: list[dict[str, Any]],
    image_group_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成章节级复用画像，作为正文生成和校验的结构化约束。"""

    chapter_text = _chapter_policy_text(level_1_node, unit_node)
    section_type = (
        CONTENT_COMPLETENESS_SECTION_TYPE
        if _is_content_completeness_unit(level_1_node, unit_node)
        else _expanded_section_type(chapter_text)
    )
    if section_type == CONTENT_COMPLETENESS_SECTION_TYPE:
        profile = "manual_review"
        allow_direct_reuse = False
        reason = "技术标完整性说明只写响应范围和完整性承诺，不套用历史施工方案素材。"
    elif _contains_any(chapter_text, NO_DIRECT_REUSE_CHAPTER_KEYWORDS) or section_type == "project_specific":
        profile = "manual_review"
        allow_direct_reuse = False
        reason = "章节包含项目概况、总平面、进度计划、踏勘现状或周边环境等强项目事实，禁止直接复用历史素材。"
    elif _contains_any(chapter_text, DIRECT_REUSE_CHAPTER_KEYWORDS) or section_type == "management_measure":
        profile = "direct_reuse_preferred"
        allow_direct_reuse = True
        reason = "章节属于质量、安全、文明环保、成品保护、应急、BIM 信息化等通用管理措施，可优先复用成熟表达、表格和通用图块。"
    elif _contains_any(chapter_text, PARAMETERIZED_REUSE_CHAPTER_KEYWORDS):
        profile = "parameterized_reuse_preferred"
        allow_direct_reuse = False
        reason = "章节依赖工期、资源、机械、流水段等项目参数，应参考优秀标书结构并项目化改写。"
    elif section_type == "construction_process":
        profile = "parameterized_reuse_preferred"
        allow_direct_reuse = False
        reason = "施工工艺章节可复用工艺流程、控制点、通用做法图，但正文需结合当前项目参数改写。"
    else:
        profile = "rewrite_reuse_preferred"
        allow_direct_reuse = False
        reason = "一般技术措施章节以结构和措施点参考为主，避免大段直接复用。"

    return {
        "profile": profile,
        "section_type": section_type,
        "allow_direct_text_reuse": allow_direct_reuse,
        "allow_table_group_reuse": profile != "manual_review",
        "allow_image_group_reuse": profile != "manual_review",
        "requires_project_parameterization": profile in {"parameterized_reuse_preferred", "manual_review"},
        "material_counts": {
            "excellent_bid_references": len(excellent_bid_references),
            "table_references": len(table_references),
            "image_candidates": len(image_candidates),
            "image_group_candidates": len(image_group_candidates),
        },
        "preferred_material_reuse_levels": _preferred_material_reuse_levels(profile),
        "blocked_direct_reuse_topics": sorted(NO_DIRECT_REUSE_CHAPTER_KEYWORDS),
        "history_trace_required": True,
        "reason": reason,
    }


def _preferred_material_reuse_levels(profile: str) -> list[str]:
    if profile == "direct_reuse_preferred":
        return ["direct_reuse", "rewrite_reuse"]
    if profile == "parameterized_reuse_preferred":
        return ["parameterized_reuse", "rewrite_reuse"]
    if profile == "rewrite_reuse_preferred":
        return ["rewrite_reuse", "parameterized_reuse"]
    return ["manual_review"]


def _expanded_section_type(chapter_text: str) -> str:
    if any(keyword in chapter_text for keyword in PROJECT_SPECIFIC_KEYWORDS):
        return "project_specific"
    if _contains_any(chapter_text, DIRECT_REUSE_CHAPTER_KEYWORDS):
        return "management_measure"
    if any(keyword in chapter_text for keyword in MANAGEMENT_KEYWORDS):
        return "management_measure"
    if any(keyword in chapter_text for keyword in PROCESS_KEYWORDS):
        return "construction_process"
    return "general_measure"


def _expanded_targets(
    section_type: str,
    *,
    child_heading_count: int,
    table_reference_count: int,
    reusable_image_count: int,
) -> dict[str, int]:
    min_image_refs = min(reusable_image_count, 3)
    if section_type == CONTENT_COMPLETENESS_SECTION_TYPE:
        min_sections = max(child_heading_count, 5)
        return {
            "min_sections": min_sections,
            "min_paragraphs_per_section": 2,
            "min_paragraphs_total": max(10, min_sections * 2),
            "min_rich_tables": 2,
            "min_rows_per_rich_table": 5,
            "min_image_refs": 0,
            "min_image_placeholders": 0,
        }
    if section_type == "construction_process":
        min_sections = max(child_heading_count, 4)
        process_image_refs = min(reusable_image_count, 4)
        if reusable_image_count:
            process_image_refs = max(2, process_image_refs)
        return {
            "min_sections": min_sections,
            "min_paragraphs_per_section": 3,
            "min_paragraphs_total": max(14, min_sections * 3),
            "min_rich_tables": max(2, min(table_reference_count, 4)),
            "min_rows_per_rich_table": 4,
            "min_image_refs": process_image_refs,
        }
    if section_type == "management_measure":
        min_sections = max(child_heading_count, 3)
        return {
            "min_sections": min_sections,
            "min_paragraphs_per_section": 3,
            "min_paragraphs_total": max(10, min_sections * 3),
            "min_rich_tables": max(2, min(table_reference_count, 4)),
            "min_rows_per_rich_table": 4,
            "min_image_refs": min_image_refs,
        }
    if section_type == "project_specific":
        min_sections = max(child_heading_count, 3)
        return {
            "min_sections": min_sections,
            "min_paragraphs_per_section": 2,
            "min_paragraphs_total": max(8, min_sections * 2),
            "min_rich_tables": max(1, min(table_reference_count, 2)),
            "min_rows_per_rich_table": 3,
            "min_image_refs": 0,
            "min_image_placeholders": 0,
        }
    min_sections = max(child_heading_count, 3)
    return {
        "min_sections": min_sections,
        "min_paragraphs_per_section": 2,
        "min_paragraphs_total": max(8, min_sections * 2),
        "min_rich_tables": max(1, min(table_reference_count, 3)),
        "min_rows_per_rich_table": 4,
        "min_image_refs": min_image_refs,
    }


def _expanded_writing_requirements(
    section_type: str,
    has_child_headings: bool,
    direct_count: int,
    parameterized_count: int,
    manual_count: int,
    reusable_image_count: int,
) -> list[str]:
    requirements = [
        "输出必须是可供编标人员修改的正式 Word 初稿，不是摘要、提纲或说明。",
        "每个小节应包含做法、控制要点、检查验收、纠偏措施，避免只写原则性口号。",
        "优先使用 rich_table 承载措施清单、控制要点、责任分工、检查频次、验收标准等内容。",
    ]
    if section_type == CONTENT_COMPLETENESS_SECTION_TYPE:
        return [
            "本章是技术标完整性与响应说明，不是施工方案章节。",
            "正文应说明技术标对招标文件技术评分点、编制要求、技术标准和目录章节的完整覆盖关系。",
            "应优先输出“评分点响应汇总表”和“章节完整性检查表”等 rich_table，便于编标人员复核。",
            "不得展开项目概况、施工部署、主要施工方法、施工工艺流程、质量安全管理体系等施工方案范式内容。",
            "不得输出 image_ref 或 image_placeholder；本章不配置施工图片、总平面图、进度图或现场照片。",
        ]
    if has_child_headings:
        requirements.append("优先沿用 generation_unit.child_headings 作为内部小节，不得遗漏已有三级目录意图。")
    if direct_count:
        requirements.append("direct_reuse 素材可高强度吸收为通用管理正文和表格，但仍需替换当前项目字段。")
    if parameterized_count:
        requirements.append("parameterized_reuse 素材必须补足参数化复核项，如工程规模、结构形式、设备型号、工期节点等。")
    if manual_count or section_type == "project_specific":
        requirements.append("涉及总平面图、进度网络图、踏勘/现状照片、项目概况等内容时，不得套用历史项目事实；无合适素材时静默跳过，不输出图片占位。")
    if reusable_image_count:
        requirements.append("存在可自动复用图片候选时，模型只需写好正文和表格；系统将在后处理按高置信语义自动插图。")
    if section_type == "construction_process":
        requirements.append("施工工艺章节应展开施工准备、工艺流程、操作要点、质量控制、安全控制、成品保护和验收标准。")
    elif section_type == "management_measure":
        requirements.append("管理措施章节应展开组织体系、责任分工、制度流程、检查频次、问题整改和闭环管理。")
    return requirements


def _chapter_policy_text(level_1_node: dict[str, Any], unit_node: dict[str, Any]) -> str:
    parts = [
        str(level_1_node.get("title") or ""),
        str(level_1_node.get("category") or ""),
        str(level_1_node.get("score_rule") or ""),
        str(unit_node.get("title") or ""),
        str(unit_node.get("category") or ""),
    ]
    parts.extend(str(child.get("title") or "") for child in unit_node.get("children") or [] if isinstance(child, dict))
    return " ".join(parts)


def _history_trace_scan(
    project_info: dict[str, str],
    *,
    excellent_bid_references: list[dict[str, Any]],
    table_references: list[dict[str, Any]],
) -> dict[str, Any]:
    current_values = _current_project_values(project_info)
    candidate_terms = _history_trace_candidate_terms(excellent_bid_references, table_references)
    return {
        "enabled": True,
        "current_project_values": current_values,
        "scan_labels": HISTORY_TRACE_SCAN_LABELS,
        "candidate_terms": candidate_terms,
        "rules": [
            "输出正文不得出现历史项目名称、历史建设单位、历史地址、历史楼栋号等项目专属信息。",
            "优秀标书中项目名称、建设单位、地址、楼栋号等字段只能作为待替换信号，不得直接进入正文。",
            "若确需引用当前项目信息，只能使用 project_info 中的当前项目字段。",
        ],
    }


def _current_project_values(project_info: dict[str, str]) -> list[str]:
    values: list[str] = []
    for key in ["project_name", "location", "scale", "scope", "duration", "quality", "safety_civilized"]:
        value = str(project_info.get(key) or "").strip()
        if value:
            values.append(value)
    return _unique_strings(values)[:12]


def _history_trace_candidate_terms(
    excellent_bid_references: list[dict[str, Any]],
    table_references: list[dict[str, Any]],
) -> list[str]:
    terms: list[str] = list(HISTORY_TRACE_FALLBACK_TERMS)
    for ref in excellent_bid_references:
        if not isinstance(ref, dict):
            continue
        terms.extend(_extract_history_terms(ref.get("title")))
        terms.extend(_extract_history_terms(" ".join(str(part) for part in ref.get("section_path") or [])))
        terms.extend(_extract_history_terms(ref.get("reference_excerpt")))
    for table in table_references:
        if not isinstance(table, dict):
            continue
        terms.extend(_extract_history_terms(table.get("title")))
        terms.extend(_extract_history_terms(" ".join(str(part) for part in table.get("source_section_path") or [])))
    return _unique_strings(terms)[:30]


def _extract_history_terms(value: Any) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    terms: list[str] = []
    for label in HISTORY_TRACE_SCAN_LABELS:
        label_index = text.find(label)
        if label_index < 0:
            continue
        window = text[label_index : label_index + 80]
        for match in re.findall(r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{4,40}", window):
            cleaned = match.strip("：:，,；;。.\n\r\t ")
            if _is_history_trace_term(cleaned):
                terms.append(cleaned)
    for match in re.findall(r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{6,45}(?:项目|工程|标段|楼|栋|厂房|实验楼|幼儿园|中学|产业园)", text):
        cleaned = match.strip("：:，,；;。.\n\r\t ")
        if _is_history_trace_term(cleaned):
            terms.append(cleaned)
    return terms


def _is_history_trace_term(value: str) -> bool:
    text = _normalize(value)
    if len(text) < 4:
        return False
    if text in {_normalize(item) for item in HISTORY_TRACE_GENERIC_TERMS}:
        return False
    if text in {_normalize(item) for item in PROJECT_SPECIFIC_KEYWORDS}:
        return False
    return True


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _unique_strings(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        key = _normalize(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _score_point_for_node(
    level_1_node: dict[str, Any],
    score_points: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    score_point_id = str(level_1_node.get("score_point_id") or "")
    source = score_points.get(score_point_id, {})
    return {
        "score_point_id": score_point_id or source.get("score_point_id"),
        "score_point_raw": source.get("original_text") or level_1_node.get("title"),
        "score_standard_raw": source.get("score_rule") or level_1_node.get("score_rule"),
        "score_value": source.get("score_value") or level_1_node.get("score"),
        "source_refs": source.get("source_refs") or ([level_1_node.get("score_point_ref")] if level_1_node.get("score_point_ref") else []),
        "must_use_original_text_as_heading": True,
    }


def _project_info(parse_result: dict[str, Any]) -> dict[str, str]:
    raw = parse_result.get("project_info") or {}
    project_type = (parse_result.get("project_type") or {}).get("value") or "construction"
    result: dict[str, str] = {}
    for output_key, source_key in PROJECT_INFO_FIELD_MAP.items():
        if output_key == "project_type":
            result[output_key] = str(project_type)
        else:
            result[output_key] = _field_value(raw.get(source_key))
    return result


def _technical_requirements(
    parse_result: dict[str, Any],
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
) -> list[dict[str, Any]]:
    if _is_content_completeness_unit(level_1_node, unit_node):
        result = [
            {
                "requirement_id": "GENERAL-COMPLETENESS-001",
                "type": "generation_guidance",
                "category": CONTENT_COMPLETENESS_CATEGORY,
                "raw_clause": "本章用于说明技术标对招标文件技术评分点、技术要求和目录章节的完整响应关系，不展开具体施工工艺。",
                "applies_to": "general",
                "target_section_hint": unit_node.get("title") or level_1_node.get("title"),
                "priority": "high",
                "source_refs": [],
                "confidence": 1.0,
                "review_required": False,
            }
        ]
        for index, score_point in enumerate(parse_result.get("technical_score_points") or [], start=1):
            if not isinstance(score_point, dict):
                continue
            title = str(score_point.get("original_text") or "").strip()
            rule = str(score_point.get("score_rule") or "").strip()
            if not title:
                continue
            result.append(
                {
                    "requirement_id": score_point.get("score_point_id") or f"SCORE-COVERAGE-{index:03d}",
                    "type": "score_point_coverage",
                    "category": CONTENT_COMPLETENESS_CATEGORY,
                    "raw_clause": f"{title}：{rule}" if rule else title,
                    "applies_to": "general",
                    "target_section_hint": "评分点逐项响应说明",
                    "priority": "high",
                    "source_refs": score_point.get("source_refs") or [],
                    "confidence": score_point.get("confidence"),
                    "review_required": bool(score_point.get("review_required")),
                }
            )
            if len(result) >= 31:
                break
        return result
    texts_for_match = " ".join(
        [
            str(level_1_node.get("title") or ""),
            str(level_1_node.get("category") or ""),
            str(level_1_node.get("score_rule") or ""),
            str(unit_node.get("title") or ""),
            " ".join(str(child.get("title") or "") for child in unit_node.get("children") or [] if isinstance(child, dict)),
        ]
    )
    domain = str(level_1_node.get("domain") or unit_node.get("domain") or "")
    result: list[dict[str, Any]] = []
    for collection_name, item_type in [
        ("technical_bid_requirements", "technical_bid_requirement"),
        ("technical_standards", "technical_standard"),
    ]:
        for item in parse_result.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("content") or item.get("summary") or item.get("original_excerpt") or "")
            if not text or not _is_requirement_relevant(texts_for_match, text, item, domain):
                continue
            result.append(
                {
                    "requirement_id": item.get("requirement_id") or item.get("standard_id"),
                    "type": item_type,
                    "category": item.get("category"),
                    "raw_clause": text,
                    "applies_to": domain or "construction",
                    "target_section_hint": unit_node.get("title") or level_1_node.get("title"),
                    "priority": "high" if item.get("review_required") is not True else "medium",
                    "source_refs": item.get("source_refs") or [],
                    "confidence": item.get("confidence"),
                    "review_required": bool(item.get("review_required")),
                }
            )
    return result[:10]


def _excellent_bid_references(relevant_slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for slice_ in relevant_slices[:3]:
        path = _slice_path(slice_)
        references.append(
            {
                "ref_id": f"excellent_bid_001-{slice_.get('slice_id')}",
                "source_bid_id": "excellent_bid_001",
                "slice_id": slice_.get("slice_id"),
                "title": path[-1] if path else "",
                "section_path": path,
                "similarity_reason": "目录范式或章节标题与当前生成单元匹配。",
                "reuse_level": _normalize_reuse_level(slice_.get("reuse_level")),
                "structure_summary": _structure_summary(slice_, relevant_slices),
                "reference_excerpt": _reference_excerpt(slice_),
                "do_not_copy": [
                    "历史项目名称",
                    "历史建设单位",
                    "历史工程规模",
                    "特殊地址",
                    "楼栋号",
                ],
            }
        )
    return references


def _excellent_bid_references_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for material in (package.get("matched_materials") or [])[:5]:
        if not isinstance(material, dict):
            continue
        references.append(
            {
                "ref_id": material.get("material_slice_id"),
                "source_bid_id": material.get("source_id"),
                "slice_id": material.get("source_slice_id"),
                "material_slice_id": material.get("material_slice_id"),
                "title": material.get("title"),
                "section_path": material.get("section_path") or [],
                "similarity_reason": "由统一优秀标书素材库按章节路径和关键词检索命中。",
                "retrieval_score": material.get("score"),
                "match_reasons": material.get("match_reasons") or [],
                "material_quality": material.get("material_quality"),
                "primary_material_source": material.get("primary_material_source"),
                "reuse_level": _normalize_reuse_level(material.get("reuse_level")),
                "reference_excerpt": _retrieval_reference_excerpt(material),
                "do_not_copy": [
                    "历史项目名称",
                    "历史建设单位",
                    "历史工程规模",
                    "特殊地址",
                    "楼栋号",
                    "历史总平面图",
                    "历史进度计划图",
                ],
            }
        )
    return references


def _parameter_conflict_scan_from_retrieval(package: dict[str, Any]) -> dict[str, Any]:
    scan = package.get("parameter_conflict_scan")
    if isinstance(scan, dict):
        return {
            "enabled": bool(scan.get("enabled")),
            "blocking_on_output": scan.get("blocking_on_output") is not False,
            "hard_constraints": scan.get("hard_constraints") or [],
            "conflicts": scan.get("conflicts") or [],
        }
    return {"enabled": False, "blocking_on_output": True, "hard_constraints": [], "conflicts": []}


def _text_image_block_candidates_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in (package.get("text_image_block_candidates") or [])[:5]:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "block_id": item.get("block_id"),
                "block_type": item.get("block_type"),
                "source_bid_id": item.get("source_id"),
                "material_slice_id": item.get("material_slice_id"),
                "title": item.get("title"),
                "section_path": item.get("section_path") or [],
                "topics": item.get("topics") or [],
                "primary_topic": item.get("primary_topic"),
                "secondary_topics": item.get("secondary_topics") or [],
                "match_level": item.get("match_level"),
                "match_confidence": item.get("match_confidence"),
                "match_reasons": item.get("match_reasons") or [],
                "risk_flags": item.get("risk_flags") or [],
                "summary": item.get("summary"),
                "image_count": item.get("image_count"),
                "image_group_count": item.get("image_group_count"),
                "table_count": item.get("table_count"),
                "captions": (item.get("captions") or [])[:8],
                "reuse_level": _normalize_reuse_level(item.get("reuse_level")),
                "project_specific_risk": item.get("project_specific_risk"),
                "use_policy": item.get("use_policy"),
                "render_policy": item.get("render_policy") or {},
                "retrieval_score": item.get("retrieval_score"),
                "llm_instruction": "如适合当前章节，只输出采用该 block_id 的意图；完整图文块由系统在后处理阶段展开，禁止编造图片 ID。",
            }
        )
    return candidates


def _text_image_block_reuse_candidates_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in package.get("text_image_block_reuse_candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "block_id": item.get("block_id"),
                "block_type": item.get("block_type"),
                "source_id": item.get("source_id"),
                "material_slice_id": item.get("material_slice_id"),
                "title": item.get("title"),
                "section_path": item.get("section_path") or [],
                "topics": item.get("topics") or [],
                "primary_topic": item.get("primary_topic"),
                "secondary_topics": item.get("secondary_topics") or [],
                "match_level": item.get("match_level"),
                "match_confidence": item.get("match_confidence"),
                "match_reasons": item.get("match_reasons") or [],
                "risk_flags": item.get("risk_flags") or [],
                "retrieval_score": item.get("retrieval_score"),
                "reuse_level": _normalize_reuse_level(item.get("reuse_level")),
                "project_specific_risk": item.get("project_specific_risk"),
                "use_policy": item.get("use_policy"),
                "render_policy": item.get("render_policy") or {},
                "row_scope": item.get("row_scope") or {},
                "image_asset_ids": item.get("image_asset_ids") or [],
                "image_group_ids": item.get("image_group_ids") or [],
                "image_candidates": [
                    _image_candidate_from_ref(package, ref)
                    for ref in item.get("image_candidates") or []
                    if isinstance(ref, dict)
                ],
                "image_group_candidates": [
                    _image_group_candidate_from_ref(package, ref)
                    for ref in item.get("image_group_candidates") or []
                    if isinstance(ref, dict)
                ],
            }
        )
    return candidates


def _table_references_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for ref in package.get("table_references") or []:
        if not isinstance(ref, dict):
            continue
        columns = _columns_from_header(ref.get("header_preview") or [], ref.get("max_column_count"))
        tables.append(
            {
                "table_id": f"{ref.get('material_slice_id')}-T{int(ref.get('table_index') or 0):04d}",
                "source_slice_id": ref.get("material_slice_id"),
                "source_bid_id": ref.get("source_id"),
                "source_section_path": _section_path_for_material(package, ref.get("material_slice_id")),
                "table_type": "measure_with_images" if int(ref.get("image_count") or 0) else "standard_table",
                "title": _table_title(package, ref),
                "columns": columns,
                "row_count": ref.get("row_count"),
                "max_column_count": ref.get("max_column_count"),
                "image_count": ref.get("image_count"),
                "use_policy": ref.get("use_policy"),
                "material_quality": ref.get("material_quality"),
                "style_hint": {
                    "header_background": "light_orange",
                    "border_style": "grid",
                    "has_image_column": bool(ref.get("image_count")),
                    "repeat_header": True,
                },
            }
        )
        if len(tables) >= 12:
            break
    return tables


def _image_candidates_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    refs = package.get("image_candidate_pool") if "image_candidate_pool" in package else package.get("image_references")
    refs = refs or []
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in _balanced_representative_image_refs(refs):
        if not isinstance(ref, dict):
            continue
        candidate = _image_candidate_from_ref(package, ref)
        image_id = str(candidate.get("image_id") or "")
        if not image_id or image_id in seen:
            continue
        seen.add(image_id)
        images.append(candidate)
        if len(images) >= 12:
            break
    return images


def _balanced_representative_image_refs(refs: list[Any]) -> list[dict[str, Any]]:
    dict_refs = [ref for ref in refs if isinstance(ref, dict)]
    if len(dict_refs) <= 12:
        return dict_refs
    per_topic: dict[str, list[dict[str, Any]]] = {}
    topic_order: list[str] = []
    for ref in dict_refs:
        topic = _image_ref_topic(ref) or "其他"
        if topic not in per_topic:
            per_topic[topic] = []
            topic_order.append(topic)
        per_topic[topic].append(ref)
    result: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    index = 0
    while len(result) < 12:
        added = False
        for topic in topic_order:
            items = per_topic.get(topic) or []
            if index >= len(items):
                continue
            key = _image_ref_identity(items[index])
            if key and key in seen_keys:
                continue
            result.append(items[index])
            if key:
                seen_keys.add(key)
            added = True
            if len(result) >= 12:
                break
        if not added:
            break
        index += 1
    return result


def _image_ref_topic(ref: dict[str, Any]) -> str:
    source_text = " ".join(
        str(part)
        for part in [
            ref.get("material_title"),
            " ".join(str(item) for item in ref.get("source_section_path") or []),
        ]
        if part
    )
    source_topic = _topic_from_text(source_text)
    if source_topic:
        return source_topic
    text = " ".join(
        str(part)
        for part in [
            ref.get("semantic_text"),
            ref.get("group_semantic_text"),
            ref.get("caption"),
            ref.get("group_title"),
            ref.get("nearby_text"),
            ref.get("material_title"),
            " ".join(str(item) for item in ref.get("source_section_path") or []),
            " ".join(str(item) for item in ref.get("tags") or []),
        ]
        if part
    )
    return _topic_from_text(text)


def _topic_from_text(text: str) -> str:
    for topic in [
        "测量",
        "土方",
        "基坑",
        "钢筋",
        "模板",
        "混凝土",
        "防水",
        "脚手架",
        "砌体",
        "后浇带",
        "变形缝",
        "止水",
        "施工缝",
    ]:
        if topic in text:
            return topic
    return ""


def _image_ref_identity(ref: dict[str, Any]) -> str:
    return str(
        ref.get("canonical_image_id")
        or ref.get("sha256")
        or ref.get("perceptual_hash")
        or ref.get("image_asset_id")
        or ref.get("image_id")
        or ref.get("part_name")
        or "|".join(
            str(part)
            for part in [
                ref.get("material_slice_id"),
                ref.get("rel_id"),
                ref.get("table_index"),
                ref.get("row_index"),
                ref.get("cell_index"),
            ]
        )
    )


def _image_group_ref_identity(ref: dict[str, Any]) -> str:
    group_key = str(ref.get("group_canonical_image_key") or "").strip()
    if group_key:
        return group_key
    for key_name in ["canonical_image_ids", "sha256_values", "perceptual_hash_values", "image_asset_ids", "image_ids"]:
        values = [str(item).strip() for item in ref.get(key_name) or [] if str(item).strip()]
        if values:
            return f"{key_name}:{'|'.join(sorted(values))}"
    return str(ref.get("image_group_id") or "")


def _image_candidate_pool_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    refs = package.get("image_candidate_pool") if "image_candidate_pool" in package else package.get("image_references")
    refs = refs or []
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        candidate = _image_candidate_from_ref(package, ref)
        key = _image_ref_identity(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        images.append(candidate)
    return images


def _image_group_candidates_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for ref in package.get("image_group_references") or []:
        if not isinstance(ref, dict):
            continue
        groups.append(_image_group_candidate_from_ref(package, ref))
        if len(groups) >= 12:
            break
    return groups


def _image_group_candidate_pool_from_retrieval(package: dict[str, Any]) -> list[dict[str, Any]]:
    refs = package.get("image_group_candidate_pool") or package.get("image_group_references") or []
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        group = _image_group_candidate_from_ref(package, ref)
        group_key = _image_group_ref_identity(group)
        if not group_key or group_key in seen:
            continue
        seen.add(group_key)
        groups.append(group)
    return groups


def _image_candidate_from_ref(package: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    material = _material_by_id(package, ref.get("material_slice_id"))
    reuse_level, risk_level = _reuse_policy_from_image_ref(ref)
    section_path = ref.get("source_section_path") or (material or {}).get("section_path") or []
    title = ref.get("caption") or ref.get("material_title") or (material or {}).get("title") or "优秀标书图片"
    candidates = [str(item) for item in ref.get("caption_candidates") or [] if str(item).strip()]
    nearby_text = str(ref.get("nearby_text") or "")
    semantic_sources = [item for item in ref.get("semantic_sources") or [] if isinstance(item, dict)]
    return {
        "image_id": _image_id(ref),
        "image_asset_id": ref.get("image_asset_id"),
        "canonical_image_id": ref.get("canonical_image_id"),
        "sha256": ref.get("sha256"),
        "perceptual_hash": ref.get("perceptual_hash"),
        "rel_id": ref.get("rel_id"),
        "target": ref.get("target"),
        "part_name": ref.get("part_name"),
        "caption": title,
        "caption_candidates": candidates,
        "semantic_sources": semantic_sources,
        "semantic_text": ref.get("semantic_text"),
        "semantic_confidence": ref.get("semantic_confidence"),
        "nearby_text": nearby_text,
        "tags": ref.get("tags") or [],
        "bound_section": (section_path or [""])[-1],
        "source_section_path": list(section_path),
        "source_id": ref.get("source_id"),
        "source_bid_id": ref.get("source_bid_id") or ref.get("source_id"),
        "source_type": ref.get("source_type") or (material or {}).get("source_type"),
        "source_slice_id": ref.get("source_slice_id") or (material or {}).get("source_slice_id"),
        "bound_table_id": _table_id(ref.get("table_index")),
        "bound_row_id": ref.get("row_index"),
        "bound_cell_key": f"col_{int(ref.get('cell_index') or 0) + 1}",
        "image_group_id": ref.get("image_group_id"),
        "group_title": ref.get("group_title"),
        "group_semantic_text": ref.get("group_semantic_text"),
        "group_member_index": ref.get("group_member_index"),
        "group_member_count": ref.get("group_member_count"),
        "must_keep_with_group": bool(ref.get("must_keep_with_group")),
        "reuse_level": reuse_level,
        "risk_level": risk_level,
        "notes": _image_notes_from_policy(ref.get("use_policy")),
        "material_slice_id": ref.get("material_slice_id"),
        "material_quality": ref.get("material_quality"),
        "use_policy": ref.get("use_policy"),
        "render_policy": ref.get("render_policy") or {},
        "row_scope": ref.get("row_scope") or {},
        "review_required": bool(ref.get("review_required")),
        "review_reason": ref.get("review_reason"),
        "primary_category": ref.get("primary_category"),
        "discipline_tags": ref.get("discipline_tags") or [],
        "scene_tags": ref.get("scene_tags") or [],
        "image_form": ref.get("image_form"),
        "fit_level": ref.get("fit_level"),
        "fit_score": ref.get("fit_score"),
        "fit_reasons": ref.get("fit_reasons") or [],
        "source_reuse_mode": ref.get("source_reuse_mode"),
        "text_image_block_id": ref.get("text_image_block_id"),
        "text_image_block_title": ref.get("text_image_block_title"),
        "text_image_block_primary_topic": ref.get("text_image_block_primary_topic"),
        "text_image_block_match_level": ref.get("text_image_block_match_level"),
        "text_image_block_match_confidence": ref.get("text_image_block_match_confidence"),
        "text_image_block_match_reasons": ref.get("text_image_block_match_reasons") or [],
        "text_image_block_risk_flags": ref.get("text_image_block_risk_flags") or [],
        "reuse_priority": ref.get("reuse_priority"),
    }


def _image_group_candidate_from_ref(package: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    material = _material_by_id(package, ref.get("material_slice_id"))
    reuse_level, risk_level = _reuse_policy_from_image_ref(ref)
    section_path = ref.get("source_section_path") or (material or {}).get("section_path") or []
    members = [
        _image_candidate_from_ref(package, member)
        for member in ref.get("members") or []
        if isinstance(member, dict)
    ]
    member_count = int(ref.get("member_count") or len(members) or 0)
    group_id = str(ref.get("image_group_id") or "")
    for index, member in enumerate(members, start=1):
        member["image_group_id"] = group_id
        member["group_title"] = ref.get("group_title")
        member["group_semantic_text"] = ref.get("semantic_text")
        member["group_member_index"] = index
        member["group_member_count"] = member_count
        member["must_keep_with_group"] = True
        _inherit_text_image_block_reuse_metadata(member, ref)
    return {
        "image_group_id": group_id,
        "group_title": ref.get("group_title") or ref.get("material_title") or (material or {}).get("title") or "优秀标书套图",
        "caption": ref.get("group_title") or ref.get("semantic_text") or "施工做法套图",
        "captions": [str(item) for item in ref.get("captions") or [] if str(item).strip()],
        "semantic_sources": [item for item in ref.get("semantic_sources") or [] if isinstance(item, dict)],
        "semantic_text": ref.get("semantic_text"),
        "semantic_confidence": ref.get("semantic_confidence"),
        "nearby_text": ref.get("nearby_text"),
        "tags": ref.get("tags") or [],
        "source_section_path": list(section_path),
        "source_bid_id": ref.get("source_id"),
        "source_type": ref.get("source_type") or (material or {}).get("source_type"),
        "source_slice_id": ref.get("source_slice_id") or (material or {}).get("source_slice_id"),
        "bound_section": (section_path or [""])[-1],
        "bound_table_id": _table_id(ref.get("table_index")),
        "bound_row_id": ref.get("start_row_index"),
        "member_count": member_count,
        "members": members,
        "canonical_image_ids": ref.get("canonical_image_ids") or [],
        "sha256_values": ref.get("sha256_values") or [],
        "perceptual_hash_values": ref.get("perceptual_hash_values") or [],
        "group_canonical_image_key": ref.get("group_canonical_image_key"),
        "image_ids": ref.get("image_ids") or [member.get("image_id") for member in members],
        "image_asset_ids": ref.get("image_asset_ids") or [member.get("image_asset_id") for member in members],
        "reuse_level": reuse_level,
        "risk_level": risk_level,
        "notes": _image_notes_from_policy(ref.get("use_policy")),
        "material_slice_id": ref.get("material_slice_id"),
        "material_quality": ref.get("material_quality"),
        "use_policy": ref.get("use_policy"),
        "render_policy": ref.get("render_policy") or {},
        "row_scope": ref.get("row_scope") or {},
        "review_required": bool(ref.get("review_required")),
        "review_reason": ref.get("review_reason"),
        "must_keep_together": bool(ref.get("must_keep_together", True)),
        "primary_category": ref.get("primary_category"),
        "discipline_tags": ref.get("discipline_tags") or [],
        "scene_tags": ref.get("scene_tags") or [],
        "image_form": ref.get("image_form"),
        "fit_level": ref.get("fit_level"),
        "fit_score": ref.get("fit_score"),
        "fit_reasons": ref.get("fit_reasons") or [],
        "source_reuse_mode": ref.get("source_reuse_mode"),
        "text_image_block_id": ref.get("text_image_block_id"),
        "text_image_block_title": ref.get("text_image_block_title"),
        "text_image_block_primary_topic": ref.get("text_image_block_primary_topic"),
        "text_image_block_match_level": ref.get("text_image_block_match_level"),
        "text_image_block_match_confidence": ref.get("text_image_block_match_confidence"),
        "text_image_block_match_reasons": ref.get("text_image_block_match_reasons") or [],
        "text_image_block_risk_flags": ref.get("text_image_block_risk_flags") or [],
        "reuse_priority": ref.get("reuse_priority"),
    }


def _inherit_text_image_block_reuse_metadata(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in [
        "source_reuse_mode",
        "text_image_block_id",
        "text_image_block_title",
        "text_image_block_primary_topic",
        "text_image_block_match_level",
        "text_image_block_match_confidence",
        "reuse_priority",
        "use_policy",
    ]:
        if target.get(key) is None and source.get(key) is not None:
            target[key] = source.get(key)
    for key in ["render_policy", "row_scope"]:
        if not target.get(key) and source.get(key):
            target[key] = source.get(key) or {}
    for key in ["text_image_block_match_reasons", "text_image_block_risk_flags"]:
        if not target.get(key) and source.get(key):
            target[key] = source.get(key) or []


def _table_references(relevant_slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for slice_ in relevant_slices:
        path = _slice_path(slice_)
        for table in slice_.get("tables") or []:
            if not isinstance(table, dict):
                continue
            columns = _columns_from_table(table)
            table_ref = {
                "table_id": f"EB-001-T{int(table.get('table_index') or 0):04d}",
                "source_slice_id": slice_.get("slice_id"),
                "source_section_path": path,
                "table_type": "measure_with_images" if int(table.get("image_count") or 0) else "standard_table",
                "title": table.get("nearest_heading_text") or (path[-1] if path else "优秀标书表格"),
                "columns": columns,
                "row_count": table.get("row_count"),
                "max_column_count": table.get("max_column_count"),
                "image_count": table.get("image_count"),
                "style_hint": {
                    "header_background": "light_orange",
                    "border_style": "grid",
                    "has_image_column": bool(table.get("image_count")),
                    "repeat_header": True,
                },
                "_row_previews": table.get("row_previews") or [],
            }
            tables.append(table_ref)
            if len(tables) >= 8:
                return tables
    return tables


def _row_examples(table_references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for table_ref in table_references:
        columns = table_ref.get("columns") or []
        column_keys = [column.get("key") for column in columns if isinstance(column, dict)]
        for row in (table_ref.get("_row_previews") or [])[1:4]:
            cells = row.get("cells") if isinstance(row, dict) else []
            cell_blocks: dict[str, list[dict[str, Any]]] = {}
            for index, key in enumerate(column_keys):
                cell = cells[index] if index < len(cells) else {}
                text = str(cell.get("text_preview") or "") if isinstance(cell, dict) else ""
                image_count = int(cell.get("image_count") or 0) if isinstance(cell, dict) else 0
                blocks: list[dict[str, Any]] = []
                if text:
                    blocks.append({"type": "paragraph", "text": text})
                if image_count:
                    blocks.append({"type": "image", "image_id": None})
                cell_blocks[str(key)] = blocks
            examples.append(
                {
                    "row_id": f"{table_ref.get('table_id')}-R{int(row.get('row_index') or 0):03d}",
                    "table_id": table_ref.get("table_id"),
                    "cell_blocks": cell_blocks,
                    "reuse_level": "rewrite_reuse",
                }
            )
            if len(examples) >= 12:
                break
        table_ref.pop("_row_previews", None)
        if len(examples) >= 12:
            break
    return examples


def _image_candidates(relevant_slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for slice_ in relevant_slices:
        path = _slice_path(slice_)
        context_text = " ".join(path)
        for binding in slice_.get("image_bindings") or []:
            if not isinstance(binding, dict):
                continue
            key = (
                binding.get("rel_id"),
                binding.get("table_index"),
                binding.get("row_index"),
                binding.get("cell_index"),
            )
            if key in seen:
                continue
            seen.add(key)
            reuse_level, risk_level, note = _image_reuse_policy(context_text)
            images.append(
                {
                    "image_id": _image_id(binding),
                    "rel_id": binding.get("rel_id"),
                    "part_name": binding.get("part_name"),
                    "caption": path[-1] if path else "优秀标书图片",
                    "bound_section": path[-1] if path else "",
                    "bound_table_id": _table_id(binding.get("table_index")),
                    "bound_row_id": binding.get("row_index"),
                    "bound_cell_key": f"col_{int(binding.get('cell_index') or 0) + 1}",
                    "reuse_level": reuse_level,
                    "risk_level": risk_level,
                    "notes": note,
                }
            )
            if len(images) >= 12:
                return images
    return images


def _material_retrieval_maps(
    material_retrieval_inputs: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, ...], dict[str, Any]]]:
    by_node: dict[str, dict[str, Any]] = {}
    by_path: dict[tuple[str, ...], dict[str, Any]] = {}
    for package in material_retrieval_inputs.get("packages") or []:
        if not isinstance(package, dict):
            continue
        target = package.get("target_section") or {}
        node_id = str(target.get("target_node_id") or "")
        if node_id:
            by_node[node_id] = package
        path_key = tuple(_normalize(part) for part in target.get("chapter_path") or [] if str(part).strip())
        if path_key:
            by_path[path_key] = package
    return by_node, by_path


def _find_material_retrieval_package(
    by_node: dict[str, dict[str, Any]],
    by_path: dict[tuple[str, ...], dict[str, Any]],
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
) -> dict[str, Any] | None:
    node_id = str(unit_node.get("node_id") or "")
    if node_id and node_id in by_node:
        return by_node[node_id]
    path_key = tuple(_normalize(part) for part in _chapter_path(level_1_node, unit_node))
    return by_path.get(path_key)


def _material_retrieval_summary(package: dict[str, Any]) -> dict[str, Any]:
    policy = package.get("retrieval_policy") or {}
    return {
        "schema_version": package.get("schema_version"),
        "matched_material_count": len(package.get("matched_materials") or []),
        "paragraph_reference_count": len(package.get("paragraph_references") or []),
        "table_reference_count": len(package.get("table_references") or []),
        "image_reference_count": len(package.get("image_references") or []),
        "image_group_reference_count": len(package.get("image_group_references") or []),
        "image_candidate_pool_count": len(package.get("image_candidate_pool") or []),
        "image_group_candidate_pool_count": len(package.get("image_group_candidate_pool") or []),
        "image_group_summary": package.get("image_group_summary") or [],
        "reuse_warning_count": len(package.get("reuse_warnings") or []),
        "retrieval_policy": policy,
    }


def _auto_image_reuse_policy(
    expanded_policy: dict[str, Any],
    image_candidate_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    reusable_count = sum(
        1
        for item in image_candidate_pool
        if isinstance(item, dict) and _image_auto_reuse_allowed(item)
    )
    targets = expanded_policy.get("targets") or {}
    min_refs = int(targets.get("min_image_refs") or 0)
    section_type = str(expanded_policy.get("section_type") or "")
    min_sections = max(int(targets.get("min_sections") or 1), 1)
    if section_type in {"project_specific", CONTENT_COMPLETENESS_SECTION_TYPE}:
        target_refs = 0
        max_total_refs = 0
        max_per_section = 0
    else:
        ratio, per_section_floor = _image_density_profile(section_type)
        target_refs = min(
            reusable_count,
            max(
                min_refs,
                min_sections * per_section_floor,
                round(reusable_count * ratio),
            ),
        )
        max_total_refs = min(reusable_count, max(target_refs, min_sections * (per_section_floor + 2)))
        max_per_section = max(per_section_floor + 1, (target_refs + min_sections - 1) // min_sections + 1)
    return {
        "enabled": bool(reusable_count),
        "strategy": "graph_block_density_insert_after_llm_generation",
        "llm_selects_images": False,
        "allow_placeholders": False,
        "missing_image_behavior": "silent_skip",
        "min_image_refs": min(min_refs, reusable_count),
        "target_image_refs": target_refs,
        "max_image_refs_total": max_total_refs,
        "max_auto_image_refs": max_total_refs,
        "max_images_per_section": max_per_section,
        "candidate_pool_count": reusable_count,
        "llm_input_limit_note": "LLM 不直接选择图片文件；完整通用图片池由系统按图文块密度和高置信语义后处理自动插入，避免人工逐张挑图。",
        "red_line": [
            "施工总平面图、施工进度计划图、踏勘、现状、周边环境、交通组织等项目事实图片不得自动复用。",
            "manual_review 或 placeholder_or_manual_review 图片不得自动插入 image_ref。",
            "无合适素材时静默跳过，不输出“图片待补充”等占位内容。",
        ],
    }


def _image_auto_reuse_allowed(item: dict[str, Any]) -> bool:
    reuse_level = str(item.get("reuse_level") or item.get("use_policy") or "")
    if reuse_level not in {"candidate_reuse", "direct_reuse"}:
        return False
    if str(item.get("risk_level") or "").lower() == "high":
        return False
    if bool(item.get("review_required")):
        return False
    return True


def _image_density_profile(section_type: str) -> tuple[float, int]:
    if section_type == "construction_process":
        return 0.6, 3
    if section_type == "management_measure":
        return 0.6, 3
    return 0.45, 2


def _retrieval_reference_excerpt(material: dict[str, Any]) -> str:
    snippets = []
    for paragraph in material.get("paragraphs") or []:
        text = str(paragraph.get("text_preview") or "").strip()
        if text:
            snippets.append(text)
    return "；".join(snippets)[:600]


def _section_path_for_material(package: dict[str, Any], material_slice_id: Any) -> list[str]:
    material = _material_by_id(package, material_slice_id)
    return list((material or {}).get("section_path") or [])


def _table_title(package: dict[str, Any], ref: dict[str, Any]) -> str:
    material = _material_by_id(package, ref.get("material_slice_id"))
    path = (material or {}).get("section_path") or []
    if path:
        return str(path[-1])
    return "优秀标书表格"


def _material_by_id(package: dict[str, Any], material_slice_id: Any) -> dict[str, Any] | None:
    for material in package.get("matched_materials") or []:
        if isinstance(material, dict) and material.get("material_slice_id") == material_slice_id:
            return material
    return None


def _reuse_policy_from_image_ref(ref: dict[str, Any]) -> tuple[str, str]:
    policy = str(ref.get("use_policy") or ref.get("reuse_level") or "")
    if policy in {"candidate_reuse", "direct_reuse"}:
        return policy, str(ref.get("risk_level") or "low")
    if policy == "manual_review":
        return "manual_review", "high"
    if policy == "placeholder_or_manual_review":
        return "placeholder_or_manual_review", "high"
    return "manual_review", "medium"


def _image_notes_from_policy(policy: Any) -> str:
    policy = str(policy or "")
    if policy == "placeholder_or_manual_review":
        return "疑似历史项目专属图纸或计划图，默认不直接复用，可在 Word 导出阶段放置占位或人工替换。"
    if policy == "manual_review":
        return "素材来自兜底或需复核匹配，图片需人工确认后使用。"
    return "优秀标书通用图片候选，正文生成后由系统按语义匹配自动插入；无合适匹配时静默跳过。"


def _normalize_reuse_level(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = LEGACY_REUSE_LEVEL_MAP.get(raw, raw)
    if normalized in REUSE_LEVELS:
        return normalized
    return "rewrite_reuse"


def _relevant_slices(
    excellent_bid_index: dict[str, Any],
    level_1_node: dict[str, Any],
    unit_node: dict[str, Any],
) -> list[dict[str, Any]]:
    slices = [item for item in excellent_bid_index.get("slices") or [] if isinstance(item, dict)]
    if not slices:
        return []

    refs = _template_refs(level_1_node, unit_node)
    matched: list[dict[str, Any]] = []
    for ref in refs:
        ref_slice_id = ref.get("slice_id")
        ref_path = [str(part).strip() for part in ref.get("section_path") or [] if str(part).strip()]
        if unit_node is not level_1_node and ref_path:
            matched.extend(_find_by_path(slices, [*ref_path, str(unit_node.get("title") or "")]))
            matched.extend(_find_descendant_by_title(slices, ref_path, str(unit_node.get("title") or "")))
        if ref_slice_id:
            matched.extend([slice_ for slice_ in slices if slice_.get("slice_id") == ref_slice_id])
        if ref_path:
            matched.extend(_find_by_path(slices, ref_path))

    if not matched:
        matched.extend(_find_by_title(slices, str(unit_node.get("title") or "")))
    unique = _unique_slices(matched)
    expanded = _expand_with_descendants(slices, unique, max_depth=2)
    return _unique_slices([*unique, *expanded])[:24]


def _expand_with_descendants(
    slices: list[dict[str, Any]],
    parents: list[dict[str, Any]],
    *,
    max_depth: int,
) -> list[dict[str, Any]]:
    descendants: list[dict[str, Any]] = []
    for parent in parents:
        parent_path = _slice_path(parent)
        if not parent_path:
            continue
        for slice_ in slices:
            path = _slice_path(slice_)
            if len(path) <= len(parent_path) or len(path) > len(parent_path) + max_depth:
                continue
            if path[: len(parent_path)] == parent_path:
                descendants.append(slice_)
    return descendants


def _find_by_path(slices: list[dict[str, Any]], target_path: list[str]) -> list[dict[str, Any]]:
    normalized = tuple(_normalize(part) for part in target_path if str(part).strip())
    if not normalized:
        return []
    return [slice_ for slice_ in slices if tuple(_normalize(part) for part in _slice_path(slice_)) == normalized]


def _find_descendant_by_title(
    slices: list[dict[str, Any]],
    parent_path: list[str],
    title: str,
) -> list[dict[str, Any]]:
    normalized_parent = tuple(_normalize(part) for part in parent_path)
    normalized_title = _normalize(title)
    result = []
    for slice_ in slices:
        path = _slice_path(slice_)
        if len(path) <= len(parent_path):
            continue
        if tuple(_normalize(part) for part in path[: len(parent_path)]) != normalized_parent:
            continue
        if _normalize(path[-1]) == normalized_title:
            result.append(slice_)
    return result


def _find_by_title(slices: list[dict[str, Any]], title: str) -> list[dict[str, Any]]:
    normalized_title = _normalize(title)
    if not normalized_title:
        return []
    return [slice_ for slice_ in slices if _normalize((_slice_path(slice_) or [""])[-1]) == normalized_title]


def _template_refs(level_1_node: dict[str, Any], unit_node: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source in [unit_node, level_1_node]:
        for ref in source.get("template_refs") or []:
            if isinstance(ref, dict):
                refs.append(ref)
    return refs


def _score_points_by_id(parse_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(point.get("score_point_id")): point
        for point in parse_result.get("technical_score_points") or []
        if isinstance(point, dict) and point.get("score_point_id")
    }


def _chapter_path(level_1_node: dict[str, Any], unit_node: dict[str, Any]) -> list[str]:
    path = [str(level_1_node.get("title") or "")]
    parent_level_2 = unit_node.get("_split_parent_level_2")
    if isinstance(parent_level_2, dict) and parent_level_2.get("title"):
        path.append(str(parent_level_2.get("title") or ""))
    if unit_node is not level_1_node:
        path.append(str(unit_node.get("title") or ""))
    return [part for part in path if part]


def _columns_from_table(table: dict[str, Any]) -> list[dict[str, Any]]:
    header = [str(item).strip() for item in table.get("header_preview") or []]
    return _columns_from_header(header, table.get("max_column_count"))


def _columns_from_header(header: list[Any], max_column_count: Any) -> list[dict[str, Any]]:
    header = [str(item).strip() for item in header or []]
    column_count = max(int(max_column_count or 0), len(header))
    if column_count <= 0:
        column_count = 1
    columns = []
    for index in range(column_count):
        title = header[index] if index < len(header) and header[index] else f"列{index + 1}"
        columns.append(
            {
                "key": f"col_{index + 1}",
                "title": title,
                "width_ratio": round(1 / column_count, 4),
            }
        )
    return columns


def _structure_summary(slice_: dict[str, Any], relevant_slices: list[dict[str, Any]]) -> list[str]:
    path = _slice_path(slice_)
    if not path:
        return []
    titles = []
    for candidate in relevant_slices:
        candidate_path = _slice_path(candidate)
        if len(candidate_path) != len(path) + 1:
            continue
        if candidate_path[: len(path)] == path:
            titles.append(candidate_path[-1])
    return titles[:10]


def _reference_excerpt(slice_: dict[str, Any]) -> str:
    snippets = []
    for paragraph in slice_.get("paragraphs") or []:
        text = str(paragraph.get("text_preview") or "").strip()
        if text:
            snippets.append(text)
    return "；".join(snippets)[:600]


def _is_requirement_relevant(
    target_text: str,
    requirement_text: str,
    item: dict[str, Any],
    domain: str,
) -> bool:
    haystack = requirement_text + " " + str(item.get("category") or "") + " " + str(item.get("generation_impact") or "")
    if domain == "design" and "设计" in haystack:
        return True
    if domain == "construction" and any(keyword in haystack for keyword in ["施工", "质量", "安全", "进度", "文明", "环保"]):
        return True
    keywords = _keywords(target_text)
    return any(keyword in haystack for keyword in keywords)


def _image_reuse_policy(context_text: str) -> tuple[str, str, str]:
    if _is_project_fact_image_context(context_text):
        return "placeholder_or_manual_review", "high", "疑似历史项目专属图，正文生成时不直接复用；如本项目缺图，后续 Word 导出可使用占位。"
    if any(keyword in context_text for keyword in GENERIC_PRACTICE_IMAGE_TERMS):
        return "candidate_reuse", "low", "通用做法或成品保护类图片，默认可作为候选复用。"
    return "manual_review", "medium", "图片用途需结合章节人工复核后使用。"


def _is_project_fact_image_context(context_text: str) -> bool:
    if any(keyword in context_text for keyword in PROJECT_FACT_IMAGE_TERMS):
        return not any(keyword in context_text for keyword in GENERIC_PRACTICE_IMAGE_TERMS)
    return False


def _image_id(binding: dict[str, Any]) -> str:
    stable_id = binding.get("image_id") or binding.get("image_asset_id")
    if stable_id:
        return str(stable_id)
    parts = [
        "EBIMG",
        str(binding.get("material_slice_id") or binding.get("source_slice_id") or "SLICE"),
        str(binding.get("rel_id") or "RID"),
        str(binding.get("table_index") if binding.get("table_index") is not None else "P"),
        str(binding.get("row_index") if binding.get("row_index") is not None else "R"),
        str(binding.get("cell_index") if binding.get("cell_index") is not None else "C"),
    ]
    return re.sub(r"[^A-Za-z0-9_]+", "_", "_".join(parts))


def _table_id(table_index: Any) -> str | None:
    if table_index is None:
        return None
    try:
        return f"EB-001-T{int(table_index):04d}"
    except (TypeError, ValueError):
        return None


def _slice_path(slice_: dict[str, Any]) -> list[str]:
    return [str(part).strip() for part in slice_.get("section_path") or [] if str(part).strip()]


def _unique_slices(slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for slice_ in slices:
        key = str(slice_.get("slice_id") or "|".join(_slice_path(slice_)))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(slice_)
    return result


def _field_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return "" if field is None else str(field)


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or "")
    return [word for word in words if len(word) >= 2][:30]


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
