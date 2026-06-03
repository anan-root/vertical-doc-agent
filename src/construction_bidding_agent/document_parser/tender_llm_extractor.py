"""对招标文件抽取输入包执行真实 LLM 抽取任务。"""

from __future__ import annotations

import json
import re
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from construction_bidding_agent.llm_config import (
    DEFAULT_API_TYPE,
    DEFAULT_BASE_URL,
    DEFAULT_ENABLE_THINKING,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_MAX_WORKERS,
    DEFAULT_STORE_RESPONSE,
    DEFAULT_STRUCTURED_OUTPUT_TYPE,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOP_P,
    LlmClientConfig,
    llm_config,
    load_dotenv,
)
from construction_bidding_agent.llm_client import (
    call_openai_json,
    effective_reasoning_effort,
    parse_json_response,
    response_output_text,
)

SCHEMA_VERSION = "tender_llm_extraction_run_v0.2"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_CACHE_DIR = Path("outputs") / "cache" / "tender_llm_tasks"

PROMPT_FILE_BY_TASK = {
    "project_info_extraction_input": "project-info-extraction-prompt.md",
    "score_points_extraction_input": "score-point-extraction-prompt.md",
    "technical_requirements_extraction_input": "technical-bid-requirements-prompt.md",
}

PRODUCTION_PROMPT_FILE_BY_TASK = {
    "project_info_extraction_input": "project-info-extraction-production.md",
    "score_points_extraction_input": "score-point-extraction-production.md",
    "technical_requirements_extraction_input": "technical-bid-requirements-production.md",
}

EXPECTED_OUTPUT_SCHEMA_BY_TASK = {
    "project_info_extraction_input": "project_info_v1",
    "score_points_extraction_input": "score_points_v1",
    "technical_requirements_extraction_input": "technical_bid_requirements_v1",
}

CELL_REF_PATTERN = re.compile(r"^B(?P<block>\d+)_R(?P<row>\d+)_C(?P<cell>\d+)$")
BACKFILLABLE_REF_TYPES = {"cell", "block", "table"}
NON_TECHNICAL_SCORE_KEYWORDS = [
    "投标报价",
    "报价",
    "商务",
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
]
NON_TECHNICAL_SCORE_SECTION_KEYWORDS = [
    "商务标评审标准",
    "综合标评审标准",
    "报价评审标准",
    "投标报价评审",
    "推荐定标候选人",
]
TECHNICAL_SCORE_SECTION_KEYWORDS = [
    "技术标评审标准",
    "技术部分评审标准",
    "技术评分标准",
    "技术标评分标准",
]
STRUCTURAL_SCORE_CELL_TEXTS = {
    "条款号",
    "条款内容",
    "编列内容",
    "技术标评审标准",
    "技术部分评审标准",
    "技术评分标准",
    "技术标评分标准",
    "商务标评审标准",
    "综合标评审标准",
}

@dataclass(slots=True)
class TenderLlmTaskRun:
    task_key: str
    task_title: str
    model: str
    status: str
    input_estimated_tokens: int
    duration_seconds: float
    started_at: str | None = None
    completed_at: str | None = None
    output_text: str = ""
    parsed_json: dict[str, Any] | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cache_status: str = "disabled"
    cache_key: str | None = None


@dataclass(slots=True)
class TenderLlmExtractionRunResult:
    schema_version: str
    source_input_path: str
    source_file: str
    provider: str
    model: str
    base_url: str | None
    api_type: str
    execution_mode: str
    max_workers: int
    duration_seconds: float
    started_at: str | None
    completed_at: str | None
    task_count: int
    completed_task_count: int
    failed_task_count: int
    tasks: list[TenderLlmTaskRun] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_tender_llm_extraction_from_file(
    input_json_path: str | Path,
    *,
    prompt_dir: str | Path,
    model: str | None = None,
    task_keys: list[str] | None = None,
    execution_mode: str = "parallel",
    max_workers: int | None = None,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> TenderLlmExtractionRunResult:
    _load_dotenv(Path.cwd() / ".env")
    input_path = Path(input_json_path)
    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    default_llm_config = _llm_config(model_override=model)
    effective_max_workers = _effective_max_workers(max_workers, default_llm_config)

    packages = input_data.get("packages", [])
    if task_keys:
        selected = set(task_keys)
        packages = [package for package in packages if package.get("task_key") in selected]

    warnings: list[str] = []
    run_started_at = _now_iso()
    run_start = time.monotonic()
    if not default_llm_config.api_key:
        warnings.append("API_KEY is not set; real LLM extraction was not run.")
        task_runs = [
            TenderLlmTaskRun(
                task_key=package.get("task_key", ""),
                task_title=package.get("task_title", ""),
                model=default_llm_config.model,
                status="skipped",
                input_estimated_tokens=int(package.get("estimated_tokens") or 0),
                duration_seconds=0.0,
                started_at=run_started_at,
                completed_at=run_started_at,
                error="API_KEY is not set.",
                cache_status="disabled",
            )
            for package in packages
        ]
        run_completed_at = _now_iso()
        return TenderLlmExtractionRunResult(
            schema_version=SCHEMA_VERSION,
            source_input_path=str(input_path),
            source_file=input_data.get("file_name", ""),
            provider=default_llm_config.provider,
            model=default_llm_config.model,
            base_url=default_llm_config.base_url,
            api_type=default_llm_config.api_type,
            execution_mode=execution_mode,
            max_workers=effective_max_workers,
            duration_seconds=time.monotonic() - run_start,
            started_at=run_started_at,
            completed_at=run_completed_at,
            task_count=len(task_runs),
            completed_task_count=0,
            failed_task_count=len(task_runs),
            tasks=task_runs,
            warnings=warnings,
        )

    prompt_path = Path(prompt_dir)
    task_runs = _run_task_packages(
        packages,
        prompt_dir=prompt_path,
        llm_config=None,
        model=model,
        execution_mode=execution_mode,
        max_workers=effective_max_workers,
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
        force_refresh=force_refresh,
    )
    run_completed_at = _now_iso()
    return TenderLlmExtractionRunResult(
        schema_version=SCHEMA_VERSION,
        source_input_path=str(input_path),
        source_file=input_data.get("file_name", ""),
        provider=default_llm_config.provider,
        model=default_llm_config.model,
        base_url=default_llm_config.base_url,
        api_type=default_llm_config.api_type,
        execution_mode=execution_mode,
        max_workers=effective_max_workers,
        duration_seconds=time.monotonic() - run_start,
        started_at=run_started_at,
        completed_at=run_completed_at,
        task_count=len(task_runs),
        completed_task_count=sum(1 for task in task_runs if task.status == "completed"),
        failed_task_count=sum(1 for task in task_runs if task.status != "completed"),
        tasks=task_runs,
        warnings=warnings,
    )


def write_tender_llm_extraction_outputs(
    result: TenderLlmExtractionRunResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_tender_llm_extraction_report(result), encoding="utf-8")


def render_tender_llm_extraction_report(result: TenderLlmExtractionRunResult) -> str:
    lines = [
        "# 招标文件 LLM 抽取运行报告",
        "",
        f"- 输入包：`{result.source_input_path}`",
        f"- 招标文件：{result.source_file}",
        f"- 服务商：{result.provider}",
        f"- 模型：{result.model}",
        f"- Base URL：{result.base_url or '默认 OpenAI'}",
        f"- API：{result.api_type}",
        f"- 执行方式：{result.execution_mode}",
        f"- 并发数：{result.max_workers}",
        f"- 总耗时秒：{result.duration_seconds:.2f}",
        f"- 任务数：{result.task_count}",
        f"- 完成：{result.completed_task_count}",
        f"- 失败/跳过：{result.failed_task_count}",
        "",
        "## 任务概览",
        "",
        "| 任务 | 状态 | 输入估算 tokens | 耗时秒 | 关键结果 | 校验问题 |",
        "|---|---|---:|---:|---|---:|",
    ]
    for task in result.tasks:
        lines.append(
            f"| {task.task_title or task.task_key} | {task.status} | "
            f"{task.input_estimated_tokens} | {task.duration_seconds:.2f} | "
            f"{_task_summary(task)} | {len(task.validation.get('issues', []))} |"
        )

    lines.extend(["", "## 任务明细", ""])
    for task in result.tasks:
        lines.extend(
            [
                f"### {task.task_title or task.task_key}",
                "",
                f"- 任务键：`{task.task_key}`",
                f"- 状态：{task.status}",
                f"- 输入估算 tokens：{task.input_estimated_tokens}",
                f"- 开始时间：{task.started_at or ''}",
                f"- 完成时间：{task.completed_at or ''}",
                f"- 耗时秒：{task.duration_seconds:.2f}",
                f"- 缓存：{task.cache_status}",
            ]
        )
        if task.error:
            lines.append(f"- 错误：{task.error}")
        if task.validation:
            lines.append(f"- 校验摘要：{task.validation.get('summary', '')}")
            issues = task.validation.get("issues", [])
            if issues:
                lines.extend(["", "校验问题："])
                for issue in issues:
                    lines.append(f"- {issue}")
        if task.parsed_json:
            lines.extend(["", "结果预览：", "", "```json"])
            lines.append(json.dumps(_preview_json(task.parsed_json), ensure_ascii=False, indent=2))
            lines.extend(["```", ""])
        lines.append("")

    if result.warnings:
        lines.extend(["## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def _run_task_packages(
    packages: list[dict[str, Any]],
    *,
    prompt_dir: Path,
    llm_config: LlmClientConfig | None = None,
    model: str | None = None,
    execution_mode: str,
    max_workers: int,
    provider: str | None = None,
    base_url: str | None = None,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> list[TenderLlmTaskRun]:
    if execution_mode == "serial" or len(packages) <= 1:
        return [
            _run_single_task(
                package,
                prompt_dir=prompt_dir,
                llm_config=llm_config,
                model=model,
                provider=provider,
                base_url=base_url,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
            for package in packages
        ]
    if execution_mode != "parallel":
        raise ValueError(f"Unsupported execution_mode: {execution_mode}")

    workers = max(1, min(max_workers, len(packages)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                copy_context().run,
                _run_single_task,
                package,
                prompt_dir=prompt_dir,
                llm_config=llm_config,
                model=model,
                provider=provider,
                base_url=base_url,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
            for package in packages
        ]
        return [future.result() for future in futures]


def _effective_max_workers(max_workers: int | None, config: LlmClientConfig) -> int:
    return max(1, int(max_workers if max_workers is not None else config.max_workers))


def _run_single_task(
    package: dict[str, Any],
    *,
    prompt_dir: Path,
    llm_config: LlmClientConfig | None = None,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> TenderLlmTaskRun:
    task_key = package.get("task_key", "")
    config = llm_config or _llm_config(
        task_key=task_key,
        model_override=model,
        provider_override=provider,
        base_url_override=base_url,
    )
    started_at = _now_iso()
    start = time.monotonic()
    response_text = ""
    try:
        prompt = _load_prompt(task_key, prompt_dir)
        cache_key = _task_cache_key(
            package=package,
            prompt=prompt,
            llm_config=config,
        )
        if cache_dir is not None and not force_refresh:
            cached_task = _read_cached_task(cache_dir, cache_key)
            if cached_task is not None:
                cached_task.started_at = started_at
                cached_task.completed_at = _now_iso()
                cached_task.duration_seconds = time.monotonic() - start
                cached_task.cache_status = "hit"
                cached_task.cache_key = cache_key
                return cached_task
        response_text = _call_openai_json(
            llm_config=config,
            task_key=task_key,
            system_prompt=prompt,
            user_input=package.get("input_text", ""),
        )
        parsed_json = _parse_json_response(response_text)
        if task_key == "project_info_extraction_input":
            _postprocess_project_info(parsed_json, package)
        validation = _validate_task_output(task_key, parsed_json)
        if task_key == "score_points_extraction_input":
            _postprocess_score_points(parsed_json, package, validation)
        _validate_output_refs(parsed_json, package, validation)
        status = "completed" if not validation.get("fatal") else "failed"
        completed_at = _now_iso()
        task_run = TenderLlmTaskRun(
            task_key=task_key,
            task_title=package.get("task_title", ""),
            model=config.model,
            status=status,
            input_estimated_tokens=int(package.get("estimated_tokens") or 0),
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=completed_at,
            output_text=response_text,
            parsed_json=parsed_json,
            validation=validation,
            cache_status="miss" if cache_dir is not None else "disabled",
            cache_key=cache_key if cache_dir is not None else None,
        )
        if cache_dir is not None and task_run.status == "completed":
            _write_cached_task(cache_dir, cache_key, task_run)
        return task_run
    except Exception as exc:  # pragma: no cover - real network path
        completed_at = _now_iso()
        return TenderLlmTaskRun(
            task_key=task_key,
            task_title=package.get("task_title", ""),
            model=config.model,
            status="failed",
            input_estimated_tokens=int(package.get("estimated_tokens") or 0),
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=completed_at,
            output_text=response_text,
            error=str(exc),
            cache_status="miss" if cache_dir is not None else "disabled",
        )


def _call_openai_json(
    *,
    llm_config: LlmClientConfig | None = None,
    task_key: str | None = None,
    model: str | None = None,
    system_prompt: str,
    user_input: str,
) -> str:
    return call_openai_json(
        config=llm_config,
        task_key=task_key,
        model=model,
        system_prompt=system_prompt,
        user_input=user_input,
    )


def _effective_reasoning_effort(config: LlmClientConfig) -> str:
    return effective_reasoning_effort(config)


def _response_output_text(response: Any) -> str:
    return response_output_text(response)


def _get_response_field(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _task_cache_key(
    *,
    package: dict[str, Any],
    prompt: str,
    llm_config: LlmClientConfig,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider": llm_config.provider,
        "base_url": llm_config.base_url,
        "model": llm_config.model,
        "api_type": llm_config.api_type,
        "temperature": llm_config.temperature,
        "top_p": llm_config.top_p,
        "max_tokens": llm_config.max_tokens,
        "structured_output_type": llm_config.structured_output_type,
        "enable_thinking": llm_config.enable_thinking,
        "reasoning_effort": llm_config.reasoning_effort,
        "store_response": llm_config.store_response,
        "task_key": package.get("task_key"),
        "input_profile": package.get("input_profile"),
        "input_text": package.get("input_text") or "",
        "cell_refs": package.get("cell_refs") or [],
        "prompt": prompt,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cached_task(cache_dir: Path, cache_key: str) -> TenderLlmTaskRun | None:
    path = _cache_path(cache_dir, cache_key)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return TenderLlmTaskRun(**data)


def _write_cached_task(cache_dir: Path, cache_key: str, task_run: TenderLlmTaskRun) -> None:
    path = _cache_path(cache_dir, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(task_run)
    data["cache_status"] = "stored"
    data["cache_key"] = cache_key
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.json"


def _load_prompt(task_key: str, prompt_dir: Path) -> str:
    prompt_file = PROMPT_FILE_BY_TASK.get(task_key)
    if prompt_file is None:
        raise ValueError(f"No prompt file configured for task: {task_key}")
    production_prompt_file = PRODUCTION_PROMPT_FILE_BY_TASK.get(task_key)
    production_prompt_path = prompt_dir / production_prompt_file if production_prompt_file else None
    prompt_path = production_prompt_path if production_prompt_path and production_prompt_path.exists() else prompt_dir / prompt_file
    prompt = prompt_path.read_text(encoding="utf-8")
    return (
        prompt
        + "\n\n补充运行规则：\n"
        + "1. 本次输入不是推荐 JSON，而是已拼接的抽取输入包；其中 [Bxxx ...] 或 [Bxxx,Byyy ...] 可作为 block 引用。\n"
        + "2. 若输入中出现 STRUCTURED_TABLE_CELLS，cell 引用必须使用其中明示的 cell_id，例如 B1564_R17_C3。\n"
        + "3. 禁止自造 table_xxx、row_xxx、cell_xxx 等输入中不存在的 id。\n"
        + "4. 当 schema 要求 cell 引用但输入包没有 cell_id 时，才可使用 block 引用，id 使用 B 开头的块号或块范围，并设置 needs_confirmation=true。\n"
        + "5. 表格受合并单元格影响时，同一物理行可能只显示 2 个 cell；若第 1 个 cell 是评分点原文，第 2 个 cell 是评分标准，"
        + "score_point_ref 必须指向第 1 个 cell，description_ref 必须指向第 2 个 cell。\n"
        + "6. score_point_ref 指向的 cell 文本应与 model_observed_text 一致；不要把评分标准长文本作为 score_point_ref。\n"
        + "7. 必须只输出 JSON 对象，不要输出 Markdown。\n"
        + "8. 输出必须是合法 JSON。"
    )


def _parse_json_response(response_text: str) -> dict[str, Any]:
    return parse_json_response(response_text)


def _validate_task_output(task_key: str, data: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    expected_schema = EXPECTED_OUTPUT_SCHEMA_BY_TASK.get(task_key)
    if expected_schema and data.get("schema_version") != expected_schema:
        issues.append(f"schema_version should be {expected_schema}, got {data.get('schema_version')!r}.")

    if task_key == "project_info_extraction_input":
        _validate_project_info(data, issues)
    elif task_key == "score_points_extraction_input":
        _validate_score_points(data, issues)
    elif task_key == "technical_requirements_extraction_input":
        _validate_technical_requirements(data, issues)

    return {
        "fatal": False,
        "issue_count": len(issues),
        "issues": issues,
        "summary": _validation_summary(task_key, data, issues),
    }


def _validate_output_refs(
    data: dict[str, Any],
    package: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    valid_block_ids = {
        f"B{block_ref.get('block_index')}"
        for block_ref in package.get("block_refs", [])
        if block_ref.get("block_index") is not None
    }
    valid_cell_ids = {
        cell_ref.get("cell_id")
        for cell_ref in package.get("cell_refs", [])
        if cell_ref.get("cell_id")
    }
    invalid_refs = sorted(_collect_invalid_ref_ids(data, valid_block_ids, valid_cell_ids))
    if not invalid_refs:
        return
    issues = validation.setdefault("issues", [])
    issues.append(
        "Invalid or non-backfillable ref id(s): "
        + ", ".join(invalid_refs[:20])
        + (" ..." if len(invalid_refs) > 20 else "")
    )
    validation["issue_count"] = len(issues)
    validation["summary"] = f"{validation.get('summary', '')}; ref_issues={len(invalid_refs)}"


def _postprocess_score_points(
    data: dict[str, Any],
    package: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    cell_by_id, cells_by_row = _cell_ref_indexes(package)
    technical_table_hints = _technical_score_table_hints(cells_by_row)
    corrections: list[dict[str, str]] = []
    points = data.get("score_points")
    if not isinstance(points, list):
        data["system_final_score_points"] = []
        quality_gate = _score_point_quality_gate(data)
        data["quality_gate"] = quality_gate
        validation["quality_gate"] = quality_gate
        _merge_quality_gate_validation(validation, quality_gate)
        return

    for index, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        _repair_score_point_ref(point, index, cell_by_id, cells_by_row, technical_table_hints, corrections)
        _repair_description_ref(point, index, cell_by_id, cells_by_row, corrections)

    final_points, backfill_issues = _backfill_final_score_points(data, cell_by_id)
    final_points, filter_issues = _filter_and_recover_technical_score_points(
        final_points,
        data=data,
        cells_by_row=cells_by_row,
        technical_table_hints=technical_table_hints,
    )
    data["system_final_score_points"] = final_points
    if corrections:
        validation["ref_corrections"] = corrections
    if backfill_issues:
        issues = validation.setdefault("issues", [])
        issues.extend(backfill_issues)
        validation["issue_count"] = len(issues)
    if filter_issues:
        issues = validation.setdefault("issues", [])
        issues.extend(filter_issues)
        validation["issue_count"] = len(issues)
    quality_gate = _score_point_quality_gate(data)
    data["quality_gate"] = quality_gate
    validation["quality_gate"] = quality_gate
    _merge_quality_gate_validation(validation, quality_gate)
    validation["summary"] = (
        f"{validation.get('summary', '')}; "
        f"backfilled_score_points={len(final_points)}; "
        f"ref_corrections={len(corrections)}; "
        f"quality_gate_blocking={quality_gate['blocking_issue_count']}; "
        f"quality_gate_warnings={quality_gate['warning_issue_count']}"
    )


def _merge_quality_gate_validation(validation: dict[str, Any], quality_gate: dict[str, Any]) -> None:
    if quality_gate["issues"]:
        issues = validation.setdefault("issues", [])
        issues.extend(issue["message"] for issue in quality_gate["issues"])
        validation["issue_count"] = len(issues)
    if quality_gate["blocking"]:
        validation["fatal"] = True


def _postprocess_project_info(data: dict[str, Any], package: dict[str, Any]) -> None:
    required_fields = ["project_name", "location", "scale", "scope", "duration", "quality", "safety_civilized"]
    fields = data.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        data["fields"] = fields
    for field_name in required_fields:
        fields.setdefault(
            field_name,
            {
                "field_ref": None,
                "model_observed_text": None,
                "value": None,
                "confidence": 0,
                "needs_confirmation": True,
                "confirmation_reason": "模型未返回该字段，需人工复核。",
            },
        )

    valid_block_ids = {
        f"B{block_ref.get('block_index')}"
        for block_ref in package.get("block_refs", [])
        if block_ref.get("block_index") is not None
    }
    valid_cell_ids = {
        cell_ref.get("cell_id")
        for cell_ref in package.get("cell_refs", [])
        if cell_ref.get("cell_id")
    }
    for field in fields.values():
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        ref = field.get("field_ref")
        ref_id = _ref_id(ref)
        if value or not ref_id:
            continue
        if _is_valid_ref_id(ref_id, valid_block_ids, valid_cell_ids):
            continue
        field["field_ref"] = None
        reason = field.get("confirmation_reason")
        suffix = "原字段引用无法回填，已移除来源引用。"
        field["confirmation_reason"] = f"{reason}；{suffix}" if reason else suffix


def _cell_ref_indexes(package: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[tuple[int, int], list[dict[str, Any]]]]:
    cell_by_id: dict[str, dict[str, Any]] = {}
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for raw_cell in package.get("cell_refs", []):
        cell_id = raw_cell.get("cell_id")
        if not isinstance(cell_id, str):
            continue
        cell = dict(raw_cell)
        cell_by_id[cell_id] = cell
        block_index = cell.get("block_index")
        row_index = cell.get("row_index")
        if isinstance(block_index, int) and isinstance(row_index, int):
            cells_by_row.setdefault((block_index, row_index), []).append(cell)
    for row_cells in cells_by_row.values():
        row_cells.sort(key=lambda cell: int(cell.get("cell_index") or 0))
    return cell_by_id, cells_by_row


def _repair_score_point_ref(
    point: dict[str, Any],
    index: int,
    cell_by_id: dict[str, dict[str, Any]],
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    technical_table_hints: dict[str, Any],
    corrections: list[dict[str, str]],
) -> None:
    observed = _normalize_ref_text(point.get("model_observed_text"))
    ref = point.get("score_point_ref")
    ref_id = _ref_id(ref)
    if not ref_id:
        return

    current_cell = cell_by_id.get(ref_id)
    if current_cell and _text_matches_observed(current_cell.get("text_raw"), observed):
        return

    row_key = _row_key_for_ref(ref_id, current_cell)
    if row_key is None:
        return

    matching_cell = _find_matching_cell(cells_by_row.get(row_key, []), observed)
    reason = "score_point_ref text did not match model_observed_text; corrected to matching cell in the same row."
    if not matching_cell:
        matching_cell = _find_matching_cell_in_technical_ranges(cells_by_row, observed, technical_table_hints)
        reason = (
            "score_point_ref text did not match model_observed_text; corrected to matching cell "
            "inside the technical score table range."
        )
    if not matching_cell:
        return

    new_ref_id = str(matching_cell["cell_id"])
    if new_ref_id == ref_id:
        return
    point["score_point_ref"] = {"type": "cell", "id": new_ref_id}
    corrections.append(
        {
            "score_point_index": str(index),
            "field": "score_point_ref",
            "from": ref_id,
            "to": new_ref_id,
            "reason": reason,
        }
    )


def _repair_description_ref(
    point: dict[str, Any],
    index: int,
    cell_by_id: dict[str, dict[str, Any]],
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    corrections: list[dict[str, str]],
) -> None:
    ref = point.get("description_ref")
    ref_id = _ref_id(ref)

    score_cell = cell_by_id.get(_ref_id(point.get("score_point_ref")) or "")
    current_cell = cell_by_id.get(ref_id or "")
    observed = _normalize_ref_text(point.get("model_observed_text"))
    if (
        current_cell
        and current_cell != score_cell
        and not _text_matches_observed(current_cell.get("text_raw"), observed)
        and _same_row(current_cell, score_cell)
    ):
        return

    row_key_source = current_cell if _same_row(current_cell, score_cell) else score_cell
    row_key = _row_key_for_ref(ref_id or "", row_key_source)
    if row_key is None:
        return

    description_cell = _find_description_cell(
        cells_by_row.get(row_key, []),
        score_cell=score_cell,
        observed=observed,
    )
    if not description_cell:
        return

    new_ref_id = str(description_cell["cell_id"])
    if new_ref_id == ref_id:
        return
    point["description_ref"] = {"type": "cell", "id": new_ref_id}
    corrections.append(
        {
            "score_point_index": str(index),
            "field": "description_ref",
            "from": ref_id,
            "to": new_ref_id,
            "reason": "description_ref was missing, shifted, or pointed at the score point cell; corrected to the likely scoring-standard cell in the same row.",
        }
    )


def _backfill_final_score_points(
    data: dict[str, Any],
    cell_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    final_points: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, point in enumerate(data.get("score_points") or []):
        if not isinstance(point, dict):
            continue
        score_point_raw = _lookup_cell_text(point.get("score_point_ref"), cell_by_id)
        score = _lookup_cell_text(point.get("score_ref"), cell_by_id)
        description = _lookup_cell_text(point.get("description_ref"), cell_by_id)
        parent_text = _lookup_cell_text(point.get("parent_ref"), cell_by_id)
        observed = point.get("model_observed_text")
        needs_confirmation = bool(point.get("needs_confirmation"))
        confirmation_reasons = [
            reason
            for reason in [point.get("confirmation_reason")]
            if isinstance(reason, str) and reason.strip()
        ]

        if not score_point_raw:
            needs_confirmation = True
            confirmation_reasons.append("score_point_ref 无法回填原文")
            issues.append(f"Score point {index + 1} score_point_ref cannot be backfilled.")
        elif observed and not _text_matches_observed(score_point_raw, _normalize_ref_text(observed)):
            needs_confirmation = True
            confirmation_reasons.append("model_observed_text 与 score_point_ref.text_raw 不一致")
            issues.append(f"Score point {index + 1} observed text does not match backfilled score point text.")

        final_points.append(
            {
                "score_point_raw": score_point_raw,
                "level_1_heading_text": _normalize_heading_text(score_point_raw),
                "score_point_ref": point.get("score_point_ref"),
                "score": score,
                "score_ref": point.get("score_ref"),
                "description": description,
                "description_ref": point.get("description_ref"),
                "parent_text": parent_text,
                "parent_ref": point.get("parent_ref"),
                "model_observed_text": observed,
                "belongs_to_technical_bid": point.get("belongs_to_technical_bid"),
                "used_as_level_1_heading": bool(point.get("belongs_to_technical_bid") is True and score_point_raw),
                "needs_confirmation": needs_confirmation,
                "confirmation_reason": "；".join(dict.fromkeys(confirmation_reasons)) or None,
            }
        )
    return final_points, issues


def _filter_and_recover_technical_score_points(
    final_points: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    technical_table_hints: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    kept_points: list[dict[str, Any]] = []
    removed_points: list[dict[str, Any]] = []
    issues: list[str] = []
    seen_refs: set[str] = set()
    seen_titles: set[str] = set()
    for point in final_points:
        if not isinstance(point, dict):
            continue
        reason = _score_point_rejection_reason(
            point,
            cells_by_row=cells_by_row,
            technical_table_hints=technical_table_hints,
        )
        if reason:
            removed_points.append(
                {
                    "score_point_raw": point.get("score_point_raw"),
                    "score_point_ref": point.get("score_point_ref"),
                    "reason": reason,
                }
            )
            continue
        _append_unique_score_point(point, kept_points, seen_refs, seen_titles, dedupe_title=False)

    recovered_points = _recover_score_points_from_technical_table(
        cells_by_row,
        existing_ref_ids=seen_refs,
        existing_titles=seen_titles,
        technical_table_hints=technical_table_hints,
    )
    for point in recovered_points:
        _append_unique_score_point(point, kept_points, seen_refs, seen_titles, dedupe_title=True)
    kept_points = _sort_score_points_by_source_order(kept_points)

    if removed_points:
        data["system_removed_score_points"] = removed_points
        issues.append(f"Removed {len(removed_points)} non-technical or structural score point candidate(s).")
    if recovered_points:
        data["system_recovered_score_points"] = recovered_points
        issues.append(f"Recovered {len(recovered_points)} technical score point candidate(s) from structured table cells.")
    return kept_points, issues


def _sort_score_points_by_source_order(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(points, key=_score_point_source_order)


def _score_point_source_order(point: dict[str, Any]) -> tuple[int, int, int]:
    ref_id = _ref_id(point.get("score_point_ref"))
    if not ref_id:
        return (10**9, 10**9, 10**9)
    match = CELL_REF_PATTERN.match(ref_id)
    if not match:
        return (10**9, 10**9, 10**9)
    return int(match.group("block")), int(match.group("row")), int(match.group("cell"))


def _append_unique_score_point(
    point: dict[str, Any],
    points: list[dict[str, Any]],
    seen_refs: set[str],
    seen_titles: set[str],
    *,
    dedupe_title: bool,
) -> bool:
    ref_id = _ref_id(point.get("score_point_ref"))
    title = _normalize_ref_text(point.get("level_1_heading_text") or point.get("score_point_raw"))
    if ref_id and ref_id in seen_refs:
        return False
    if dedupe_title and title and title in seen_titles:
        return False
    points.append(point)
    if ref_id:
        seen_refs.add(ref_id)
    if title:
        seen_titles.add(title)
    return True


def _technical_score_table_hints(
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    technical_rows_by_block: dict[int, tuple[int, int]] = {}
    non_technical_rows_by_block: dict[int, list[int]] = {}
    for (block_index, row_index), row_cells in cells_by_row.items():
        row_text = _normalize_ref_text("".join(str(cell.get("text_raw") or "") for cell in row_cells))
        if any(keyword in row_text for keyword in TECHNICAL_SCORE_SECTION_KEYWORDS):
            start, end = technical_rows_by_block.get(block_index, (row_index, row_index))
            technical_rows_by_block[block_index] = (min(start, row_index), max(end, row_index))
        if any(keyword in row_text for keyword in NON_TECHNICAL_SCORE_SECTION_KEYWORDS):
            non_technical_rows_by_block.setdefault(block_index, []).append(row_index)

    ranges: dict[int, tuple[int, int]] = {}
    for block_index, (start_row, _end_row) in technical_rows_by_block.items():
        next_non_technical_rows = [
            row_index
            for row_index in non_technical_rows_by_block.get(block_index, [])
            if row_index > start_row
        ]
        end_row = min(next_non_technical_rows) - 1 if next_non_technical_rows else max(
            row
            for (candidate_block, row) in cells_by_row
            if candidate_block == block_index
        )
        ranges[block_index] = (start_row, end_row)
    return {"ranges": ranges}


def _score_point_rejection_reason(
    point: dict[str, Any],
    *,
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    technical_table_hints: dict[str, Any],
) -> str | None:
    raw = point.get("score_point_raw")
    normalized_raw = _normalize_ref_text(raw)
    if not normalized_raw:
        return "missing_score_point_raw"
    if _looks_like_structural_score_cell(raw):
        return "structural_or_clause_number"
    if _looks_like_non_technical_score_point(raw):
        return "business_price_or_credit_score_point"
    if point.get("belongs_to_technical_bid") is not True:
        return "not_confirmed_as_technical_bid"
    ref_id = _ref_id(point.get("score_point_ref"))
    row_key = _row_key_for_ref(ref_id, None) if ref_id else None
    if row_key and _row_is_non_technical(row_key, cells_by_row):
        return "non_technical_score_section"
    if technical_table_hints.get("ranges") and row_key and not _row_in_technical_score_range(row_key, technical_table_hints):
        return "outside_technical_score_section"
    return None


def _recover_score_points_from_technical_table(
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    *,
    existing_ref_ids: set[str],
    existing_titles: set[str],
    technical_table_hints: dict[str, Any],
) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    seen_ref_ids = set(existing_ref_ids)
    seen_titles = set(existing_titles)
    ranges = technical_table_hints.get("ranges") or {}
    for block_index, (start_row, end_row) in sorted(ranges.items()):
        for row_index in range(start_row, end_row + 1):
            row_cells = cells_by_row.get((block_index, row_index), [])
            if not row_cells:
                continue
            score_cell = _technical_score_point_cell_from_row(row_cells)
            if not score_cell:
                continue
            ref_id = str(score_cell.get("cell_id") or "")
            title = score_cell.get("text_raw")
            normalized_title = _normalize_ref_text(title)
            if not ref_id or ref_id in seen_ref_ids or normalized_title in seen_titles:
                continue
            description_cell = _find_description_cell(row_cells, score_cell=score_cell, observed=normalized_title)
            recovered.append(
                {
                    "score_point_raw": title,
                    "level_1_heading_text": _normalize_heading_text(title),
                    "score_point_ref": {"type": "cell", "id": ref_id},
                    "score": None,
                    "score_ref": None,
                    "description": description_cell.get("text_raw") if description_cell else None,
                    "description_ref": (
                        {"type": "cell", "id": str(description_cell.get("cell_id"))}
                        if description_cell and description_cell.get("cell_id")
                        else None
                    ),
                    "parent_text": "技术标评审标准",
                    "parent_ref": None,
                    "model_observed_text": title,
                    "belongs_to_technical_bid": True,
                    "used_as_level_1_heading": True,
                    "needs_confirmation": False,
                    "confirmation_reason": None,
                    "recovered_by_system": True,
                }
            )
            seen_ref_ids.add(ref_id)
            if normalized_title:
                seen_titles.add(normalized_title)
    return recovered


def _technical_score_point_cell_from_row(row_cells: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for cell in sorted(row_cells, key=lambda item: int(item.get("cell_index") or 0)):
        text = cell.get("text_raw")
        normalized = _normalize_ref_text(text)
        if not normalized:
            continue
        if _looks_like_structural_score_cell(text) or _looks_like_non_technical_score_point(text):
            continue
        if _looks_like_score_rule_text(text):
            continue
        candidates.append(cell)
    if not candidates:
        return None
    return min(candidates, key=lambda cell: int(cell.get("cell_index") or 0))


def _row_is_non_technical(
    row_key: tuple[int, int],
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
) -> bool:
    row_text = _normalize_ref_text("".join(str(cell.get("text_raw") or "") for cell in cells_by_row.get(row_key, [])))
    return any(keyword in row_text for keyword in NON_TECHNICAL_SCORE_SECTION_KEYWORDS)


def _row_in_technical_score_range(row_key: tuple[int, int], technical_table_hints: dict[str, Any]) -> bool:
    block_index, row_index = row_key
    row_range = (technical_table_hints.get("ranges") or {}).get(block_index)
    return bool(row_range and row_range[0] <= row_index <= row_range[1])


def _looks_like_structural_score_cell(text: Any) -> bool:
    normalized = _normalize_ref_text(text)
    if not normalized:
        return True
    structural_texts = {_normalize_ref_text(item) for item in STRUCTURAL_SCORE_CELL_TEXTS}
    if normalized in structural_texts:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)+(?:[（(]\d+[）)])?", normalized):
        return True
    if re.fullmatch(r"[（(]\d+[）)]", normalized):
        return True
    return False


def _looks_like_score_rule_text(text: Any) -> bool:
    normalized = _normalize_ref_text(text)
    if len(normalized) >= 80:
        return True
    return any(keyword in normalized for keyword in ["若不提供", "评委确认", "判为", "合格", "不合格", "评分标准"])


def _score_point_quality_gate(data: dict[str, Any]) -> dict[str, Any]:
    final_points = [point for point in data.get("system_final_score_points") or [] if isinstance(point, dict)]
    issues: list[dict[str, Any]] = []

    if not final_points:
        issues.append(_quality_issue("blocking", "score_points_empty", "未抽取到可用于生成技术标一级目录的评分点。"))

    seen_titles: dict[str, int] = {}
    total_score = 0.0
    scored_count = 0
    unscored_count = 0
    for index, point in enumerate(final_points, start=1):
        raw = point.get("score_point_raw")
        title = point.get("level_1_heading_text")
        normalized_raw = _normalize_ref_text(raw)
        normalized_title = _normalize_ref_text(title)

        if not raw:
            issues.append(_quality_issue("blocking", "score_point_raw_missing", f"第 {index} 个评分点无法回填原文。"))
        if raw and title and normalized_raw != normalized_title:
            issues.append(
                _quality_issue(
                    "warning",
                    "level_1_title_normalized",
                    f"第 {index} 个评分点一级目录与原文存在空格/版式归一化差异，目录生成前需确认。",
                )
            )
        if point.get("used_as_level_1_heading") is not True:
            issues.append(
                _quality_issue(
                    "blocking",
                    "not_technical_bid_score_point",
                    f"第 {index} 个评分点未被确认为技术标评分点，不能直接进入一级目录。",
                )
            )
        if point.get("score_point_ref") is None:
            issues.append(_quality_issue("blocking", "score_point_ref_missing", f"第 {index} 个评分点缺少来源引用。"))
        if point.get("needs_confirmation"):
            issues.append(
                _quality_issue(
                    "warning",
                    "score_point_needs_confirmation",
                    f"第 {index} 个评分点需要人工复核：{point.get('confirmation_reason') or '未说明原因'}",
                )
            )
        if _looks_like_non_technical_score_point(raw):
            issues.append(
                _quality_issue(
                    "warning",
                    "possible_non_technical_score_point",
                    f"第 {index} 个评分点疑似商务/报价/资信项：{raw}",
                )
            )

        if normalized_title:
            seen_titles[normalized_title] = seen_titles.get(normalized_title, 0) + 1

        score_value = _score_value(point.get("score"))
        if score_value is None:
            unscored_count += 1
        else:
            scored_count += 1
            total_score += score_value

    duplicate_titles = sorted(title for title, count in seen_titles.items() if count > 1)
    if duplicate_titles:
        issues.append(
            _quality_issue(
                "warning",
                "duplicate_score_point_title",
                "存在重复评分点标题，需确认是否为父子层级误拆或重复抽取。",
            )
        )

    if scored_count > 0 and total_score > 100:
        issues.append(
            _quality_issue(
                "warning",
                "score_total_over_100",
                f"可解析评分点分值合计为 {total_score:g}，超过 100 分，需复核是否混入商务/报价项。",
            )
        )

    blocking_count = sum(1 for issue in issues if issue["severity"] == "blocking")
    warning_count = sum(1 for issue in issues if issue["severity"] != "blocking")
    return {
        "blocking": blocking_count > 0,
        "issue_count": len(issues),
        "blocking_issue_count": blocking_count,
        "warning_issue_count": warning_count,
        "score_point_count": len(final_points),
        "scored_count": scored_count,
        "unscored_count": unscored_count,
        "score_total": total_score if scored_count else None,
        "issues": issues,
    }


def _quality_issue(severity: str, issue_type: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "type": issue_type,
        "message": message,
    }


def _looks_like_non_technical_score_point(text: Any) -> bool:
    normalized = _normalize_ref_text(text)
    if not normalized:
        return False
    return any(keyword in normalized for keyword in NON_TECHNICAL_SCORE_KEYWORDS)


def _score_value(text: Any) -> float | None:
    if text is None:
        return None
    value = str(text)
    if not value.strip():
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*分", value)
    if not match:
        return None
    return float(match.group(1))


def _ref_id(ref: Any) -> str | None:
    if isinstance(ref, dict) and isinstance(ref.get("id"), str):
        return ref["id"]
    return None


def _row_key_for_ref(ref_id: str, cell: dict[str, Any] | None) -> tuple[int, int] | None:
    if cell:
        block_index = cell.get("block_index")
        row_index = cell.get("row_index")
        if isinstance(block_index, int) and isinstance(row_index, int):
            return block_index, row_index
    match = CELL_REF_PATTERN.match(ref_id)
    if not match:
        return None
    return int(match.group("block")), int(match.group("row"))


def _same_row(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    return (
        left.get("block_index") == right.get("block_index")
        and left.get("row_index") == right.get("row_index")
    )


def _find_matching_cell(cells: list[dict[str, Any]], observed: str) -> dict[str, Any] | None:
    if not observed:
        return None
    for cell in cells:
        if _text_matches_observed(cell.get("text_raw"), observed):
            return cell
    return None


def _find_matching_cell_in_technical_ranges(
    cells_by_row: dict[tuple[int, int], list[dict[str, Any]]],
    observed: str,
    technical_table_hints: dict[str, Any],
) -> dict[str, Any] | None:
    if not observed:
        return None
    ranges = technical_table_hints.get("ranges") or {}
    for block_index, (start_row, end_row) in sorted(ranges.items()):
        for row_index in range(start_row, end_row + 1):
            for cell in cells_by_row.get((block_index, row_index), []):
                if _text_matches_observed(cell.get("text_raw"), observed):
                    return cell
    return None


def _find_description_cell(
    cells: list[dict[str, Any]],
    *,
    score_cell: dict[str, Any] | None,
    observed: str,
) -> dict[str, Any] | None:
    if not cells:
        return None
    score_cell_index = score_cell.get("cell_index") if score_cell else None
    candidates = [
        cell
        for cell in cells
        if cell is not score_cell
        and not _text_matches_observed(cell.get("text_raw"), observed)
        and _normalize_ref_text(cell.get("text_raw"))
    ]
    if isinstance(score_cell_index, int):
        after_score = [cell for cell in candidates if int(cell.get("cell_index") or 0) > score_cell_index]
        if after_score:
            candidates = after_score
    if not candidates:
        return None
    return max(candidates, key=lambda cell: len(_normalize_ref_text(cell.get("text_raw"))))


def _lookup_cell_text(ref: Any, cell_by_id: dict[str, dict[str, Any]]) -> str | None:
    ref_id = _ref_id(ref)
    if not ref_id:
        return None
    cell = cell_by_id.get(ref_id)
    if not cell:
        return None
    text = cell.get("text_raw")
    return text if isinstance(text, str) else None


def _text_matches_observed(text: Any, observed: str) -> bool:
    normalized_text = _normalize_ref_text(text)
    if not normalized_text or not observed:
        return False
    return (
        normalized_text == observed
        or normalized_text in observed
        or observed in normalized_text
    )


def _normalize_ref_text(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", "", str(text)).strip()


def _normalize_heading_text(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return stripped
    if _contains_cjk(stripped):
        return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", stripped)
    return re.sub(r"\s+", " ", stripped)


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _collect_invalid_ref_ids(data: Any, valid_block_ids: set[str], valid_cell_ids: set[str]) -> set[str]:
    invalid: set[str] = set()
    if isinstance(data, dict):
        ref_id = data.get("id")
        ref_type = data.get("type")
        if (
            isinstance(ref_id, str)
            and isinstance(ref_type, str)
            and ref_type in BACKFILLABLE_REF_TYPES
            and not _is_valid_ref_id(ref_id, valid_block_ids, valid_cell_ids)
        ):
            invalid.add(ref_id)
        for value in data.values():
            invalid.update(_collect_invalid_ref_ids(value, valid_block_ids, valid_cell_ids))
    elif isinstance(data, list):
        for item in data:
            invalid.update(_collect_invalid_ref_ids(item, valid_block_ids, valid_cell_ids))
    return invalid


def _is_valid_ref_id(ref_id: str, valid_block_ids: set[str], valid_cell_ids: set[str]) -> bool:
    if ref_id in valid_cell_ids:
        return True
    block_ids = re.findall(r"B\d+(?!_R)", ref_id)
    return bool(block_ids) and all(block_id in valid_block_ids for block_id in block_ids)


def _validate_project_info(data: dict[str, Any], issues: list[str]) -> None:
    if data.get("project_type") not in {"construction", "epc"}:
        issues.append("project_type must be construction or epc.")
    if not isinstance(data.get("contains_design_task"), bool):
        issues.append("contains_design_task must be boolean.")
    fields = data.get("fields")
    if not isinstance(fields, dict):
        issues.append("fields must be an object.")
        return
    for field_name in ["project_name", "location", "scale", "scope", "duration", "quality", "safety_civilized"]:
        if field_name not in fields:
            issues.append(f"Missing project info field: {field_name}.")


def _validate_score_points(data: dict[str, Any], issues: list[str]) -> None:
    if not isinstance(data.get("is_score_region"), bool):
        issues.append("is_score_region must be boolean.")
    points = data.get("score_points")
    if not isinstance(points, list):
        issues.append("score_points must be a list.")
        return
    if not points:
        issues.append("score_points is empty.")
    non_technical = [
        point.get("model_observed_text")
        for point in points
        if point.get("belongs_to_technical_bid") is not True
    ]
    if non_technical:
        issues.append(f"{len(non_technical)} score point(s) are not marked as technical bid.")


def _validate_technical_requirements(data: dict[str, Any], issues: list[str]) -> None:
    for key in ["requirements", "technical_standards", "technical_risks"]:
        if not isinstance(data.get(key), list):
            issues.append(f"{key} must be a list.")


def _validation_summary(task_key: str, data: dict[str, Any], issues: list[str]) -> str:
    if task_key == "project_info_extraction_input":
        fields = data.get("fields") or {}
        filled = sum(1 for value in fields.values() if isinstance(value, dict) and value.get("value"))
        return f"project_type={data.get('project_type')}; filled_fields={filled}; issues={len(issues)}"
    if task_key == "score_points_extraction_input":
        return f"score_points={len(data.get('score_points') or [])}; issues={len(issues)}"
    if task_key == "technical_requirements_extraction_input":
        return (
            f"requirements={len(data.get('requirements') or [])}; "
            f"standards={len(data.get('technical_standards') or [])}; "
            f"risks={len(data.get('technical_risks') or [])}; issues={len(issues)}"
        )
    return f"issues={len(issues)}"


def _task_summary(task: TenderLlmTaskRun) -> str:
    if task.validation:
        return str(task.validation.get("summary", ""))
    if task.error:
        return task.error
    return ""


def _preview_json(data: dict[str, Any]) -> dict[str, Any]:
    preview = dict(data)
    if "score_points" in preview and isinstance(preview["score_points"], list):
        preview["score_points"] = preview["score_points"][:8]
    if "requirements" in preview and isinstance(preview["requirements"], list):
        preview["requirements"] = preview["requirements"][:8]
    if "technical_standards" in preview and isinstance(preview["technical_standards"], list):
        preview["technical_standards"] = preview["technical_standards"][:8]
    if "technical_risks" in preview and isinstance(preview["technical_risks"], list):
        preview["technical_risks"] = preview["technical_risks"][:8]
    if "system_final_score_points" in preview and isinstance(preview["system_final_score_points"], list):
        preview["system_final_score_points"] = preview["system_final_score_points"][:8]
    return preview


def _llm_config(
    *,
    task_key: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    base_url_override: str | None = None,
) -> LlmClientConfig:
    return llm_config(
        task_key=task_key,
        model_override=model_override,
        provider_override=provider_override,
        base_url_override=base_url_override,
    )


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(timespec="seconds")


def _load_dotenv(path: Path) -> None:
    load_dotenv(path)
