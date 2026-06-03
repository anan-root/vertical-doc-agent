"""项目主流程的轻量真实执行器。

这里先把“上传招标文件 -> 解析报告 -> 目录 -> 正文初稿”接成可落盘、
可被前端读取的闭环。重型 LLM 抽取和完整 Word 生成后续可以替换到
同一任务接口下。
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from construction_bidding_agent.chapter_generator.chapter_batch_runner import run_chapter_generation_batch
from construction_bidding_agent.chapter_generator.chapter_writer import write_chapter_generation_outputs
from construction_bidding_agent.chapter_generator.draft_preview import render_chapter_draft_preview
from construction_bidding_agent.chapter_generator.full_bid_docx_exporter import (
    DEFAULT_LIBRARY,
    export_full_bid_docx_from_files,
)
from construction_bidding_agent.chapter_generator.chapter_docx_renderer import FINAL_DOCX_MODE
from construction_bidding_agent.chapter_generator.input_builder import (
    build_chapter_generation_inputs,
    write_chapter_generation_inputs,
)
from construction_bidding_agent.chapter_generator.material_retrieval_input_builder import (
    build_chapter_material_retrieval_inputs,
    write_chapter_material_retrieval_inputs,
)
from construction_bidding_agent.document_parser.tender_document_index import (
    build_tender_document_index,
    render_tender_document_index_report,
)
from construction_bidding_agent.document_parser.tender_extraction_input_builder import (
    build_tender_extraction_inputs_from_path,
    render_tender_extraction_input_report,
)
from construction_bidding_agent.document_parser.tender_parse_report import (
    build_tender_parse_result,
    write_tender_parse_report_outputs,
)
from construction_bidding_agent.document_parser.tender_llm_extractor import (
    run_tender_llm_extraction_from_file,
    write_tender_llm_extraction_outputs,
)
from construction_bidding_agent.llm_config import llm_config
from construction_bidding_agent.outline_generator import (
    build_outline_refinement_inputs,
    build_outline_tree,
    run_outline_refinement,
    write_outline_refinement_outputs,
    write_refinement_inputs,
)

from .storage import LocalStorageService


SUPPORTED_WORKFLOW_JOB_TYPES = {
    "tender_parse",
    "outline_generation",
    "chapter_generation",
    "chapter_llm_generation",
    "chapter_aggregate_refresh",
}
DEFAULT_TZ = "Asia/Shanghai"
LIGHTWEIGHT_SCHEMA_VERSION = "backend_lightweight_workflow_v0.1"
LLM_SCHEMA_VERSION = "backend_llm_workflow_v0.1"
HYBRID_SCHEMA_VERSION = "backend_hybrid_workflow_v0.1"
TENDER_PARSE_MODE_ENV = "TENDER_PARSE_MODE"
DEFAULT_TENDER_PARSE_MODE = "llm_with_rule_fallback"
ProgressCallback = Callable[..., None]
TECHNICAL_SCORE_TITLE_HINTS = (
    "内容完整性",
    "施工方案",
    "技术措施",
    "施工组织",
    "质量管理",
    "安全管理",
    "文明施工",
    "环境保护",
    "扬尘治理",
    "工期保证",
    "资源配备",
    "施工进度",
    "总平面",
    "技术创新",
    "创新应用",
    "建造方式",
    "装配式",
    "新工艺",
    "新技术",
    "BIM",
    "信息化",
    "风险管理",
    "重点",
    "难点",
    "绿色施工",
)
NON_TECHNICAL_SCORE_KEYWORDS = (
    "投标报价",
    "报价",
    "商务",
    "措施费",
    "安全文明施工措施费",
    "分部分项",
    "清单",
    "综合单价",
    "评标基准价",
    "偏差率",
    "投标保证金",
    "投标有效期",
    "形式评审",
    "资格评审",
    "响应性评审",
    "企业业绩",
    "项目经理业绩",
    "项目负责人业绩",
    "类似业绩",
    "信誉",
    "信用",
    "资信",
    "财务",
    "纳税",
    "证书",
    "奖项",
)
TECHNICAL_SCORE_SECTION_START_KEYWORDS = (
    "施工组织设计（总分",
    "施工组织设计(总分",
    "施工组织设计评分",
    "技术标评审标准",
    "技术部分评审标准",
    "技术评分标准",
)
TECHNICAL_SCORE_SECTION_END_KEYWORDS = (
    "投标报价（总分",
    "投标报价(总分",
    "综合标（总分",
    "综合标(总分",
    "商务标（总分",
    "商务标(总分",
    "2.2.4（2）",
    "2.2.4(2)",
    "2.2.4（3）",
    "2.2.4(3)",
)
STRUCTURAL_SCORE_TEXTS = {
    "条款号",
    "条款内容",
    "编列内容",
    "评分因素",
    "参考评分标准",
    "评审因素",
    "评审标准",
    "施工组织设计",
}
TECHNICAL_SCORE_SECTION_TITLE_PATTERNS = (
    r"^施工组织设计[：:]\s*\d+(?:\.\d+)?\s*分$",
    r"^施工组织设计[（(]总分\s*\d+(?:\.\d+)?\s*分[）)]$",
    r"^分值构成[（(]总分\s*\d+(?:\.\d+)?\s*分[）)]$",
)
FALLBACK_SCORE_POINTS = [
    ("内容完整性", "技术标内容完整，符合招标文件技术标编制要求。"),
    ("主要施工方案与技术措施", "施工方案总体安排合理，主要施工工艺、施工部署和关键技术措施完整。"),
    ("质量管理体系与措施", "质量目标、质量保证体系和关键工序控制措施完整。"),
    ("安全管理体系与措施", "安全生产管理体系、危险源控制和安全防护措施完整。"),
    ("文明施工、环境保护管理体系及施工现场扬尘治理措施", "文明施工、环境保护和扬尘治理措施完整。"),
    ("工期保证措施", "工期承诺、进度计划管理和纠偏措施完整。"),
    ("拟投入资源配备计划", "劳动力、机械设备、材料供应计划与进度安排匹配。"),
    ("施工进度表", "施工进度计划、关键线路和阶段节点安排完整。"),
    ("施工总平面布置图", "施工总平面布置合理，满足安全文明施工和材料运输要求。"),
    ("风险管理措施", "风险识别、风险控制和应急措施完整。"),
]


@dataclass(slots=True)
class WorkflowExecutionResult:
    status: str
    message: str
    result_ref: str | None = None
    progress_total: int | None = None
    progress_completed: int | None = None
    progress_failed: int | None = None
    progress_percent: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StageTimer:
    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []

    def record(self, key: str, label: str, started: float) -> None:
        self.stages.append(
            {
                "key": key,
                "label": label,
                "duration_seconds": round(time.monotonic() - started, 4),
            }
        )

    def to_dict(self, *, total_started: float, llm_called: bool = False) -> dict[str, Any]:
        return {
            "schema_version": "workflow_stage_timing_v0.1",
            "duration_seconds": round(time.monotonic() - total_started, 4),
            "llm_called": llm_called,
            "stages": self.stages,
        }


class WorkflowExecutionError(RuntimeError):
    """工作流执行失败，但失败信息可以回写到任务记录。"""


def execute_workflow_job(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    files: list[dict[str, Any]],
    job_id: str,
    job_type: str,
    job_config: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> WorkflowExecutionResult:
    """执行一个项目工作流任务。"""

    if job_type == "tender_parse":
        return _execute_tender_parse(
            project_root=project_root,
            storage=storage,
            project=project,
            files=files,
            job_id=job_id,
            progress_callback=progress_callback,
        )
    if job_type == "outline_generation":
        return _execute_outline_generation(
            project_root=project_root,
            storage=storage,
            project=project,
            job_id=job_id,
            progress_callback=progress_callback,
        )
    if job_type == "chapter_generation":
        return _execute_chapter_generation(project_root=project_root, storage=storage, project=project, job_id=job_id)
    if job_type == "chapter_llm_generation":
        return _execute_chapter_llm_generation(
            project_root=project_root,
            storage=storage,
            project=project,
            job_id=job_id,
            job_config=job_config,
            progress_callback=progress_callback,
        )
    if job_type == "chapter_aggregate_refresh":
        return _execute_chapter_aggregate_refresh(
            project_root=project_root,
            storage=storage,
            project=project,
            job_id=job_id,
            progress_callback=progress_callback,
        )
    raise WorkflowExecutionError(f"暂不支持的任务类型：{job_type}")


def _execute_tender_parse(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    files: list[dict[str, Any]],
    job_id: str,
    progress_callback: ProgressCallback | None = None,
) -> WorkflowExecutionResult:
    started = time.monotonic()
    _report_progress(progress_callback, 10.0, "正在读取上传文件并定位招标文件。")
    tender_files = [item for item in files if item.get("business_type") == "tender_document"]
    if not tender_files:
        raise WorkflowExecutionError("请先上传招标文件，再启动解析。")

    primary = _select_primary_tender_file(tender_files)
    primary_path = _resolve_upload_path(storage, primary)
    if primary_path is None or not primary_path.exists():
        raise WorkflowExecutionError("未找到已上传招标文件的本地副本。")

    _report_progress(progress_callback, 20.0, "正在解析招标文件结构，生成关键区域索引。")
    file_id = str(primary.get("file_id") or f"{project.get('project_id')}_tender")
    document_index_data, document_index_report_text, extraction_inputs_data, extraction_inputs_report_text = (
        _build_parse_intermediate_outputs(primary_path, file_id=file_id)
    )

    _report_progress(progress_callback, 35.0, "正在构建项目信息、评分点、技术要求抽取输入包。")
    parse_dir = _project_artifact_dir(storage, str(project["project_id"]), "parse")
    document_index_json = parse_dir / "tender_document_index.json"
    document_index_report = parse_dir / "tender_document_index_report.md"
    extraction_inputs_json = parse_dir / "tender_extraction_inputs.json"
    extraction_inputs_report = parse_dir / "tender_extraction_inputs_report.md"
    document_index_json.write_text(json.dumps(document_index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    document_index_report.write_text(document_index_report_text, encoding="utf-8")
    extraction_inputs_json.write_text(json.dumps(extraction_inputs_data, ensure_ascii=False, indent=2), encoding="utf-8")
    extraction_inputs_report.write_text(extraction_inputs_report_text, encoding="utf-8")

    parse_mode = _tender_parse_mode()
    llm_run_data: dict[str, Any] | None = None
    llm_error: str | None = None
    llm_failed_tasks: list[dict[str, Any]] = []
    execution_mode = "lightweight_rule_based"
    if parse_mode in {"llm", "llm_with_rule_fallback"}:
        try:
            _report_progress(progress_callback, 50.0, "正在调用模型抽取项目信息、技术评分点和技术要求，可能需要数分钟。")
            llm_run_data = _run_llm_tender_extraction(
                project_root=project_root,
                storage=storage,
                project_id=str(project["project_id"]),
                extraction_inputs_json=extraction_inputs_json,
                parse_dir=parse_dir,
            )
            if _llm_run_complete(llm_run_data):
                execution_mode = "llm"
                _report_progress(progress_callback, 85.0, "模型抽取已完成，正在合并解析结果。")
            elif _llm_run_partially_usable(llm_run_data):
                execution_mode = "llm_with_rule_fallback"
                llm_failed_tasks = _llm_failed_tasks(llm_run_data)
                _report_progress(progress_callback, 85.0, "模型抽取部分完成，正在用规则补齐失败项。")
        except Exception as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            if parse_mode == "llm":
                raise WorkflowExecutionError(f"真实 LLM 招标文件解析失败：{llm_error}") from exc
            _report_progress(progress_callback, 70.0, "模型抽取失败，正在自动回退到轻量规则解析。")

    if execution_mode == "llm" and llm_run_data is not None:
        project_technical_run = llm_run_data
        score_run = llm_run_data
    elif execution_mode == "llm_with_rule_fallback" and llm_run_data is not None:
        _report_progress(progress_callback, 75.0, "正在合并 LLM 成功结果，并用轻量规则补齐失败解析项。")
        lightweight_project_run = _project_technical_run(extraction_inputs_data, project=project)
        lightweight_score_run = _score_points_run(extraction_inputs_data)
        hybrid_run = _hybrid_tender_parse_run(
            llm_run_data=llm_run_data,
            fallback_runs=[lightweight_project_run, lightweight_score_run],
            failed_tasks=llm_failed_tasks,
        )
        project_technical_run = hybrid_run
        score_run = hybrid_run
    else:
        _report_progress(progress_callback, 75.0, "正在执行轻量规则解析并生成兜底结果。")
        project_technical_run = _project_technical_run(extraction_inputs_data, project=project)
        score_run = _score_points_run(extraction_inputs_data)
    _report_progress(progress_callback, 92.0, "正在生成招标文件解析报告和人工复核清单。")
    parse_result = build_tender_parse_result(
        extraction_inputs_data,
        project_technical_run,
        score_run,
        generated_at=_now_iso(),
        job_id=job_id,
    )
    parse_result["input_files"] = [_parse_input_file(item, storage, is_primary=item is primary) for item in tender_files]
    _annotate_parse_execution(
        parse_result,
        mode=execution_mode,
        parse_mode=parse_mode,
        llm_error=llm_error,
        failed_tasks=llm_failed_tasks,
    )

    parse_result_json = parse_dir / "tender_parse_result.json"
    parse_report = parse_dir / "tender_parse_report.md"
    write_tender_parse_report_outputs(parse_result, parse_result_json, parse_report)

    artifacts = _artifact_bundle(
        storage,
        str(project["project_id"]),
        [
            ("parse_result", "parse", "tender_parse_result.json"),
            ("parse_report", "parse", "tender_parse_report.md"),
            ("document_index", "parse", "tender_document_index.json"),
            ("document_index_report", "parse", "tender_document_index_report.md"),
            ("extraction_inputs", "parse", "tender_extraction_inputs.json"),
            ("extraction_inputs_report", "parse", "tender_extraction_inputs_report.md"),
            ("llm_extraction", "parse", "tender_llm_extraction.json"),
            ("llm_extraction_report", "parse", "tender_llm_extraction_report.md"),
        ],
    )
    score_count = len(parse_result.get("technical_score_points") or [])
    review_count = len(parse_result.get("review_items") or [])
    duration = time.monotonic() - started
    _report_progress(progress_callback, 98.0, "解析产物已写入，正在更新任务状态。")
    return WorkflowExecutionResult(
        status="succeeded",
        message=f"招标文件解析报告已生成：识别 {score_count} 个技术标评分点，形成 {review_count} 条复核项。",
        result_ref=storage.to_uri(parse_result_json),
        progress_total=3,
        progress_completed=3,
        progress_failed=0,
        progress_percent=100.0,
        metadata={
            "schema_version": _workflow_schema_version(execution_mode),
            "execution_mode": execution_mode,
            "requested_parse_mode": parse_mode,
            "llm_error": llm_error,
            "llm_failed_tasks": llm_failed_tasks,
            "duration_seconds": round(duration, 2),
            "primary_file": primary.get("file_name"),
            "score_point_count": score_count,
            "review_item_count": review_count,
            "artifacts": artifacts,
        },
    )


def _execute_outline_generation(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    job_id: str,
    progress_callback: ProgressCallback | None = None,
) -> WorkflowExecutionResult:
    started = time.monotonic()
    project_id = str(project["project_id"])
    _report_progress(progress_callback, 10.0, "正在读取招标文件解析结果。")
    parse_result_path = _project_artifact_path(storage, project_id, "parse", "tender_parse_result.json")
    if not parse_result_path.exists():
        raise WorkflowExecutionError("请先完成招标文件解析，再生成技术标目录。")

    parse_result = _read_json(parse_result_path)
    _report_progress(progress_callback, 20.0, "正在构建一级评分点目录和规则骨架。")
    excellent_bid_index = _load_existing_excellent_bid_index(storage)
    base_outline = build_outline_tree(
        parse_result,
        excellent_bid_index=excellent_bid_index,
        generated_at=_now_iso(),
        outline_id=f"{project_id}_{job_id}",
    )
    llm_seed_outline = _prepare_rule_skeleton_for_llm_outline(base_outline)
    _report_progress(progress_callback, 30.0, "正在生成 LLM 二三级目录补强输入包。")
    refinement_inputs = build_outline_refinement_inputs(
        llm_seed_outline,
        parse_result,
        excellent_bid_index=excellent_bid_index,
    )
    if not refinement_inputs:
        raise WorkflowExecutionError("目录生成失败：未形成可供 LLM 生成二三级目录的输入包。")
    outline_dir = _project_artifact_dir(storage, project_id, "outline")
    refinement_inputs_json = outline_dir / "outline_refinement_inputs.json"
    write_refinement_inputs(refinement_inputs, refinement_inputs_json)
    config = llm_config(task_key="outline_refinement")
    if not config.api_key:
        raise WorkflowExecutionError("目录生成失败：未配置 API_KEY，无法执行 LLM 二三级目录生成。")
    _report_progress(progress_callback, 40.0, "正在调用模型补强二三级目录。")
    refinement = run_outline_refinement(
        llm_seed_outline,
        refinement_inputs,
        llm_config_override=config,
        max_workers=config.max_workers,
        cache_dir=outline_dir / "llm_cache",
    )
    _report_progress(progress_callback, 90.0, "正在校验并写入目录产物。")
    outline = refinement.outline
    outline_json = outline_dir / "technical_bid_outline.json"
    outline_report = outline_dir / "technical_bid_outline_report.md"
    refinement_json = outline_dir / "outline_refinement_result.json"
    refinement_report = outline_dir / "outline_refinement_report.md"
    write_outline_refinement_outputs(
        refinement,
        refinement_json,
        refinement_report,
        outline_json_path=outline_json if refinement.applied_count == refinement.task_count else None,
        outline_report_path=outline_report if refinement.applied_count == refinement.task_count else None,
    )
    if refinement.applied_count != refinement.task_count or refinement.failed_count or refinement.skipped_count:
        failed_titles = [
            task.level_1_title
            for task in refinement.tasks
            if not task.applied
        ]
        raise WorkflowExecutionError(
            "目录生成失败：LLM 二三级目录生成未全部通过。"
            f"未应用节点：{'、'.join(failed_titles[:8]) or '未知'}"
        )

    _report_progress(progress_callback, 98.0, "目录产物已写入，正在更新任务状态。")
    artifacts = _artifact_bundle(
        storage,
        project_id,
        [
            ("outline", "outline", "technical_bid_outline.json"),
            ("outline_report", "outline", "technical_bid_outline_report.md"),
            ("outline_refinement_inputs", "outline", "outline_refinement_inputs.json"),
            ("outline_refinement_result", "outline", "outline_refinement_result.json"),
            ("outline_refinement_report", "outline", "outline_refinement_report.md"),
        ],
    )
    duration = time.monotonic() - started
    level_1_count = int(outline.get("level_1_count") or 0)
    return WorkflowExecutionResult(
        status="succeeded",
        message=f"技术标目录已生成：一级目录 {level_1_count} 个，一级标题保持评分点原文。",
        result_ref=storage.to_uri(outline_json),
        progress_total=2,
        progress_completed=2,
        progress_failed=0,
        progress_percent=100.0,
        metadata={
            "schema_version": LIGHTWEIGHT_SCHEMA_VERSION,
            "duration_seconds": round(duration, 2),
            "execution_mode": "llm_refinement",
            "llm_model": refinement.model,
            "llm_provider": refinement.provider,
            "llm_duration_seconds": round(refinement.duration_seconds, 2),
            "llm_task_count": refinement.task_count,
            "llm_applied_count": refinement.applied_count,
            "llm_max_workers": refinement.max_workers,
            "level_1_count": level_1_count,
            "review_item_count": len(outline.get("review_items") or []),
            "artifacts": artifacts,
        },
    )


def _prepare_rule_skeleton_for_llm_outline(outline: dict[str, Any]) -> dict[str, Any]:
    """保留规则骨架作为 LLM 草稿输入，一级标题仍锁定评分点原文。"""

    result = copy.deepcopy(outline)
    for node in result.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("level") != 1:
            continue
        node["template_source"] = "rule_skeleton_for_llm"
        node["requires_review"] = True
        node["review_reason"] = "二三级目录以规则骨架为草稿，由 LLM 结合评分点和优秀标书素材补强。"
    result["generator_version"] = "level1_score_points_rule_skeleton_for_llm"
    result["status"] = "pending_llm_outline_generation"
    result["outline_generation_mode"] = "rule_skeleton_plus_llm_refinement"
    return result


def _report_progress(
    progress_callback: ProgressCallback | None,
    percent: float,
    message: str,
    **extra: Any,
) -> None:
    if progress_callback is None:
        return
    progress_callback(percent, message, extra or None)


def _chapter_generation_progress_callback(
    progress_callback: ProgressCallback | None,
    *,
    total: int,
    mode_label: str,
    workers: int,
) -> Callable[[dict[str, Any]], None] | None:
    if progress_callback is None:
        return None

    def callback(payload: dict[str, Any]) -> None:
        finished = int(payload.get("finished") or 0)
        completed = int(payload.get("completed") or 0)
        failed = int(payload.get("failed") or 0)
        skipped = int(payload.get("skipped") or 0)
        denominator = max(int(payload.get("total") or total or 0), 1)
        percent = 18.0 + min(1.0, finished / denominator) * 68.0
        path_text = " > ".join(str(item) for item in payload.get("chapter_path") or [] if item)
        retrying = int(payload.get("retrying") or 0)
        message = f"正在生成{mode_label}正文：已完成 {completed + skipped}/{denominator}，失败 {failed}，并发 {workers}。"
        if retrying:
            message += f"自动重试中 {retrying} 个。"
        if path_text:
            message += f"刚处理：{path_text}"
        _report_progress(
            progress_callback,
            percent,
            message,
            progress_total=denominator,
            progress_completed=completed + skipped,
            progress_failed=failed,
            chapter_unit_id=payload.get("unit_id"),
            chapter_path=payload.get("chapter_path") or [],
            task_status=payload.get("status"),
            event=payload.get("event"),
            retrying=retrying,
            max_workers=workers,
        )

    return callback


def _tender_parse_mode() -> str:
    value = os.getenv(TENDER_PARSE_MODE_ENV, DEFAULT_TENDER_PARSE_MODE).strip().lower()
    allowed = {"llm_with_rule_fallback", "llm", "lightweight"}
    if value not in allowed:
        return DEFAULT_TENDER_PARSE_MODE
    return value


def _run_llm_tender_extraction(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project_id: str,
    extraction_inputs_json: Path,
    parse_dir: Path,
) -> dict[str, Any]:
    config = llm_config()
    if not config.api_key:
        raise WorkflowExecutionError("未配置 API_KEY，无法执行真实 LLM 招标文件解析。")
    result = run_tender_llm_extraction_from_file(
        extraction_inputs_json,
        prompt_dir=project_root / "docs" / "prompts",
        execution_mode="parallel",
        max_workers=config.max_workers,
        cache_dir=storage.storage_root / "projects" / project_id / "parse" / "llm_cache",
    )
    json_path = parse_dir / "tender_llm_extraction.json"
    report_path = parse_dir / "tender_llm_extraction_report.md"
    write_tender_llm_extraction_outputs(result, json_path, report_path)
    return result.to_dict()


def _workflow_schema_version(execution_mode: str) -> str:
    if execution_mode == "llm":
        return LLM_SCHEMA_VERSION
    if execution_mode == "llm_with_rule_fallback":
        return HYBRID_SCHEMA_VERSION
    return LIGHTWEIGHT_SCHEMA_VERSION


def _llm_run_complete(run_data: dict[str, Any]) -> bool:
    tasks = run_data.get("tasks") or []
    required = {
        "project_info_extraction_input",
        "score_points_extraction_input",
        "technical_requirements_extraction_input",
    }
    completed = {task.get("task_key") for task in tasks if task.get("status") == "completed"}
    return required.issubset(completed)


def _llm_run_partially_usable(run_data: dict[str, Any]) -> bool:
    tasks = run_data.get("tasks") or []
    return any(task.get("status") == "completed" for task in tasks)


def _llm_failed_tasks(run_data: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for task in run_data.get("tasks") or []:
        if task.get("status") == "completed":
            continue
        failed.append(
            {
                "task_key": task.get("task_key"),
                "task_title": task.get("task_title"),
                "status": task.get("status"),
                "error": task.get("error"),
            }
        )
    return failed


def _hybrid_tender_parse_run(
    *,
    llm_run_data: dict[str, Any],
    fallback_runs: list[dict[str, Any]],
    failed_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    tasks_by_key: dict[str, dict[str, Any]] = {}
    for task in llm_run_data.get("tasks") or []:
        if task.get("task_key") and task.get("status") == "completed":
            tasks_by_key[str(task["task_key"])] = dict(task)
    for run in fallback_runs:
        for task in run.get("tasks") or []:
            task_key = str(task.get("task_key") or "")
            if not task_key or task_key in tasks_by_key:
                continue
            fallback_task = copy.deepcopy(task)
            fallback_task["status"] = "fallback_completed"
            fallback_task["cache_status"] = "rule_fallback"
            fallback_task["fallback_reason"] = _fallback_reason_for_task(task_key, failed_tasks)
            validation = dict(fallback_task.get("validation") or {})
            validation["summary"] = f"{validation.get('summary') or '轻量规则兜底完成。'}（LLM 任务失败后补齐）"
            fallback_task["validation"] = validation
            tasks_by_key[task_key] = fallback_task
    ordered_task_keys = [
        "project_info_extraction_input",
        "score_points_extraction_input",
        "technical_requirements_extraction_input",
    ]
    tasks = [tasks_by_key[key] for key in ordered_task_keys if key in tasks_by_key]
    completed_count = sum(1 for task in tasks if task.get("status") == "completed")
    fallback_count = sum(1 for task in tasks if task.get("status") == "fallback_completed")
    return {
        **llm_run_data,
        "schema_version": llm_run_data.get("schema_version") or "tender_llm_extraction_run_v0.2",
        "execution_mode": "llm_with_rule_fallback",
        "task_count": len(tasks),
        "completed_task_count": completed_count,
        "fallback_task_count": fallback_count,
        "failed_task_count": len(failed_tasks),
        "tasks": tasks,
        "warnings": [
            *(llm_run_data.get("warnings") or []),
            "部分 LLM 解析任务失败，已使用轻量规则补齐失败项。",
        ],
        "failed_tasks": failed_tasks,
    }


def _fallback_reason_for_task(task_key: str, failed_tasks: list[dict[str, Any]]) -> str:
    for task in failed_tasks:
        if task.get("task_key") == task_key:
            error = task.get("error") or "LLM 任务失败。"
            return f"LLM 任务失败，已使用轻量规则兜底：{error}"
    return "LLM 任务缺失，已使用轻量规则兜底。"


def _annotate_parse_execution(
    parse_result: dict[str, Any],
    *,
    mode: str,
    parse_mode: str,
    llm_error: str | None,
    failed_tasks: list[dict[str, Any]] | None = None,
) -> None:
    execution = parse_result.setdefault("execution", {})
    if mode == "llm":
        execution["mode"] = "llm"
        execution["requested_parse_mode"] = parse_mode
        execution["note"] = "本次解析使用真实 LLM 抽取结果生成。"
        return
    if mode == "llm_with_rule_fallback":
        execution["mode"] = "llm_with_rule_fallback"
        execution["requested_parse_mode"] = parse_mode
        execution["note"] = "本次解析已调用真实 LLM；部分 LLM 任务失败的字段由轻量规则补齐。"
        execution["llm_failed_tasks"] = failed_tasks or []
        for task in failed_tasks or []:
            title = task.get("task_title") or task.get("task_key") or "LLM 子任务"
            error = task.get("error") or "未知错误"
            parse_result.setdefault("warnings", []).append(
                {
                    "warning_id": f"W{len(parse_result.get('warnings') or []) + 1:03d}",
                    "level": "high",
                    "message": f"{title}调用了模型但结果不可用，已使用规则兜底：{error}",
                    "source_refs": [],
                }
            )
        return
    execution["mode"] = "lightweight_rule_based"
    execution["requested_parse_mode"] = parse_mode
    execution["lightweight_note"] = "当前由后端轻量规则执行器生成，用于前端主流程和 LLM 失败兜底。"
    if llm_error:
        execution["llm_fallback_reason"] = llm_error
        parse_result.setdefault("warnings", []).append(
            {
                "warning_id": f"W{len(parse_result.get('warnings') or []) + 1:03d}",
                "level": "high",
                "message": f"真实 LLM 解析失败，已自动回退到轻量规则解析：{llm_error}",
                "source_refs": [],
            }
        )
    elif parse_mode == "lightweight":
        execution["llm_fallback_reason"] = "TENDER_PARSE_MODE=lightweight"


def _execute_chapter_generation(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    job_id: str,
) -> WorkflowExecutionResult:
    started = time.monotonic()
    project_id = str(project["project_id"])
    parse_result_path = _project_artifact_path(storage, project_id, "parse", "tender_parse_result.json")
    outline_path = _project_artifact_path(storage, project_id, "outline", "technical_bid_outline.json")
    if not parse_result_path.exists():
        raise WorkflowExecutionError("请先完成招标文件解析，再生成正文初稿。")
    if not outline_path.exists():
        raise WorkflowExecutionError("请先生成并确认技术标目录，再生成正文初稿。")

    parse_result = _read_json(parse_result_path)
    outline = _read_json(outline_path)
    packages = build_chapter_generation_inputs(
        outline,
        parse_result,
        include_domains={"construction", "management", "general"},
        split_core_level2=True,
    )

    generation_dir = _project_artifact_dir(storage, project_id, "generation")
    documents_dir = _project_artifact_dir(storage, project_id, "documents")
    inputs_json = generation_dir / "chapter_generation_inputs.json"
    inputs_report = generation_dir / "chapter_generation_inputs_report.md"
    write_chapter_generation_inputs(packages, inputs_json, inputs_report)

    draft = _render_lightweight_chapter_draft(project, outline, parse_result, packages)
    draft_path = documents_dir / "technical_bid_draft.md"
    draft_path.write_text(draft, encoding="utf-8")
    aggregate_result = _lightweight_chapter_generation_result(project_id, packages)
    aggregate_result_json = generation_dir / "chapter_llm_generation_aggregate_result.json"
    aggregate_result_json.write_text(json.dumps(aggregate_result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = _chapter_generation_summary(project_id, job_id, outline, packages, storage, draft_path, inputs_json)
    summary_path = generation_dir / "chapter_generation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = _artifact_bundle(
        storage,
        project_id,
        [
            ("chapter_inputs", "generation", "chapter_generation_inputs.json"),
            ("chapter_inputs_report", "generation", "chapter_generation_inputs_report.md"),
            ("generation_summary", "generation", "chapter_generation_summary.json"),
            ("llm_generation_aggregate_result", "generation", "chapter_llm_generation_aggregate_result.json"),
            ("draft_markdown", "documents", "technical_bid_draft.md"),
        ],
    )
    duration = time.monotonic() - started
    unit_count = len(packages)
    return WorkflowExecutionResult(
        status="succeeded",
        message=f"正文初稿预览已生成：形成 {unit_count} 个章节生成单元。",
        result_ref=storage.to_uri(summary_path),
        progress_total=max(unit_count, 1),
        progress_completed=unit_count,
        progress_failed=0,
        progress_percent=100.0,
        metadata={
            "schema_version": LIGHTWEIGHT_SCHEMA_VERSION,
            "duration_seconds": round(duration, 2),
            "generation_unit_count": unit_count,
            "draft_uri": storage.to_uri(draft_path),
            "aggregate_result_uri": storage.to_uri(aggregate_result_json),
            "artifacts": artifacts,
        },
    )


def _execute_chapter_llm_generation(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    job_id: str,
    job_config: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> WorkflowExecutionResult:
    started = time.monotonic()
    project_id = str(project["project_id"])
    config = job_config or {}
    _report_progress(progress_callback, 8.0, "正在准备章节正文生成输入包。")
    packages, inputs_json = _ensure_chapter_generation_inputs(
        storage=storage,
        project_id=project_id,
    )
    selected_packages = _select_chapter_packages(packages, config)
    if not selected_packages:
        raise WorkflowExecutionError("请先选择要生成的章节，或点击一键生成全部章节。")

    effective_workers = _chapter_llm_max_workers(config, selected_count=len(selected_packages))
    mode_label = "全部章节" if config.get("run_all") else "选中章节"
    _report_progress(
        progress_callback,
        18.0,
        f"正在调用模型生成{mode_label}正文：{len(selected_packages)} 个小节包，并发 {effective_workers}。",
        progress_total=len(selected_packages),
        progress_completed=0,
        progress_failed=0,
        max_workers=effective_workers,
    )
    generation_dir = _project_artifact_dir(storage, project_id, "generation")
    documents_dir = _project_artifact_dir(storage, project_id, "documents")
    state_dir = generation_dir / "chapter_llm_state"
    result = run_chapter_generation_batch(
        selected_packages,
        state_dir=state_dir,
        max_workers=effective_workers,
        force=bool(config.get("force")),
        retry_failed=True,
        progress_callback=_chapter_generation_progress_callback(
            progress_callback,
            total=len(selected_packages),
            mode_label=mode_label,
            workers=effective_workers,
        ),
    )

    _report_progress(progress_callback, 86.0, "正在写入正文生成结果和预览稿。")
    result_json = generation_dir / "chapter_llm_generation_result.json"
    result_report = generation_dir / "chapter_llm_generation_report.md"
    write_chapter_generation_outputs(result, result_json, result_report)
    refresh = _refresh_chapter_generation_outputs(
        project_root=project_root,
        storage=storage,
        project_id=project_id,
        inputs_json=inputs_json,
        state_dir=state_dir,
        current_result=result.to_dict(),
    )
    aggregate_result = refresh["aggregate_result"]
    aggregate_result_json = refresh["aggregate_result_json"]
    preview_path = refresh["preview_path"]
    word_summary = refresh["word_summary"]
    summary = _chapter_llm_generation_summary(
        project_id=project_id,
        job_id=job_id,
        result=result.to_dict(),
        aggregate_result=aggregate_result,
        selected_packages=selected_packages,
        all_packages=packages,
        storage=storage,
        inputs_json=inputs_json,
        result_json=aggregate_result_json,
        preview_path=preview_path,
        state_dir=state_dir,
        effective_workers=effective_workers,
        mode="all" if config.get("run_all") else "selected",
        duration_seconds=time.monotonic() - started,
        word_summary=word_summary,
    )
    summary_path = generation_dir / "chapter_llm_generation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    _report_progress(progress_callback, 98.0, "正文生成完成，正在更新任务状态。")
    artifacts = _artifact_bundle(
        storage,
        project_id,
        [
            ("chapter_inputs", "generation", "chapter_generation_inputs.json"),
            ("chapter_inputs_report", "generation", "chapter_generation_inputs_report.md"),
            ("llm_generation_result", "generation", "chapter_llm_generation_result.json"),
            ("llm_generation_aggregate_result", "generation", "chapter_llm_generation_aggregate_result.json"),
            ("llm_generation_report", "generation", "chapter_llm_generation_report.md"),
            ("llm_generation_summary", "generation", "chapter_llm_generation_summary.json"),
            ("llm_draft_markdown", "documents", "technical_bid_llm_draft_preview.md"),
            ("word_draft_docx", "documents", "technical_bid_draft.docx"),
            ("word_draft_json", "documents", "technical_bid_draft.json"),
        ],
    )
    failed = int(result.failed_count or 0)
    status = "succeeded" if failed == 0 else "failed"
    message = (
        f"真实正文生成完成：{result.completed_count}/{result.task_count} 个小节包成功，"
        f"跳过 {result.skipped_count} 个，失败 {result.failed_count} 个。"
    )
    if failed:
        message = (
            f"真实正文生成存在失败小节包：成功 {result.completed_count} 个，失败 {result.failed_count} 个，"
            "可稍后重试失败小节包。"
        )
    return WorkflowExecutionResult(
        status=status,
        message=message,
        result_ref=storage.to_uri(summary_path),
        progress_total=max(int(result.task_count or 0), 1),
        progress_completed=int(result.completed_count or 0) + int(result.skipped_count or 0),
        progress_failed=failed,
        progress_percent=100.0,
        metadata={
            "schema_version": LLM_SCHEMA_VERSION,
            "duration_seconds": round(time.monotonic() - started, 2),
            "generation_unit_count": int(result.task_count or 0),
            "completed_count": int(result.completed_count or 0),
            "failed_count": failed,
            "skipped_count": int(result.skipped_count or 0),
            "max_workers": effective_workers,
            "mode": summary["mode"],
            "workflow_refresh_timing": refresh.get("workflow_refresh_timing") or {},
            "word_refresh_timing": ((word_summary.get("summary") or {}).get("word_refresh_timing") or {}),
            "draft_uri": storage.to_uri(preview_path),
            "word_review_uri": word_summary.get("docx_uri"),
            "artifacts": artifacts,
        },
    )


def _execute_chapter_aggregate_refresh(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project: dict[str, Any],
    job_id: str,
    progress_callback: ProgressCallback | None = None,
) -> WorkflowExecutionResult:
    started = time.monotonic()
    timer = StageTimer()
    project_id = str(project["project_id"])
    _report_progress(progress_callback, 12.0, "正在读取已生成章节状态，不调用大模型。")
    inputs_started = time.monotonic()
    packages, inputs_json = _ensure_chapter_generation_inputs(storage=storage, project_id=project_id)
    timer.record("prepare_chapter_inputs", "准备章节输入包", inputs_started)
    generation_dir = _project_artifact_dir(storage, project_id, "generation")
    state_dir = generation_dir / "chapter_llm_state"
    state_started = time.monotonic()
    current_result = _chapter_state_run_result(project_id=project_id, job_id=job_id, packages=packages, state_dir=state_dir)
    timer.record("read_chapter_state", "读取已生成章节状态", state_started)
    _report_progress(progress_callback, 45.0, "正在重新聚合章节正文和预览稿。")
    refresh = _refresh_chapter_generation_outputs(
        project_root=project_root,
        storage=storage,
        project_id=project_id,
        inputs_json=inputs_json,
        state_dir=state_dir,
        current_result=current_result,
    )
    for stage in (refresh.get("workflow_refresh_timing") or {}).get("stages") or []:
        if isinstance(stage, dict):
            timer.stages.append(stage)
    aggregate_result = refresh["aggregate_result"]
    aggregate_result_json = refresh["aggregate_result_json"]
    preview_path = refresh["preview_path"]
    word_summary = refresh["word_summary"]
    _report_progress(progress_callback, 86.0, "正在写入聚合摘要和 Word 初稿。")
    summary = _chapter_llm_generation_summary(
        project_id=project_id,
        job_id=job_id,
        result=current_result,
        aggregate_result=aggregate_result,
        selected_packages=[],
        all_packages=packages,
        storage=storage,
        inputs_json=inputs_json,
        result_json=aggregate_result_json,
        preview_path=preview_path,
        state_dir=state_dir,
        effective_workers=0,
        mode="aggregate_refresh",
        duration_seconds=time.monotonic() - started,
        word_summary=word_summary,
    )
    refresh_timing = timer.to_dict(total_started=started, llm_called=False)
    summary["mode"] = "llm_aggregate_refresh"
    summary["refresh_only"] = True
    summary["refresh_timing"] = refresh_timing
    summary_path = generation_dir / "chapter_llm_generation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts = _artifact_bundle(
        storage,
        project_id,
        [
            ("chapter_inputs", "generation", "chapter_generation_inputs.json"),
            ("chapter_inputs_report", "generation", "chapter_generation_inputs_report.md"),
            ("llm_generation_aggregate_result", "generation", "chapter_llm_generation_aggregate_result.json"),
            ("llm_generation_summary", "generation", "chapter_llm_generation_summary.json"),
            ("llm_draft_markdown", "documents", "technical_bid_llm_draft_preview.md"),
            ("word_draft_docx", "documents", "technical_bid_draft.docx"),
            ("word_draft_json", "documents", "technical_bid_draft.json"),
        ],
    )
    failed = int(aggregate_result.get("failed_count") or 0)
    completed = int(aggregate_result.get("completed_count") or 0)
    task_count = int(aggregate_result.get("task_count") or 0)
    status = "succeeded" if failed == 0 else "failed"
    message = f"已刷新正文聚合结果和 Word 初稿：{completed}/{task_count} 个章节可用，失败 {failed} 个。"
    return WorkflowExecutionResult(
        status=status,
        message=message,
        result_ref=storage.to_uri(summary_path),
        progress_total=max(task_count, 1),
        progress_completed=completed,
        progress_failed=failed,
        progress_percent=100.0,
        metadata={
            "schema_version": LLM_SCHEMA_VERSION,
            "refresh_only": True,
            "duration_seconds": round(time.monotonic() - started, 2),
            "refresh_timing": refresh_timing,
            "generation_unit_count": task_count,
            "completed_count": completed,
            "failed_count": failed,
            "skipped_count": int(aggregate_result.get("skipped_count") or 0),
            "max_workers": 0,
            "mode": "llm_aggregate_refresh",
            "draft_uri": storage.to_uri(preview_path),
            "word_review_uri": word_summary.get("docx_uri"),
            "artifacts": artifacts,
        },
    )


def _ensure_chapter_generation_inputs(
    *,
    storage: LocalStorageService,
    project_id: str,
) -> tuple[list[dict[str, Any]], Path]:
    parse_result_path = _project_artifact_path(storage, project_id, "parse", "tender_parse_result.json")
    outline_path = _project_artifact_path(storage, project_id, "outline", "technical_bid_outline.json")
    if not parse_result_path.exists():
        raise WorkflowExecutionError("请先完成招标文件解析，再生成正文初稿。")
    if not outline_path.exists():
        raise WorkflowExecutionError("请先生成并确认技术标目录，再生成正文初稿。")

    parse_result = _read_json(parse_result_path)
    outline = _read_json(outline_path)
    generation_dir = _project_artifact_dir(storage, project_id, "generation")
    excellent_bid_index = _load_existing_excellent_bid_index(storage)
    material_inputs = None
    if excellent_bid_index:
        try:
            material_packages = build_chapter_material_retrieval_inputs(
                outline,
                excellent_bid_index,
                parse_result=parse_result,
                include_domains={"construction", "management", "general"},
                top_k=5,
            )
            material_json = generation_dir / "chapter_material_retrieval_inputs.json"
            material_report = generation_dir / "chapter_material_retrieval_inputs_report.md"
            write_chapter_material_retrieval_inputs(material_packages, material_json, material_report)
            material_inputs = {"packages": material_packages}
        except Exception:
            material_inputs = None

    packages = build_chapter_generation_inputs(
        outline,
        parse_result,
        excellent_bid_index=excellent_bid_index,
        material_retrieval_inputs=material_inputs,
        include_domains={"construction", "management", "general"},
        split_core_level2=True,
    )
    inputs_json = generation_dir / "chapter_generation_inputs.json"
    inputs_report = generation_dir / "chapter_generation_inputs_report.md"
    write_chapter_generation_inputs(packages, inputs_json, inputs_report)
    return packages, inputs_json


def _select_chapter_packages(packages: list[dict[str, Any]], job_config: dict[str, Any]) -> list[dict[str, Any]]:
    selected = packages
    title_contains = str(job_config.get("chapter_title_contains") or "").strip()
    if title_contains:
        selected = [
            package
            for package in selected
            if title_contains in " > ".join(str(part) for part in (package.get("generation_unit") or {}).get("chapter_path") or [])
        ]

    if not job_config.get("run_all"):
        requested_ids = [
            str(item)
            for item in (job_config.get("target_unit_ids") or [])
            if str(item).strip()
        ]
        if not requested_ids:
            return []
        requested = set(requested_ids)
        selected = [
            package
            for package in selected
            if _chapter_package_matches_requested_ids(
                package,
                requested,
                expand_parent=not bool(job_config.get("retry_failed_only")),
            )
        ]

    max_packages = job_config.get("max_packages")
    if max_packages is not None:
        try:
            selected = selected[: max(1, int(max_packages))]
        except (TypeError, ValueError):
            pass
    return selected


def _chapter_package_matches_requested_ids(
    package: dict[str, Any],
    requested_ids: set[str],
    *,
    expand_parent: bool = True,
) -> bool:
    generation_unit = package.get("generation_unit") if isinstance(package.get("generation_unit"), dict) else {}
    unit_id = str(generation_unit.get("unit_id") or "")
    if unit_id in requested_ids:
        return True
    if expand_parent and generation_unit.get("unit_type") == "level3_subsection_unit" and generation_unit.get("parent_level_2_node_id"):
        parent_unit_id = f"GU-{generation_unit.get('parent_level_2_node_id')}"
        return parent_unit_id in requested_ids
    return False


def _chapter_llm_max_workers(job_config: dict[str, Any], *, selected_count: int) -> int:
    configured = llm_config(task_key="technical_bid_chapter_generation").max_workers
    requested = job_config.get("max_workers")
    try:
        workers = int(requested if requested is not None else configured)
    except (TypeError, ValueError):
        workers = configured
    return max(1, min(workers, max(selected_count, 1)))


def _refresh_chapter_generation_outputs(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project_id: str,
    inputs_json: Path,
    state_dir: Path,
    current_result: dict[str, Any],
) -> dict[str, Any]:
    total_started = time.monotonic()
    timer = StageTimer()
    generation_dir = _project_artifact_dir(storage, project_id, "generation")
    documents_dir = _project_artifact_dir(storage, project_id, "documents")
    aggregate_started = time.monotonic()
    aggregate_result = _aggregate_chapter_generation_result(current_result, state_dir)
    aggregate_result["generated_at"] = _now_iso()
    aggregate_result_json = generation_dir / "chapter_llm_generation_aggregate_result.json"
    aggregate_result_json.write_text(json.dumps(aggregate_result, ensure_ascii=False, indent=2), encoding="utf-8")
    timer.record("aggregate_chapter_state", "聚合章节状态 JSON", aggregate_started)
    preview_path = documents_dir / "technical_bid_llm_draft_preview.md"
    preview_started = time.monotonic()
    preview_path.write_text(render_chapter_draft_preview(aggregate_result), encoding="utf-8")
    timer.record("render_markdown_preview", "生成 Markdown 预览", preview_started)
    word_started = time.monotonic()
    word_summary = _try_export_word_draft_docx(
        project_root=project_root,
        storage=storage,
        project_id=project_id,
        inputs_json=inputs_json,
        result_json=aggregate_result_json,
        output_docx=documents_dir / "technical_bid_draft.docx",
        output_json=documents_dir / "technical_bid_draft.json",
    )
    timer.record("export_word_draft", "导出 Word 初稿", word_started)
    workflow_timing = timer.to_dict(total_started=total_started, llm_called=False)
    if isinstance(word_summary.get("summary"), dict):
        word_summary["summary"]["workflow_refresh_timing"] = workflow_timing
        word_summary["summary"]["quality_gate"] = _word_quality_gate(
            inputs_json=inputs_json,
            aggregate_result=aggregate_result,
            word_summary=word_summary,
        )
    return {
        "aggregate_result": aggregate_result,
        "aggregate_result_json": aggregate_result_json,
        "preview_path": preview_path,
        "word_summary": word_summary,
        "workflow_refresh_timing": workflow_timing,
    }


def _try_export_word_draft_docx(
    *,
    project_root: Path,
    storage: LocalStorageService,
    project_id: str,
    inputs_json: Path,
    result_json: Path,
    output_docx: Path,
    output_json: Path,
) -> dict[str, Any]:
    library_path = _resolve_word_export_material_library(project_root=project_root, storage=storage)
    raw_root = _resolve_word_export_raw_root(project_root=project_root, storage=storage)
    try:
        summary = export_full_bid_docx_from_files(
            inputs_json,
            [result_json],
            output_docx,
            output_json=output_json,
            material_library_json=library_path,
            raw_root=raw_root,
            title="技术标 Word 初稿",
            output_mode=FINAL_DOCX_MODE,
        )
        return {
            "enabled": True,
            "status": "succeeded",
            "docx_uri": storage.to_uri(output_docx),
            "json_uri": storage.to_uri(output_json),
            "summary": summary,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "status": "failed",
            "error": str(exc),
            "docx_uri": None,
            "json_uri": None,
            "summary": {},
        }


def _resolve_word_export_material_library(*, project_root: Path, storage: LocalStorageService) -> Path | None:
    manifest_path = storage.storage_root / "knowledge_base" / "excellent_bids" / "indexes" / "library_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        aggregate_uri = str(manifest.get("aggregate_index_uri") or "")
        if aggregate_uri.startswith("local://"):
            candidate = storage.resolve_local_path(aggregate_uri)
            if candidate.exists():
                return candidate

    for output_root in _word_export_output_roots(project_root):
        candidate = output_root / "json" / Path(DEFAULT_LIBRARY).name
        if candidate.exists():
            return candidate

    default_path = Path(DEFAULT_LIBRARY)
    if not default_path.is_absolute():
        default_path = project_root / default_path
    return default_path if default_path.exists() else None


def _word_export_output_roots(project_root: Path) -> list[Path]:
    candidates: list[Path] = []
    output_dir = os.getenv("OUTPUT_DIR")
    if output_dir:
        path = Path(output_dir)
        candidates.append(path if path.is_absolute() else project_root / path)
    candidates.append(project_root / "outputs")

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _resolve_word_export_raw_root(*, project_root: Path, storage: LocalStorageService) -> Path:
    candidates = [
        storage.storage_root / "raw",
        project_root / "data" / "raw",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _chapter_llm_generation_summary(
    *,
    project_id: str,
    job_id: str,
    result: dict[str, Any],
    aggregate_result: dict[str, Any],
    selected_packages: list[dict[str, Any]],
    all_packages: list[dict[str, Any]],
    storage: LocalStorageService,
    inputs_json: Path,
    result_json: Path,
    preview_path: Path,
    state_dir: Path,
    effective_workers: int,
    mode: str,
    duration_seconds: float,
    word_summary: dict[str, Any],
) -> dict[str, Any]:
    selected_ids = {
        str((package.get("generation_unit") or {}).get("unit_id") or "")
        for package in selected_packages
    }
    tasks_by_unit = _chapter_generation_task_map(result, state_dir)
    generation_units = []
    for package in all_packages:
        unit = package.get("generation_unit") or {}
        unit_id = str(unit.get("unit_id") or "")
        path = [str(item) for item in unit.get("chapter_path") or [] if item]
        task = tasks_by_unit.get(unit_id)
        task_status = str(task.get("status") or "") if task else ""
        if task_status == "completed":
            status = "已生成"
        elif task_status == "failed":
            status = "生成失败"
        elif task_status == "skipped":
            status = "已跳过"
        elif unit_id in selected_ids:
            status = "本次未完成"
        else:
            status = "待生成"
        generation_units.append(
            {
                "unit_id": unit_id,
                "target_node_id": unit.get("target_node_id"),
                "chapter": path[-1] if path else "未命名章节",
                "chapter_path": path,
                "domain": unit.get("domain"),
                "unit_type": unit.get("unit_type"),
                "status": status,
                "material": _material_summary(package),
                "duration_seconds": task.get("duration_seconds") if task else None,
                "validation_issue_count": (task.get("validation") or {}).get("issue_count") if task else None,
                "error": task.get("error") if task else None,
                "cache_status": task.get("cache_status") if task else None,
                "cache_generated_at": task.get("completed_at") if task else None,
                "resume_reason": task.get("resume_reason") if task else None,
                "failure_type": task.get("failure_type") if task else None,
                "failure_reason": task.get("failure_reason") if task else None,
                "retry_attempt_count": task.get("retry_attempt_count") if task else 0,
                "retry_summary": task.get("retry_summary") if task else {},
                "repair_attempt_count": task.get("repair_attempt_count") if task else 0,
            }
        )

    warnings = list(result.get("warnings") or [])
    if word_summary.get("status") == "failed":
        warnings.append(f"Word 初稿导出失败：{word_summary.get('error')}")
    quality_gate = {}
    if isinstance(word_summary.get("summary"), dict):
        quality_gate = word_summary["summary"].get("quality_gate") or {}
        warnings.extend(str(item) for item in quality_gate.get("warnings") or [])
    return {
        "schema_version": "chapter_llm_generation_summary_v0.1",
        "project_id": project_id,
        "job_id": job_id,
        "generated_at": _now_iso(),
        "mode": f"llm_{mode}_chapter_generation",
        "generation_unit_count": len(all_packages),
        "selected_generation_unit_count": len(selected_packages),
        "completed_count": aggregate_result.get("completed_count") or result.get("completed_count") or 0,
        "skipped_count": result.get("skipped_count") or 0,
        "failed_count": result.get("failed_count") or 0,
        "aggregate_completed_count": aggregate_result.get("completed_count") or 0,
        "duration_seconds": round(duration_seconds, 2),
        "max_workers": effective_workers,
        "draft_uri": storage.to_uri(preview_path),
        "chapter_inputs_uri": storage.to_uri(inputs_json),
        "llm_generation_result_uri": storage.to_uri(result_json),
        "state_dir": str(state_dir),
        "word_review": word_summary,
        "quality_gate": quality_gate,
        "generation_units": generation_units,
        "warnings": warnings,
    }


def _word_quality_gate(
    *,
    inputs_json: Path,
    aggregate_result: dict[str, Any],
    word_summary: dict[str, Any],
) -> dict[str, Any]:
    try:
        inputs = json.loads(Path(inputs_json).read_text(encoding="utf-8"))
    except Exception:
        inputs = {}
    packages = [package for package in inputs.get("packages") or [] if isinstance(package, dict)]
    chapters = [chapter for chapter in aggregate_result.get("chapters") or [] if isinstance(chapter, dict)]
    directory_heading_count = _expected_official_heading_count(packages)
    generated_section_count = _generated_section_count(chapters)
    image_ref_count = _image_ref_count(chapters)
    major_image_count = _major_section_image_count(chapters)
    empty_core_process_units = _empty_core_process_image_units(chapters)
    render_stats = {}
    if isinstance(word_summary.get("summary"), dict):
        render_stats = word_summary["summary"].get("render_stats") or {}
    docx_heading_count = int(render_stats.get("heading_count") or 0)
    warnings: list[str] = []
    if generated_section_count > directory_heading_count + max(5, directory_heading_count // 10):
        warnings.append(
            f"正文标题可能未完全受目录约束：目录预计正式标题 {directory_heading_count} 个，聚合小节 {generated_section_count} 个。"
        )
    if major_image_count < 8:
        warnings.append(f"主要施工方案与技术措施图片偏少：当前 {major_image_count} 张。")
    if len(empty_core_process_units) >= 3:
        warnings.append(f"核心施工工艺无图小节较多：{len(empty_core_process_units)} 个。")
    return {
        "schema_version": "word_quality_gate_v0.1",
        "directory_heading_count": directory_heading_count,
        "generated_section_count": generated_section_count,
        "docx_heading_count": docx_heading_count,
        "image_ref_count": image_ref_count,
        "major_section_image_count": major_image_count,
        "empty_core_process_image_units": empty_core_process_units[:30],
        "status": "warning" if warnings else "passed",
        "warnings": warnings,
    }


def _expected_official_heading_count(packages: list[dict[str, Any]]) -> int:
    top_ids: set[str] = set()
    level2_ids: set[str] = set()
    level3_ids: set[str] = set()
    child_heading_count = 0
    for package in packages:
        unit = package.get("generation_unit") or {}
        top_id = str(unit.get("parent_level_1_node_id") or "")
        target_id = str(unit.get("target_node_id") or "")
        unit_type = str(unit.get("unit_type") or "")
        if top_id:
            top_ids.add(top_id)
        if unit_type == "level3_subsection_unit":
            parent_id = str(unit.get("parent_level_2_node_id") or "")
            if parent_id:
                level2_ids.add(parent_id)
            if target_id:
                level3_ids.add(target_id)
        elif target_id:
            level2_ids.add(target_id)
            child_heading_count += len([item for item in unit.get("child_headings") or [] if str(item).strip()])
    return len(top_ids) + len(level2_ids) + len(level3_ids) + child_heading_count


def _generated_section_count(chapters: list[dict[str, Any]]) -> int:
    return sum(1 for chapter in chapters for section in chapter.get("sections") or [] if isinstance(section, dict))


def _image_ref_count(obj: Any) -> int:
    if isinstance(obj, dict):
        return (1 if obj.get("type") == "image_ref" else 0) + sum(_image_ref_count(value) for value in obj.values())
    if isinstance(obj, list):
        return sum(_image_ref_count(item) for item in obj)
    return 0


def _major_section_image_count(chapters: list[dict[str, Any]]) -> int:
    total = 0
    for chapter in chapters:
        title = " ".join(str(part) for part in chapter.get("chapter_path") or [chapter.get("title") or ""])
        if "主要施工方案" in title:
            total += _image_ref_count(chapter)
    return total


def _empty_core_process_image_units(chapters: list[dict[str, Any]]) -> list[str]:
    keywords = ["测量", "土方", "基坑", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体"]
    result: list[str] = []
    for chapter in chapters:
        top_title = " > ".join(str(part) for part in chapter.get("chapter_path") or [chapter.get("title") or ""])
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "")
            if any(keyword in heading for keyword in keywords) and _image_ref_count(section) == 0:
                result.append(" > ".join([top_title, heading]))
    return result


def _chapter_generation_task_map(result: dict[str, Any], state_dir: Path) -> dict[str, dict[str, Any]]:
    tasks_by_unit: dict[str, dict[str, Any]] = {}
    chapter_dir = state_dir / "chapters"
    if chapter_dir.exists():
        for path in chapter_dir.glob("*.json"):
            try:
                artifact = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            task = artifact.get("task") if isinstance(artifact.get("task"), dict) else artifact
            unit_id = str(task.get("unit_id") or artifact.get("unit_id") or "")
            if unit_id:
                tasks_by_unit[unit_id] = _normalize_task_dict(task)
    for task in result.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        unit_id = str(task.get("unit_id") or "")
        if unit_id:
            tasks_by_unit[unit_id] = _normalize_task_dict(task)
    return tasks_by_unit


def _chapter_state_run_result(
    *,
    project_id: str,
    job_id: str,
    packages: list[dict[str, Any]],
    state_dir: Path,
) -> dict[str, Any]:
    tasks = list(_chapter_generation_task_map({"tasks": []}, state_dir).values())
    known_ids = {str(task.get("unit_id") or "") for task in tasks if task.get("unit_id")}
    for package in packages:
        unit = package.get("generation_unit") or {}
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id or unit_id in known_ids:
            continue
        tasks.append(
            {
                "unit_id": unit_id,
                "target_node_id": unit.get("target_node_id"),
                "chapter_path": unit.get("chapter_path") or [],
                "status": "pending",
                "duration_seconds": 0,
                "started_at": None,
                "completed_at": None,
                "model": None,
                "validation": {},
                "error": "尚未生成。",
            }
        )
    model_counts: dict[str, int] = {}
    for task in tasks:
        model = str(task.get("model") or "")
        if model:
            model_counts[model] = model_counts.get(model, 0) + 1
    primary_model = max(model_counts, key=model_counts.get) if model_counts else llm_config().model
    return {
        "schema_version": "chapter_generation_batch_run_v0.1",
        "generated_at": _now_iso(),
        "provider": llm_config().provider,
        "model": primary_model,
        "base_url": llm_config().base_url,
        "project_id": project_id,
        "job_id": job_id,
        "task_count": len(tasks),
        "completed_count": sum(1 for task in tasks if task.get("status") == "completed"),
        "skipped_count": sum(1 for task in tasks if task.get("status") == "skipped"),
        "failed_count": sum(1 for task in tasks if task.get("status") == "failed"),
        "duration_seconds": 0,
        "execution_mode": "aggregate_refresh_only",
        "max_workers": 0,
        "tasks": tasks,
        "chapters": [],
        "warnings": ["本次仅刷新聚合结果、正文预览和 Word 初稿，未调用大模型生成正文。"],
    }


def _aggregate_chapter_generation_result(current_result: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    tasks = list(_chapter_generation_task_map(current_result, state_dir).values())
    tasks.sort(key=lambda item: " > ".join(str(part) for part in item.get("chapter_path") or []))
    chapters = [
        _chapter_from_state_artifact(task, state_dir)
        for task in tasks
        if task.get("status") == "completed"
    ]
    chapters = [chapter for chapter in chapters if isinstance(chapter, dict)]
    aggregate = dict(current_result)
    aggregate["schema_version"] = "chapter_generation_aggregate_run_v0.1"
    aggregate["task_count"] = len(tasks)
    aggregate["completed_count"] = len(chapters)
    aggregate["skipped_count"] = sum(1 for task in tasks if task.get("status") == "skipped")
    aggregate["failed_count"] = sum(1 for task in tasks if task.get("status") == "failed")
    aggregate["chapters"] = chapters
    aggregate["tasks"] = tasks
    aggregate["execution_mode"] = "aggregate_resumable_state"
    aggregate.setdefault("warnings", [])
    return aggregate


def _chapter_from_state_artifact(task: dict[str, Any], state_dir: Path) -> dict[str, Any] | None:
    unit_id = str(task.get("unit_id") or "")
    if not unit_id:
        return None
    artifact_path = state_dir / "chapters" / f"{_safe_state_filename(unit_id)}.json"
    if not artifact_path.exists():
        parsed = task.get("parsed_json")
        return parsed if isinstance(parsed, dict) else None
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    chapter = artifact.get("chapter")
    return chapter if isinstance(chapter, dict) else None


def _safe_state_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    text = text.strip("._")
    return text or "unit"


def _normalize_task_dict(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": task.get("unit_id"),
        "target_node_id": task.get("target_node_id"),
        "chapter_path": task.get("chapter_path") or [],
        "status": task.get("status"),
        "duration_seconds": task.get("duration_seconds"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "model": task.get("model"),
        "validation": task.get("validation") or {},
        "error": task.get("error"),
        "cache_status": task.get("cache_status"),
        "cache_key": task.get("cache_key"),
        "resume_action": task.get("resume_action"),
        "resume_reason": task.get("resume_reason"),
        "failure_type": task.get("failure_type"),
        "failure_reason": task.get("failure_reason"),
        "repair_attempt_count": task.get("repair_attempt_count") or 0,
        "repair_duration_seconds": task.get("repair_duration_seconds") or 0,
    }


def _project_technical_run(extraction_inputs: dict[str, Any], *, project: dict[str, Any]) -> dict[str, Any]:
    project_package = _package(extraction_inputs, "project_info_extraction_input")
    technical_package = _package(extraction_inputs, "technical_requirements_extraction_input")
    project_info = _extract_project_info(project_package, fallback_project_name=str(project.get("name") or ""))
    technical = _extract_technical_requirements(technical_package)
    return _run_result(
        extraction_inputs,
        tasks=[
            _task_result(
                "project_info_extraction_input",
                "项目基础信息抽取",
                project_info,
                summary="轻量规则抽取项目基础信息。",
            ),
            _task_result(
                "technical_requirements_extraction_input",
                "技术标准与编制要求抽取",
                technical,
                summary=f"轻量规则抽取 {len(technical.get('requirements') or [])} 条技术编制要求。",
            ),
        ],
    )


def _score_points_run(extraction_inputs: dict[str, Any]) -> dict[str, Any]:
    score_package = _package(extraction_inputs, "score_points_extraction_input")
    points, used_fallback = _extract_score_points(score_package)
    issues = []
    if used_fallback:
        issues.append(
            {
                "severity": "warning",
                "type": "score_points_rule_fallback",
                "message": "轻量规则未能从评标办法前附表稳定识别评分点，已使用房建技术标常见评分点兜底，目录生成前必须人工复核。",
            }
        )
    quality_gate = {
        "blocking": False,
        "issue_count": len(issues),
        "blocking_issue_count": 0,
        "warning_issue_count": len(issues),
        "score_point_count": len(points),
        "scored_count": sum(1 for item in points if item.get("score")),
        "unscored_count": sum(1 for item in points if not item.get("score")),
        "score_total": _score_total(points),
        "issues": issues,
    }
    parsed_json = {
        "score_points": [
            {
                "model_observed_text": point.get("score_point_raw"),
                "score_point_ref": point.get("score_point_ref"),
                "score_ref": point.get("score_ref"),
                "description_ref": point.get("description_ref"),
                "belongs_to_technical_bid": True,
                "confidence": point.get("confidence"),
                "needs_confirmation": point.get("needs_confirmation"),
                "confirmation_reason": point.get("confirmation_reason"),
            }
            for point in points
        ],
        "system_final_score_points": points,
        "quality_gate": quality_gate,
    }
    return _run_result(
        extraction_inputs,
        tasks=[
            _task_result(
                "score_points_extraction_input",
                "技术标评分点抽取",
                parsed_json,
                summary=f"轻量规则抽取 {len(points)} 个技术标评分点。",
                quality_gate=quality_gate,
            )
        ],
    )


def _extract_project_info(package: dict[str, Any], *, fallback_project_name: str) -> dict[str, Any]:
    rows = _cell_rows(package)
    blocks = package.get("block_refs") or []
    fields = {
        "project_name": _find_project_field(rows, blocks, ("项目名称", "工程名称"), fallback=fallback_project_name),
        "location": _find_project_field(rows, blocks, ("建设地点", "项目地点", "工程地点")),
        "scale": _find_project_field(rows, blocks, ("建设规模", "工程规模", "建筑面积")),
        "scope": _find_project_field(rows, blocks, ("招标范围", "承包范围", "采购范围")),
        "duration": _find_project_field(rows, blocks, ("计划工期", "工期要求", "工期")),
        "quality": _find_project_field(rows, blocks, ("质量要求", "质量标准")),
        "safety_civilized": _find_project_field(rows, blocks, ("安全文明", "安全生产", "文明施工")),
    }
    all_text = _package_text(package)
    contains_design = bool(re.search(r"EPC|工程总承包|设计施工总承包|勘察设计施工|设计采购施工", all_text, re.I))
    return {
        "project_type": "epc" if contains_design else "construction",
        "contains_design_task": contains_design,
        "project_type_confidence": 0.82 if contains_design else 0.7,
        "project_type_needs_confirmation": not contains_design and "设计" in all_text,
        "fields": fields,
    }


def _extract_technical_requirements(package: dict[str, Any]) -> dict[str, Any]:
    block_refs = package.get("block_refs") or []
    requirement_blocks = _matching_blocks(
        block_refs,
        ("技术标", "施工组织设计", "施工方案", "质量", "安全", "文明施工", "绿色施工", "进度", "工期", "BIM"),
        limit=12,
    )
    standard_blocks = _matching_blocks(
        block_refs,
        ("技术标准", "技术要求", "发包人要求", "规范", "标准", "规程", "验收", "图纸"),
        limit=12,
    )
    return {
        "requirements": [
            {
                "requirement_type": _requirement_type(item["text"]),
                "model_observed_text": item["text"],
                "requirement_ref": {"type": "block", "id": f"B{item['block_index']}"},
                "confidence": 0.72,
                "needs_confirmation": True,
            }
            for item in requirement_blocks
        ],
        "technical_standards": [
            {
                "standard_type": _requirement_type(item["text"]),
                "model_observed_text": item["text"],
                "standard_ref": {"type": "block", "id": f"B{item['block_index']}"},
                "target_section_hint": _target_section_hint(item["text"]),
                "confidence": 0.72,
                "needs_confirmation": True,
            }
            for item in standard_blocks
        ],
        "technical_risks": [],
        "conflicts": [],
    }


def _extract_score_points(package: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = _cell_rows(package)
    technical_rows = _technical_score_rows(rows)
    candidate_rows = technical_rows or rows
    for row in candidate_rows:
        point = _score_point_from_row(row)
        if point is None:
            continue
        key = _normalize_title(point["score_point_raw"])
        if key in seen:
            continue
        seen.add(key)
        points.append(point)

    if not points:
        for block in package.get("block_refs") or []:
            for line in _split_text_lines(str(block.get("text_preview") or "")):
                point = _score_point_from_line(line, block)
                if point is None:
                    continue
                key = _normalize_title(point["score_point_raw"])
                if key in seen:
                    continue
                seen.add(key)
                points.append(point)

    if points:
        return points[:40], False

    fallback_points = [
        {
            "score_point_raw": title,
            "level_1_heading_text": _normalize_heading_text(title),
            "score_point_ref": None,
            "score": None,
            "score_ref": None,
            "description": rule,
            "description_ref": None,
            "parent_text": None,
            "parent_ref": None,
            "model_observed_text": title,
            "belongs_to_technical_bid": True,
            "used_as_level_1_heading": True,
            "needs_confirmation": True,
            "confirmation_reason": "未能从上传文件中稳定识别评分点来源，使用常见技术标评分点兜底。",
            "confidence": 0.35,
        }
        for title, rule in FALLBACK_SCORE_POINTS
    ]
    return fallback_points, True


def _score_point_from_row(row: list[dict[str, Any]]) -> dict[str, Any] | None:
    texts = [str(cell.get("text_raw") or "").strip() for cell in row]
    row_text = " ".join(texts)
    if _is_score_section_title(row_text):
        return None
    if not _contains_any(row_text, TECHNICAL_SCORE_TITLE_HINTS):
        return None
    title_cell = _choose_score_title_cell(row)
    if title_cell is None:
        return None
    title = _clean_score_title(str(title_cell.get("text_raw") or ""))
    if (
        not title
        or len(title) > 60
        or _is_score_section_title(title)
        or _is_non_technical_score_row(title)
        or not _contains_any(title + row_text, TECHNICAL_SCORE_TITLE_HINTS)
    ):
        return None
    score_cell = _find_score_cell(row)
    score_value = _extract_score_text(str(score_cell.get("text_raw") or "")) if score_cell else None
    description_cell = _find_description_cell(row, exclude_ids={title_cell.get("cell_id"), score_cell.get("cell_id") if score_cell else None})
    description = str((description_cell or {}).get("text_raw") or "").strip()
    return {
        "score_point_raw": title,
        "level_1_heading_text": _normalize_heading_text(title),
        "score_point_ref": {"type": "cell", "id": title_cell["cell_id"]},
        "score": score_value,
        "score_ref": {"type": "cell", "id": score_cell["cell_id"]} if score_cell else None,
        "description": description or row_text,
        "description_ref": {"type": "cell", "id": description_cell["cell_id"]} if description_cell else None,
        "parent_text": None,
        "parent_ref": None,
        "model_observed_text": title,
        "belongs_to_technical_bid": True,
        "used_as_level_1_heading": True,
        "needs_confirmation": False,
        "confirmation_reason": None,
        "confidence": 0.86,
    }


def _score_point_from_line(line: str, block: dict[str, Any]) -> dict[str, Any] | None:
    text = _clean_score_title(line)
    if not text or len(text) > 80 or not _looks_like_technical_score_text(text):
        return None
    score = _extract_score_text(text)
    title = re.sub(r"[（(]?\d+(?:\.\d+)?\s*分[）)]?", "", text).strip(" ：:；;，,。")
    if not title:
        return None
    return {
        "score_point_raw": title,
        "level_1_heading_text": _normalize_heading_text(title),
        "score_point_ref": {"type": "block", "id": f"B{block.get('block_index')}"},
        "score": score,
        "score_ref": {"type": "block", "id": f"B{block.get('block_index')}"} if score else None,
        "description": text,
        "description_ref": {"type": "block", "id": f"B{block.get('block_index')}"},
        "parent_text": None,
        "parent_ref": None,
        "model_observed_text": title,
        "belongs_to_technical_bid": True,
        "used_as_level_1_heading": True,
        "needs_confirmation": True,
        "confirmation_reason": "该评分点由段落文本轻量规则识别，需人工对照评标办法前附表确认。",
        "confidence": 0.55,
    }


def _render_lightweight_chapter_draft(
    project: dict[str, Any],
    outline: dict[str, Any],
    parse_result: dict[str, Any],
    packages: list[dict[str, Any]],
) -> str:
    project_info = parse_result.get("project_info") or {}
    lines = [
        "# 技术标正文初稿（轻量预览版）",
        "",
        "> 当前文件由后端轻量规则执行器生成，用于打通“招标文件上传 -> 解析报告 -> 目录 -> 正文初稿”主流程；正式正文生成时将替换为 LLM 扩写与 Word 渲染结果。",
        "",
        "## 项目基础信息",
        "",
        f"- 项目名称：{_field_value(project_info.get('project_name')) or project.get('name') or '未明确'}",
        f"- 项目类型：{(parse_result.get('project_type') or {}).get('value') or 'construction'}",
        f"- 建设地点：{_field_value(project_info.get('construction_location')) or '未明确'}",
        f"- 工期要求：{_field_value(project_info.get('duration_requirement')) or '未明确'}",
        f"- 质量要求：{_field_value(project_info.get('quality_requirement')) or '未明确'}",
        "",
        "## 评分点响应摘要",
        "",
    ]
    for index, point in enumerate(parse_result.get("technical_score_points") or [], start=1):
        lines.append(f"{index}. {point.get('original_text') or point.get('catalog_level_1_title') or '未命名评分点'}")
    lines.extend(["", "## 正文初稿", ""])

    package_by_path = {
        " > ".join((package.get("generation_unit") or {}).get("chapter_path") or []): package
        for package in packages
    }
    for node in outline.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("domain") == "design":
            lines.extend(
                [
                    f"## {node.get('number') or ''}. {node.get('title') or ''}",
                    "",
                    "本评分点属于设计方案域，一期正文生成暂不自动扩写，不影响施工方案正文生成和导出。",
                    "",
                ]
            )
            continue
        lines.extend(
            [
                f"## {node.get('number') or ''}. {node.get('title') or ''}",
                "",
                _score_response_paragraph(node),
                "",
            ]
        )
        children = [child for child in node.get("children") or [] if isinstance(child, dict)]
        if not children:
            lines.extend(_draft_unit_lines([str(node.get("title") or "")], package_by_path))
            continue
        for child in children:
            path = [str(node.get("title") or ""), str(child.get("title") or "")]
            lines.extend(
                [
                    f"### {child.get('number') or ''} {child.get('title') or ''}",
                    "",
                    _draft_unit_intro(path, package_by_path),
                    "",
                ]
            )
            grand_children = [item for item in child.get("children") or [] if isinstance(item, dict)]
            for grandchild in grand_children:
                lines.extend(
                    [
                        f"#### {grandchild.get('number') or ''} {grandchild.get('title') or ''}",
                        "",
                        "围绕本小节要求，后续正式生成将结合招标文件技术标准、企业优秀标书素材和项目参数扩写为完整正文、表格和图片引用。",
                        "",
                    ]
                )
    review_items = parse_result.get("review_items") or []
    lines.extend(["## 人工复核清单", ""])
    for item in review_items[:30]:
        lines.append(f"- [{item.get('priority') or 'medium'}] {item.get('item') or ''}：{item.get('suggested_action') or ''}")
    if not review_items:
        lines.append("- 暂无自动复核项。")
    lines.append("")
    return "\n".join(lines)


def _chapter_generation_summary(
    project_id: str,
    job_id: str,
    outline: dict[str, Any],
    packages: list[dict[str, Any]],
    storage: LocalStorageService,
    draft_path: Path,
    inputs_json: Path,
) -> dict[str, Any]:
    generation_units = []
    for package in packages:
        unit = package.get("generation_unit") or {}
        path = [str(item) for item in unit.get("chapter_path") or [] if item]
        generation_units.append(
            {
                "unit_id": unit.get("unit_id"),
                "chapter": path[-1] if path else "未命名章节",
                "chapter_path": path,
                "domain": unit.get("domain"),
                "unit_type": unit.get("unit_type"),
                "status": "已生成轻量预览",
                "material": _material_summary(package),
            }
        )
    design_nodes = [node for node in outline.get("nodes") or [] if isinstance(node, dict) and node.get("domain") == "design"]
    return {
        "schema_version": "chapter_generation_summary_v0.1",
        "project_id": project_id,
        "job_id": job_id,
        "generated_at": _now_iso(),
        "mode": "lightweight_markdown_preview",
        "generation_unit_count": len(generation_units),
        "skipped_design_node_count": len(design_nodes),
        "draft_uri": storage.to_uri(draft_path),
        "chapter_inputs_uri": storage.to_uri(inputs_json),
        "generation_units": generation_units,
        "warnings": [
            "当前为轻量 Markdown 正文初稿，尚未调用 LLM 扩写，也尚未渲染为正式 Word。",
            "施工进度图、施工总平面图等项目专属图仍需人工补充。",
        ],
    }


def _lightweight_chapter_generation_result(project_id: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
    chapters = [_lightweight_generated_chapter(package) for package in packages]
    generated_at = _now_iso()
    return {
        "schema_version": "technical_bid_chapter_batch_result_v1",
        "project_id": project_id,
        "generated_at": generated_at,
        "provider": "backend_rule_based",
        "model": "lightweight-chapter-preview",
        "base_url": None,
        "execution_mode": "lightweight_markdown_preview",
        "max_workers": 0,
        "duration_seconds": 0,
        "task_count": len(chapters),
        "completed_count": len(chapters),
        "skipped_count": 0,
        "failed_count": 0,
        "chapters": chapters,
        "tasks": [
            {
                "unit_id": chapter["unit_id"],
                "target_node_id": chapter["target_node_id"],
                "chapter_path": chapter["chapter_path"],
                "status": "completed",
                "started_at": generated_at,
                "completed_at": generated_at,
                "duration_seconds": 0,
                "cache_status": "lightweight_preview",
                "validation": {"summary": "轻量规则正文预览已生成，正式投标文件仍建议执行 LLM 扩写并人工复核。"},
            }
            for chapter in chapters
        ],
        "warnings": [
            "当前正文聚合结果由轻量预览生成，主要用于本地演示、Word 导出链路验收和人工复核占位。",
            "正式成稿前建议执行章节大模型生成，并复核评分点覆盖、表格、图片和格式。",
        ],
    }


def _lightweight_generated_chapter(package: dict[str, Any]) -> dict[str, Any]:
    unit = package.get("generation_unit") or {}
    path = [str(item) for item in unit.get("chapter_path") or [] if item]
    title = path[-1] if path else "章节正文"
    material = _material_summary(package)
    score_point = package.get("score_point") or {}
    score_title = (
        score_point.get("score_point_raw")
        or score_point.get("catalog_level_1_title")
        or (path[0] if path else title)
    )
    return {
        "schema_version": "technical_bid_chapter_draft_v1",
        "unit_id": unit.get("unit_id") or "",
        "target_node_id": unit.get("target_node_id") or "",
        "chapter_path": path,
        "title": title,
        "sections": [
            {
                "heading": title,
                "level": 2 if len(path) <= 2 else 3,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": (
                            f"本节围绕评分点“{score_title}”展开，重点补充编制依据、目标承诺、"
                            "组织体系、主要措施、过程控制和复核要点。"
                        ),
                    },
                    {
                        "type": "paragraph",
                        "text": f"当前为轻量规则预览稿，素材状态：{material}。正式成稿前应执行 LLM 扩写并由编标人员复核。",
                    },
                ],
            }
        ],
        "score_response_check": {
            "score_point_raw": score_title,
            "response_summary": f"已形成“{title}”轻量预览正文，后续需结合招标原文和参考资料扩写。",
            "covered": False,
            "evidence_headings": [title],
        },
        "source_usage": [],
        "review_items": [
            {
                "severity": "medium",
                "type": "lightweight_preview_review",
                "message": f"{' > '.join(path) or title} 当前为轻量预览，正式提交前需执行 LLM 正文生成并人工确认。",
            }
        ],
    }


def _task_result(
    task_key: str,
    task_title: str,
    parsed_json: dict[str, Any],
    *,
    summary: str,
    quality_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation: dict[str, Any] = {"summary": summary, "issues": []}
    if quality_gate is not None:
        validation["quality_gate"] = quality_gate
        validation["issues"] = [item.get("message") for item in quality_gate.get("issues") or [] if item.get("message")]
        validation["issue_count"] = len(validation["issues"])
    return {
        "task_key": task_key,
        "task_title": task_title,
        "status": "completed",
        "started_at": _now_iso(),
        "completed_at": _now_iso(),
        "duration_seconds": 0.0,
        "input_estimated_tokens": 0,
        "parsed_json": parsed_json,
        "validation": validation,
        "cache_status": "disabled",
    }


def _run_result(extraction_inputs: dict[str, Any], *, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "tender_llm_extraction_run_v0.1",
        "source_input_path": extraction_inputs.get("source_path") or "",
        "source_file": extraction_inputs.get("file_name") or "",
        "provider": "backend_rule_based",
        "model": "lightweight-rule-extractor",
        "base_url": None,
        "api_type": "none",
        "execution_mode": "lightweight_rule_based",
        "max_workers": 1,
        "duration_seconds": 0.0,
        "started_at": _now_iso(),
        "completed_at": _now_iso(),
        "task_count": len(tasks),
        "completed_task_count": len(tasks),
        "failed_task_count": 0,
        "tasks": tasks,
        "warnings": [],
    }


def _select_primary_tender_file(files: list[dict[str, Any]]) -> dict[str, Any]:
    priority = {"docx": 0, "doc": 1, "pdf": 2}
    return sorted(files, key=lambda item: priority.get(str(item.get("file_ext") or "").lower(), 9))[0]


def _build_parse_intermediate_outputs(
    primary_path: Path,
    *,
    file_id: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    try:
        document_index = build_tender_document_index(primary_path, file_id=file_id)
        extraction_inputs = build_tender_extraction_inputs_from_path(
            primary_path,
            file_id=file_id,
            input_profile="balanced",
        )
    except Exception as exc:
        document_index_data = _fallback_document_index(primary_path, file_id=file_id, error=exc)
        extraction_inputs_data = _fallback_extraction_inputs(primary_path, file_id=file_id, error=exc)
        return (
            document_index_data,
            _fallback_document_index_report(document_index_data),
            extraction_inputs_data,
            _fallback_extraction_inputs_report(extraction_inputs_data),
        )
    return (
        document_index.to_dict(),
        render_tender_document_index_report(document_index),
        extraction_inputs.to_dict(),
        render_tender_extraction_input_report(extraction_inputs),
    )


def _fallback_document_index(primary_path: Path, *, file_id: str, error: Exception) -> dict[str, Any]:
    file_type = primary_path.suffix.lower().lstrip(".") or "unknown"
    return {
        "schema_version": "tender_document_index_v0.1",
        "source_path": str(primary_path),
        "file_id": file_id,
        "file_name": primary_path.name,
        "file_type": file_type,
        "document_profile": {
            "file_count": 1,
            "has_word": file_type in {"doc", "docx"},
            "has_pdf": file_type == "pdf",
            "has_scanned_pdf": False,
            "paragraph_count": 0,
            "table_count": 0,
            "image_count": 0,
            "page_count": None,
            "header_footer_ignored": True,
            "toc_detected": False,
        },
        "detected_sections": [],
        "blocks": [],
        "warnings": [f"文件结构解析失败：{type(error).__name__}: {error}"],
    }


def _fallback_extraction_inputs(primary_path: Path, *, file_id: str, error: Exception) -> dict[str, Any]:
    file_type = primary_path.suffix.lower().lstrip(".") or "unknown"
    packages = []
    for task_key, title, description, region_keys in [
        (
            "project_info_extraction_input",
            "项目信息抽取输入包",
            "用于抽取项目名称、建设地点、建设规模、招标范围、工期要求、质量要求、安全文明要求和项目类型。",
            ["chapter_1_notice", "bidder_instructions_preface_table"],
        ),
        (
            "score_points_extraction_input",
            "技术标评分点抽取输入包",
            "用于抽取技术标评分点；一级目录名称必须保留招标文件原文表述。",
            ["evaluation_method_preface_table"],
        ),
        (
            "technical_requirements_extraction_input",
            "技术标准与编制要求抽取输入包",
            "用于抽取技术标准、发包人要求、技术标编制要求、格式/内容约束。",
            ["bidder_instructions_preface_table", "technical_standards_and_requirements"],
        ),
    ]:
        packages.append(
            {
                "task_key": task_key,
                "task_title": title,
                "task_description": description,
                "input_profile": "balanced",
                "source_path": str(primary_path),
                "file_id": file_id,
                "file_name": primary_path.name,
                "file_type": file_type,
                "region_keys": region_keys,
                "regions": [],
                "source_refs": [],
                "block_refs": [],
                "cell_refs": [],
                "block_count": 0,
                "source_text_char_count": 0,
                "included_block_count": 0,
                "dropped_duplicate_block_count": 0,
                "input_unit_count": 0,
                "included_text_char_count": 0,
                "text_char_count": 0,
                "estimated_tokens": 0,
                "input_text": "",
                "warnings": [f"文件解析失败，无法构建真实输入包：{type(error).__name__}: {error}"],
            }
        )
    return {
        "schema_version": "tender_extraction_inputs_v0.1",
        "source_path": str(primary_path),
        "file_id": file_id,
        "file_name": primary_path.name,
        "file_type": file_type,
        "input_profile": "balanced",
        "package_count": len(packages),
        "packages": packages,
        "warnings": [f"文件解析失败，已生成兜底输入包：{type(error).__name__}: {error}"],
    }


def _fallback_document_index_report(document_index_data: dict[str, Any]) -> str:
    warnings = "\n".join(f"- {warning}" for warning in document_index_data.get("warnings") or [])
    return "\n".join(
        [
            "# 招标文件结构索引报告",
            "",
            f"- 文件：`{document_index_data.get('source_path')}`",
            f"- 文件类型：{document_index_data.get('file_type')}",
            "- 解析状态：失败，已生成兜底索引。",
            "",
            "## 警告",
            "",
            warnings or "- 无",
            "",
        ]
    )


def _fallback_extraction_inputs_report(extraction_inputs_data: dict[str, Any]) -> str:
    warnings = "\n".join(f"- {warning}" for warning in extraction_inputs_data.get("warnings") or [])
    return "\n".join(
        [
            "# 招标文件抽取输入包报告",
            "",
            f"- 文件：`{extraction_inputs_data.get('source_path')}`",
            "- 解析状态：失败，已生成兜底输入包。",
            f"- 输入包数量：{extraction_inputs_data.get('package_count')}",
            "",
            "## 警告",
            "",
            warnings or "- 无",
            "",
        ]
    )


def _resolve_upload_path(storage: LocalStorageService, file_record: dict[str, Any]) -> Path | None:
    storage_uri = file_record.get("storage_uri")
    if not isinstance(storage_uri, str):
        return None
    try:
        return storage.resolve_local_path(storage_uri)
    except ValueError:
        return None


def _project_artifact_dir(storage: LocalStorageService, project_id: str, category: str) -> Path:
    path = _project_artifact_path(storage, project_id, category, ".keep").parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_artifact_path(storage: LocalStorageService, project_id: str, category: str, file_name: str) -> Path:
    return storage.resolve_local_path(f"local://projects/{project_id}/{category}/{file_name}")


def _artifact_bundle(
    storage: LocalStorageService,
    project_id: str,
    specs: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    artifacts = []
    for kind, category, file_name in specs:
        path = _project_artifact_path(storage, project_id, category, file_name)
        if not path.exists():
            continue
        try:
            storage_uri = storage.to_uri(path)
        except ValueError:
            storage_uri = str(path)
        artifacts.append(
            {
                "kind": kind,
                "category": category,
                "file_name": file_name,
                "storage_uri": storage_uri,
                "size": path.stat().st_size,
            }
        )
    return artifacts


def _package(extraction_inputs: dict[str, Any], task_key: str) -> dict[str, Any]:
    for item in extraction_inputs.get("packages") or []:
        if item.get("task_key") == task_key:
            return item
    return {"task_key": task_key, "cell_refs": [], "block_refs": [], "input_text": ""}


def _cell_rows(package: dict[str, Any]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for cell in package.get("cell_refs") or []:
        block_index = cell.get("block_index")
        row_index = cell.get("row_index")
        if not isinstance(block_index, int) or not isinstance(row_index, int):
            continue
        grouped.setdefault((block_index, row_index), []).append(cell)
    rows = []
    for key in sorted(grouped):
        row = sorted(grouped[key], key=lambda item: int(item.get("cell_index") or 0))
        rows.append(row)
    return rows


def _find_project_field(
    rows: list[list[dict[str, Any]]],
    blocks: list[dict[str, Any]],
    labels: tuple[str, ...],
    *,
    fallback: str | None = None,
) -> dict[str, Any]:
    for row in rows:
        for index, cell in enumerate(row):
            text = str(cell.get("text_raw") or "").strip()
            if not _contains_any(text, labels):
                continue
            inline = _value_after_label(text, labels)
            if inline:
                return _field(inline, cell, confidence=0.78)
            for value_cell in row[index + 1 :]:
                value = str(value_cell.get("text_raw") or "").strip()
                if value and not _contains_any(value, labels):
                    return _field(value, value_cell, confidence=0.82)
    for block in blocks:
        text = str(block.get("text_preview") or "").strip()
        if not _contains_any(text, labels):
            continue
        value = _value_after_label(text, labels)
        if value:
            return _field(value, block, confidence=0.65, ref_type="block")
    if fallback:
        return {
            "field_ref": None,
            "model_observed_text": fallback,
            "value": fallback,
            "confidence": 0.35,
            "needs_confirmation": True,
            "confirmation_reason": "未从招标文件中稳定识别，暂使用项目名称兜底。",
        }
    return {
        "field_ref": None,
        "model_observed_text": None,
        "value": None,
        "confidence": 0.0,
        "needs_confirmation": True,
        "confirmation_reason": "轻量规则未识别到该字段，需人工复核。",
    }


def _field(value: str, source: dict[str, Any], *, confidence: float, ref_type: str = "cell") -> dict[str, Any]:
    ref_id = source.get("cell_id") if ref_type == "cell" else f"B{source.get('block_index')}"
    return {
        "field_ref": {"type": ref_type, "id": ref_id} if ref_id else None,
        "model_observed_text": value,
        "value": value,
        "confidence": confidence,
        "needs_confirmation": confidence < 0.8,
        "confirmation_reason": None if confidence >= 0.8 else "轻量规则抽取结果，需人工确认。",
    }


def _value_after_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        if label not in text:
            continue
        pattern = rf"{re.escape(label)}\s*[:：]?\s*(.+)$"
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip(" ：:；;，,。")
            if value and value != label:
                return value[:200]
    return ""


def _matching_blocks(blocks: list[dict[str, Any]], keywords: tuple[str, ...], *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        text = str(block.get("text_preview") or "").strip()
        if len(text) < 4 or not _contains_any(text, keywords):
            continue
        normalized = _normalize_title(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append({"block_index": block.get("block_index"), "text": text[:500]})
        if len(result) >= limit:
            break
    return result


def _choose_score_title_cell(row: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for index, cell in enumerate(row):
        text = _clean_score_title(str(cell.get("text_raw") or ""))
        if not text or text in STRUCTURAL_SCORE_TEXTS or text in {"序号", "评分标准", "分值", "内容"}:
            continue
        if _is_score_section_title(text) or _is_non_technical_score_row(text):
            continue
        if _looks_like_non_title_cell(text):
            continue
        score = 0
        if _contains_any(text, TECHNICAL_SCORE_TITLE_HINTS):
            score += 6
        if 2 <= len(text) <= 36:
            score += 2
        if index <= 2:
            score += 1
        if score:
            candidates.append((score, index, cell))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _find_score_cell(row: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cell in row:
        text = str(cell.get("text_raw") or "")
        if _extract_score_text(text):
            return cell
    return None


def _find_description_cell(row: list[dict[str, Any]], *, exclude_ids: set[Any]) -> dict[str, Any] | None:
    available = [cell for cell in row if cell.get("cell_id") not in exclude_ids]
    if not available:
        return None
    return max(available, key=lambda cell: len(str(cell.get("text_raw") or "")))


def _clean_score_title(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^[\d一二三四五六七八九十]+[、.)．]\s*", "", cleaned)
    return cleaned.strip(" ：:；;，,。")


def _looks_like_technical_score_text(text: str) -> bool:
    if not text or _contains_any(text, NON_TECHNICAL_SCORE_KEYWORDS):
        return False
    return _contains_any(text, TECHNICAL_SCORE_TITLE_HINTS)


def _looks_like_non_title_cell(text: str) -> bool:
    if len(text) > 80:
        return True
    if len(text) > 28 and re.search(r"[。；;，,].*[（(]0\s*[-－]", text):
        return True
    if re.fullmatch(r"[\d.]+", text):
        return True
    if len(text) <= 4 and _extract_score_text(text):
        return True
    if text.count("。") >= 2:
        return True
    if _is_score_section_title(text):
        return True
    return False


def _technical_score_rows(rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    selected: list[list[dict[str, Any]]] = []
    in_technical_section = False
    for row in rows:
        row_text = " ".join(str(cell.get("text_raw") or "").strip() for cell in row if str(cell.get("text_raw") or "").strip())
        if not row_text:
            continue
        if _contains_any(row_text, TECHNICAL_SCORE_SECTION_START_KEYWORDS):
            in_technical_section = True
            continue
        if in_technical_section and _contains_any(row_text, TECHNICAL_SCORE_SECTION_END_KEYWORDS):
            break
        if in_technical_section:
            selected.append(row)
    return selected


def _is_score_section_title(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    if normalized in STRUCTURAL_SCORE_TEXTS:
        return True
    return any(re.search(pattern, normalized) for pattern in TECHNICAL_SCORE_SECTION_TITLE_PATTERNS)


def _is_non_technical_score_row(text: str) -> bool:
    return _contains_any(text, NON_TECHNICAL_SCORE_KEYWORDS)


def _extract_score_text(text: str) -> str | None:
    stripped = text.strip()
    match = re.search(r"\d+(?:\.\d+)?\s*分", stripped)
    if match:
        return match.group(0)
    if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
        return f"{stripped}分"
    return None


def _score_total(points: list[dict[str, Any]]) -> float | None:
    total = 0.0
    count = 0
    for item in points:
        score = _extract_score_text(str(item.get("score") or ""))
        if not score:
            continue
        total += float(re.search(r"\d+(?:\.\d+)?", score).group(0))  # type: ignore[union-attr]
        count += 1
    return total if count else None


def _requirement_type(text: str) -> str:
    if "质量" in text:
        return "quality"
    if "安全" in text:
        return "safety"
    if "文明" in text or "扬尘" in text or "环保" in text:
        return "civilized_environment"
    if "工期" in text or "进度" in text:
        return "schedule"
    if "设计" in text:
        return "design"
    return "technical"


def _target_section_hint(text: str) -> str | None:
    mapping = [
        ("质量", "质量管理体系与措施"),
        ("安全", "安全管理体系与措施"),
        ("文明", "文明施工与环境保护措施"),
        ("扬尘", "文明施工与环境保护措施"),
        ("工期", "工期保证措施"),
        ("进度", "施工进度计划"),
        ("设计", "设计方案"),
    ]
    for keyword, hint in mapping:
        if keyword in text:
            return hint
    return None


def _parse_input_file(file_record: dict[str, Any], storage: LocalStorageService, *, is_primary: bool) -> dict[str, Any]:
    path = _resolve_upload_path(storage, file_record)
    warnings = [] if is_primary else ["当前轻量解析仅选择一份主文件执行结构化解析，该文件作为辅助资料保留。"]
    return {
        "file_id": file_record.get("file_id"),
        "file_name": file_record.get("file_name"),
        "file_type": file_record.get("file_ext"),
        "source_path": str(path) if path else "",
        "parse_status": "success" if is_primary else "uploaded_not_primary",
        "warnings": warnings,
    }


def _load_existing_excellent_bid_index(storage: LocalStorageService) -> dict[str, Any] | None:
    manifest_path = storage.storage_root / "knowledge_base" / "excellent_bids" / "indexes" / "library_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        uri = manifest.get("aggregate_index_uri")
        if not isinstance(uri, str) or not uri.startswith("local://"):
            return None
        index_path = storage.resolve_local_path(uri)
        if not index_path.exists():
            return None
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _material_summary(package: dict[str, Any]) -> str:
    text_refs = len(package.get("excellent_bid_references") or [])
    table_refs = len(package.get("table_references") or [])
    image_refs = len(package.get("image_candidates") or [])
    if not any([text_refs, table_refs, image_refs]):
        return "等待正式素材匹配"
    return f"正文参考 {text_refs} 条，表格 {table_refs} 个，图片候选 {image_refs} 张"


def _score_response_paragraph(node: dict[str, Any]) -> str:
    score = node.get("score") or "未明确分值"
    rule = str(node.get("score_rule") or "").strip()
    if rule:
        return f"本章响应评分点“{node.get('title') or ''}”（{score}）。编制时应重点覆盖：{rule[:240]}。"
    return f"本章响应评分点“{node.get('title') or ''}”（{score}），正式生成时将结合招标文件要求和优秀标书素材扩写。"


def _draft_unit_lines(path: list[str], package_by_path: dict[str, dict[str, Any]]) -> list[str]:
    return [_draft_unit_intro(path, package_by_path), ""]


def _draft_unit_intro(path: list[str], package_by_path: dict[str, dict[str, Any]]) -> str:
    package = package_by_path.get(" > ".join(path))
    material = _material_summary(package) if package else "等待正式素材匹配"
    title = path[-1] if path else "本节"
    return f"{title}将围绕招标评分要求展开，正式生成时补充编制依据、目标承诺、组织体系、主要措施、控制要点和复核提示。素材状态：{material}。"


def _field_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return ""


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _package_text(package: dict[str, Any]) -> str:
    parts = [str(package.get("input_text") or "")]
    parts.extend(str(block.get("text_preview") or "") for block in package.get("block_refs") or [])
    parts.extend(str(cell.get("text_raw") or "") for cell in package.get("cell_refs") or [])
    return "\n".join(parts)


def _split_text_lines(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    compact = _normalize_title(text)
    return any(_normalize_title(keyword) in compact for keyword in keywords)


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _normalize_heading_text(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return cleaned
    if re.search(r"[\u4e00-\u9fff]", cleaned):
        cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", "", cleaned)
        cleaned = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", cleaned)
        cleaned = re.sub(r"(?<=[、，,；;：:（）()])\s+(?=[A-Za-z0-9\u4e00-\u9fff])", "", cleaned)
    return cleaned


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")
