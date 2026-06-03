"""整本技术标 Word 初稿导出器。

本模块不调用 LLM，只把章节生成输入包和已生成章节结果合并为整本技术标草稿：
- 已生成的章节写入正文；
- 尚未生成的章节保留目录骨架和待生成提示；
- 一级目录保持评分点原文表达。
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from construction_bidding_agent.document_parser.image_fingerprints import (
    ImageFingerprintIndex,
    asset_metadata_for_record,
    build_image_fingerprint_index,
    fingerprint_metadata_for_record,
    image_fingerprint_keys,
)
from .chapter_docx_renderer import (
    DEFAULT_LIBRARY,
    DEFAULT_RAW_ROOT,
    DEFAULT_RENDER_PROFILE,
    FINAL_DOCX_MODE,
    REVIEW_DOCX_MODE,
    write_chapter_docx,
)
from .chapter_writer import postprocess_chapter_images
from .word_version_manager import publish_system_generated_docx, write_word_quality_summary


DEFAULT_TIMEZONE = "Asia/Shanghai"
FULL_BID_SCHEMA_VERSION = "technical_bid_full_draft_v0.1"
PLACEHOLDER_TEXT = "【待生成】本小节需结合招标文件评分要求、项目基础信息及企业优秀标书素材补充正文。"
PACKAGE_PLACEHOLDER_TEXT = "【待生成】本节正文尚未生成，需在后续章节生成阶段补齐正文、表格、图片及评分点响应内容。"
_PROCESS_TOPIC_TERMS = {
    "测量": ["测量", "控制网", "轴线", "标高", "放线", "铅垂仪", "内控点", "监测"],
    "钢筋": ["钢筋", "箍筋", "套筒", "马凳筋", "梯子筋", "直螺纹", "绑扎"],
    "模板": ["模板", "支模", "木方", "对拉螺栓", "覆膜板", "满堂架"],
    "混凝土": ["混凝土", "浇筑", "振捣", "测温", "养护", "温控", "大体积"],
    "防水": ["防水", "卷材", "涂膜", "止水", "屋面", "地下室"],
    "脚手架": ["脚手架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "悬挑"],
    "砌体": ["砌体", "砌筑", "砖", "加气块", "构造柱", "拉结筋"],
    "后浇带": ["后浇带", "变形缝", "施工缝"],
    "电梯": ["电梯", "井道", "导轨", "轿厢", "层门", "预埋件"],
    "进度计划": ["工期", "进度", "计划", "关键线路", "网络图", "横道图", "纠偏", "赶工"],
    "安全防护": ["安全", "防护", "临边", "洞口", "用电", "消防", "塔吊", "机械", "防护棚", "安全网", "配电箱", "开关箱"],
    "环境保护": [
        "环境保护",
        "环保",
        "扬尘",
        "噪声",
        "噪音",
        "大气",
        "污染",
        "水污染",
        "光污染",
        "固体废弃物",
        "绿色",
        "节能",
        "沉淀池",
        "洗车槽",
        "围挡",
        "垃圾",
        "危废",
        "污水",
        "降尘",
    ],
}
_MANAGEMENT_TERMS = ["流程", "组织", "架构", "体系", "闭环", "责任", "分工", "检查", "验收", "制度", "目标", "管理"]
_DEPLOYMENT_TERMS = ["部署", "流程", "流水", "流水段", "区段", "穿插", "总体", "组织", "平面布置"]
_GENERAL_NO_PROCESS_IMAGE_TERMS = ["编制依据", "工程重点", "重点难点", "难点分析", "项目概况", "工程概况"]
_ENVIRONMENT_TOPIC = "环境保护"
_SAFETY_TOPIC = "安全防护"
_STRICT_SUBTOPIC_NAMES = {
    "临边洞口",
    "临时用电",
    "消防",
    "机械塔吊",
    "个人防护",
    "噪声",
    "水污染",
    "光污染",
    "固废",
    "扬尘大气",
    "绿色节能",
}
_SPECIFIC_PROCESS_TOPIC_NAMES = set(_PROCESS_TOPIC_TERMS) - {_ENVIRONMENT_TOPIC, _SAFETY_TOPIC, "进度计划"}


@dataclass(slots=True)
class FullBidExportBuild:
    generation_result: dict[str, Any]
    summary: dict[str, Any]


def export_full_bid_docx_from_files(
    chapter_inputs_json: str | Path,
    generation_result_jsons: list[str | Path],
    output_docx: str | Path,
    *,
    output_json: str | Path | None = None,
    material_library_json: str | Path | None = DEFAULT_LIBRARY,
    raw_root: str | Path = DEFAULT_RAW_ROOT,
    render_profile_json: str | Path | None = DEFAULT_RENDER_PROFILE,
    word_export_profile: dict[str, Any] | str | Path | None = None,
    title: str = "技术标整本 Word 初稿",
    apply_current_image_policy: bool = True,
    output_mode: str = REVIEW_DOCX_MODE,
) -> dict[str, Any]:
    """从文件构建整本技术标草稿并渲染为 DOCX。"""

    total_started = time.monotonic()
    timings: list[dict[str, Any]] = []
    load_started = time.monotonic()
    chapter_inputs = _load_json(chapter_inputs_json)
    generation_results = [_load_json(path) for path in generation_result_jsons if str(path).strip()]
    _record_timing(timings, "load_inputs", "读取章节输入包和生成结果", load_started)

    compose_started = time.monotonic()
    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=apply_current_image_policy,
        include_review_artifacts=_normalize_output_mode(output_mode) == REVIEW_DOCX_MODE,
        material_library_json=material_library_json,
        raw_root=raw_root,
    )
    _record_timing(timings, "compose_full_bid_json", "聚合章节并执行图片策略", compose_started)

    render_started = time.monotonic()
    render_stats = write_chapter_docx(
        build.generation_result,
        output_docx,
        material_library_json=material_library_json,
        raw_root=raw_root,
        render_profile_json=render_profile_json,
        word_export_profile=word_export_profile,
        title=title,
        output_mode=output_mode,
    )
    _record_timing(timings, "write_docx", "写入 Word 初稿", render_started)
    output_docx_path = Path(output_docx)
    output_json_path = Path(output_json) if output_json is not None else None
    documents_dir = output_docx_path.parent
    system_generated_docx = publish_system_generated_docx(output_docx_path, documents_dir)
    summary = {
        **build.summary,
        "render_stats": render_stats,
        "word_versions": {
            "system_generated_docx": str(system_generated_docx),
            "legacy_output_docx": str(output_docx_path),
        },
        "word_refresh_timing": _word_refresh_timing_summary(
            timings,
            total_started=total_started,
            output_docx=output_docx_path,
            output_json=output_json_path,
            render_stats=render_stats,
        ),
    }
    build.generation_result["full_bid_export_summary"] = summary
    if output_json is not None:
        json_write_started = time.monotonic()
        target_json = Path(output_json)
        target_json.parent.mkdir(parents=True, exist_ok=True)
        _record_timing(timings, "write_full_bid_json", "写入整本技术标 JSON", json_write_started)
        _write_json_with_stable_size(
            target_json,
            build.generation_result,
            summary,
            timings,
            total_started=total_started,
            output_docx=output_docx_path,
            render_stats=render_stats,
        )
    word_quality_summary = write_word_quality_summary(
        documents_dir,
        draft_json=build.generation_result,
        render_stats=render_stats,
        extra_summary=copy.deepcopy(summary),
    )
    summary["word_quality_summary"] = word_quality_summary
    return summary


def build_full_bid_generation_result(
    chapter_inputs: dict[str, Any],
    generation_results: list[dict[str, Any]],
    *,
    apply_current_image_policy: bool = True,
    include_review_artifacts: bool = True,
    material_library_json: str | Path | None = None,
    raw_root: str | Path = DEFAULT_RAW_ROOT,
) -> FullBidExportBuild:
    """合成可交给现有 Word 渲染器消费的整本技术标章节结果。"""

    packages = [package for package in chapter_inputs.get("packages") or [] if isinstance(package, dict)]
    generated_lookup = _index_generated_chapters(generation_results)
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    generated_package_count = 0
    placeholder_package_count = 0
    package_summaries: list[dict[str, Any]] = []

    for package in packages:
        unit = package.get("generation_unit") or {}
        path = _chapter_path(unit)
        if not path:
            continue
        top_title = path[0]
        top = groups.setdefault(top_title, _new_top_level_chapter(top_title, package))
        generated_chapter = _find_generated_chapter(package, generated_lookup)
        if generated_chapter:
            generated_package_count += 1
            working_chapter = copy.deepcopy(generated_chapter)
            if apply_current_image_policy:
                working_chapter = postprocess_chapter_images(working_chapter, package)
            sections = _generated_package_sections(
                path,
                working_chapter,
                package=package,
                top_level_chapter=top,
                include_review_artifacts=include_review_artifacts,
            )
            package_status = "generated"
            _append_source_usage(top, working_chapter)
            if include_review_artifacts:
                _append_review_items(top, working_chapter.get("review_items") or [], path)
        else:
            placeholder_package_count += 1
            sections = _placeholder_package_sections(package)
            package_status = "placeholder"
            if include_review_artifacts:
                _append_review_items(top, _placeholder_review_items(package), path)
        top["sections"].extend(sections)
        top["_package_total"] += 1
        if package_status == "generated":
            top["_generated_total"] += 1
        package_summaries.append(_package_summary(package, package_status))

    chapters = [
        _finalize_top_level_chapter(chapter, include_review_artifacts=include_review_artifacts)
        for chapter in groups.values()
    ]
    summary = {
        "schema_version": FULL_BID_SCHEMA_VERSION,
        "package_count": len(packages),
        "level1_chapter_count": len(chapters),
        "generated_package_count": generated_package_count,
        "placeholder_package_count": placeholder_package_count,
        "coverage_ratio": round(generated_package_count / len(packages), 4) if packages else 0,
        "empty_heading_summary": _empty_heading_summary(chapters),
        "package_summaries": package_summaries,
    }
    result = {
        "schema_version": FULL_BID_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "provider": _first_value(generation_results, "provider"),
        "model": _first_value(generation_results, "model") or "mixed-or-not-generated",
        "base_url": _first_value(generation_results, "base_url"),
        "task_count": len(packages),
        "completed_count": generated_package_count,
        "skipped_count": placeholder_package_count,
        "failed_count": 0,
        "duration_seconds": 0,
        "execution_mode": "compose_existing_chapters",
        "max_workers": 0,
        "full_bid_export_summary": summary,
        "chapters": chapters,
        "tasks": [],
        "warnings": _collect_warnings(generation_results),
    }
    fingerprint_summary = _enrich_full_bid_image_fingerprints(result, material_library_json, raw_root)
    if fingerprint_summary["enabled"]:
        summary["image_fingerprint_summary"] = fingerprint_summary
    dedupe_summary = _dedupe_full_bid_image_refs(result)
    summary["image_dedupe_summary"] = dedupe_summary
    summary["quality_gate_summary"] = _quality_gate_summary(chapters, summary)
    return FullBidExportBuild(generation_result=result, summary=summary)


def _record_timing(
    timings: list[dict[str, Any]],
    key: str,
    label: str,
    started: float,
) -> None:
    timings.append(
        {
            "key": key,
            "label": label,
            "duration_seconds": round(time.monotonic() - started, 4),
        }
    )


def _empty_heading_summary(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    empty_headings: list[dict[str, Any]] = []
    consecutive_empty_heading_count = 0
    for chapter in chapters:
        previous_empty = False
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                previous_empty = False
                continue
            if section.get("structural_heading") is True:
                previous_empty = False
                continue
            is_empty = not _section_has_content(section)
            if is_empty:
                empty_headings.append(
                    {
                        "chapter": chapter.get("title"),
                        "heading": section.get("heading"),
                        "level": section.get("level"),
                    }
                )
                if previous_empty:
                    consecutive_empty_heading_count += 1
            previous_empty = is_empty
    return {
        "empty_heading_count": len(empty_headings),
        "consecutive_empty_heading_count": consecutive_empty_heading_count,
        "samples": empty_headings[:20],
    }


def _quality_gate_summary(chapters: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    empty_summary = summary.get("empty_heading_summary") or {}
    image_summary = _chapter_image_summary(chapters)
    dedupe_summary = summary.get("image_dedupe_summary") or {}

    if int(empty_summary.get("consecutive_empty_heading_count") or 0) > 0:
        issues.append(
            {
                "severity": "warning",
                "type": "consecutive_empty_headings",
                "message": "存在连续空标题，建议复核目录聚合结果。",
            }
        )
    if int(empty_summary.get("empty_heading_count") or 0) > 20:
        issues.append(
            {
                "severity": "warning",
                "type": "too_many_empty_headings",
                "message": "空标题数量较多，可能存在正文生成单元与目录不匹配。",
            }
        )
    if int(dedupe_summary.get("removed_duplicate_asset_count") or 0) > 0 or int(dedupe_summary.get("removed_duplicate_group_count") or 0) > 0:
        issues.append(
            {
                "severity": "info",
                "type": "duplicate_images_removed",
                "message": "已在整本范围移除重复图片。",
            }
        )
    for chapter in image_summary["chapter_summaries"]:
        title = str(chapter.get("title") or "")
        image_count = int(chapter.get("image_ref_count") or 0)
        if _is_construction_method_chapter_title(title) and image_count < 8:
            issues.append(
                {
                    "severity": "warning",
                    "type": "construction_method_images_low",
                    "message": f"主要施工方案与技术措施类章节图片偏少：{title}，当前 {image_count} 张。",
                }
            )
    status = "passed"
    if any(issue["severity"] == "warning" for issue in issues):
        status = "warning"
    if any(issue["severity"] == "blocking" for issue in issues):
        status = "blocked"
    return {
        "schema_version": "technical_bid_quality_gate_v0.1",
        "status": status,
        "blocking_issue_count": sum(1 for issue in issues if issue["severity"] == "blocking"),
        "warning_issue_count": sum(1 for issue in issues if issue["severity"] == "warning"),
        "info_issue_count": sum(1 for issue in issues if issue["severity"] == "info"),
        "empty_heading_summary": empty_summary,
        "image_summary": image_summary,
        "dedupe_summary": {
            "removed_count": dedupe_summary.get("removed_count", 0),
            "removed_duplicate_asset_count": dedupe_summary.get("removed_duplicate_asset_count", 0),
            "removed_duplicate_group_count": dedupe_summary.get("removed_duplicate_group_count", 0),
            "removed_incompatible_count": dedupe_summary.get("removed_incompatible_count", 0),
        },
        "issues": issues,
    }


def _chapter_image_summary(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    chapter_summaries: list[dict[str, Any]] = []
    total = 0
    for chapter in chapters:
        refs = [
            block
            for section in chapter.get("sections") or []
            if isinstance(section, dict)
            for block in section.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "image_ref"
        ]
        section_count = sum(
            1
            for section in chapter.get("sections") or []
            if isinstance(section, dict)
            and any(isinstance(block, dict) and block.get("type") == "image_ref" for block in section.get("blocks") or [])
        )
        total += len(refs)
        chapter_summaries.append(
            {
                "title": chapter.get("title"),
                "image_ref_count": len(refs),
                "section_with_image_count": section_count,
                "section_count": len([section for section in chapter.get("sections") or [] if isinstance(section, dict)]),
            }
        )
    return {
        "total_image_ref_count": total,
        "chapter_summaries": chapter_summaries,
    }


def _is_construction_method_chapter_title(title: str) -> bool:
    return any(keyword in title for keyword in ["主要施工方案", "施工方案与技术措施", "施工方法与技术措施", "施工技术措施"])


def _word_refresh_timing_summary(
    timings: list[dict[str, Any]],
    *,
    total_started: float,
    output_docx: Path,
    output_json: Path | None,
    render_stats: dict[str, Any],
) -> dict[str, Any]:
    total_duration = round(time.monotonic() - total_started, 4)
    image_duration = float(render_stats.get("image_processing_duration_seconds") or 0)
    stages = list(timings)
    if image_duration:
        stages.append(
            {
                "key": "image_processing",
                "label": "图片解析、排版与写入累计耗时",
                "duration_seconds": round(image_duration, 4),
            }
        )
    return {
        "schema_version": "word_refresh_timing_v0.1",
        "duration_seconds": total_duration,
        "llm_called": False,
        "stages": stages,
        "docx_size_bytes": output_docx.stat().st_size if output_docx.exists() else None,
        "json_size_bytes": output_json.stat().st_size if output_json and output_json.exists() else None,
        "image_ref_count": render_stats.get("image_ref_count"),
        "rendered_image_count": render_stats.get("rendered_image_count"),
        "missing_image_count": render_stats.get("missing_image_count"),
        "placeholder_count": render_stats.get("placeholder_count"),
    }


def _write_json_with_stable_size(
    target_json: Path,
    generation_result: dict[str, Any],
    summary: dict[str, Any],
    timings: list[dict[str, Any]],
    *,
    total_started: float,
    output_docx: Path,
    render_stats: dict[str, Any],
) -> None:
    previous_size: int | None = None
    for _ in range(5):
        summary["word_refresh_timing"] = _word_refresh_timing_summary(
            timings,
            total_started=total_started,
            output_docx=output_docx,
            output_json=target_json,
            render_stats=render_stats,
        )
        if previous_size is not None:
            summary["word_refresh_timing"]["json_size_bytes"] = previous_size
        generation_result["full_bid_export_summary"] = summary
        target_json.write_text(json.dumps(generation_result, ensure_ascii=False, indent=2), encoding="utf-8")
        current_size = target_json.stat().st_size
        if summary["word_refresh_timing"].get("json_size_bytes") == current_size:
            return
        previous_size = current_size
    summary["word_refresh_timing"]["json_size_bytes"] = target_json.stat().st_size
    generation_result["full_bid_export_summary"] = summary
    target_json.write_text(json.dumps(generation_result, ensure_ascii=False, indent=2), encoding="utf-8")


def _index_generated_chapters(generation_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for result in generation_results:
        for chapter in result.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            for key in _generated_chapter_keys(chapter):
                lookup[key] = chapter
    return lookup


def _find_generated_chapter(package: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    unit = package.get("generation_unit") or {}
    keys = [
        _lookup_key("unit_id", unit.get("unit_id")),
        _lookup_key("target_node_id", unit.get("target_node_id")),
        _lookup_key("chapter_path", " > ".join(_chapter_path(unit))),
    ]
    for key in keys:
        if key and key in lookup:
            return lookup[key]
    return None


def _generated_chapter_keys(chapter: dict[str, Any]) -> list[str]:
    return [
        key
        for key in [
            _lookup_key("unit_id", chapter.get("unit_id")),
            _lookup_key("target_node_id", chapter.get("target_node_id")),
            _lookup_key("chapter_path", " > ".join(str(part) for part in chapter.get("chapter_path") or [])),
        ]
        if key
    ]


def _new_top_level_chapter(title: str, package: dict[str, Any]) -> dict[str, Any]:
    unit = package.get("generation_unit") or {}
    score_point = package.get("score_point") or {}
    return {
        "schema_version": "technical_bid_full_chapter_v1",
        "unit_id": f"FULL-{unit.get('parent_level_1_node_id') or unit.get('target_node_id') or title}",
        "target_node_id": unit.get("parent_level_1_node_id") or unit.get("target_node_id") or "",
        "chapter_path": [title],
        "title": title,
        "sections": [],
        "score_response_check": {
            "score_point_raw": score_point.get("score_point_raw") or title,
            "response_summary": "",
            "covered": False,
            "evidence_headings": [],
        },
        "source_usage": [],
        "review_items": [],
        "_package_total": 0,
        "_generated_total": 0,
        "_inserted_level2_headings": set(),
    }


def _finalize_top_level_chapter(chapter: dict[str, Any], *, include_review_artifacts: bool = True) -> dict[str, Any]:
    total = int(chapter.pop("_package_total", 0) or 0)
    generated = int(chapter.pop("_generated_total", 0) or 0)
    chapter.pop("_inserted_level2_headings", None)
    pending = max(total - generated, 0)
    check = chapter.setdefault("score_response_check", {})
    check["response_summary"] = (
        f"本一级目录包含 {total} 个生成单元，已生成 {generated} 个，待生成 {pending} 个。"
        "当前 Word 用于整本技术标结构预览和编制复核，待生成单元需继续补齐正文。"
        if include_review_artifacts
        else ""
    )
    check["covered"] = bool(total and generated == total)
    check["evidence_headings"] = [
        str(section.get("heading") or "")
        for section in chapter.get("sections") or []
        if isinstance(section, dict) and str(section.get("heading") or "").strip()
    ][:20]
    return chapter


def _generated_package_sections(
    path: list[str],
    chapter: dict[str, Any],
    *,
    package: dict[str, Any],
    top_level_chapter: dict[str, Any] | None = None,
    include_review_artifacts: bool = True,
) -> list[dict[str, Any]]:
    generated_sections = _non_empty_sections([section for section in chapter.get("sections") or [] if isinstance(section, dict)])
    unit = package.get("generation_unit") or {}
    child_headings = [str(item).strip() for item in unit.get("child_headings") or [] if str(item).strip()]
    if len(path) <= 1:
        if child_headings:
            return _generated_level2_child_sections(
                path[-1],
                generated_sections,
                child_headings=child_headings,
                parent_level=2,
                child_level=2,
                include_structural_parent=False,
            )
        return [
            {
                "heading": path[-1],
                "level": 2,
                "blocks": _collapse_generated_sections_to_blocks(generated_sections, skip_heading=path[-1]),
            }
        ]
    if len(path) >= 3:
        return _generated_nested_package_sections(
            path,
            generated_sections,
            top_level_chapter=top_level_chapter,
            include_review_artifacts=include_review_artifacts,
        )
    blocks = _package_summary_blocks(chapter) if include_review_artifacts else []
    if not blocks and not generated_sections:
        return []
    if child_headings:
        sections = _generated_level2_child_sections(
            path[-1],
            generated_sections,
            child_headings=child_headings,
            parent_level=2,
            child_level=3,
            parent_blocks=blocks,
            include_structural_parent=True,
        )
        if sections:
            return sections
    return [
        {
            "heading": path[-1],
            "level": 2,
            "blocks": [*blocks, *_collapse_generated_sections_to_blocks(generated_sections, skip_heading=path[-1])],
        }
    ]


def _generated_nested_package_sections(
    path: list[str],
    generated_sections: list[dict[str, Any]],
    *,
    top_level_chapter: dict[str, Any] | None,
    include_review_artifacts: bool,
) -> list[dict[str, Any]]:
    parent_heading = path[-2]
    sections: list[dict[str, Any]] = []
    inserted = top_level_chapter.get("_inserted_level2_headings") if isinstance(top_level_chapter, dict) else None
    inserted_headings = inserted if isinstance(inserted, set) else set()
    parent_key = _normalize_text(parent_heading)
    should_insert_parent = (
        parent_key
        and parent_key not in inserted_headings
        and bool(generated_sections)
        and not _sections_include_heading(generated_sections, parent_heading)
    )
    if should_insert_parent:
        sections.append({"heading": parent_heading, "level": 2, "blocks": [], "structural_heading": True})
        inserted_headings.add(parent_key)
        if isinstance(top_level_chapter, dict):
            top_level_chapter["_inserted_level2_headings"] = inserted_headings
    sections.append(
        {
            "heading": path[-1],
            "level": 3,
            "blocks": _collapse_generated_sections_to_blocks(generated_sections, skip_heading=path[-1]),
        }
    )
    return sections


def _generated_level2_child_sections(
    parent_heading: str,
    generated_sections: list[dict[str, Any]],
    *,
    child_headings: list[str],
    parent_level: int,
    child_level: int | None = None,
    parent_blocks: list[dict[str, Any]] | None = None,
    include_structural_parent: bool = False,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    parent_blocks = list(parent_blocks or [])
    matched_indexes: set[int] = set()
    child_lookup = {_normalize_text(heading): heading for heading in child_headings}
    for index, section in enumerate(generated_sections):
        key = _normalize_text(section.get("heading"))
        if key not in child_lookup:
            continue
        matched_indexes.add(index)
    unmatched = [section for index, section in enumerate(generated_sections) if index not in matched_indexes]
    if parent_blocks or unmatched or (include_structural_parent and matched_indexes):
        sections.append(
            {
                "heading": parent_heading,
                "level": parent_level,
                "blocks": [
                    *parent_blocks,
                    *_collapse_generated_sections_to_blocks(unmatched, skip_heading=parent_heading),
                ],
                **(
                    {"structural_heading": True}
                    if include_structural_parent and not parent_blocks and not unmatched
                    else {}
                ),
            }
        )
    for child_heading in child_headings:
        child_key = _normalize_text(child_heading)
        child_sections = [
            section
            for index, section in enumerate(generated_sections)
            if index in matched_indexes and _normalize_text(section.get("heading")) == child_key
        ]
        if child_sections:
            blocks = _collapse_generated_sections_to_blocks(child_sections, skip_heading=child_heading)
        else:
            blocks = []
        if blocks:
            resolved_child_level = child_level if child_level is not None else parent_level + 1
            sections.append({"heading": child_heading, "level": resolved_child_level, "blocks": blocks})
    return sections


def _collapse_generated_sections_to_blocks(
    sections: list[dict[str, Any]],
    *,
    skip_heading: str = "",
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    skip_key = _normalize_text(skip_heading)
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        if heading and _normalize_text(heading) != skip_key:
            blocks.append({"type": "internal_heading", "text": heading})
        for block in section.get("blocks") or []:
            if isinstance(block, dict):
                blocks.append(copy.deepcopy(block))
    return blocks


def _non_empty_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [section for section in sections if _section_has_content(section)]


def _section_has_content(section: dict[str, Any]) -> bool:
    blocks = section.get("blocks")
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"paragraph", "heading"} and str(block.get("text") or "").strip():
            return True
        if block.get("type") in {"rich_table", "table"}:
            rows = block.get("rows")
            if isinstance(rows, list) and rows:
                return True
        if block.get("type") in {"image_ref", "image_placeholder"}:
            return True
    return False


def _sections_include_heading(sections: list[dict[str, Any]], heading: str) -> bool:
    normalized_heading = _normalize_text(heading)
    if not normalized_heading:
        return False
    return any(_normalize_text(section.get("heading")) == normalized_heading for section in sections)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _normalize_generated_section(section: dict[str, Any], *, default_level: int) -> dict[str, Any]:
    normalized = copy.deepcopy(section)
    try:
        level = int(normalized.get("level") or default_level)
    except (TypeError, ValueError):
        level = default_level
    normalized["level"] = max(default_level, level)
    return normalized


def _enrich_full_bid_image_fingerprints(
    result: dict[str, Any],
    material_library_json: str | Path | None,
    raw_root: str | Path,
) -> dict[str, Any]:
    """根据素材库和原始 DOCX 图片 bytes 回填稳定指纹字段。"""

    refs = _collect_full_bid_image_refs(result)
    if not refs or not material_library_json:
        return {"enabled": False, "enriched_count": 0, "missing_count": 0}
    index = _build_image_fingerprint_index(material_library_json, raw_root)
    enriched_count = 0
    missing_count = 0
    for ref in refs:
        block = ref["block"]
        before = image_fingerprint_keys(block)
        metadata = fingerprint_metadata_for_record(block, index)
        if not metadata:
            missing_count += 1
            continue
        for key in ["canonical_image_id", "sha256", "perceptual_hash"]:
            value = metadata.get(key)
            if value and not block.get(key):
                block[key] = value
        if image_fingerprint_keys(block) != before:
            enriched_count += 1
    return {
        "enabled": True,
        "enriched_count": enriched_count,
        "missing_count": missing_count,
        "source": "material_library_docx_media",
    }


def _build_image_fingerprint_index(
    material_library_json: str | Path,
    raw_root: str | Path,
) -> ImageFingerprintIndex:
    return build_image_fingerprint_index(material_library_json, raw_root)


def _asset_metadata_for_block(block: dict[str, Any], index: ImageFingerprintIndex) -> dict[str, str]:
    return asset_metadata_for_record(block, index)


def _dedupe_full_bid_image_refs(result: dict[str, Any]) -> dict[str, Any]:
    """整本技术标范围内移除重复或明显不兼容的图片引用。"""

    all_refs = _collect_full_bid_image_refs(result)
    group_refs_by_location = _group_refs_by_location(all_refs)
    incompatible_group_block_ids = _incompatible_group_block_ids(group_refs_by_location)
    kept_asset_keys: set[str] = set()
    kept_group_asset_keys: set[str] = set()
    kept_group_locations: dict[str, tuple[int, int]] = {}
    removed: list[dict[str, Any]] = []
    for ref in sorted(all_refs, key=_image_ref_priority, reverse=True):
        block = ref["block"]
        group_key = _image_group_key(block)
        asset_keys = _image_asset_keys(block)
        group_location = (int(ref["chapter_index"]), int(ref["section_index"]))
        if id(block) in incompatible_group_block_ids:
            _remove_ref(ref)
            removed.append(_removed_image_ref(ref, "full_bid_section_image_topic_incompatible"))
            continue
        if group_key and group_key in kept_group_locations and kept_group_locations[group_key] != group_location:
            _remove_ref(ref)
            removed.append(_removed_image_ref(ref, "duplicate_image_group"))
            continue
        if group_key and asset_keys and asset_keys & kept_group_asset_keys and group_key not in kept_group_locations:
            _remove_ref(ref)
            removed.append(_removed_image_ref(ref, "duplicate_image_group_asset_overlap"))
            continue
        if asset_keys and asset_keys & kept_asset_keys and not group_key:
            _remove_ref(ref)
            removed.append(_removed_image_ref(ref, "duplicate_image_asset"))
            continue
        if _full_bid_image_topic_incompatible(ref):
            _remove_ref(ref)
            removed.append(_removed_image_ref(ref, "full_bid_section_image_topic_incompatible"))
            continue
        if group_key:
            kept_group_locations.setdefault(group_key, group_location)
            kept_group_asset_keys.update(asset_keys)
        kept_asset_keys.update(asset_keys)
    summary = {
        "enabled": True,
        "removed_count": len(removed),
        "removed_duplicate_asset_count": sum(1 for item in removed if item["reason"] == "duplicate_image_asset"),
        "removed_duplicate_group_count": sum(
            1 for item in removed if item["reason"] in {"duplicate_image_group", "duplicate_image_group_asset_overlap"}
        ),
        "removed_incompatible_count": sum(1 for item in removed if item["reason"] == "full_bid_section_image_topic_incompatible"),
        "removed": removed[:80],
    }
    if removed:
        result.setdefault("image_dedupe", {}).update(summary)
    return summary


def _group_refs_by_location(refs: list[dict[str, Any]]) -> dict[tuple[str, int, int], list[dict[str, Any]]]:
    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for ref in refs:
        group_key = _image_group_key(ref["block"])
        if not group_key:
            continue
        key = (group_key, int(ref["chapter_index"]), int(ref["section_index"]))
        groups.setdefault(key, []).append(ref)
    return groups


def _incompatible_group_block_ids(group_refs_by_location: dict[tuple[str, int, int], list[dict[str, Any]]]) -> set[int]:
    block_ids: set[int] = set()
    for refs in group_refs_by_location.values():
        if not refs:
            continue
        if any(_full_bid_image_topic_incompatible(ref) for ref in refs):
            block_ids.update(id(ref["block"]) for ref in refs)
    return block_ids


def _collect_full_bid_image_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for chapter_index, chapter in enumerate(result.get("chapters") or []):
        if not isinstance(chapter, dict):
            continue
        chapter_path = [str(part) for part in chapter.get("chapter_path") or []]
        for section_index, section in enumerate(chapter.get("sections") or []):
            if not isinstance(section, dict):
                continue
            blocks = section.get("blocks")
            if not isinstance(blocks, list):
                continue
            for block_index, block in enumerate(blocks):
                if isinstance(block, dict) and block.get("type") == "image_ref":
                    refs.append(
                        {
                            "chapter_index": chapter_index,
                            "section_index": section_index,
                            "block_index": block_index,
                            "chapter_path": chapter_path,
                            "section": section,
                            "blocks": blocks,
                            "block": block,
                        }
                    )
    return refs


def _image_ref_priority(ref: dict[str, Any]) -> tuple[int, int, int, int, int]:
    block = ref["block"]
    section_text = _section_match_text(ref["chapter_path"], ref["section"])
    compatibility = _full_bid_image_ref_compatibility_score(section_text, block)
    confidence = int(float(block.get("semantic_confidence") or 0) * 100)
    group_bonus = int(bool(block.get("image_group_id"))) * 10
    specific_section_bonus = len(_topics(section_text)) * 3
    auto_penalty = -2 if block.get("auto_inserted") else 0
    first_position_bonus = -int(ref["chapter_index"]) - int(ref["section_index"]) - int(ref["block_index"])
    return (compatibility, confidence, group_bonus, specific_section_bonus, auto_penalty + first_position_bonus)


def _remove_ref(ref: dict[str, Any]) -> None:
    block = ref["block"]
    blocks = ref["blocks"]
    for index, item in enumerate(list(blocks)):
        if item is block:
            del blocks[index]
            return


def _removed_image_ref(ref: dict[str, Any], reason: str) -> dict[str, Any]:
    block = ref["block"]
    return {
        "reason": reason,
        "chapter_path": " > ".join(ref["chapter_path"]),
        "section_heading": ref["section"].get("heading"),
        "image_id": block.get("image_id"),
        "image_asset_id": block.get("image_asset_id"),
        "source_part_name": block.get("source_part_name") or block.get("part_name"),
        "image_group_id": block.get("image_group_id"),
        "caption": block.get("caption"),
    }


def _full_bid_image_topic_incompatible(ref: dict[str, Any]) -> bool:
    section_text = _section_match_text(ref["chapter_path"], ref["section"])
    return _full_bid_image_ref_compatibility_score(section_text, ref["block"]) < 0


def _full_bid_image_ref_compatibility_score(section_text: str, block: dict[str, Any]) -> int:
    return _full_bid_image_compatibility_score(
        section_text,
        _image_match_text(block),
        image_subtopics=_image_strict_subtopics(block),
    )


def _full_bid_image_compatibility_score(
    section_text: str,
    image_text: str,
    *,
    image_subtopics: set[str] | None = None,
) -> int:
    section_topics = _topics(section_text)
    image_topics = _topics(image_text)
    section_subtopics = _subtopics(section_text)
    image_subtopics = _subtopics(image_text) if image_subtopics is None else image_subtopics
    strict_subtopics = _strict_subtopics(section_subtopics)
    if strict_subtopics:
        if not image_subtopics:
            return -14
        if not (strict_subtopics & image_subtopics):
            return -14
    if _is_general_no_process_image_section(section_text) and image_topics:
        return -20
    if "电梯" in section_topics and image_topics and "电梯" not in image_topics:
        return -20
    specific_section_topics = section_topics & _SPECIFIC_PROCESS_TOPIC_NAMES
    specific_image_topics = image_topics & _SPECIFIC_PROCESS_TOPIC_NAMES
    if specific_section_topics and image_topics and not (specific_section_topics & specific_image_topics):
        return -16
    if _ENVIRONMENT_TOPIC in section_topics and _ENVIRONMENT_TOPIC in image_topics:
        return 10
    if _SAFETY_TOPIC in section_topics and _SAFETY_TOPIC in image_topics:
        return 10
    if _is_management_section(section_text):
        if image_topics and not _is_management_image(image_text):
            return -12
        if _is_management_image(image_text):
            return 10
    if _is_deployment_section(section_text):
        if image_topics and not _is_deployment_image(image_text):
            return -10
        if _is_deployment_image(image_text):
            return 8
    if section_topics and image_topics:
        return 12 if section_topics & image_topics else -12
    if section_topics and not image_topics:
        return 0
    return 1


def _section_match_text(chapter_path: list[str], section: dict[str, Any]) -> str:
    heading = str(section.get("heading") or "")
    if _strict_subtopics(_subtopics(heading)):
        return heading
    return " ".join([*chapter_path, heading])


def _image_match_text(block: dict[str, Any]) -> str:
    parts = [
        block.get("caption"),
        block.get("group_title"),
        block.get("group_semantic_text"),
        block.get("semantic_text"),
        block.get("nearby_text"),
        " ".join(str(item) for item in block.get("caption_candidates") or []),
        " ".join(str(part) for part in block.get("source_section_path") or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _image_strict_subtopics(block: dict[str, Any]) -> set[str]:
    primary = _subtopics(
        " ".join(
            str(part)
            for part in [
                block.get("caption"),
                block.get("group_title"),
                block.get("group_semantic_text"),
            ]
            if part
        )
    )
    if primary:
        return primary
    semantic = _subtopics(block.get("semantic_text") or "")
    if semantic:
        return semantic
    return _subtopics(" ".join(str(item) for item in block.get("caption_candidates") or []))


def _strict_subtopics(subtopics: set[str]) -> set[str]:
    return subtopics & _STRICT_SUBTOPIC_NAMES


def _subtopics(text: str) -> set[str]:
    value = str(text or "")
    groups = {
        "临边洞口": ["临边", "洞口", "防护栏杆", "楼层边", "预留洞"],
        "临时用电": ["临时用电", "施工用电", "配电箱", "开关箱", "TN-S", "漏电保护", "三级配电"],
        "消防": ["消防", "灭火器", "消防泵", "消防管", "动火"],
        "机械塔吊": ["机械", "塔吊", "起重", "吊装", "设备"],
        "个人防护": ["安全帽", "安全带", "防护用品", "劳保"],
        "噪声": ["噪声", "噪音", "声屏障", "扰民"],
        "水污染": ["水污染", "污水", "沉淀池", "洗车槽", "排水", "废水"],
        "光污染": ["光污染", "照明", "眩光"],
        "固废": ["固体废弃物", "垃圾", "危废", "分类"],
        "扬尘大气": ["扬尘", "大气", "雾炮", "喷淋", "降尘", "围挡"],
        "绿色节能": ["绿色", "节能", "四节一环保", "节水", "节材", "节地"],
    }
    return {name for name, terms in groups.items() if any(term in value for term in terms)}


def _topics(text: str) -> set[str]:
    value = str(text or "")
    return {
        topic
        for topic, terms in _PROCESS_TOPIC_TERMS.items()
        if any(term in value for term in terms)
    }


def _is_general_no_process_image_section(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in _GENERAL_NO_PROCESS_IMAGE_TERMS)


def _is_management_section(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in ["项目管理目标", "管理目标", "质量目标", "安全目标", "管理体系", "组织机构", "责任分工"])


def _is_management_image(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in _MANAGEMENT_TERMS)


def _is_deployment_section(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in ["施工方案总体安排", "总体施工部署", "施工部署", "总体安排", "流水段", "专业穿插"])


def _is_deployment_image(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in _DEPLOYMENT_TERMS)


def _image_group_key(block: dict[str, Any]) -> str:
    return str(block.get("image_group_id") or "").strip()


def _image_asset_keys(block: dict[str, Any]) -> set[str]:
    return {
        key
        for key in [
            _lookup_key("canonical_image_id", block.get("canonical_image_id")),
            _lookup_key("sha256", block.get("sha256")),
            _lookup_key("perceptual_hash", block.get("perceptual_hash")),
            _lookup_key("image_asset_id", block.get("image_asset_id")),
            _lookup_key("source_part_name", block.get("source_part_name") or block.get("part_name")),
            _lookup_key("image_id", block.get("image_id")),
        ]
        if key
    }


def _package_summary_blocks(chapter: dict[str, Any]) -> list[dict[str, str]]:
    check = chapter.get("score_response_check") or {}
    summary = str(check.get("response_summary") or "").strip()
    if not summary:
        summary = "本节正文已生成，以下内容为可编辑技术标初稿。"
    return [{"type": "paragraph", "text": summary}]


def _placeholder_package_sections(package: dict[str, Any]) -> list[dict[str, Any]]:
    unit = package.get("generation_unit") or {}
    path = _chapter_path(unit)
    children = [str(item).strip() for item in unit.get("child_headings") or [] if str(item).strip()]
    if len(path) > 1:
        sections = [
            {
                "heading": path[-1],
                "level": 2,
                "blocks": [{"type": "paragraph", "text": PACKAGE_PLACEHOLDER_TEXT}],
            }
        ]
        sections.extend(_placeholder_child_section(child, level=3) for child in children)
        return sections
    if children:
        return [_placeholder_child_section(child, level=2) for child in children]
    return [
        {
            "heading": "章节正文",
            "level": 2,
            "blocks": [{"type": "paragraph", "text": PLACEHOLDER_TEXT}],
        }
    ]


def _placeholder_child_section(heading: str, *, level: int) -> dict[str, Any]:
    return {"heading": heading, "level": level, "blocks": [{"type": "paragraph", "text": PLACEHOLDER_TEXT}]}


def _placeholder_review_items(package: dict[str, Any]) -> list[dict[str, str]]:
    unit = package.get("generation_unit") or {}
    path_text = " > ".join(_chapter_path(unit)) or str(unit.get("unit_id") or "未命名章节")
    return [
        {
            "severity": "medium",
            "type": "generation_pending",
            "message": f"{path_text} 尚未生成正文，需继续执行章节生成并人工复核评分点响应。",
        }
    ]


def _append_review_items(chapter: dict[str, Any], items: list[Any], path: list[str]) -> None:
    prefix = " > ".join(path)
    target = chapter.setdefault("review_items", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        copied = copy.deepcopy(item)
        message = str(copied.get("message") or "").strip()
        if prefix and message and prefix not in message:
            copied["message"] = f"{prefix}：{message}"
        target.append(copied)


def _normalize_output_mode(output_mode: str) -> str:
    value = str(output_mode or REVIEW_DOCX_MODE).strip().lower()
    if value in {FINAL_DOCX_MODE, "正式版", "final_draft"}:
        return FINAL_DOCX_MODE
    return REVIEW_DOCX_MODE


def _append_source_usage(chapter: dict[str, Any], generated_chapter: dict[str, Any]) -> None:
    source_usage = chapter.setdefault("source_usage", [])
    for item in generated_chapter.get("source_usage") or []:
        if isinstance(item, dict):
            source_usage.append(copy.deepcopy(item))


def _package_summary(package: dict[str, Any], status: str) -> dict[str, Any]:
    unit = package.get("generation_unit") or {}
    score_point = package.get("score_point") or {}
    return {
        "unit_id": unit.get("unit_id") or "",
        "target_node_id": unit.get("target_node_id") or "",
        "chapter_path": _chapter_path(unit),
        "unit_type": unit.get("unit_type") or "",
        "score_point_raw": score_point.get("score_point_raw") or "",
        "status": status,
    }


def _chapter_path(unit: dict[str, Any]) -> list[str]:
    return [str(part).strip() for part in unit.get("chapter_path") or [] if str(part).strip()]


def _lookup_key(kind: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return f"{kind}:{text}"


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).replace(microsecond=0).isoformat()


def _first_value(results: list[dict[str, Any]], key: str) -> Any:
    for result in results:
        value = result.get(key)
        if value:
            return value
    return None


def _collect_warnings(results: list[dict[str, Any]]) -> list[Any]:
    warnings: list[Any] = []
    for result in results:
        warnings.extend(result.get("warnings") or [])
    return warnings
