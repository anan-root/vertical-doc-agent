"""执行 LLM 二三级目录补强并合并回目录树。"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from construction_bidding_agent.llm_client import call_openai_json, parse_json_response
from construction_bidding_agent.llm_config import DEFAULT_MAX_WORKERS, LlmClientConfig, llm_config, load_dotenv
from construction_bidding_agent.outline_generator.generator import (
    refresh_outline_confirmation,
    render_outline_report,
)
from construction_bidding_agent.outline_generator.refinement import (
    OUTPUT_SCHEMA_VERSION,
    validate_outline_refinement_output,
)


SCHEMA_VERSION = "outline_refinement_run_v0.1"
TASK_KEY = "outline_refinement"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_CACHE_DIR = Path("outputs") / "cache" / "outline_refinement_tasks"
DEFAULT_PROMPT = """你是房建工程技术标目录编制专家。

任务：
在不修改一级目录的前提下，为当前一级目录补充和优化二级、三级目录。

硬性规则：
1. 一级目录标题必须等于输入 level_1_title，不得改写、概括、拆分、合并、删除或重排。
2. 只输出二级、三级目录；标题不要带编号，编号由系统生成。
3. 二三级目录必须服务当前评分点，严格满足 granularity_rule 的章节类型、二级目录上下限、三级目录允许范围、单个二级下三级数量上限和三级总数上限。
4. 优先参考优秀标书候选目录，但必须删除历史项目名称、历史地址、人员姓名电话、医院路线等残留。
5. design 节点不得套用施工类范式；construction 节点可参考施工类优秀标书。
6. “技术标完整性说明/内容完整性”不是施工方案，围绕响应范围、章节完整性、评分点响应、响应依据、完整性承诺展开；禁止项目概况、编制依据、施工部署、主要施工方法、施工方案总体安排。
7. “施工进度表”“施工总平面布置图”偏图表型，目录应围绕编制说明、图表内容、控制要点、保障措施展开。
8. “拟投入资源配备计划”围绕劳动力、机械设备、材料、周转资源和保障措施展开。
9. “技术创新/BIM/信息化”围绕应用目标、应用内容、实施路径、保障措施和成效展开。
10. 主要施工方案与技术措施类章节可生成三级目录，但不得把钢筋加工、连接、绑扎、质量控制等细部步骤继续无限目录化；细部步骤应放入正文。
11. 只输出符合 JSON Schema 的 JSON，不要输出解释文字。

输出 JSON 字段：
- schema_version 固定为 outline_refinement_v1；
- target_node_id 等于输入 target_outline_node.node_id；
- level_1_title 等于输入 target_outline_node.level_1_title；
- level_1_title_unchanged 必须为 true；
- domain、category 跟随输入；
- refined_children 为二级目录数组，children 为三级目录数组。
"""


@dataclass(slots=True)
class OutlineRefinementTaskRun:
    target_node_id: str
    level_1_title: str
    status: str
    duration_seconds: float
    started_at: str | None = None
    completed_at: str | None = None
    model: str | None = None
    output_text: str = ""
    parsed_json: dict[str, Any] | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    error: str | None = None
    cache_status: str = "disabled"
    cache_key: str | None = None


@dataclass(slots=True)
class OutlineRefinementRunResult:
    schema_version: str
    generated_at: str
    provider: str
    model: str
    base_url: str | None
    task_count: int
    applied_count: int
    skipped_count: int
    failed_count: int
    duration_seconds: float
    execution_mode: str
    max_workers: int
    outline: dict[str, Any]
    tasks: list[OutlineRefinementTaskRun] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LlmCallable = Callable[[dict[str, Any], LlmClientConfig], str]


def run_outline_refinement_from_files(
    outline_json: str | Path,
    refinement_inputs_json: str | Path,
    *,
    prompt_path: str | Path | None = None,
    model: str | None = None,
    max_workers: int | None = None,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    target_node_ids: list[str] | None = None,
) -> OutlineRefinementRunResult:
    load_dotenv(Path.cwd() / ".env")
    outline = json.loads(Path(outline_json).read_text(encoding="utf-8"))
    inputs_data = json.loads(Path(refinement_inputs_json).read_text(encoding="utf-8"))
    packages = inputs_data.get("packages") if isinstance(inputs_data, dict) else inputs_data
    if not isinstance(packages, list):
        raise ValueError("Refinement input JSON must contain a packages list.")
    packages = _filter_packages_by_target_node_ids(packages, target_node_ids)
    prompt = Path(prompt_path).read_text(encoding="utf-8") if prompt_path else DEFAULT_PROMPT
    return run_outline_refinement(
        outline,
        packages,
        prompt=prompt,
        model=model,
        max_workers=max_workers,
        llm_config_override=llm_config_override,
        llm_callable=llm_callable,
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
        force_refresh=force_refresh,
    )


def run_outline_refinement(
    outline: dict[str, Any],
    packages: list[dict[str, Any]],
    *,
    prompt: str = DEFAULT_PROMPT,
    model: str | None = None,
    max_workers: int | None = None,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> OutlineRefinementRunResult:
    config = llm_config_override or llm_config(task_key=TASK_KEY, model_override=model)
    effective_max_workers = _effective_max_workers(max_workers, config)
    working_outline = copy.deepcopy(outline)
    generated_at = _now_iso()
    started = time.monotonic()
    tasks: list[OutlineRefinementTaskRun] = []
    warnings: list[str] = []

    if not packages:
        warnings.append("没有需要 LLM 补强的目录节点。")

    if not config.api_key and llm_callable is None:
        warnings.append("API_KEY 未配置，目录补强未调用 LLM，保留规则版目录。")
        tasks = [_skipped_task(package, config.model, "API_KEY 未配置。") for package in packages]
    else:
        tasks = _run_refinement_tasks(
            packages,
            prompt=prompt,
            config=config,
            llm_callable=llm_callable,
            max_workers=effective_max_workers,
            cache_dir=cache_dir,
            force_refresh=force_refresh,
        )
        for task, package in zip(tasks, packages, strict=False):
            if task.validation.get("valid") and task.parsed_json:
                _apply_refinement_output(working_outline, package, task.parsed_json, task.validation)
                task.applied = True

    _finalize_outline(working_outline, tasks)
    duration = time.monotonic() - started
    return OutlineRefinementRunResult(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        task_count=len(tasks),
        applied_count=sum(1 for task in tasks if task.applied),
        skipped_count=sum(1 for task in tasks if task.status == "skipped"),
        failed_count=sum(1 for task in tasks if task.status == "failed"),
        duration_seconds=duration,
        execution_mode="parallel" if len(packages) > 1 and effective_max_workers > 1 else "serial",
        max_workers=effective_max_workers,
        outline=working_outline,
        tasks=tasks,
        warnings=warnings,
    )


def write_outline_refinement_outputs(
    result: OutlineRefinementRunResult,
    json_path: str | Path,
    report_path: str | Path,
    outline_json_path: str | Path | None = None,
    outline_report_path: str | Path | None = None,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_outline_refinement_report(result), encoding="utf-8")
    if outline_json_path:
        outline_json_target = Path(outline_json_path)
        outline_json_target.parent.mkdir(parents=True, exist_ok=True)
        outline_json_target.write_text(json.dumps(result.outline, ensure_ascii=False, indent=2), encoding="utf-8")
    if outline_report_path:
        outline_report_target = Path(outline_report_path)
        outline_report_target.parent.mkdir(parents=True, exist_ok=True)
        outline_report_target.write_text(render_outline_report(result.outline), encoding="utf-8")


def render_outline_refinement_report(result: OutlineRefinementRunResult) -> str:
    lines = [
        "# LLM 二三级目录补强运行报告",
        "",
        f"- 生成时间：{result.generated_at}",
        f"- 服务商：{result.provider}",
        f"- 模型：{result.model}",
        f"- Base URL：{result.base_url or '默认 OpenAI'}",
        f"- 总耗时秒：{result.duration_seconds:.2f}",
        f"- 执行方式：{result.execution_mode}",
        f"- 并发数：{result.max_workers}",
        f"- 任务数：{result.task_count}",
        f"- 已应用：{result.applied_count}",
        f"- 跳过：{result.skipped_count}",
        f"- 失败：{result.failed_count}",
        "",
        "## 任务概览",
        "",
        "| 一级目录 | 状态 | 缓存 | 是否应用 | 校验问题 | 错误 |",
        "|---|---|---|---|---:|---|",
    ]
    for task in result.tasks:
        lines.append(
            f"| {_cell(task.level_1_title)} | {_cell(task.status)} | "
            f"{_cell(task.cache_status)} | "
            f"{'是' if task.applied else '否'} | {task.validation.get('issue_count', 0)} | {_cell(task.error)} |"
        )
    if not result.tasks:
        lines.append("| - | 无任务 | disabled | 否 | 0 | - |")

    lines.extend(["", "## 校验明细", ""])
    for task in result.tasks:
        lines.extend([f"### {task.level_1_title}", ""])
        lines.append(f"- 节点 ID：`{task.target_node_id}`")
        lines.append(f"- 状态：{task.status}")
        lines.append(f"- 是否应用：{'是' if task.applied else '否'}")
        if task.error:
            lines.append(f"- 错误：{task.error}")
        issues = task.validation.get("issues", []) if task.validation else []
        if issues:
            lines.append("")
            lines.append("问题：")
            for issue in issues:
                lines.append(
                    f"- [{issue.get('severity')}] {issue.get('type')}：{issue.get('message')}"
                )
        lines.append("")
    if result.warnings:
        lines.extend(["## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def _run_single_refinement(
    package: dict[str, Any],
    *,
    prompt: str,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
    cache_dir: Path | None,
    force_refresh: bool,
) -> OutlineRefinementTaskRun:
    target = package.get("target_outline_node") or {}
    target_node_id = str(target.get("node_id") or "")
    level_1_title = str(target.get("level_1_title") or "")
    started_at = _now_iso()
    start = time.monotonic()
    try:
        cache_key = _task_cache_key(package=package, prompt=prompt, config=config)
        if cache_dir is not None and not force_refresh:
            cached_task = _read_cached_task(cache_dir, cache_key)
            if cached_task is not None:
                cached_task.started_at = started_at
                cached_task.completed_at = _now_iso()
                cached_task.duration_seconds = time.monotonic() - start
                cached_task.cache_status = "hit"
                cached_task.cache_key = cache_key
                cached_task.applied = False
                return cached_task
        response_text = (
            llm_callable(package, config)
            if llm_callable is not None
            else call_openai_json(
                config=config,
                system_prompt=prompt,
                user_input=json.dumps(package, ensure_ascii=False, indent=2),
            )
        )
        parsed_json = parse_json_response(response_text)
        validation = validate_outline_refinement_output(parsed_json, package)
        status = "failed" if validation.get("blocking") else "completed"
        completed_at = _now_iso()
        task = OutlineRefinementTaskRun(
            target_node_id=target_node_id,
            level_1_title=level_1_title,
            status=status,
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=completed_at,
            model=config.model,
            output_text=response_text,
            parsed_json=parsed_json,
            validation=validation,
            applied=False,
            cache_status="miss" if cache_dir is not None else "disabled",
            cache_key=cache_key if cache_dir is not None else None,
        )
        if cache_dir is not None and task.status == "completed":
            _write_cached_task(cache_dir, cache_key, task)
        return task
    except Exception as exc:  # pragma: no cover - 真实网络调用和异常兜底
        return OutlineRefinementTaskRun(
            target_node_id=target_node_id,
            level_1_title=level_1_title,
            status="failed",
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=_now_iso(),
            model=config.model,
            validation={"valid": False, "blocking": True, "issue_count": 1, "issues": []},
            error=str(exc),
            cache_status="miss" if cache_dir is not None else "disabled",
        )


def _run_refinement_tasks(
    packages: list[dict[str, Any]],
    *,
    prompt: str,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
    max_workers: int,
    cache_dir: Path | None,
    force_refresh: bool,
) -> list[OutlineRefinementTaskRun]:
    if len(packages) <= 1 or max_workers <= 1:
        return [
            _run_single_refinement(
                package,
                prompt=prompt,
                config=config,
                llm_callable=llm_callable,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
            for package in packages
        ]
    workers = max(1, min(max_workers, len(packages)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                copy_context().run,
                _run_single_refinement,
                package,
                prompt=prompt,
                config=config,
                llm_callable=llm_callable,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
            for package in packages
        ]
        return [future.result() for future in futures]


def _effective_max_workers(max_workers: int | None, config: LlmClientConfig) -> int:
    return max(1, int(max_workers if max_workers is not None else config.max_workers))


def _filter_packages_by_target_node_ids(
    packages: list[dict[str, Any]],
    target_node_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if not target_node_ids:
        return packages
    selected = set(target_node_ids)
    return [
        package
        for package in packages
        if str((package.get("target_outline_node") or {}).get("node_id") or "") in selected
    ]


def _task_cache_key(
    *,
    package: dict[str, Any],
    prompt: str,
    config: LlmClientConfig,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task_key": TASK_KEY,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "api_type": config.api_type,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "structured_output_type": config.structured_output_type,
        "enable_thinking": config.enable_thinking,
        "reasoning_effort": config.reasoning_effort,
        "store_response": config.store_response,
        "prompt": prompt,
        "package": package,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cached_task(cache_dir: Path, cache_key: str) -> OutlineRefinementTaskRun | None:
    path = _cache_path(cache_dir, cache_key)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return OutlineRefinementTaskRun(**data)


def _write_cached_task(cache_dir: Path, cache_key: str, task: OutlineRefinementTaskRun) -> None:
    path = _cache_path(cache_dir, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(task)
    data["cache_status"] = "stored"
    data["cache_key"] = cache_key
    data["applied"] = False
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.json"


def _apply_refinement_output(
    outline: dict[str, Any],
    package: dict[str, Any],
    output: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    node = _find_node(outline, output.get("target_node_id"))
    if node is None:
        raise ValueError(f"Target outline node not found: {output.get('target_node_id')}")
    parent_number = str(node.get("number") or "")
    refined_children = [
        _child_from_refined(
            node,
            child,
            child_index=index,
            parent_number=parent_number,
            validation=validation,
        )
        for index, child in enumerate(output.get("refined_children") or [], start=1)
        if isinstance(child, dict)
    ]
    node["children"] = refined_children
    node["template_source"] = "llm_refined"
    node["llm_refinement"] = {
        "status": "applied",
        "schema_version": output.get("schema_version") or OUTPUT_SCHEMA_VERSION,
        "trigger_reasons": package.get("trigger_reasons") or [],
        "validation": validation,
        "quality_self_check": output.get("quality_self_check") or {},
    }
    quality_self_check = output.get("quality_self_check") if isinstance(output.get("quality_self_check"), dict) else {}
    if validation.get("warning_issue_count", 0) > 0 or quality_self_check.get("needs_human_review") is True:
        node["requires_review"] = True
        node["review_reason"] = _append_reason(node.get("review_reason"), "LLM 补强结果存在警告，需人工复核。")
    else:
        node["requires_review"] = False
        node["review_reason"] = None


def _child_from_refined(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    child_index: int,
    parent_number: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    number = f"{parent_number}.{child_index}" if parent_number else str(child_index)
    node_id = f"{parent.get('node_id')}_{child_index:03d}"
    requires_review = bool(child.get("requires_review")) or validation.get("warning_issue_count", 0) > 0
    result = {
        "node_id": node_id,
        "level": 2,
        "number": number,
        "title": str(child.get("title") or "").strip(),
        "title_source": child.get("title_source") or "generated",
        "domain": parent.get("domain"),
        "category": parent.get("category"),
        "template_refs": parent.get("template_refs") or [],
        "children": [],
        "requires_review": requires_review,
        "review_reason": child.get("reason") if requires_review else None,
        "generation_status": parent.get("generation_status"),
    }
    result["children"] = [
        _grandchild_from_refined(
            result,
            grandchild,
            grandchild_index=index,
        )
        for index, grandchild in enumerate(child.get("children") or [], start=1)
        if isinstance(grandchild, dict)
    ]
    return result


def _grandchild_from_refined(
    parent: dict[str, Any],
    grandchild: dict[str, Any],
    *,
    grandchild_index: int,
) -> dict[str, Any]:
    number = f"{parent.get('number')}.{grandchild_index}"
    return {
        "node_id": f"{parent.get('node_id')}_{grandchild_index:03d}",
        "level": 3,
        "number": number,
        "title": str(grandchild.get("title") or "").strip(),
        "title_source": grandchild.get("title_source") or "generated",
        "domain": parent.get("domain"),
        "category": parent.get("category"),
        "template_refs": parent.get("template_refs") or [],
        "children": [],
        "requires_review": bool(grandchild.get("requires_review")),
        "review_reason": grandchild.get("reason") if grandchild.get("requires_review") else None,
        "generation_status": parent.get("generation_status"),
    }


def _finalize_outline(outline: dict[str, Any], tasks: list[OutlineRefinementTaskRun]) -> None:
    outline["refinement"] = {
        "schema_version": SCHEMA_VERSION,
        "task_key": TASK_KEY,
        "status": _refinement_status(tasks),
        "task_count": len(tasks),
        "applied_count": sum(1 for task in tasks if task.applied),
        "failed_count": sum(1 for task in tasks if task.status == "failed"),
        "skipped_count": sum(1 for task in tasks if task.status == "skipped"),
    }
    outline["generator_version"] = f"{outline.get('generator_version') or 'unknown'}+llm_refinement"
    refresh_outline_confirmation(outline)


def _refinement_status(tasks: list[OutlineRefinementTaskRun]) -> str:
    if not tasks:
        return "no_tasks"
    if all(task.applied for task in tasks):
        return "completed"
    if any(task.applied for task in tasks):
        return "partial"
    if all(task.status == "skipped" for task in tasks):
        return "skipped"
    return "failed"


def _skipped_task(package: dict[str, Any], model: str, error: str) -> OutlineRefinementTaskRun:
    target = package.get("target_outline_node") or {}
    now = _now_iso()
    return OutlineRefinementTaskRun(
        target_node_id=str(target.get("node_id") or ""),
        level_1_title=str(target.get("level_1_title") or ""),
        status="skipped",
        duration_seconds=0.0,
        started_at=now,
        completed_at=now,
        model=model,
        validation={"valid": False, "blocking": False, "issue_count": 0, "issues": []},
        error=error,
    )


def _find_node(outline: dict[str, Any], node_id: Any) -> dict[str, Any] | None:
    for node in outline.get("nodes") or []:
        if isinstance(node, dict) and node.get("node_id") == node_id:
            return node
    return None


def _append_reason(existing: Any, reason: str) -> str:
    parts = [str(existing).strip()] if existing else []
    parts.append(reason)
    return "；".join(dict.fromkeys(part for part in parts if part))


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(timespec="seconds")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
