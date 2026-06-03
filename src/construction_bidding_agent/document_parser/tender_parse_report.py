"""构建招标文件解析汇总结果和 Markdown 报告。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "tender_parse_result_v0.1"
PARSER_VERSION = "stage0"
DEFAULT_TZ = "Asia/Shanghai"

PROJECT_FIELD_MAP = {
    "project_name": ("project_name", "项目名称"),
    "location": ("construction_location", "建设地点"),
    "scale": ("construction_scale", "建设规模"),
    "scope": ("tender_scope", "招标范围"),
    "duration": ("duration_requirement", "工期要求"),
    "quality": ("quality_requirement", "质量要求"),
    "safety_civilized": ("safety_civilization_requirement", "安全文明要求"),
}

TASK_FAILURE_RULES = {
    "project_info_extraction_input": {
        "level": "high",
        "review_priority": "high",
        "title": "项目基础信息抽取",
        "impact": "解析报告可继续生成，但项目基础信息可能不完整。",
        "suggested_action": "单独重跑项目基础信息抽取；人工复核项目名称、地点、规模、范围、工期、质量和安全文明要求。",
        "blocks_next_stage": False,
    },
    "score_points_extraction_input": {
        "level": "blocking",
        "review_priority": "blocking",
        "title": "技术标评分点抽取",
        "impact": "后续技术标一级目录生成必须暂停。",
        "suggested_action": "单独重跑技术标评分点抽取；人工确认评标办法前附表中的技术评分点原文后再继续目录生成。",
        "blocks_next_stage": True,
    },
    "technical_requirements_extraction_input": {
        "level": "high",
        "review_priority": "high",
        "title": "技术标准与编制要求抽取",
        "impact": "解析报告可继续生成，但正文生成前需要复核技术标准、发包人要求和编制约束。",
        "suggested_action": "单独重跑技术标准与编制要求抽取；人工复核技术标准和发包人要求区域。",
        "blocks_next_stage": False,
    },
}

SUCCESS_TASK_STATUSES = {"completed", "fallback_completed"}


def build_tender_parse_result_from_files(
    extraction_inputs_json: str | Path,
    project_technical_llm_json: str | Path,
    score_points_llm_json: str | Path,
    *,
    generated_at: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    extraction_inputs = _read_json(extraction_inputs_json)
    project_technical_run = _read_json(project_technical_llm_json)
    score_points_run = _read_json(score_points_llm_json)
    return build_tender_parse_result(
        extraction_inputs,
        project_technical_run,
        score_points_run,
        generated_at=generated_at,
        job_id=job_id,
    )


def build_tender_parse_result(
    extraction_inputs: dict[str, Any],
    project_technical_run: dict[str, Any],
    score_points_run: dict[str, Any],
    *,
    generated_at: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _now_iso()
    ref_index = _build_ref_index(extraction_inputs)
    project_info_task = _task(project_technical_run, "project_info_extraction_input")
    technical_task = _task(project_technical_run, "technical_requirements_extraction_input")
    score_task = _task(score_points_run, "score_points_extraction_input")
    project_data = project_info_task.get("parsed_json") or {}
    technical_data = technical_task.get("parsed_json") or {}
    score_data = dict(score_task.get("parsed_json") or {})
    quality_gate = _score_quality_gate_from_task(score_task)
    if quality_gate and not score_data.get("quality_gate"):
        score_data["quality_gate"] = quality_gate

    warnings = _collect_warnings(
        extraction_inputs,
        [project_info_task, technical_task, score_task],
        ref_index,
    )
    technical_score_points = _technical_score_points(score_data, ref_index)
    project_info = _project_info(project_data, ref_index)
    technical_requirements = _technical_bid_requirements(technical_data, ref_index)
    technical_standards = _technical_standards(technical_data, ref_index)
    conflicts = _conflicts(technical_data, [project_info_task, technical_task, score_task], ref_index)
    review_items = _review_items(
        project_info=project_info,
        technical_score_points=technical_score_points,
        technical_bid_requirements=technical_requirements,
        technical_standards=technical_standards,
        conflicts=conflicts,
        warnings=warnings,
    )
    execution = _execution_summary(
        project_technical_run,
        score_points_run,
        expected_tasks=[project_info_task, technical_task, score_task],
    )
    status = _parse_job_status(warnings, review_items)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "parse_job": {
            "job_id": job_id or _default_job_id(extraction_inputs, generated),
            "parser_version": PARSER_VERSION,
            "status": status,
            "started_at": None,
            "completed_at": generated,
        },
        "execution": execution,
        "input_files": [_input_file(extraction_inputs)],
        "document_profile": _document_profile(extraction_inputs),
        "structure_index": {"detected_sections": _detected_sections(extraction_inputs)},
        "project_type": _project_type(project_data),
        "project_info": project_info,
        "technical_score_points": technical_score_points,
        "technical_bid_requirements": technical_requirements,
        "technical_standards": technical_standards,
        "conflicts": conflicts,
        "review_items": review_items,
        "warnings": warnings,
    }


def write_tender_parse_report_outputs(
    result: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        report_target.write_text(render_tender_parse_report(result), encoding="utf-8")
    except Exception as exc:
        error_path = report_target.with_suffix(report_target.suffix + ".render_error.txt")
        error_path.write_text(
            f"Report render failed. JSON result was kept at: {json_target}\nError: {exc}\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            f"Tender parse JSON was written to {json_target}, but Markdown report rendering failed. "
            f"Retry report rendering after fixing the render error: {exc}"
        ) from exc


def render_tender_parse_report(result: dict[str, Any]) -> str:
    project_info = result.get("project_info") or {}
    project_type = result.get("project_type") or {}
    lines = [
        "# 招标文件解析报告",
        "",
        f"- 解析时间：{result.get('generated_at') or ''}",
        f"- 项目名称：{_field_value(project_info.get('project_name')) or '未明确'}",
        f"- 项目类型：{_project_type_label(project_type.get('value'))}",
        "",
        "## 一、项目信息",
        "",
        "| 字段 | 抽取结果 |",
        "|---|---|",
    ]
    for field_key, label in [
        ("project_name", "项目名称"),
        ("construction_location", "建设地点"),
        ("construction_scale", "建设规模"),
        ("tender_scope", "招标范围"),
        ("duration_requirement", "工期要求"),
        ("quality_requirement", "质量要求"),
        ("safety_civilization_requirement", "安全文明要求"),
    ]:
        field = project_info.get(field_key) or {}
        lines.append(f"| {label} | {_cell(_compact_report_text(field.get('value')))} |")

    lines.extend(["", "## 二、技术要求信息", ""])
    requirement_rows = []
    for item in result.get("technical_bid_requirements") or []:
        requirement_rows.append((item.get("category") or "技术标编制要求", item.get("content")))
    for item in result.get("technical_standards") or []:
        requirement_rows.append((item.get("category") or "技术标准和要求", item.get("summary")))
    if requirement_rows:
        lines.extend(["| 类别 | 内容摘要 |", "|---|---|"])
        for category, content in requirement_rows[:8]:
            lines.append(f"| {_cell(category)} | {_cell(_compact_report_text(content, limit=180))} |")
    else:
        lines.append("未发现明确技术标准和要求，后续正文生成前建议人工复核招标文件相关章节。")

    review_items = result.get("review_items") or []
    if review_items:
        lines.extend(["", "## 三、待复核事项", ""])
        for item in review_items[:5]:
            lines.append(f"- {_compact_report_text(item.get('item'), limit=80)}")
    lines.append("")
    return "\n".join(lines)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _execution_summary(
    project_technical_run: dict[str, Any],
    score_points_run: dict[str, Any],
    *,
    expected_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runs = [project_technical_run, score_points_run]
    unique_runs: list[dict[str, Any]] = []
    seen_run_keys: set[tuple[Any, ...]] = set()
    for run in runs:
        run_key = _run_dedupe_key(run)
        if run_key in seen_run_keys:
            continue
        seen_run_keys.add(run_key)
        unique_runs.append(run)
    tasks = []
    seen_task_keys: set[str] = set()
    for run in unique_runs:
        for task in run.get("tasks") or []:
            task_key = task.get("task_key")
            if task_key in seen_task_keys:
                continue
            if task_key:
                seen_task_keys.add(task_key)
            tasks.append(
                {
                    "task_key": task_key,
                    "task_title": task.get("task_title"),
                    "status": task.get("status"),
                    "started_at": task.get("started_at"),
                    "completed_at": task.get("completed_at"),
                    "duration_seconds": task.get("duration_seconds"),
                    "input_estimated_tokens": task.get("input_estimated_tokens"),
                    "output_summary": (task.get("validation") or {}).get("summary"),
                    "error": task.get("error"),
                    "cache_status": task.get("cache_status"),
                }
            )
    for expected_task in expected_tasks or []:
        task_key = expected_task.get("task_key")
        if not task_key or task_key in seen_task_keys:
            continue
        seen_task_keys.add(task_key)
        tasks.append(
            {
                "task_key": task_key,
                "task_title": expected_task.get("task_title"),
                "status": expected_task.get("status"),
                "started_at": expected_task.get("started_at"),
                "completed_at": expected_task.get("completed_at"),
                "duration_seconds": expected_task.get("duration_seconds"),
                "input_estimated_tokens": expected_task.get("input_estimated_tokens"),
                "output_summary": (expected_task.get("validation") or {}).get("summary"),
                "error": expected_task.get("error"),
                "cache_status": expected_task.get("cache_status"),
            }
        )
    task_durations = [
        task.get("duration_seconds")
        for task in tasks
        if isinstance(task.get("duration_seconds"), int | float)
    ]
    run_durations = [
        run.get("duration_seconds")
        for run in unique_runs
        if isinstance(run.get("duration_seconds"), int | float)
    ]
    modes = [run.get("execution_mode") for run in unique_runs if run.get("execution_mode")]
    mode = "parallel" if "parallel" in modes else (modes[0] if modes else "unknown")
    failed_tasks = [task for task in tasks if task.get("status") not in SUCCESS_TASK_STATUSES]
    blocking_failed_tasks = [
        task
        for task in failed_tasks
        if _task_failure_rule(str(task.get("task_key") or "")).get("blocks_next_stage") is True
    ]
    score_quality_blocking = _has_score_quality_blocking(tasks)
    return {
        "mode": mode,
        "timing": {
            "document_parse_seconds": None,
            "input_build_seconds": None,
            "llm_wall_clock_seconds": sum(run_durations) if run_durations else None,
            "llm_task_duration_sum_seconds": sum(task_durations) if task_durations else None,
            "report_build_seconds": None,
            "total_seconds": None,
        },
        "task_count": len(tasks),
        "completed_task_count": sum(1 for task in tasks if task.get("status") in SUCCESS_TASK_STATUSES),
        "llm_completed_task_count": sum(1 for task in tasks if task.get("status") == "completed"),
        "fallback_task_count": sum(1 for task in tasks if task.get("status") == "fallback_completed"),
        "failed_task_count": len(failed_tasks),
        "failed_tasks": [
            {
                "task_key": task.get("task_key"),
                "task_title": task.get("task_title"),
                "status": task.get("status"),
                "error": task.get("error"),
            }
            for task in failed_tasks
        ],
        "has_blocking_failure": bool(blocking_failed_tasks) or score_quality_blocking,
        "can_generate_outline": not blocking_failed_tasks and not score_quality_blocking,
        "tasks": tasks,
    }


def _run_dedupe_key(run: dict[str, Any]) -> tuple[Any, ...]:
    task_keys = tuple(task.get("task_key") for task in run.get("tasks") or [])
    return (
        run.get("schema_version"),
        run.get("source_input_path"),
        run.get("source_file"),
        run.get("execution_mode"),
        run.get("started_at"),
        run.get("completed_at"),
        run.get("duration_seconds"),
        task_keys,
    )


def _has_score_quality_blocking(tasks: list[dict[str, Any]]) -> bool:
    for task in tasks:
        if task.get("task_key") != "score_points_extraction_input":
            continue
        quality_gate = _score_quality_gate_from_task(task)
        if quality_gate.get("blocking") is True:
            return True
    return False


def _score_quality_gate_from_task(task: dict[str, Any]) -> dict[str, Any]:
    parsed = task.get("parsed_json") or {}
    validation = task.get("validation") or {}
    quality_gate = parsed.get("quality_gate") or validation.get("quality_gate") or {}
    return quality_gate if isinstance(quality_gate, dict) else {}


def _task(run: dict[str, Any], task_key: str) -> dict[str, Any]:
    for task in run.get("tasks") or []:
        if task.get("task_key") == task_key:
            return task
    return {
        "task_key": task_key,
        "task_title": _task_failure_rule(task_key).get("title") or task_key,
        "status": "missing",
        "parsed_json": {},
        "validation": {"issues": []},
        "error": "Task result is missing from LLM extraction output.",
    }


def _build_ref_index(extraction_inputs: dict[str, Any]) -> dict[str, Any]:
    source_info = {
        "file_id": extraction_inputs.get("file_id"),
        "file_name": extraction_inputs.get("file_name"),
        "file_type": extraction_inputs.get("file_type"),
    }
    cells: dict[str, dict[str, Any]] = {}
    blocks: dict[str, dict[str, Any]] = {}
    for package in extraction_inputs.get("packages") or []:
        for block in package.get("block_refs") or []:
            block_index = block.get("block_index")
            if block_index is None:
                continue
            blocks[f"B{block_index}"] = {
                **source_info,
                "block_index": block_index,
                "page_no": block.get("page_no"),
                "paragraph_index": block.get("paragraph_index"),
                "table_index": block.get("table_index"),
                "text_excerpt": block.get("text_preview") or "",
            }
        for cell in package.get("cell_refs") or []:
            cell_id = cell.get("cell_id")
            if not cell_id:
                continue
            cells[cell_id] = {
                **source_info,
                "block_index": cell.get("block_index"),
                "page_no": cell.get("page_no"),
                "table_index": cell.get("table_index"),
                "row_index": cell.get("row_index"),
                "cell_index": cell.get("cell_index"),
                "text_excerpt": cell.get("text_raw") or "",
            }
    return {
        "source": source_info,
        "blocks": blocks,
        "cells": cells,
    }


def _source_refs(ref: Any, ref_index: dict[str, Any]) -> list[dict[str, Any]]:
    ref_id = (ref or {}).get("id") if isinstance(ref, dict) else None
    if not isinstance(ref_id, str):
        return []
    if ref_id in ref_index["cells"]:
        return [ref_index["cells"][ref_id]]
    if ref_id in ref_index["blocks"]:
        return [ref_index["blocks"][ref_id]]
    source_refs = []
    for block_id in re.findall(r"B\d+(?!_R)", ref_id):
        block_ref = ref_index["blocks"].get(block_id)
        if block_ref:
            source_refs.append(block_ref)
    if source_refs:
        return source_refs
    return [
        {
            **ref_index["source"],
            "text_excerpt": ref_id,
        }
    ]


def _input_file(extraction_inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": extraction_inputs.get("file_id"),
        "file_name": extraction_inputs.get("file_name"),
        "file_type": extraction_inputs.get("file_type"),
        "source_path": extraction_inputs.get("source_path"),
        "parse_status": "success",
        "warnings": extraction_inputs.get("warnings") or [],
    }


def _document_profile(extraction_inputs: dict[str, Any]) -> dict[str, Any]:
    file_type = extraction_inputs.get("file_type")
    block_keys = set()
    paragraph_count = 0
    table_keys = set()
    for package in extraction_inputs.get("packages") or []:
        for block in package.get("block_refs") or []:
            block_index = block.get("block_index")
            if block_index in block_keys:
                continue
            block_keys.add(block_index)
            if block.get("block_type") == "paragraph":
                paragraph_count += 1
            if block.get("block_type") == "table":
                table_keys.add((block.get("table_index"), block_index))
    return {
        "file_count": 1,
        "has_word": file_type == "docx",
        "has_pdf": file_type == "pdf",
        "has_scanned_pdf": False,
        "paragraph_count": paragraph_count,
        "table_count": len(table_keys),
        "image_count": 0,
        "header_footer_ignored": True,
        "toc_detected": False,
    }


def _detected_sections(extraction_inputs: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    for package in extraction_inputs.get("packages") or []:
        for region in package.get("regions") or []:
            key = region.get("region_key")
            if not key or key in seen:
                continue
            seen.add(key)
            sections.append(
                {
                    "section_key": key,
                    "title": region.get("region_title") or key,
                    "found": True,
                    "source_refs": region.get("source_refs") or [],
                    "note": region.get("note") or "",
                }
            )
    return sections


def _project_type(project_data: dict[str, Any]) -> dict[str, Any]:
    value = project_data.get("project_type") or "construction"
    contains_design = bool(project_data.get("contains_design_task"))
    if value == "epc" or contains_design:
        evidence = "项目基础信息识别到 EPC 或设计工作内容，后续设计方案与施工方案应分开处理。"
    else:
        evidence = "项目基础信息未识别到设计工作内容，按施工类项目处理。"
    return {
        "value": value,
        "allowed_values": ["construction", "epc"],
        "confidence": project_data.get("project_type_confidence"),
        "evidence_summary": evidence,
        "source_refs": [],
        "review_required": bool(project_data.get("project_type_needs_confirmation")),
    }


def _project_info(project_data: dict[str, Any], ref_index: dict[str, Any]) -> dict[str, Any]:
    fields = project_data.get("fields") or {}
    result: dict[str, Any] = {}
    for raw_key, (target_key, _label) in PROJECT_FIELD_MAP.items():
        raw_field = fields.get(raw_key) or {}
        value = raw_field.get("value")
        confidence = raw_field.get("confidence")
        review_required = bool(raw_field.get("needs_confirmation")) or not value or _low_confidence(confidence)
        result[target_key] = {
            "value": value or "未明确",
            "source_refs": _source_refs(raw_field.get("field_ref"), ref_index),
            "confidence": confidence,
            "review_required": review_required,
            "confirmation_reason": raw_field.get("confirmation_reason"),
        }
    return result


def _technical_score_points(score_data: dict[str, Any], ref_index: dict[str, Any]) -> list[dict[str, Any]]:
    points = score_data.get("system_final_score_points") or []
    raw_points = score_data.get("score_points") or []
    quality_issues_by_index = _score_quality_issues_by_index(score_data)
    result = []
    for index, point in enumerate(points, start=1):
        raw_point = raw_points[index - 1] if index - 1 < len(raw_points) and isinstance(raw_points[index - 1], dict) else {}
        notes = [
            note
            for note in [point.get("confirmation_reason")]
            if isinstance(note, str) and note.strip()
        ]
        notes.extend(issue["message"] for issue in quality_issues_by_index.get(index, []) if issue["severity"] != "blocking")
        blocking_quality_issues = [
            issue["message"]
            for issue in quality_issues_by_index.get(index, [])
            if issue["severity"] == "blocking"
        ]
        notes.extend(blocking_quality_issues)
        raw = point.get("score_point_raw")
        title = point.get("level_1_heading_text") or raw
        if raw and title and raw != title:
            notes.append("PDF/表格抽取存在版式空格，一级目录候选已做空格归一化。")
        result.append(
            {
                "score_point_id": f"TSP{index:03d}",
                "original_text": raw,
                "catalog_level_1_title": title,
                "score_value": point.get("score"),
                "score_rule": point.get("description"),
                "source_refs": _source_refs(point.get("score_point_ref"), ref_index),
                "must_use_original_text_as_heading": True,
                "confidence": point.get("confidence", raw_point.get("confidence")),
                "review_required": bool(point.get("needs_confirmation")) or bool(quality_issues_by_index.get(index)),
                "blocks_outline_generation": bool(blocking_quality_issues),
                "notes": notes,
            }
        )
    return result


def _technical_bid_requirements(technical_data: dict[str, Any], ref_index: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(technical_data.get("requirements") or [], start=1):
        confidence = item.get("confidence")
        result.append(
            {
                "requirement_id": f"TBR{index:03d}",
                "category": item.get("requirement_type") or "other",
                "content": item.get("model_observed_text"),
                "source_refs": _source_refs(item.get("requirement_ref"), ref_index),
                "confidence": confidence,
                "review_required": bool(item.get("needs_confirmation")) or _low_confidence(confidence),
            }
        )
    return result


def _technical_standards(technical_data: dict[str, Any], ref_index: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(technical_data.get("technical_standards") or [], start=1):
        target_hint = item.get("target_section_hint")
        result.append(
            {
                "standard_id": f"TS{index:03d}",
                "category": item.get("standard_type") or "other",
                "summary": item.get("model_observed_text"),
                "original_excerpt": item.get("model_observed_text"),
                "source_refs": _source_refs(item.get("standard_ref"), ref_index),
                "generation_impact": f"生成“{target_hint}”相关章节时应响应该要求。" if target_hint else "生成正文时应作为约束参考。",
                "confidence": item.get("confidence"),
                "review_required": bool(item.get("needs_confirmation")) or _low_confidence(item.get("confidence")),
            }
        )
    return result


def _conflicts(
    technical_data: dict[str, Any],
    tasks: list[dict[str, Any]],
    ref_index: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for task in tasks:
        for issue in (task.get("validation") or {}).get("issues") or []:
            if "project_info_" in str(issue):
                continue
            conflicts.append(
                {
                    "conflict_id": f"C{len(conflicts) + 1:03d}",
                    "type": "validation_issue",
                    "field": task.get("task_key"),
                    "description": str(issue),
                    "source_refs": [],
                    "suggested_action": "人工复核对应抽取结果和来源。",
                }
            )
    for item in technical_data.get("technical_risks") or []:
        conflicts.append(
            {
                "conflict_id": f"C{len(conflicts) + 1:03d}",
                "type": item.get("risk_type") or "technical_risk",
                "field": item.get("applies_to"),
                "description": item.get("model_observed_text"),
                "source_refs": _source_refs(item.get("risk_ref"), ref_index),
                "suggested_action": "人工确认该风险是否会影响技术标目录或正文生成。",
            }
        )
    return conflicts


def _score_quality_issues_by_index(score_data: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    quality_gate = score_data.get("quality_gate") or {}
    for issue in quality_gate.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        index = _issue_score_point_index(issue.get("message"))
        if index is None:
            continue
        result.setdefault(index, []).append(issue)
    return result


def _issue_score_point_index(message: Any) -> int | None:
    if not isinstance(message, str):
        return None
    match = re.search(r"第\s*(\d+)\s*个评分点", message)
    if not match:
        return None
    return int(match.group(1))


def _collect_warnings(
    extraction_inputs: dict[str, Any],
    tasks: list[dict[str, Any]],
    ref_index: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    def add(level: str, message: str, ref: Any = None) -> None:
        warnings.append(
            {
                "warning_id": f"W{len(warnings) + 1:03d}",
                "level": level,
                "message": message,
                "source_refs": _source_refs(ref, ref_index),
            }
        )

    for warning in extraction_inputs.get("warnings") or []:
        add("medium", str(warning))
    for task in tasks:
        score_quality_issues: list[dict[str, Any]] = []
        if task.get("task_key") == "score_points_extraction_input":
            quality_gate = _score_quality_gate_from_task(task)
            score_quality_issues = [
                issue for issue in quality_gate.get("issues") or [] if isinstance(issue, dict)
            ]
        if task.get("status") not in SUCCESS_TASK_STATUSES:
            rule = _task_failure_rule(task.get("task_key") or "")
            title = rule.get("title") or task.get("task_title") or task.get("task_key")
            status = task.get("status") or "missing"
            error = task.get("error") or "No task output was available."
            add(
                rule.get("level", "high"),
                f"{title}任务{status}：{error} {rule.get('impact', '')} 建议：{rule.get('suggested_action', '')}",
            )
            for issue in score_quality_issues:
                level = "blocking" if issue.get("severity") == "blocking" else "high"
                add(level, issue.get("message") or str(issue))
            continue
        parsed = task.get("parsed_json") or {}
        for warning in parsed.get("warnings") or []:
            if isinstance(warning, dict):
                add(_warning_level(warning), warning.get("message") or str(warning), warning.get("ref"))
            else:
                add("medium", str(warning))
        if task.get("task_key") == "score_points_extraction_input":
            for issue in score_quality_issues:
                level = "blocking" if issue.get("severity") == "blocking" else "high"
                add(level, issue.get("message") or str(issue))
    return warnings


def _parse_job_status(warnings: list[dict[str, Any]], review_items: list[dict[str, Any]]) -> str:
    if any(warning.get("level") == "blocking" for warning in warnings):
        return "failed"
    if warnings or review_items:
        return "completed_with_warnings"
    return "completed"


def _task_failure_rule(task_key: str) -> dict[str, Any]:
    return TASK_FAILURE_RULES.get(
        task_key,
        {
            "level": "high",
            "review_priority": "high",
            "title": task_key or "未知抽取",
            "impact": "解析结果可能不完整。",
            "suggested_action": "单独重跑该抽取任务并人工复核输出。",
            "blocks_next_stage": False,
        },
    )


def _render_execution_task_section(execution: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## 0. LLM Task Status",
        "",
        f"- Outline generation: {'allowed' if execution.get('can_generate_outline', True) else 'blocked'}",
        "",
        "| Task | Status | Duration | Error or action |",
        "| Task | Status | Cache | Duration | Error or action |",
        "|---|---|---|---:|---|",
    ]
    tasks = execution.get("tasks") or []
    if not tasks:
        lines.append("| No LLM task | unknown |  |  | No task metadata available. |")
        lines.append("")
        return lines

    for task in tasks:
        rule = _task_failure_rule(task.get("task_key") or "")
        if task.get("status") in SUCCESS_TASK_STATUSES:
            action = task.get("output_summary") or ""
        else:
            action = task.get("error") or rule.get("suggested_action") or ""
        lines.append(
            f"| {_cell(task.get('task_title') or task.get('task_key'))} | "
            f"{_cell(task.get('status'))} | {_cell(task.get('cache_status') or '')} | "
            f"{_format_seconds(task.get('duration_seconds'))} | "
            f"{_cell(action)} |"
        )
    lines.append("")
    return lines


def _review_items(
    *,
    project_info: dict[str, Any],
    technical_score_points: list[dict[str, Any]],
    technical_bid_requirements: list[dict[str, Any]],
    technical_standards: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key, field in project_info.items():
        if field.get("review_required"):
            label = _project_label(key)
            items.append(
                _review_item(
                    len(items) + 1,
                    "high" if field.get("value") == "未明确" else "medium",
                    f"复核{label}",
                    field.get("confirmation_reason") or "字段缺失或置信度较低。",
                    "人工对照招标公告和投标人须知前附表确认。",
                )
            )
    for point in technical_score_points:
        if point.get("review_required"):
            priority = "blocking" if point.get("blocks_outline_generation") else "high"
            items.append(
                _review_item(
                    len(items) + 1,
                    priority,
                    f"复核评分点：{point.get('catalog_level_1_title')}",
                    "; ".join(point.get("notes") or []) or "评分点来源需要确认。",
                    "人工对照评标办法前附表确认评分点原文。",
                )
            )
    for collection, label in [
        (technical_bid_requirements, "技术标编制要求"),
        (technical_standards, "技术标准和要求"),
    ]:
        for item in collection:
            if item.get("review_required"):
                items.append(
                    _review_item(
                        len(items) + 1,
                        "medium",
                        f"复核{label}",
                        item.get("content") or item.get("summary") or "存在需确认内容。",
                        "人工确认该条要求是否完整、是否影响正文生成。",
                    )
                )
    for conflict in conflicts:
        items.append(
            _review_item(
                len(items) + 1,
                "medium",
                "复核冲突与疑点",
                conflict.get("description") or "",
                conflict.get("suggested_action") or "人工复核。",
            )
        )
    for warning in warnings:
        level = warning.get("level")
        if level in {"high", "blocking"}:
            items.append(
                _review_item(
                    len(items) + 1,
                    "high" if level == "high" else "blocking",
                    "复核解析警告",
                    warning.get("message") or "",
                    "人工确认是否影响后续目录和正文生成。",
                )
            )
    return items


def _review_item(index: int, priority: str, item: str, reason: str, suggested_action: str) -> dict[str, Any]:
    return {
        "review_id": f"R{index:03d}",
        "priority": priority,
        "item": item,
        "reason": reason,
        "suggested_action": suggested_action,
    }


def _warning_level(warning: dict[str, Any]) -> str:
    warning_type = str(warning.get("type") or "")
    if warning_type in {"cross_page_content", "missing_content"}:
        return "high"
    if warning_type in {
        "score_points_empty",
        "score_point_raw_missing",
        "not_technical_bid_score_point",
        "score_point_ref_missing",
    }:
        return "blocking"
    if warning_type in {
        "possible_non_technical_score_point",
        "duplicate_score_point_title",
        "score_total_over_100",
        "score_point_needs_confirmation",
        "level_1_title_normalized",
    }:
        return "high"
    if warning_type in {"missing_field", "table_structure_change"}:
        return "medium"
    return "info"


def _project_label(key: str) -> str:
    labels = {
        target_key: label
        for _raw_key, (target_key, label) in PROJECT_FIELD_MAP.items()
    }
    return labels.get(key, key)


def _low_confidence(value: Any) -> bool:
    return isinstance(value, int | float) and value < 0.7


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")


def _default_job_id(extraction_inputs: dict[str, Any], generated_at: str) -> str:
    file_id = extraction_inputs.get("file_id") or "tender"
    digits = re.sub(r"\D+", "", generated_at)[:14]
    return f"{file_id}_{digits}"


def _field_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return ""


def _project_type_label(value: Any) -> str:
    if value == "epc":
        return "EPC 项目"
    if value == "construction":
        return "施工项目"
    return "未明确"


def _compact_report_text(value: Any, *, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.replace("\r", "\n").split())
    if not text:
        return "未明确"
    return text if len(text) <= limit else f"{text[:limit]}..."


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _confidence(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value:.2f}"
    return ""


def _format_seconds(value: Any) -> str:
    if not isinstance(value, int | float):
        return ""
    if value < 60:
        return f"{value:.1f}秒"
    minutes = int(value // 60)
    seconds = int(round(value % 60))
    return f"{minutes}分{seconds:02d}秒"


def _render_sources(source_refs: Any) -> str:
    refs = source_refs or []
    if not isinstance(refs, list):
        return ""
    rendered = [_render_source(ref) for ref in refs[:3]]
    if len(refs) > 3:
        rendered.append(f"另 {len(refs) - 3} 处")
    return "; ".join(item for item in rendered if item)


def _render_source(ref: dict[str, Any]) -> str:
    if not isinstance(ref, dict):
        return ""
    parts = [str(ref.get("file_name") or "")]
    if ref.get("page_no") is not None:
        parts.append(f"第{ref.get('page_no')}页")
    if ref.get("block_index") is not None:
        parts.append(f"B{ref.get('block_index')}")
    if ref.get("table_index") is not None:
        parts.append(f"T{ref.get('table_index')}")
    if ref.get("row_index") is not None:
        parts.append(f"R{ref.get('row_index')}")
    if ref.get("cell_index") is not None:
        parts.append(f"C{ref.get('cell_index')}")
    return " / ".join(part for part in parts if part)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
