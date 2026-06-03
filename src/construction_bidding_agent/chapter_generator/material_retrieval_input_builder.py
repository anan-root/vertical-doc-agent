"""为章节正文生成构建优秀标书素材检索输入包。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from construction_bidding_agent.document_parser.excellent_bid_material_library import (
    _library_slices,
    search_excellent_bid_materials,
)
from construction_bidding_agent.document_parser.excellent_bid_text_image_block_index import (
    build_text_image_block_index,
    search_text_image_blocks,
)
from construction_bidding_agent.document_parser.models import (
    ExcellentBidMaterialLibraryResult,
    ExcellentBidMaterialSlice,
)
from construction_bidding_agent.chapter_generator.parameter_conflict_guard import (
    apply_parameter_conflict_guard,
    material_has_parameter_conflict,
    parameter_conflict_warnings,
)


SCHEMA_VERSION = "chapter_material_retrieval_input_v1"
INDEX_SCHEMA_VERSION = "chapter_material_retrieval_input_index_v1"
MAX_IMAGE_BINDINGS_PER_MATERIAL_REFERENCE = 24
MAX_IMAGE_REFERENCES = 12
MAX_IMAGE_REFERENCES_PER_MATERIAL = 4
MAX_IMAGE_CANDIDATE_POOL = 60
MAX_IMAGE_CANDIDATE_POOL_PER_MATERIAL = 20
MAX_TEXT_IMAGE_BLOCK_REUSE_CANDIDATES = 10
MAX_CHILD_HEADING_SUPPLEMENTAL_HITS = 2
MIN_CHILD_HEADING_IMAGE_HITS = 2
MATERIAL_LEVEL2_SPLIT_MIN_CHILDREN = 4
DEFAULT_ALLOWED_QUALITIES = {"high", "usable", "pdf_fallback", "review_required"}
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
PROJECT_FACT_IMAGE_TERMS = ["总平面图", "平面布置图", "进度计划", "网络图", "横道图", "踏勘", "现状", "周边环境", "周边道路", "交通组织"]
AUTO_IMAGE_FIT_LEVELS = {"preferred", "candidate"}
REVIEW_IMAGE_FIT_LEVELS = {"review_only", *AUTO_IMAGE_FIT_LEVELS}
PROCESS_DISCIPLINE_TOPICS = {
    "measure",
    "earthwork_foundation",
    "rebar",
    "formwork",
    "concrete",
    "waterproof",
    "scaffold",
    "masonry",
    "post_pour_joint",
    "mechanical_electrical",
}
CLEAR_MANAGEMENT_IMAGE_TERMS = [
    "管理体系",
    "保证体系",
    "保障体系",
    "组织机构",
    "组织架构",
    "管理流程",
    "责任制",
    "岗位职责",
    "职责分工",
    "应急机制",
    "应急响应",
]
GENERIC_REUSABLE_IMAGE_TERMS = [
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
    "公示牌",
    "宣传长廊",
    "安全宣传",
]


def build_chapter_material_retrieval_inputs_from_files(
    outline_json: str | Path,
    material_library_json: str | Path,
    *,
    include_domains: set[str] | list[str] | tuple[str, ...] | None = None,
    max_packages: int | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    outline = _read_json(outline_json)
    material_library = _read_json(material_library_json)
    return build_chapter_material_retrieval_inputs(
        outline,
        material_library,
        parse_result=None,
        include_domains=include_domains,
        max_packages=max_packages,
        top_k=top_k,
    )


def build_chapter_material_retrieval_inputs(
    outline: dict[str, Any],
    material_library: ExcellentBidMaterialLibraryResult | dict[str, Any],
    *,
    parse_result: dict[str, Any] | None = None,
    include_domains: set[str] | list[str] | tuple[str, ...] | None = None,
    max_packages: int | None = None,
    top_k: int = 5,
    allowed_qualities: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_domains = set(include_domains) if include_domains else None
    qualities = allowed_qualities or DEFAULT_ALLOWED_QUALITIES
    image_assets_by_material = _image_assets_by_material(material_library)
    image_groups_by_material = _image_groups_by_material(material_library)
    text_image_block_index = build_text_image_block_index(_material_library_dict(material_library))
    packages: list[dict[str, Any]] = []

    for node_path in _iter_generation_nodes(outline):
        node = node_path[-1]
        domain = str(node.get("domain") or node_path[0].get("domain") or "construction")
        if allowed_domains is not None and domain not in allowed_domains:
            continue
        target = _target_section(node_path)
        supplemental_hits: list[Any] = []
        filtered_promotion_hit_count = 0
        if _should_skip_material_retrieval(target):
            hits = []
        else:
            hits = search_excellent_bid_materials(
                material_library,
                query=target["query"],
                section_path=target["chapter_path"],
                top_k=top_k,
                min_quality=qualities,
            )
            supplemental_hits = _child_heading_search_hits(
                material_library,
                target,
                top_k=MAX_CHILD_HEADING_SUPPLEMENTAL_HITS,
                qualities=qualities,
            )
            hits = _merge_search_hits(hits, supplemental_hits)
            filtered_hits = _filter_material_hits_for_target(hits, target)
            filtered_promotion_hit_count = len(hits) - len(filtered_hits)
            hits = filtered_hits
        chapter_image_profile = _chapter_image_profile(target)
        materials = [
            _material_reference(
                hit.slice,
                rank=index + 1,
                score=hit.score,
                reasons=hit.reasons,
                image_assets_by_material=image_assets_by_material,
                image_groups_by_material=image_groups_by_material,
            )
            for index, hit in enumerate(hits)
            if hit.slice
        ]
        parameter_scan = apply_parameter_conflict_guard(
            materials,
            parse_result=parse_result,
            target_section=target,
        )
        reusable_materials = [material for material in materials if not material_has_parameter_conflict(material)]
        block_candidates = _text_image_block_candidates(
            text_image_block_index,
            target,
            top_k=top_k,
        )
        block_candidates = _filter_parameter_conflict_blocks(block_candidates, materials)
        block_reuse_candidates = _text_image_block_reuse_candidates(
            block_candidates,
            reusable_materials,
            chapter_image_profile,
        )
        image_candidate_pool = _merge_reference_pools(
            _block_image_candidate_pool(block_reuse_candidates),
            _image_candidate_pool(reusable_materials, chapter_image_profile),
        )
        image_group_candidate_pool = _merge_reference_pools(
            _block_image_group_candidate_pool(block_reuse_candidates),
            _image_group_candidate_pool(reusable_materials, chapter_image_profile),
        )
        packages.append(
            {
                "schema_version": SCHEMA_VERSION,
                "target_section": target,
                "chapter_image_profile": chapter_image_profile,
                "parameter_conflict_scan": parameter_scan,
                "matched_materials": materials,
                "paragraph_references": _paragraph_references(reusable_materials),
                "table_references": _table_references(reusable_materials),
                "image_references": _image_references(reusable_materials, chapter_image_profile),
                "image_group_references": _image_group_references(reusable_materials, chapter_image_profile),
                "image_candidate_pool": image_candidate_pool,
                "image_group_candidate_pool": image_group_candidate_pool,
                "text_image_block_candidates": block_candidates,
                "text_image_block_reuse_candidates": block_reuse_candidates,
                "image_group_summary": _image_group_summary(materials),
                "reuse_warnings": [*parameter_conflict_warnings(materials), *_reuse_warnings(materials)],
                "retrieval_policy": {
                    "top_k": top_k,
                    "child_heading_supplement_enabled": bool(target.get("child_headings")),
                    "child_heading_supplement_top_k": MAX_CHILD_HEADING_SUPPLEMENTAL_HITS,
                    "child_heading_supplement_hit_count": len(supplemental_hits),
                    "filtered_promotion_hit_count": filtered_promotion_hit_count,
                    "allowed_qualities": sorted(qualities),
                    "exclude_pdf_reference_material": True,
                    "default_material_priority": ["docx", "pdf_fallback"],
                    "image_adaptation_enabled": True,
                    "skip_reason": _skip_reason(target),
                },
            }
        )
        if max_packages is not None and len(packages) >= max_packages:
            return packages
    return packages


def _filter_parameter_conflict_blocks(
    block_candidates: list[dict[str, Any]],
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conflicted_material_ids = {
        str(material.get("material_slice_id") or "")
        for material in materials
        if isinstance(material, dict) and material_has_parameter_conflict(material)
    }
    if not conflicted_material_ids:
        return block_candidates
    return [
        block
        for block in block_candidates
        if str(block.get("material_slice_id") or "") not in conflicted_material_ids
    ]


def write_chapter_material_retrieval_inputs(
    packages: list[dict[str, Any]],
    json_path: str | Path,
    report_path: str | Path | None = None,
) -> None:
    target = Path(json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "package_count": len(packages),
        "packages": packages,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path:
        report_target = Path(report_path)
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(render_chapter_material_retrieval_report(packages), encoding="utf-8")


def render_chapter_material_retrieval_report(packages: list[dict[str, Any]]) -> str:
    quality_counts: Counter[str] = Counter()
    material_source_counts: Counter[str] = Counter()
    for package in packages:
        for material in package.get("matched_materials") or []:
            quality_counts[str(material.get("material_quality") or "unknown")] += 1
            material_source_counts[str(material.get("primary_material_source") or "unknown")] += 1

    lines = [
        "# 章节生成素材检索输入包报告",
        "",
        f"- 输入包数量：{len(packages)}",
        f"- 素材质量分布：{_format_counts(dict(quality_counts))}",
        f"- 素材来源分布：{_format_counts(dict(material_source_counts))}",
        "",
        "## 输入包清单",
        "",
        "| 序号 | 目标章节 | 命中素材 | 段落 | 表格 | 图片 | 风险提示 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for index, package in enumerate(packages, start=1):
        target = package.get("target_section") or {}
        path = " > ".join(target.get("chapter_path") or [])
        lines.append(
            f"| {index} | {_cell(path)} | "
            f"{len(package.get('matched_materials') or [])} | "
            f"{len(package.get('paragraph_references') or [])} | "
            f"{len(package.get('table_references') or [])} | "
            f"{len(package.get('image_references') or [])} | "
            f"{len(package.get('reuse_warnings') or [])} |"
        )

    lines.extend(["", "## 命中预览", ""])
    for package in packages[:80]:
        target = package.get("target_section") or {}
        lines.append(f"### {' > '.join(target.get('chapter_path') or [])}")
        for material in (package.get("matched_materials") or [])[:5]:
            lines.append(
                f"- {material.get('material_slice_id')} score={material.get('score')} "
                f"quality={material.get('material_quality')} source={material.get('primary_material_source')} "
                f"T{material.get('table_count')}/I{material.get('image_count')} "
                f"{' > '.join(material.get('section_path') or [])}"
            )
        if not package.get("matched_materials"):
            lines.append("- 未命中优秀标书素材。")
    lines.append("")
    return "\n".join(lines)


def _iter_generation_nodes(outline: dict[str, Any]) -> list[list[dict[str, Any]]]:
    node_paths: list[list[dict[str, Any]]] = []
    for level_1 in outline.get("nodes") or []:
        if not isinstance(level_1, dict):
            continue
        children = [child for child in level_1.get("children") or [] if isinstance(child, dict)]
        category = str(level_1.get("category") or "")
        title = str(level_1.get("title") or "")
        if children and _is_core_split_level1(category, title):
            for child in children:
                if _should_split_material_level2_child(level_1, child):
                    for grandchild in _bounded_material_level2_children(child):
                        node_paths.append([level_1, child, grandchild])
                    continue
                node_paths.append([level_1, child])
        else:
            node_paths.append([level_1])
    return node_paths


def _should_split_material_level2_child(level_1_node: dict[str, Any], child: dict[str, Any]) -> bool:
    child_nodes = [item for item in child.get("children") or [] if isinstance(item, dict)]
    if len(child_nodes) < MATERIAL_LEVEL2_SPLIT_MIN_CHILDREN:
        return False
    if not _is_construction_method_level1(level_1_node):
        return False
    text = f"{child.get('title') or ''} {child.get('category') or ''}"
    return any(keyword in text for keyword in ["土建", "装饰", "机电", "安装", "其他", "鍦熷缓", "瑁呴グ", "鏈虹數", "瀹夎", "鍏朵粬"])


def _is_core_split_level1(category: str, title: str) -> bool:
    text = f"{category} {title}"
    if title in CONTENT_COMPLETENESS_TITLES or "内容完整性" in title:
        return False
    if category in CORE_SPLIT_CATEGORIES:
        return True
    return any(
        keyword in text
        for keyword in [
            "施工方案",
            "技术措施",
            "质量管理",
            "安全管理",
            "文明施工",
            "环境保护",
            "工期",
            "风险管理",
            "重点难点",
            "绿色施工",
        ]
    )


def _is_construction_method_level1(level_1_node: dict[str, Any]) -> bool:
    text = f"{level_1_node.get('title') or ''} {level_1_node.get('category') or ''}"
    return any(keyword in text for keyword in ["主要施工方案", "施工方案", "技术措施", "鏂藉伐鏂规"])


def _bounded_material_level2_children(child: dict[str, Any]) -> list[dict[str, Any]]:
    child_nodes = [item for item in child.get("children") or [] if isinstance(item, dict)]
    if len(child_nodes) <= 12:
        return child_nodes
    priority_keywords = ["测量", "土方", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体"]
    prioritized = [
        item
        for item in child_nodes
        if any(keyword in str(item.get("title") or "") for keyword in priority_keywords)
    ]
    remainder = [item for item in child_nodes if item not in prioritized]
    return (prioritized + remainder)[:12]


def _target_section(node_path: list[dict[str, Any]]) -> dict[str, Any]:
    node = node_path[-1]
    chapter_path = [str(item.get("title") or "") for item in node_path if str(item.get("title") or "").strip()]
    child_headings = [
        str(child.get("title") or "")
        for child in node.get("children") or []
        if isinstance(child, dict) and str(child.get("title") or "").strip()
    ]
    query_parts = [*chapter_path, str(node.get("category") or ""), str(node_path[0].get("score_rule") or "")]
    query_parts.extend(child_headings)
    return {
        "target_node_id": node.get("node_id"),
        "parent_level_1_node_id": node_path[0].get("node_id"),
        "original_text": node_path[0].get("original_text") or node_path[0].get("title"),
        "score_rule": node_path[0].get("score_rule"),
        "domain": node.get("domain") or node_path[0].get("domain") or "construction",
        "category": node.get("category") or node_path[0].get("category"),
        "chapter_path": chapter_path,
        "child_headings": child_headings,
        "query": " ".join(part for part in query_parts if part),
    }


def _child_heading_search_hits(
    material_library: ExcellentBidMaterialLibraryResult | dict[str, Any],
    target: dict[str, Any],
    *,
    top_k: int,
    qualities: set[str],
) -> list[Any]:
    """按子小节标题补充召回，避免长章节只命中前几个工艺主题。"""

    chapter_path = [str(part) for part in target.get("chapter_path") or [] if str(part).strip()]
    category = str(target.get("category") or "")
    hits: list[Any] = []
    headings = [
        *(chapter_path[-1:] if chapter_path else []),
        *[str(item) for item in target.get("child_headings") or []],
    ]
    seen_headings: set[str] = set()
    for heading in headings:
        heading_text = str(heading or "").strip()
        if not heading_text or heading_text in seen_headings:
            continue
        seen_headings.add(heading_text)
        query = " ".join(part for part in [*chapter_path, heading_text, category] if part)
        section_path = [*chapter_path, heading_text]
        search_hits = search_excellent_bid_materials(
            material_library,
            query=query,
            section_path=section_path,
            top_k=max(top_k * 3, top_k),
            min_quality=qualities,
        )
        direct_hits = [hit for hit in search_hits if _heading_hit_tokens(hit, heading_text)]
        hits.extend(direct_hits[:top_k])
        hits.extend(
            _fallback_slice_hits_for_heading(
                material_library,
                heading_text,
                qualities=qualities,
                top_k=top_k,
                min_image_hits=MIN_CHILD_HEADING_IMAGE_HITS,
            )
        )
    return hits


def _merge_search_hits(primary_hits: list[Any], supplemental_hits: list[Any]) -> list[Any]:
    """保留主召回排序，同时追加按子标题补充命中的不同素材。"""

    merged: list[Any] = []
    seen: set[str] = set()
    for hit in [*primary_hits, *supplemental_hits]:
        material_id = str(getattr(hit, "material_slice_id", "") or "")
        if not material_id or material_id in seen:
            continue
        seen.add(material_id)
        merged.append(hit)
    return merged


def _filter_material_hits_for_target(hits: list[Any], target: dict[str, Any]) -> list[Any]:
    """对新入库图片切片做更严格的主题过滤，避免弱关键词跨章节误召回。"""

    target_text = " ".join(
        str(part)
        for part in [
            *(target.get("chapter_path") or []),
            *(target.get("child_headings") or []),
            target.get("category"),
        ]
        if part
    )
    target_topics = _retrieval_topics(target_text)
    target_bim_or_info = _has_bim_or_info_topic(target_text)
    result: list[Any] = []
    for hit in hits:
        slice_ = getattr(hit, "slice", None)
        if not slice_:
            result.append(hit)
            continue
        if str(getattr(slice_, "source_type", "") or "") != "docx_image_promotion":
            result.append(hit)
            continue
        if _promotion_slice_allowed_for_target(slice_, target_text, target_topics, target_bim_or_info):
            result.append(hit)
    return result


def _promotion_slice_allowed_for_target(
    slice_: ExcellentBidMaterialSlice,
    target_text: str,
    target_topics: set[str],
    target_bim_or_info: bool,
) -> bool:
    primary_text = _promotion_slice_primary_text(slice_)
    slice_text = " ".join(
        [
            primary_text,
            str(slice_.search_text or ""),
        ]
    )
    slice_topics = _retrieval_topics(slice_text)
    primary_bim_or_info = _has_bim_or_info_topic(primary_text)
    if target_bim_or_info:
        return primary_bim_or_info
    if primary_bim_or_info:
        return False
    if _has_safety_experience_topic(slice_text) and not _has_safety_target(target_text):
        return False
    if _has_site_living_area_topic(slice_text) and not _has_civilized_site_target(target_text):
        return False
    if not target_topics:
        return not _is_process_specific_promotion_slice(slice_text)
    if slice_topics and target_topics & slice_topics:
        return True
    if _is_process_specific_promotion_slice(slice_text):
        return False
    return not _has_strong_exclusion_against_target(slice_text, target_text)


def _promotion_slice_primary_text(slice_: ExcellentBidMaterialSlice) -> str:
    """仅使用标题和章节路径判断切片主语义，避免被大切片全文关键词带偏。"""

    return " ".join(
        [
            str(slice_.title or ""),
            str(slice_.clean_title or ""),
            *[str(part) for part in slice_.section_path],
        ]
    )


def _heading_has_direct_hit(hits: list[Any], heading: str) -> bool:
    if not _heading_tokens(heading):
        return True
    return any(_heading_hit_tokens(hit, heading) for hit in hits)


def _heading_hit_tokens(hit: Any, heading: str) -> set[str]:
    heading_tokens = _expanded_heading_tokens(heading)
    if not heading_tokens:
        return set()
    slice_ = getattr(hit, "slice", None)
    if not slice_:
        return set()
    text = " ".join(
        [
            str(getattr(slice_, "title", "") or ""),
            str(getattr(slice_, "clean_title", "") or ""),
            *list(getattr(slice_, "section_path", []) or []),
            str(getattr(slice_, "search_text", "") or ""),
        ]
    )
    return heading_tokens & set(_heading_tokens(text))


def _fallback_slice_hits_for_heading(
    material_library: ExcellentBidMaterialLibraryResult | dict[str, Any],
    heading: str,
    *,
    qualities: set[str],
    top_k: int,
    min_image_hits: int = 0,
) -> list[Any]:
    from construction_bidding_agent.document_parser.models import ExcellentBidMaterialSearchHit

    heading_tokens = _expanded_heading_tokens(heading)
    if not heading_tokens:
        return []
    scored: list[tuple[float, str, ExcellentBidMaterialSlice]] = []
    for slice_ in _library_slices(material_library):
        if slice_.material_quality not in qualities:
            continue
        text = " ".join(
            [
                str(slice_.title or ""),
                str(slice_.clean_title or ""),
                *[str(part) for part in slice_.section_path],
                str(slice_.search_text or ""),
            ]
        )
        overlap = heading_tokens & set(_heading_tokens(text))
        if not overlap:
            continue
        score = _heading_slice_score(slice_, heading_tokens, overlap)
        material_id = str(slice_.material_slice_id or "")
        scored.append((score, material_id, slice_))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = _select_heading_hits_with_image_supplements(scored, top_k=top_k, min_image_hits=min_image_hits)
    return [
        ExcellentBidMaterialSearchHit(
            material_slice_id=material_id,
            score=round(score, 4),
            reasons=["child_heading_keyword_fallback", *_image_supplement_reasons(slice_, selected[:top_k])],
            slice=slice_,
        )
        for score, material_id, slice_ in selected
    ]


def _select_heading_hits_with_image_supplements(
    scored: list[tuple[float, str, ExcellentBidMaterialSlice]],
    *,
    top_k: int,
    min_image_hits: int,
) -> list[tuple[float, str, ExcellentBidMaterialSlice]]:
    selected = list(scored[:top_k])
    if min_image_hits <= 0:
        return selected
    selected_ids = {material_id for _, material_id, _ in selected}
    image_hit_count = sum(1 for _, _, slice_ in selected if int(slice_.image_count or 0) > 0)
    target_image_hits = min(min_image_hits, top_k)
    for item in scored[top_k:]:
        if image_hit_count >= target_image_hits:
            break
        _, material_id, slice_ = item
        if material_id in selected_ids or int(slice_.image_count or 0) <= 0:
            continue
        selected.append(item)
        selected_ids.add(material_id)
        image_hit_count += 1
    return selected


def _image_supplement_reasons(
    slice_: ExcellentBidMaterialSlice,
    primary_items: list[tuple[float, str, ExcellentBidMaterialSlice]],
) -> list[str]:
    primary_ids = {material_id for _, material_id, _ in primary_items}
    if str(slice_.material_slice_id or "") in primary_ids:
        return []
    if int(slice_.image_count or 0) <= 0:
        return []
    return ["child_heading_image_supplement"]


def _heading_slice_score(
    slice_: ExcellentBidMaterialSlice,
    heading_tokens: set[str],
    overlap: set[str],
) -> float:
    leaf = str(slice_.section_path[-1] if slice_.section_path else "")
    title_text = " ".join([str(slice_.title or ""), str(slice_.clean_title or ""), leaf])
    score = len(overlap) * 2.0
    if overlap == heading_tokens:
        score += 1.0
    if any(token in title_text for token in overlap):
        score += 0.8
    if any(token in leaf for token in overlap):
        score += 0.4
    score += min(int(slice_.image_count or 0), 5) * 0.08
    score += min(int(slice_.table_count or 0), 5) * 0.03
    if int(slice_.image_count or 0) <= 0:
        score -= 0.3
    if slice_.material_quality == "high":
        score += 0.4
    elif slice_.material_quality == "usable":
        score += 0.2
    elif slice_.material_quality == "review_required":
        score -= 0.3
    elif slice_.material_quality == "pdf_fallback":
        score -= 0.5
    if slice_.primary_material_source == "docx":
        score += 0.2
    elif slice_.primary_material_source == "pdf":
        score -= 0.1
    if slice_.source_type == "docx_only":
        score += 0.35
    elif slice_.source_type == "pdf_docx_fusion":
        score -= 0.2
    if slice_.project_specific_risk == "high":
        score -= 0.5
    return score


def _fallback_slice_hit_for_heading(
    material_library: ExcellentBidMaterialLibraryResult | dict[str, Any],
    heading: str,
    *,
    qualities: set[str],
) -> Any | None:
    hits = _fallback_slice_hits_for_heading(material_library, heading, qualities=qualities, top_k=1)
    return hits[0] if hits else None


def _heading_tokens(text: str) -> list[str]:
    value = str(text or "")
    terms = [
        "测量",
        "控制网",
        "轴线",
        "土方",
        "开挖",
        "基坑",
        "支护",
        "降水",
        "排水",
        "边坡",
        "护坡",
        "钢筋",
        "模板",
        "混凝土",
        "浇筑",
        "大体积",
        "温控",
        "防水",
        "地下室",
        "屋面",
        "脚手架",
        "砌体",
        "后浇带",
        "变形缝",
        "止水",
        "施工缝",
        "质量管理",
        "质量保证",
        "质量通病",
        "成品保护",
        "创优",
        "安全管理",
        "安全生产",
        "保障体系",
        "组织机构",
        "岗位职责",
        "责任制",
        "危险源",
        "应急",
        "文明施工",
        "安全文明",
        "环境保护",
        "扬尘",
        "工期",
        "进度管理",
        "组织保证",
        "技术保证",
        "资源保证",
        "经济保证",
    ]
    return [term for term in terms if term in value]


def _expanded_heading_tokens(text: str) -> set[str]:
    tokens = set(_heading_tokens(text))
    expanded = list(tokens)
    if any(term in tokens for term in ["土方", "基坑", "支护"]):
        for related in ["开挖", "降水", "排水", "边坡", "护坡"]:
            if related not in expanded:
                expanded.append(related)
    return set(expanded)


def _retrieval_topics(text: str) -> set[str]:
    value = str(text or "")
    profiles = {
        "measure": ["测量", "控制网", "轴线", "标高", "监测", "放线"],
        "earthwork_foundation": ["土方", "开挖", "基坑", "支护", "降水", "护坡", "边坡"],
        "rebar": ["钢筋", "套筒", "直螺纹", "箍筋", "绑扎", "马凳"],
        "formwork": ["模板", "支撑", "支模", "墙柱板", "顶板", "梁模", "斜撑", "K板"],
        "concrete": ["混凝土", "浇筑", "振捣", "养护", "温控", "大体积"],
        "waterproof": ["防水", "卷材", "涂膜", "止水", "地下室", "屋面"],
        "scaffold": ["脚手架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网"],
        "masonry": ["砌体", "砌筑", "砌块", "灰缝", "构造柱", "拉结筋"],
        "post_pour_joint": ["后浇带", "变形缝", "施工缝", "止水带"],
        "mechanical_electrical": ["机电", "接地", "避雷", "配电箱", "线管", "管线", "风机", "水泵", "阀门"],
        "safety": ["安全", "高处坠落", "坠落", "塔吊", "吊装", "触电", "火灾", "危险源", "洞口防护", "临边", "坍塌"],
        "civilized_site": [
            "安全文明",
            "标准化防护",
            "优秀做法",
            "文明",
            "扬尘",
            "洗车",
            "围挡",
            "垃圾",
            "生活区",
            "办公区",
            "食堂",
            "宿舍",
            "卫生间",
            "污水",
        ],
        "bim_info": ["BIM", "信息化", "智慧工地", "模型", "平台", "监控", "数据", "族库", "碰撞检测", "深化"],
        "quality_management": ["质量", "检验", "验收", "创优", "通病", "保证体系"],
        "schedule_management": ["工期", "进度管理", "关键线路", "纠偏", "组织保证", "技术保证", "经济保证", "工期保证"],
        "resource_equipment": ["资源", "劳动力", "机械设备", "物资", "材料供应", "机械", "设备"],
    }
    return {name for name, terms in profiles.items() if any(term in value for term in terms)}


def _has_bim_or_info_topic(text: str) -> bool:
    return "bim_info" in _retrieval_topics(text)


def _has_safety_target(text: str) -> bool:
    topics = _retrieval_topics(text)
    value = str(text or "")
    return "safety" in topics or "安全管理" in value or "危险性较大" in value


def _has_civilized_site_target(text: str) -> bool:
    topics = _retrieval_topics(text)
    value = str(text or "")
    return "civilized_site" in topics or "文明施工" in value or "环境保护" in value or "扬尘" in value


def _has_safety_experience_topic(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in ["高处坠落", "塔吊倾覆", "吊装坠物", "采光井洞口", "卸料平台事故", "火灾逃生", "触电伤害"])


def _has_site_living_area_topic(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in ["污水排放", "生活区", "食堂", "宿舍", "卫生间", "盥洗室", "垃圾桶", "扬尘", "洗车"])


def _is_process_specific_promotion_slice(text: str) -> bool:
    topics = _retrieval_topics(text)
    strong_topics = {
        "measure",
        "earthwork_foundation",
        "rebar",
        "formwork",
        "concrete",
        "waterproof",
        "scaffold",
        "masonry",
        "post_pour_joint",
        "mechanical_electrical",
        "safety",
        "civilized_site",
        "bim_info",
    }
    return bool(topics & strong_topics)


def _has_strong_exclusion_against_target(slice_text: str, target_text: str) -> bool:
    slice_topics = _retrieval_topics(slice_text)
    target_topics = _retrieval_topics(target_text)
    if not slice_topics or not target_topics:
        return False
    process_topics = {
        "measure",
        "earthwork_foundation",
        "rebar",
        "formwork",
        "concrete",
        "waterproof",
        "scaffold",
        "masonry",
        "post_pour_joint",
        "mechanical_electrical",
        "safety",
        "civilized_site",
        "bim_info",
    }
    return bool(slice_topics & process_topics) and not bool(slice_topics & target_topics)


def _should_skip_material_retrieval(target: dict[str, Any]) -> bool:
    titles = {str(part) for part in target.get("chapter_path") or []}
    return bool(titles & CONTENT_COMPLETENESS_TITLES)


def _skip_reason(target: dict[str, Any]) -> str | None:
    if _should_skip_material_retrieval(target):
        return "内容完整性章节用于总览整本技术标响应范围和章节完整性，默认不套用历史施工工艺素材。"
    return None


def _chapter_image_profile(target: dict[str, Any]) -> dict[str, Any]:
    """根据目标章节生成图片适配画像，供候选过滤和排序使用。"""

    chapter_path = [str(part) for part in target.get("chapter_path") or [] if str(part).strip()]
    child_headings = [str(part) for part in target.get("child_headings") or [] if str(part).strip()]
    target_text = " ".join([*chapter_path, *child_headings, str(target.get("category") or "")])
    category = str(target.get("category") or "")
    chapter_type = _chapter_image_type(target_text, category)
    target_topics = _retrieval_topics(target_text)
    allowed_categories, preferred_categories, blocked_categories = _chapter_image_categories(
        chapter_type,
        target_topics,
    )
    preferred_topics = _chapter_preferred_topics(chapter_type, target_topics)
    image_density_profile = _chapter_image_density(chapter_type, target_topics)
    return {
        "chapter_type": chapter_type,
        "chapter_path": chapter_path,
        "target_text": target_text,
        "target_topics": sorted(target_topics),
        "preferred_topics": sorted(preferred_topics),
        "allowed_categories": sorted(allowed_categories),
        "preferred_categories": sorted(preferred_categories),
        "blocked_categories": sorted(blocked_categories),
        "allow_project_specific_images": False,
        "allow_management_images": chapter_type in {"management_system_section", "schedule_management_section", "risk_management_section"},
        "image_density_profile": image_density_profile,
        "group_policy": "prefer_whole_group",
    }


def _chapter_image_type(target_text: str, category: str) -> str:
    value = str(target_text or "")
    if "内容完整性" in value:
        return "content_completeness"
    if value.startswith(("施工总平面", "总平面布置", "平面布置图")) or "施工总平面" in category:
        return "site_layout_section"
    if "施工方案" in category:
        return "construction_process_section"
    if (
        any(term in value for term in ["技术创新", "BIM", "信息化", "智慧工地", "数据处理"])
        or value.startswith(("采用新工艺", "采用新技术", "新技术", "新工艺"))
    ):
        return "bim_information_section"
    if "风险" in value or "应急" in value or "危险源" in value:
        return "risk_management_section"
    if any(term in value for term in ["安全管理", "安全生产", "安全防护", "危险性较大"]):
        return "safety_management_section"
    if any(term in value for term in ["安全文明", "文明环保", "文明施工", "标准化防护", "环境保护", "扬尘", "建筑垃圾", "绿色施工"]):
        return "civilized_environment_section"
    if "质量" in value or "创优" in value or "通病" in value or "成品保护" in value:
        return "quality_management_section"
    if value.startswith(("施工进度表", "施工进度计划", "进度计划", "横道图", "网络图")) or "施工进度" in category:
        return "schedule_plan_section"
    if "工期" in value or "进度管理" in value or "工期管理" in category:
        return "schedule_management_section"
    if "拟投入资源" in value or "资源配备" in value or "机械设备" in value or "劳动力" in value:
        return "resource_equipment_section"
    if any(term in value for term in ["新技术", "新工艺"]):
        return "bim_information_section"
    if "管理" in category and not (PROCESS_DISCIPLINE_TOPICS & _retrieval_topics(value)):
        return "management_system_section"
    if PROCESS_DISCIPLINE_TOPICS & _retrieval_topics(value) or "施工方案" in category or "施工" in value:
        return "construction_process_section"
    return "general_technical_section"


def _chapter_image_categories(chapter_type: str, target_topics: set[str]) -> tuple[set[str], set[str], set[str]]:
    blocked = {"unknown", "site_layout", "schedule_plan", "project_fact_photo"}
    if chapter_type == "content_completeness":
        return set(), set(), {"unknown", "site_layout", "schedule_plan", "project_fact_photo", "construction_process"}
    if chapter_type in {"site_layout_section", "schedule_plan_section"}:
        return {"site_layout", "schedule_plan"}, set(), {"project_fact_photo", "construction_process", "quality_control"}
    if chapter_type == "construction_process_section":
        return {"construction_process", "quality_control", "safety_protection"}, {"construction_process"}, blocked
    if chapter_type == "quality_management_section":
        return {"quality_control", "management_system", "construction_process"}, {"quality_control", "management_system"}, blocked
    if chapter_type == "safety_management_section":
        return {"safety_protection", "management_system", "emergency_risk"}, {"safety_protection", "management_system", "emergency_risk"}, blocked
    if chapter_type == "civilized_environment_section":
        return {"civilized_site", "environmental_protection", "management_system"}, {"civilized_site", "environmental_protection", "management_system"}, blocked
    if chapter_type == "schedule_management_section":
        return {"management_system", "resource_equipment"}, {"management_system"}, blocked
    if chapter_type == "resource_equipment_section":
        return {"resource_equipment", "management_system"}, {"resource_equipment"}, blocked
    if chapter_type == "bim_information_section":
        return {"bim_information", "management_system", "construction_process"}, {"bim_information"}, blocked
    if chapter_type == "risk_management_section":
        return {"emergency_risk", "safety_protection", "management_system"}, {"emergency_risk", "management_system"}, blocked
    if chapter_type == "management_system_section":
        return {"management_system", "quality_control", "safety_protection"}, {"management_system"}, blocked
    if target_topics:
        return {"construction_process", "quality_control", "management_system"}, {"construction_process"}, blocked
    return {"management_system", "quality_control", "construction_process"}, {"management_system"}, blocked


def _chapter_preferred_topics(chapter_type: str, target_topics: set[str]) -> set[str]:
    if target_topics:
        return set(target_topics)
    defaults = {
        "quality_management_section": {"quality_management"},
        "safety_management_section": {"safety"},
        "civilized_environment_section": {"civilized_site"},
        "schedule_management_section": {"schedule_management"},
        "resource_equipment_section": {"resource_equipment"},
        "bim_information_section": {"bim_info"},
        "risk_management_section": {"risk_emergency", "safety"},
    }
    return set(defaults.get(chapter_type, set()))


def _chapter_image_density(chapter_type: str, target_topics: set[str]) -> str:
    if chapter_type in {"content_completeness", "site_layout_section", "schedule_plan_section"}:
        return "none"
    if chapter_type == "construction_process_section":
        return "rich" if target_topics & PROCESS_DISCIPLINE_TOPICS else "normal"
    if chapter_type in {"civilized_environment_section", "quality_management_section", "safety_management_section"}:
        return "normal"
    return "light"


def _material_reference(
    slice_: ExcellentBidMaterialSlice,
    *,
    rank: int,
    score: float,
    reasons: list[str],
    image_assets_by_material: dict[str, list[dict[str, Any]]] | None = None,
    image_groups_by_material: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    image_assets = (image_assets_by_material or {}).get(slice_.material_slice_id, [])
    image_groups = (image_groups_by_material or {}).get(slice_.material_slice_id, [])
    referenced_group_asset_ids = _referenced_group_asset_ids(image_groups[:MAX_IMAGE_REFERENCES_PER_MATERIAL])
    limited_image_assets = _limit_image_assets_preserving_group_members(
        image_assets,
        referenced_group_asset_ids,
        limit=MAX_IMAGE_CANDIDATE_POOL_PER_MATERIAL,
    )
    return {
        "rank": rank,
        "score": score,
        "match_reasons": reasons,
        "material_slice_id": slice_.material_slice_id,
        "source_id": slice_.source_id,
        "source_type": slice_.source_type,
        "source_slice_id": slice_.source_slice_id,
        "title": slice_.title,
        "section_path": slice_.section_path,
        "material_quality": slice_.material_quality,
        "primary_material_source": slice_.primary_material_source,
        "match_status": slice_.match_status,
        "match_method": slice_.match_method,
        "confidence": slice_.confidence,
        "reuse_level": slice_.reuse_level,
        "project_specific_risk": slice_.project_specific_risk,
        "paragraph_count": slice_.paragraph_count,
        "table_count": slice_.table_count,
        "image_count": slice_.image_count,
        "docx_table_count": slice_.docx_table_count,
        "docx_image_count": slice_.docx_image_count,
        "pdf_table_like_count": slice_.pdf_table_like_count,
        "pdf_image_count": slice_.pdf_image_count,
        "page_range": _page_range(slice_),
        "paragraphs": [asdict(item) for item in slice_.paragraphs[:3]],
        "tables": [asdict(item) for item in slice_.tables[:4]],
        "image_bindings": [asdict(item) for item in slice_.image_bindings[:MAX_IMAGE_BINDINGS_PER_MATERIAL_REFERENCE]],
        "image_assets": limited_image_assets,
        "image_groups": image_groups[:MAX_IMAGE_REFERENCES_PER_MATERIAL],
    }


def _paragraph_references(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for material in materials:
        for paragraph in material.get("paragraphs") or []:
            text = str(paragraph.get("text_preview") or "").strip()
            if not text:
                continue
            references.append(
                {
                    "material_slice_id": material["material_slice_id"],
                    "source_id": material["source_id"],
                    "paragraph_index": paragraph.get("paragraph_index"),
                    "text_preview": text,
                    "use_policy": "rewrite_reference",
                    "material_quality": material.get("material_quality"),
                }
            )
            if len(references) >= 15:
                return references
    return references


def _table_references(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for material in materials:
        for table in material.get("tables") or []:
            references.append(
                {
                    "material_slice_id": material["material_slice_id"],
                    "source_id": material["source_id"],
                    "table_index": table.get("table_index"),
                    "row_count": table.get("row_count"),
                    "max_column_count": table.get("max_column_count"),
                    "image_count": table.get("image_count"),
                    "header_preview": table.get("header_preview") or [],
                    "use_policy": "reuse_structure_rewrite_content",
                    "material_quality": material.get("material_quality"),
                }
            )
            if len(references) >= MAX_IMAGE_REFERENCES:
                return references
    return references


def _image_references(materials: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for material in materials:
        material_count = 0
        for image in _material_images(material):
            reference = _image_reference(material, image, chapter_profile=chapter_profile)
            if not _is_review_image_fit(reference):
                continue
            references.append(reference)
            material_count += 1
            if len(references) >= 12:
                return references
            if material_count >= MAX_IMAGE_REFERENCES_PER_MATERIAL:
                break
    return references


def _image_group_references(materials: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for material in materials:
        material_count = 0
        for group in _material_image_groups(material):
            reference = _image_group_reference(material, group, chapter_profile=chapter_profile)
            if not _is_review_image_fit(reference):
                continue
            references.append(reference)
            material_count += 1
            if len(references) >= MAX_IMAGE_REFERENCES:
                return references
            if material_count >= MAX_IMAGE_REFERENCES_PER_MATERIAL:
                break
    return references


def _image_candidate_pool(materials: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> list[dict[str, Any]]:
    per_material: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for material in materials:
        material_refs: list[dict[str, Any]] = []
        material_count = 0
        for image in _material_images(material):
            key = _image_reference_key(material, image)
            if key in seen:
                continue
            reference = _image_reference(material, image, chapter_profile=chapter_profile)
            if not _is_auto_image_fit(reference):
                continue
            seen.add(key)
            material_refs.append(reference)
            material_count += 1
            if material_count >= MAX_IMAGE_CANDIDATE_POOL_PER_MATERIAL:
                break
        if material_refs:
            per_material.append(material_refs)
    return _theme_balanced_image_pool(_round_robin_limited(per_material, MAX_IMAGE_CANDIDATE_POOL), per_material)


def _round_robin_limited(groups: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """跨素材均衡取图，防止一个富图表格占满整章图片候选池。"""

    references: list[dict[str, Any]] = []
    index = 0
    while len(references) < limit:
        added = False
        for group in groups:
            if index < len(group):
                references.append(group[index])
                added = True
                if len(references) >= limit:
                    break
        if not added:
            break
        index += 1
    return references


def _image_group_candidate_pool(materials: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> list[dict[str, Any]]:
    per_material: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for material in materials:
        material_refs: list[dict[str, Any]] = []
        material_count = 0
        for group in _material_image_groups(material):
            key = _image_group_reference_key(material, group)
            if not key or key in seen:
                continue
            reference = _image_group_reference(material, group, chapter_profile=chapter_profile)
            if not _is_auto_image_fit(reference):
                continue
            seen.add(key)
            material_refs.append(reference)
            material_count += 1
            if material_count >= MAX_IMAGE_CANDIDATE_POOL_PER_MATERIAL:
                break
        if material_refs:
            per_material.append(material_refs)
    return _theme_balanced_image_pool(_round_robin_limited(per_material, MAX_IMAGE_CANDIDATE_POOL), per_material)


def _theme_balanced_image_pool(
    initial: list[dict[str, Any]],
    per_material: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """用后续精准主题图片替换前部重复主题，保证长章节多个工艺主题都有候选图。"""

    selected = list(initial[:MAX_IMAGE_CANDIDATE_POOL])
    selected_keys = {_pool_reference_key(item) for item in selected}
    selected_theme_counts = Counter(_primary_heading_token(_reference_theme_text(item)) for item in selected)
    all_refs = [item for group in per_material for item in group]
    for theme in _ordered_missing_themes(all_refs, selected):
        supplemental = [
            item
            for item in all_refs
            if _primary_heading_token(_reference_theme_text(item)) == theme
            and _pool_reference_key(item) not in selected_keys
        ]
        if not supplemental:
            continue
        for item in supplemental[:2]:
            if len(selected) < MAX_IMAGE_CANDIDATE_POOL:
                selected.append(item)
                selected_keys.add(_pool_reference_key(item))
                selected_theme_counts[theme] += 1
                continue
            replace_index = _replaceable_pool_index(selected, selected_theme_counts)
            if replace_index is None:
                break
            old = selected[replace_index]
            old_theme = _primary_heading_token(_reference_theme_text(old))
            selected[replace_index] = item
            selected_keys.discard(_pool_reference_key(old))
            selected_keys.add(_pool_reference_key(item))
            if old_theme:
                selected_theme_counts[old_theme] -= 1
            selected_theme_counts[theme] += 1
    return selected


def _ordered_missing_themes(all_refs: list[dict[str, Any]], selected: list[dict[str, Any]]) -> list[str]:
    all_themes: list[str] = []
    for item in all_refs:
        theme = _primary_heading_token(_reference_theme_text(item))
        if theme and theme not in all_themes:
            all_themes.append(theme)
    selected_themes = {_primary_heading_token(_reference_theme_text(item)) for item in selected}
    return [theme for theme in all_themes if theme not in selected_themes]


def _replaceable_pool_index(
    selected: list[dict[str, Any]],
    theme_counts: Counter[str],
) -> int | None:
    for index in range(len(selected) - 1, -1, -1):
        theme = _primary_heading_token(_reference_theme_text(selected[index]))
        if theme and theme_counts.get(theme, 0) > 3:
            return index
    return len(selected) - 1 if selected else None


def _pool_reference_key(item: dict[str, Any]) -> str:
    return str(
        item.get("group_canonical_image_key")
        or item.get("image_group_id")
        or item.get("canonical_image_id")
        or item.get("sha256")
        or item.get("perceptual_hash")
        or item.get("image_asset_id")
        or item.get("image_id")
        or "|".join(
            str(part)
            for part in [
                item.get("material_slice_id"),
                item.get("rel_id"),
                item.get("part_name"),
                item.get("table_index"),
                item.get("row_index"),
                item.get("cell_index"),
            ]
        )
    )


def _primary_heading_token(text: str) -> str:
    tokens = _heading_tokens(text)
    return tokens[0] if tokens else ""


def _reference_theme_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("material_title"),
        " ".join(str(part) for part in item.get("source_section_path") or []),
        item.get("semantic_text"),
        item.get("group_semantic_text"),
        item.get("caption"),
        item.get("group_title"),
        item.get("nearby_text"),
        " ".join(str(tag) for tag in item.get("tags") or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _image_group_summary(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for material in materials:
        policy_counts: Counter[str] = Counter()
        image_count = 0
        for image in _material_images(material):
            policy_counts[_image_use_policy(material, image)] += 1
            image_count += 1
        image_group_count = len(_material_image_groups(material))
        if not image_count:
            continue
        groups.append(
            {
                "material_slice_id": material.get("material_slice_id"),
                "title": material.get("title"),
                "section_path": material.get("section_path") or [],
                "reuse_level": material.get("reuse_level"),
                "material_quality": material.get("material_quality"),
                "image_count": image_count,
                "image_group_count": image_group_count,
                "candidate_reuse_count": policy_counts.get("candidate_reuse", 0),
                "manual_review_count": policy_counts.get("manual_review", 0),
                "placeholder_or_manual_review_count": policy_counts.get("placeholder_or_manual_review", 0),
            }
        )
    return groups


def _text_image_block_candidates(
    block_index: dict[str, Any],
    target: dict[str, Any],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    if _should_skip_material_retrieval(target):
        return []
    target_text = _target_text(target)
    block_top_k = max(6, min(top_k * 3, 12)) if _has_process_target(target_text) else max(3, min(top_k, 5))
    return search_text_image_blocks(
        block_index,
        query=str(target.get("query") or ""),
        section_path=[str(part) for part in target.get("chapter_path") or [] if str(part).strip()],
        top_k=block_top_k,
    )


def _text_image_block_reuse_candidates(
    block_candidates: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    chapter_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    material_by_id = {str(item.get("material_slice_id") or ""): item for item in materials if isinstance(item, dict)}
    result: list[dict[str, Any]] = []
    for block in block_candidates:
        if len(result) >= MAX_TEXT_IMAGE_BLOCK_REUSE_CANDIDATES:
            break
        if not isinstance(block, dict) or not _text_image_block_allows_auto_reuse(block):
            continue
        material = material_by_id.get(str(block.get("material_slice_id") or ""))
        if not material:
            continue
        groups = _block_reuse_group_references(material, block, chapter_profile)
        images = _block_reuse_image_references(material, block, chapter_profile, groups)
        if not groups and not images:
            continue
        result.append(
            {
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "material_slice_id": block.get("material_slice_id"),
                "source_id": block.get("source_id"),
                "title": block.get("title"),
                "section_path": block.get("section_path") or [],
                "topics": block.get("topics") or [],
                "primary_topic": block.get("primary_topic"),
                "secondary_topics": block.get("secondary_topics") or [],
                "match_level": block.get("match_level"),
                "match_confidence": block.get("match_confidence"),
                "match_reasons": block.get("match_reasons") or [],
                "risk_flags": block.get("risk_flags") or [],
                "retrieval_score": block.get("retrieval_score"),
                "reuse_level": block.get("reuse_level"),
                "project_specific_risk": block.get("project_specific_risk"),
                "use_policy": block.get("use_policy"),
                "render_policy": block.get("render_policy") or {},
                "row_scope": block.get("row_scope") or {},
                "image_asset_ids": block.get("image_asset_ids") or [],
                "image_group_ids": block.get("image_group_ids") or [],
                "image_candidates": images,
                "image_group_candidates": groups,
            }
        )
    return result


def _text_image_block_allows_auto_reuse(block: dict[str, Any]) -> bool:
    if str(block.get("match_level") or "") != "strong":
        return False
    if float(block.get("match_confidence") or 0) < 0.75:
        return False
    if str(block.get("reuse_level") or "") == "manual_review":
        return False
    if str(block.get("project_specific_risk") or "").lower() == "high":
        return False
    risk_flags = {str(flag) for flag in block.get("risk_flags") or []}
    blocked_flags = {
        "general_analysis",
        "manual_review",
        "primary_topic_only_from_caption",
        "subtopic_only_from_caption",
        "missing_target_subtopic",
        "target_topic_is_secondary",
    }
    if risk_flags & blocked_flags:
        return False
    if any(flag.startswith(("primary_topic_mismatch", "other_process_primary_topic")) for flag in risk_flags):
        return False
    return bool(block.get("image_asset_ids") or block.get("image_group_ids"))


def _target_text(target: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            *(target.get("chapter_path") or []),
            *(target.get("child_headings") or []),
            target.get("category"),
            target.get("query"),
        ]
        if part
    )


def _has_process_target(text: str) -> bool:
    return bool(_retrieval_topics(text) & PROCESS_DISCIPLINE_TOPICS)


def _block_reuse_group_references(
    material: dict[str, Any],
    block: dict[str, Any],
    chapter_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    allowed_ids = {str(item) for item in block.get("image_group_ids") or [] if str(item).strip()}
    if not allowed_ids:
        return []
    references: list[dict[str, Any]] = []
    for group in _material_image_groups(material):
        group_id = str(group.get("image_group_id") or "")
        if group_id not in allowed_ids:
            continue
        reference = _image_group_reference(material, group, chapter_profile=chapter_profile)
        if not _is_auto_image_fit(reference):
            continue
        _mark_text_image_block_reuse(reference, block)
        for member in reference.get("members") or []:
            if isinstance(member, dict):
                _mark_text_image_block_reuse(member, block)
        references.append(reference)
    return references


def _block_reuse_image_references(
    material: dict[str, Any],
    block: dict[str, Any],
    chapter_profile: dict[str, Any],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_ids = {str(item) for item in block.get("image_asset_ids") or [] if str(item).strip()}
    if not allowed_ids:
        return []
    grouped_asset_ids = {
        str(asset_id)
        for group in groups
        for asset_id in group.get("image_asset_ids") or []
        if str(asset_id).strip()
    }
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in _material_images(material):
        asset_id = str(image.get("image_asset_id") or "")
        if asset_id not in allowed_ids or asset_id in grouped_asset_ids:
            continue
        reference = _image_reference(material, image, chapter_profile=chapter_profile)
        if not _is_auto_image_fit(reference):
            continue
        key = _image_reference_key(material, image)
        if key in seen:
            continue
        seen.add(key)
        _mark_text_image_block_reuse(reference, block)
        references.append(reference)
    return references


def _mark_text_image_block_reuse(reference: dict[str, Any], block: dict[str, Any]) -> None:
    reference["source_reuse_mode"] = "text_image_block"
    reference["text_image_block_id"] = block.get("block_id")
    reference["text_image_block_title"] = block.get("title")
    reference["text_image_block_primary_topic"] = block.get("primary_topic")
    reference["text_image_block_match_level"] = block.get("match_level")
    reference["text_image_block_match_confidence"] = block.get("match_confidence")
    reference["text_image_block_match_reasons"] = block.get("match_reasons") or []
    reference["text_image_block_risk_flags"] = block.get("risk_flags") or []
    reference["reuse_priority"] = "text_image_block_strong"
    reference["render_policy"] = block.get("render_policy") or {}
    reference["row_scope"] = block.get("row_scope") or {}


def _block_image_candidate_pool(block_reuse_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for block in block_reuse_candidates
        for item in block.get("image_candidates") or []
        if isinstance(item, dict)
    ]


def _block_image_group_candidate_pool(block_reuse_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for block in block_reuse_candidates
        for item in block.get("image_group_candidates") or []
        if isinstance(item, dict)
    ]


def _merge_reference_pools(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        if not isinstance(item, dict):
            continue
        key = _pool_reference_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _material_library_dict(material_library: ExcellentBidMaterialLibraryResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(material_library, dict):
        return material_library
    return material_library.to_dict()


def _image_reference(
    material: dict[str, Any],
    image: dict[str, Any],
    *,
    chapter_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = _classify_image_reference(material, image)
    fit = _image_fit_for_chapter(classification, chapter_profile or {})
    governance = _caption_governance(image)
    use_policy = _image_use_policy(material, image)
    if governance.get("action") == "manual_review":
        use_policy = "manual_review"
    if fit["fit_level"] == "review_only" and use_policy == "candidate_reuse":
        use_policy = "manual_review"
    caption = _governed_caption(image)
    reference = {
        "material_slice_id": material["material_slice_id"],
        "source_id": material["source_id"],
        "source_type": material.get("source_type"),
        "source_slice_id": image.get("source_slice_id") or material.get("source_slice_id"),
        "source_section_path": image.get("section_path") or material.get("section_path") or [],
        "material_title": material.get("title"),
        "image_asset_id": image.get("image_asset_id"),
        "image_id": image.get("image_id"),
        "canonical_image_id": image.get("canonical_image_id"),
        "sha256": image.get("sha256"),
        "perceptual_hash": image.get("perceptual_hash"),
        "rel_id": image.get("rel_id"),
        "target": image.get("target"),
        "part_name": image.get("part_name"),
        "context": image.get("context"),
        "table_index": image.get("table_index"),
        "row_index": image.get("row_index"),
        "cell_index": image.get("cell_index"),
        "image_group_id": image.get("image_group_id"),
        "group_title": image.get("group_title"),
        "group_semantic_text": image.get("group_semantic_text"),
        "group_member_index": image.get("group_member_index"),
        "group_member_count": image.get("group_member_count"),
        "must_keep_with_group": bool(image.get("must_keep_with_group")),
        "caption": caption,
        "caption_original": image.get("caption_actual") or image.get("caption"),
        "caption_governance": governance or None,
        "caption_candidates": image.get("caption_candidates") or [],
        "semantic_sources": image.get("semantic_sources") or [],
        "semantic_text": image.get("semantic_text"),
        "semantic_confidence": image.get("semantic_confidence"),
        "nearby_text": image.get("nearby_text"),
        "tags": image.get("tags") or [],
        "review_required": bool(image.get("review_required")),
        "review_reason": image.get("review_reason"),
        "use_policy": use_policy,
        "reuse_level": image.get("reuse_level"),
        "risk_level": image.get("project_specific_risk") or image.get("risk_level"),
        "material_quality": material.get("material_quality"),
        "primary_material_source": material.get("primary_material_source"),
    }
    reference.update(classification)
    reference.update(fit)
    return reference


def _image_group_reference(
    material: dict[str, Any],
    group: dict[str, Any],
    *,
    chapter_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = _classify_image_group_reference(material, group)
    fit = _image_fit_for_chapter(classification, chapter_profile or {})
    governance = _caption_governance(group)
    use_policy = _image_group_use_policy(material, group)
    if governance.get("action") == "manual_review":
        use_policy = "manual_review"
    if fit["fit_level"] == "review_only" and use_policy == "candidate_reuse":
        use_policy = "manual_review"
    member_refs = []
    for index, image in enumerate(_group_member_images(material, group), start=1):
        ref = _image_reference(material, image, chapter_profile=chapter_profile)
        ref["image_group_id"] = group.get("image_group_id")
        ref["group_title"] = group.get("group_title")
        ref["group_semantic_text"] = group.get("semantic_text")
        ref["group_member_index"] = index
        ref["group_member_count"] = int(group.get("member_count") or len(group.get("image_asset_ids") or []))
        ref["must_keep_with_group"] = True
        member_refs.append(ref)
    reference = {
        "image_group_id": group.get("image_group_id"),
        "material_slice_id": material["material_slice_id"],
        "source_id": material["source_id"],
        "source_type": material.get("source_type"),
        "source_slice_id": group.get("source_slice_id") or material.get("source_slice_id"),
        "source_section_path": group.get("section_path") or material.get("section_path") or [],
        "material_title": material.get("title"),
        "group_title": _governed_caption(group) or group.get("group_title") or group.get("title") or material.get("title"),
        "group_title_original": group.get("group_title") or group.get("title") or material.get("title"),
        "caption_governance": governance or None,
        "semantic_sources": group.get("semantic_sources") or [],
        "semantic_text": group.get("semantic_text"),
        "semantic_confidence": group.get("semantic_confidence"),
        "nearby_text": group.get("nearby_text"),
        "tags": group.get("tags") or [],
        "table_index": group.get("table_index"),
        "start_row_index": group.get("start_row_index"),
        "end_row_index": group.get("end_row_index"),
        "member_count": group.get("member_count"),
        "canonical_image_ids": group.get("canonical_image_ids") or [],
        "sha256_values": group.get("sha256_values") or [],
        "perceptual_hash_values": group.get("perceptual_hash_values") or [],
        "group_canonical_image_key": group.get("group_canonical_image_key"),
        "image_asset_ids": group.get("image_asset_ids") or [],
        "image_ids": group.get("image_ids") or [],
        "captions": group.get("captions") or [],
        "members": member_refs,
        "review_required": bool(group.get("review_required")),
        "review_reason": group.get("review_reason"),
        "use_policy": use_policy,
        "reuse_level": group.get("reuse_level"),
        "risk_level": group.get("project_specific_risk") or group.get("risk_level"),
        "material_quality": material.get("material_quality"),
        "primary_material_source": material.get("primary_material_source"),
        "must_keep_together": bool(group.get("must_keep_together", True)),
    }
    reference.update(classification)
    reference.update(fit)
    return reference


def _reuse_warnings(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for material in materials:
        reuse_level = str(material.get("reuse_level") or "")
        if reuse_level == "manual_review":
            if material_has_parameter_conflict(material):
                continue
            warnings.append(
                {
                    "material_slice_id": material.get("material_slice_id"),
                    "risk_level": "high",
                    "reason": "该素材被判定为人工复核类，可能包含历史项目专属图纸、踏勘/现状照片、项目概况或进度图，不能自动复用到正文。",
                }
            )
        elif reuse_level == "parameterized_reuse":
            warnings.append(
                {
                    "material_slice_id": material.get("material_slice_id"),
                    "risk_level": "medium",
                    "reason": "该素材属于参数化复用类，可参考工艺框架和表格结构，但必须替换工程规模、部位、设备、工期等项目参数。",
                }
            )

        quality = str(material.get("material_quality") or "")
        if quality in {"review_required", "pdf_fallback"}:
            warnings.append(
                {
                    "material_slice_id": material.get("material_slice_id"),
                    "risk_level": "medium" if quality == "review_required" else "high",
                    "reason": "该素材来自兜底或需复核匹配，生成正文时只能参考改写，不能直接照搬。",
                }
            )
        if str(material.get("project_specific_risk") or "") == "high":
            warnings.append(
                {
                    "material_slice_id": material.get("material_slice_id"),
                    "risk_level": "high",
                    "reason": "素材可能包含历史项目专属信息，需替换项目名称、地点、楼栋号、平面图等内容。",
                }
            )
    return warnings


def _is_auto_image_fit(reference: dict[str, Any]) -> bool:
    return (
        str(reference.get("fit_level") or "") in AUTO_IMAGE_FIT_LEVELS
        and str(reference.get("use_policy") or "") == "candidate_reuse"
    )


def _is_review_image_fit(reference: dict[str, Any]) -> bool:
    return str(reference.get("fit_level") or "") in REVIEW_IMAGE_FIT_LEVELS


def _classify_image_reference(material: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    local_text = _image_local_semantic_text(image)
    full_text = _image_semantic_text(material, image)
    local_topics = _retrieval_topics(local_text)
    text = local_text if local_topics else full_text
    topics = _retrieval_topics(text)
    primary_category = _primary_image_category(text, topics, material, image)
    if _is_clear_management_image(text, image):
        primary_category = "management_system"
    discipline_tags = _discipline_tags(topics, text)
    scene_tags = _scene_tags(text)
    semantic_confidence = _semantic_confidence_level(image)
    image_form = _image_form(text, primary_category)
    return {
        "primary_category": primary_category,
        "discipline_tags": sorted(discipline_tags),
        "scene_tags": sorted(scene_tags),
        "image_form": image_form,
        "semantic_confidence_level": semantic_confidence,
        "classification_topics": sorted(topics),
        "classification_text_excerpt": text[:240],
    }


def _classify_image_group_reference(material: dict[str, Any], group: dict[str, Any]) -> dict[str, Any]:
    local_text = _group_local_semantic_text(group)
    full_text = _group_semantic_text(material, group)
    local_topics = _retrieval_topics(local_text)
    text = local_text if local_topics else full_text
    topics = _retrieval_topics(text)
    primary_category = _primary_group_category(text, topics, material, group)
    if _is_clear_management_image(text, group):
        primary_category = "management_system"
    return {
        "primary_category": primary_category,
        "discipline_tags": sorted(_discipline_tags(topics, text)),
        "scene_tags": sorted(_scene_tags(text)),
        "image_form": _image_form(text, primary_category),
        "semantic_confidence_level": _semantic_confidence_level(group),
        "classification_topics": sorted(topics),
        "classification_text_excerpt": text[:240],
        "group_use_policy": "use_as_whole" if group.get("must_keep_together", True) else "allow_split",
    }


def _image_fit_for_chapter(classification: dict[str, Any], chapter_profile: dict[str, Any]) -> dict[str, Any]:
    if not chapter_profile:
        return {"fit_level": "candidate", "fit_score": 50, "fit_reasons": ["未提供章节画像，保守作为候选。"]}

    category = str(classification.get("primary_category") or "unknown")
    topics = set(str(item) for item in classification.get("classification_topics") or [])
    target_topics = set(str(item) for item in chapter_profile.get("target_topics") or [])
    preferred_topics = set(str(item) for item in chapter_profile.get("preferred_topics") or [])
    allowed = set(str(item) for item in chapter_profile.get("allowed_categories") or [])
    preferred = set(str(item) for item in chapter_profile.get("preferred_categories") or [])
    blocked = set(str(item) for item in chapter_profile.get("blocked_categories") or [])
    chapter_type = str(chapter_profile.get("chapter_type") or "")
    reasons: list[str] = []
    score = 0
    image_process_topics = topics & PROCESS_DISCIPLINE_TOPICS
    target_process_topics = target_topics & PROCESS_DISCIPLINE_TOPICS

    if category in blocked or category == "unknown":
        return {
            "fit_level": "review_only",
            "fit_score": 0,
            "fit_reasons": [f"图片分类 {category} 不允许自动用于当前章节。"],
        }
    if allowed and category not in allowed:
        return {
            "fit_level": "review_only",
            "fit_score": 10,
            "fit_reasons": [f"图片分类 {category} 不在当前章节允许范围内。"],
        }
    if image_process_topics and target_process_topics and not (image_process_topics & target_process_topics):
        return {
            "fit_level": "review_only",
            "fit_score": 10,
            "fit_reasons": ["图片工序主题与当前小节专业主题冲突。"],
        }

    if category in preferred:
        score += 40
        reasons.append("图片主分类匹配章节优先分类。")
    elif category in allowed:
        score += 25
        reasons.append("图片主分类在章节允许范围内。")

    topic_overlap = topics & (preferred_topics or target_topics)
    if topic_overlap:
        score += 30
        reasons.append("图片专业/主题标签与小节语义匹配：" + "、".join(sorted(topic_overlap)))
    elif _requires_specific_topic(chapter_type, target_topics, category):
        return {
            "fit_level": "review_only",
            "fit_score": score,
            "fit_reasons": ["当前小节需要明确专业主题，图片缺少对应主题标签。"],
        }
    elif topics and target_topics and not _topics_compatible_for_chapter(chapter_type, topics, target_topics):
        return {
            "fit_level": "review_only",
            "fit_score": score,
            "fit_reasons": ["图片主题与当前小节主题冲突。"],
        }

    confidence = str(classification.get("semantic_confidence_level") or "")
    if confidence == "high":
        score += 20
        reasons.append("图片有题注、同行或邻近文本等高可信语义来源。")
    elif confidence == "medium":
        score += 10
        reasons.append("图片有一定邻近语义来源。")
    else:
        score -= 20
        reasons.append("图片语义来源较弱。")

    if str(classification.get("group_use_policy") or "") == "use_as_whole":
        score += 10
        reasons.append("套图可整体使用。")

    if score >= 70:
        fit_level = "preferred"
    elif score >= 35:
        fit_level = "candidate"
    else:
        fit_level = "review_only"
    return {
        "fit_level": fit_level,
        "fit_score": max(score, 0),
        "fit_reasons": reasons or ["按规则判定为候选图片。"],
    }


def _image_semantic_text(material: dict[str, Any], image: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            _image_local_semantic_text(image),
            material.get("title"),
            " ".join(str(item) for item in image.get("section_path") or material.get("section_path") or []),
        ]
        if part
    )


def _image_local_semantic_text(image: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            _governed_caption(image),
            image.get("caption_actual") or image.get("caption"),
            image.get("semantic_text"),
            image.get("nearby_text"),
            image.get("cell_text"),
            image.get("row_text"),
            image.get("header_text"),
            image.get("previous_row_text"),
            image.get("next_row_text"),
            image.get("group_title"),
            image.get("group_semantic_text"),
            " ".join(str(tag) for tag in image.get("tags") or []),
        ]
        if part
    )


def _group_semantic_text(material: dict[str, Any], group: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            _group_local_semantic_text(group),
            material.get("title"),
            " ".join(str(item) for item in group.get("section_path") or material.get("section_path") or []),
        ]
        if part
    )


def _group_local_semantic_text(group: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            _governed_caption(group),
            group.get("group_title"),
            group.get("group_semantic_text"),
            group.get("semantic_text"),
            group.get("nearby_text"),
            " ".join(str(item) for item in group.get("captions") or []),
            " ".join(str(tag) for tag in group.get("tags") or []),
        ]
        if part
    )


def _primary_image_category(
    text: str,
    topics: set[str],
    material: dict[str, Any],
    image: dict[str, Any],
) -> str:
    risk = str(image.get("project_specific_risk") or image.get("risk_level") or material.get("project_specific_risk") or "")
    if (risk == "high" and not _has_generic_reusable_image_topic(text)) or _has_project_fact_image_topic(text):
        if any(term in text for term in ["总平面", "平面布置", "交通组织"]):
            return "site_layout"
        if any(term in text for term in ["进度计划", "横道图", "网络图"]):
            return "schedule_plan"
        return "project_fact_photo"
    if _has_schedule_plan_topic(text):
        return "schedule_plan"
    if _has_site_layout_topic(text):
        return "site_layout"
    if "bim_info" in topics:
        return "bim_information"
    if "civilized_site" in topics and (
        _has_strong_civilized_image_topic(text)
        or not (topics & PROCESS_DISCIPLINE_TOPICS or "resource_equipment" in topics)
    ):
        return "environmental_protection" if any(term in text for term in ["扬尘", "喷淋", "洗车", "裸土", "垃圾", "污水"]) else "civilized_site"
    if "safety" in topics and _has_strong_safety_image_topic(text):
        return "emergency_risk" if any(term in text for term in ["应急", "风险", "危险源", "救援", "预案"]) else "safety_protection"
    if "quality_management" in topics or any(term in text for term in ["样板", "通病", "验收", "检查", "成品保护", "质量"]):
        return "quality_control"
    if any(term in text for term in ["机械", "设备", "劳动力", "材料", "资源"]):
        return "resource_equipment"
    if topics & PROCESS_DISCIPLINE_TOPICS:
        return "construction_process"
    if "safety" in topics:
        return "emergency_risk" if any(term in text for term in ["应急", "风险", "危险源", "救援", "预案"]) else "safety_protection"
    if any(term in text for term in ["组织机构", "管理体系", "流程", "闭环", "责任", "制度", "检查表", "目标分解"]):
        return "management_system"
    if str(material.get("reuse_level") or "") == "manual_review":
        return "project_fact_photo"
    return "unknown"


def _primary_group_category(
    text: str,
    topics: set[str],
    material: dict[str, Any],
    group: dict[str, Any],
) -> str:
    return _primary_image_category(text, topics, material, group)


def _item_caption_semantic_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            item.get("caption_actual") or item.get("caption"),
            item.get("semantic_text"),
            item.get("group_title"),
            item.get("group_semantic_text"),
            " ".join(str(caption) for caption in item.get("captions") or []),
            item.get("nearby_text"),
        ]
        if part
    )


def _is_clear_management_image(text: str, item: dict[str, Any]) -> bool:
    value = str(text or "")
    if not value:
        return False
    if _has_project_fact_image_topic(value) or _has_site_layout_topic(value) or _has_schedule_plan_topic(value):
        return False
    risk = str(item.get("project_specific_risk") or item.get("risk_level") or "")
    if risk == "high":
        return False
    topics = _retrieval_topics(value)
    if topics & PROCESS_DISCIPLINE_TOPICS and not any(
        term in value for term in ["管理体系", "保证体系", "保障体系", "组织机构", "组织架构", "责任制", "岗位职责", "职责分工"]
    ):
        return False
    return any(term in value for term in CLEAR_MANAGEMENT_IMAGE_TERMS)


def _has_strong_civilized_image_topic(text: str) -> bool:
    return any(
        term in str(text or "")
        for term in [
            "标准化防护",
            "标准化做法",
            "优秀做法",
            "安全文明",
            "现场照片",
            "围挡",
            "扬尘",
            "洗车",
            "喷淋",
            "裸土",
            "垃圾",
            "生活区",
            "办公区",
            "食堂",
            "宿舍",
            "卫生间",
            "宣传墙",
            "宣传长廊",
            "安全宣传",
            "材料堆放",
            "分类摆放",
            "标识标牌",
            "公示牌",
            "大门",
            "保安亭",
            "休息室",
        ]
    )


def _has_strong_safety_image_topic(text: str) -> bool:
    return any(
        term in str(text or "")
        for term in ["安全", "防护", "临边", "坠落", "塔吊", "吊装", "触电", "火灾", "危险源", "坍塌", "用电", "消防", "应急", "救援"]
    )


def _discipline_tags(topics: set[str], text: str) -> set[str]:
    tags = set(topics & PROCESS_DISCIPLINE_TOPICS)
    if "quality_management" in topics:
        tags.add("quality")
    if "civilized_site" in topics:
        tags.add("civilized_site")
    if "bim_info" in topics:
        tags.add("bim_information")
    if "safety" in topics:
        tags.add("safety")
    if any(term in text for term in ["电梯", "井道", "导轨", "轿厢"]):
        tags.add("elevator")
    return tags


def _scene_tags(text: str) -> set[str]:
    mapping = {
        "processing": ["加工", "成型", "切断", "调直"],
        "installation": ["安装", "绑扎", "支设", "搭设", "铺贴"],
        "inspection": ["检查", "验收", "复核", "检测"],
        "protection": ["保护", "防护", "覆盖", "围挡"],
        "flow": ["流程", "第一步", "第二步", "第三步"],
        "standard_practice": ["标准化", "样板", "示意图", "做法"],
    }
    return {name for name, terms in mapping.items() if any(term in text for term in terms)}


def _image_form(text: str, primary_category: str) -> str:
    if any(term in text for term in ["流程", "闭环", "体系", "组织机构", "责任"]):
        return "management_diagram"
    if primary_category in {"site_layout", "schedule_plan"}:
        return primary_category
    if "示意图" in text or "做法" in text:
        return "practice_diagram"
    if "照片" in text or "现场" in text:
        return "process_photo"
    return "image"


def _semantic_confidence_level(item: dict[str, Any]) -> str:
    if item.get("review_required") is True:
        if _has_generic_reusable_image_topic(_item_caption_semantic_text(item)):
            return "medium"
        if _is_clear_management_image(_item_caption_semantic_text(item), item):
            return "high"
        return "low"
    if item.get("caption_actual") or item.get("captions"):
        return "high"
    if item.get("semantic_text") and float(item.get("semantic_confidence") or 0) >= 0.75:
        return "high"
    if item.get("nearby_text") or item.get("row_text") or item.get("cell_text"):
        return "medium"
    return "low"


def _requires_specific_topic(chapter_type: str, target_topics: set[str], category: str) -> bool:
    if chapter_type == "construction_process_section" and target_topics & PROCESS_DISCIPLINE_TOPICS:
        return category == "construction_process"
    if chapter_type in {"quality_management_section", "safety_management_section"} and target_topics & PROCESS_DISCIPLINE_TOPICS:
        return category in {"construction_process", "quality_control", "safety_protection"}
    return False


def _topics_compatible_for_chapter(chapter_type: str, image_topics: set[str], target_topics: set[str]) -> bool:
    if image_topics & target_topics:
        return True
    if chapter_type == "quality_management_section" and "quality_management" in image_topics:
        return True
    if chapter_type == "safety_management_section" and "safety" in image_topics:
        return True
    if chapter_type == "civilized_environment_section" and "civilized_site" in image_topics:
        return True
    if chapter_type == "bim_information_section" and "bim_info" in image_topics:
        return True
    image_process_topics = image_topics & PROCESS_DISCIPLINE_TOPICS
    target_process_topics = target_topics & PROCESS_DISCIPLINE_TOPICS
    return not image_process_topics or not target_process_topics


def _has_project_fact_image_topic(text: str) -> bool:
    return any(term in text for term in ["踏勘", "现状", "周边道路", "周边环境", "航拍", "实景", "救援路线"])


def _has_site_layout_topic(text: str) -> bool:
    return any(term in text for term in ["总平面", "平面布置", "临设布置", "交通组织"])


def _has_schedule_plan_topic(text: str) -> bool:
    return any(term in text for term in ["进度计划", "横道图", "网络图", "计划开工", "计划竣工"])


def _image_use_policy(material: dict[str, Any], binding: dict[str, Any]) -> str:
    governance = _caption_governance(binding)
    if governance.get("action") == "manual_review":
        return "manual_review"
    text = _image_semantic_text(material, binding)
    if _is_clear_management_image(text, binding):
        return "candidate_reuse"
    if _has_generic_reusable_image_topic(text) and not _has_hard_project_fact_image_topic(text):
        return "candidate_reuse"
    if binding.get("review_required") is True:
        return "manual_review"
    if str(binding.get("reuse_level") or "") == "manual_review":
        return "manual_review"
    if str(binding.get("project_specific_risk") or binding.get("risk_level") or "") == "high" and not _has_generic_reusable_image_topic(text):
        return "manual_review"
    if material.get("reuse_level") == "manual_review":
        return "manual_review"
    section_text = " ".join(material.get("section_path") or [])
    if any(keyword in section_text for keyword in PROJECT_FACT_IMAGE_TERMS):
        return "placeholder_or_manual_review"
    if material.get("material_quality") in {"review_required", "pdf_fallback"}:
        return "manual_review"
    if material.get("primary_material_source") == "docx" and material.get("material_quality") in {"high", "usable"}:
        return "candidate_reuse"
    return "candidate_reuse"


def _image_group_use_policy(material: dict[str, Any], group: dict[str, Any]) -> str:
    governance = _caption_governance(group)
    if governance.get("action") == "manual_review":
        return "manual_review"
    text = _group_semantic_text(material, group)
    if _is_clear_management_image(text, group):
        return "candidate_reuse"
    if _has_generic_reusable_image_topic(text) and not _has_hard_project_fact_image_topic(text):
        return "candidate_reuse"
    if group.get("review_required") is True:
        return "manual_review"
    if str(group.get("reuse_level") or "") == "manual_review":
        return "manual_review"
    if str(group.get("project_specific_risk") or group.get("risk_level") or "") == "high" and not _has_generic_reusable_image_topic(text):
        return "manual_review"
    if material.get("reuse_level") == "manual_review":
        return "manual_review"
    section_text = " ".join(group.get("section_path") or material.get("section_path") or [])
    if any(keyword in section_text for keyword in PROJECT_FACT_IMAGE_TERMS):
        return "placeholder_or_manual_review"
    if material.get("material_quality") in {"review_required", "pdf_fallback"}:
        return "manual_review"
    return "candidate_reuse"


def _material_images(material: dict[str, Any]) -> list[dict[str, Any]]:
    assets = [item for item in material.get("image_assets") or [] if isinstance(item, dict)]
    if assets:
        return assets
    return [item for item in material.get("image_bindings") or [] if isinstance(item, dict)]


def _material_image_groups(material: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in material.get("image_groups") or [] if isinstance(item, dict)]


def _referenced_group_asset_ids(image_groups: list[dict[str, Any]]) -> set[str]:
    asset_ids: set[str] = set()
    for group in image_groups:
        if not isinstance(group, dict):
            continue
        asset_ids.update(str(item) for item in group.get("image_asset_ids") or [] if str(item).strip())
    return asset_ids


def _limit_image_assets_preserving_group_members(
    image_assets: list[dict[str, Any]],
    group_asset_ids: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    limited: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in image_assets[:limit]:
        if not isinstance(image, dict):
            continue
        key = _image_asset_stable_key(image)
        if key in seen:
            continue
        seen.add(key)
        limited.append(image)
    if not group_asset_ids:
        return limited
    for image in image_assets[limit:]:
        if not isinstance(image, dict):
            continue
        asset_id = str(image.get("image_asset_id") or "")
        if asset_id not in group_asset_ids:
            continue
        key = _image_asset_stable_key(image)
        if key in seen:
            continue
        seen.add(key)
        limited.append(image)
    return limited


def _image_asset_stable_key(image: dict[str, Any]) -> str:
    return str(
        image.get("canonical_image_id")
        or image.get("sha256")
        or image.get("perceptual_hash")
        or image.get("image_asset_id")
        or image.get("image_id")
        or "|".join(
            str(part)
            for part in [
                image.get("rel_id"),
                image.get("part_name"),
                image.get("table_index"),
                image.get("row_index"),
                image.get("cell_index"),
            ]
        )
    )


def _caption_governance(item: dict[str, Any]) -> dict[str, Any]:
    governance = item.get("caption_governance")
    return governance if isinstance(governance, dict) else {}


def _governed_caption(item: dict[str, Any]) -> str:
    governance = _caption_governance(item)
    if governance.get("action") == "rewrite":
        caption = str(governance.get("suggested_caption") or item.get("caption_governance_suggested") or "").strip()
        if caption:
            return caption
    return str(item.get("caption_actual") or item.get("caption") or "").strip()


def _has_generic_reusable_image_topic(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in GENERIC_REUSABLE_IMAGE_TERMS)


def _has_hard_project_fact_image_topic(text: str) -> bool:
    value = str(text or "")
    return _has_project_fact_image_topic(value) or _has_site_layout_topic(value) or _has_schedule_plan_topic(value)


def _group_member_images(material: dict[str, Any], group: dict[str, Any]) -> list[dict[str, Any]]:
    asset_ids = [str(item) for item in group.get("image_asset_ids") or [] if str(item).strip()]
    image_ids = [str(item) for item in group.get("image_ids") or [] if str(item).strip()]
    assets = _material_images(material)
    if asset_ids:
        by_asset = {str(image.get("image_asset_id") or ""): image for image in assets}
        members = [by_asset[asset_id] for asset_id in asset_ids if asset_id in by_asset]
        if members:
            return members
    if image_ids:
        by_image = {str(image.get("image_id") or ""): image for image in assets}
        members = [by_image[image_id] for image_id in image_ids if image_id in by_image]
        if members:
            return members
    return [
        image
        for image in assets
        if image.get("image_group_id") and image.get("image_group_id") == group.get("image_group_id")
    ]


def _image_reference_key(material: dict[str, Any], image: dict[str, Any]) -> str:
    stable_id = (
        image.get("canonical_image_id")
        or image.get("sha256")
        or image.get("perceptual_hash")
        or image.get("image_asset_id")
        or image.get("image_id")
    )
    if stable_id:
        return str(stable_id)
    return "|".join(
        str(part)
        for part in [
            material.get("material_slice_id"),
            image.get("rel_id"),
            image.get("table_index"),
            image.get("row_index"),
            image.get("cell_index"),
        ]
    )


def _image_group_reference_key(material: dict[str, Any], group: dict[str, Any]) -> str:
    group_key = str(group.get("group_canonical_image_key") or "").strip()
    if group_key:
        return group_key
    for key_name in ["canonical_image_ids", "sha256_values", "perceptual_hash_values", "image_asset_ids", "image_ids"]:
        values = [str(item).strip() for item in group.get(key_name) or [] if str(item).strip()]
        if values:
            return f"{key_name}:{'|'.join(sorted(values))}"
    group_id = str(group.get("image_group_id") or "").strip()
    if group_id:
        return group_id
    return "|".join(
        str(part)
        for part in [
            material.get("material_slice_id"),
            group.get("table_index"),
            group.get("start_row_index"),
            group.get("end_row_index"),
            group.get("group_title"),
        ]
    )


def _image_assets_by_material(
    library: ExcellentBidMaterialLibraryResult | dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    if isinstance(library, ExcellentBidMaterialLibraryResult):
        items = [asdict(asset) for asset in library.image_assets]
    else:
        items = [item for item in library.get("image_assets") or [] if isinstance(item, dict)]
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        material_id = str(item.get("material_slice_id") or "")
        if not material_id:
            continue
        result.setdefault(material_id, []).append(item)
    return result


def _image_groups_by_material(
    library: ExcellentBidMaterialLibraryResult | dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    if isinstance(library, ExcellentBidMaterialLibraryResult):
        items = [asdict(group) for group in library.image_groups]
    else:
        items = [item for item in library.get("image_groups") or [] if isinstance(item, dict)]
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        material_id = str(item.get("material_slice_id") or "")
        if not material_id:
            continue
        result.setdefault(material_id, []).append(item)
    return result


def _page_range(slice_: ExcellentBidMaterialSlice) -> str | None:
    if slice_.start_page is None:
        return None
    if slice_.end_page is None or slice_.end_page == slice_.start_page:
        return str(slice_.start_page)
    return f"{slice_.start_page}-{slice_.end_page}"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
