"""技术标章节正文批量生成调度器。

本模块负责把章节正文生成输入包拆成独立任务执行，并将每个生成单元即时落盘。
它不改写单章节生成逻辑，只提供断点续跑、失败重试、跳过已完成章节和汇总输出能力。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from construction_bidding_agent.llm_config import LlmClientConfig, llm_config, load_dotenv

from .chapter_writer import (
    DEFAULT_PROMPT,
    DEFAULT_TIMEZONE,
    LLM_INPUT_PROFILE,
    LLM_INPUT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    TASK_KEY,
    ChapterGenerationRunResult,
    ChapterGenerationTaskRun,
    LlmCallable,
    _filter_packages,
    dedupe_images_across_chapters,
    run_chapter_generation,
    validate_chapter_output,
)


BATCH_ARTIFACT_SCHEMA_VERSION = "chapter_generation_task_artifact_v0.1"
BATCH_RUN_SCHEMA_VERSION = "chapter_generation_batch_run_v0.1"
AUTO_RETRYABLE_FAILURE_TYPES = {"empty_response", "json_parse_error", "timeout", "connection_error", "transient_llm_error"}
ChapterTaskProgressCallback = Callable[[dict[str, Any]], None]


def run_chapter_generation_batch_from_files(
    chapter_inputs_json: str | Path,
    *,
    state_dir: str | Path,
    prompt_path: str | Path | None = None,
    model: str | None = None,
    max_workers: int | None = None,
    max_packages: int | None = None,
    chapter_title_contains: str | None = None,
    force: bool = False,
    retry_failed: bool = True,
    dry_run: bool = False,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
) -> ChapterGenerationRunResult:
    """从章节输入包文件执行可断点续跑的批量生成。"""

    load_dotenv(Path.cwd() / ".env")
    inputs_data = json.loads(Path(chapter_inputs_json).read_text(encoding="utf-8"))
    packages = inputs_data.get("packages") if isinstance(inputs_data, dict) else inputs_data
    if not isinstance(packages, list):
        raise ValueError("Chapter generation input JSON must contain a packages list.")
    packages = _filter_packages(packages, chapter_title_contains=chapter_title_contains)
    if max_packages is not None:
        packages = packages[:max_packages]
    prompt = Path(prompt_path).read_text(encoding="utf-8") if prompt_path else DEFAULT_PROMPT
    return run_chapter_generation_batch(
        packages,
        state_dir=state_dir,
        prompt=prompt,
        model=model,
        max_workers=max_workers,
        force=force,
        retry_failed=retry_failed,
        dry_run=dry_run,
        llm_config_override=llm_config_override,
        llm_callable=llm_callable,
    )


def run_chapter_generation_batch(
    packages: list[dict[str, Any]],
    *,
    state_dir: str | Path,
    prompt: str = DEFAULT_PROMPT,
    model: str | None = None,
    max_workers: int | None = None,
    force: bool = False,
    retry_failed: bool = True,
    dry_run: bool = False,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
    progress_callback: ChapterTaskProgressCallback | None = None,
) -> ChapterGenerationRunResult:
    """执行章节正文批量生成，并按生成单元独立落盘。"""

    config = llm_config_override or llm_config(task_key=TASK_KEY, model_override=model)
    effective_max_workers = _effective_max_workers(max_workers, config)
    started = time.monotonic()
    generated_at = _now_iso()
    state_path = Path(state_dir)
    chapter_dir = state_path / "chapters"
    if not dry_run:
        chapter_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if dry_run:
        warnings.append("dry-run 模式：仅检查断点状态，不调用 LLM，也不写入章节状态文件。")
    if not packages:
        warnings.append("没有需要生成正文的章节输入包。")

    prepared = [_prepare_package(package, index, chapter_dir) for index, package in enumerate(packages)]
    _prepare_cache_metadata(prepared, prompt=prompt, config=config)
    existing_tasks: dict[int, ChapterGenerationTaskRun] = {}
    pending: list[dict[str, Any]] = []
    task_progress = _new_task_progress(len(prepared))
    for item in prepared:
        artifact = _read_existing_artifact(item)
        decision = _resume_decision(
            artifact,
            item["package"],
            item["package_hash"],
            str(item.get("cache_key") or ""),
            force=force,
            retry_failed=retry_failed,
        )
        item["resume_decision"] = decision
        if decision["action"] == "skip":
            if artifact is not None:
                artifact["_current_package"] = item["package"]
            task = _task_from_artifact(artifact)
            _apply_resume_metadata(task, item, cache_status="hit")
            existing_tasks[item["index"]] = task
            if not dry_run:
                _ensure_canonical_artifact(item, artifact)
            _notify_task_progress(
                progress_callback,
                task_progress,
                task=task,
                event="skipped_existing",
            )
        else:
            pending.append(item)

    dry_run_pending_count = len(pending)
    generated_tasks: dict[int, ChapterGenerationTaskRun] = {}
    if dry_run:
        for item in pending:
            task = _pending_task(item["package"], config.model, item["resume_decision"]["reason"])
            _apply_resume_metadata(task, item, cache_status="miss")
            generated_tasks[item["index"]] = task
    elif pending:
        if not config.api_key and llm_callable is None:
            warnings.append("API_KEY 未配置，待生成章节全部跳过，未调用 LLM。")
            for item in pending:
                task = _skipped_task(item["package"], config.model, "API_KEY 未配置。")
                _apply_resume_metadata(task, item, cache_status="disabled")
                generated_tasks[item["index"]] = task
                _write_task_artifact(item, task, config=config, status=task.status)
                _notify_task_progress(progress_callback, task_progress, task=task, event="skipped")
        else:
            workers = max(1, min(effective_max_workers, len(pending)))
            execution_pending = _scheduled_pending_items(pending)
            if workers <= 1:
                for item in execution_pending:
                    task = _generate_one(item, prompt=prompt, config=config, llm_callable=llm_callable)
                    generated_tasks[item["index"]] = task
                    _notify_task_progress(progress_callback, task_progress, task=task, event="completed")
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_map = {
                        executor.submit(
                            copy_context().run,
                            _generate_one,
                            item,
                            prompt=prompt,
                            config=config,
                            llm_callable=llm_callable,
                        ): item
                        for item in execution_pending
                    }
                    for future in as_completed(future_map):
                        item = future_map[future]
                        generated_tasks[item["index"]] = future.result()
                        _notify_task_progress(
                            progress_callback,
                            task_progress,
                            task=generated_tasks[item["index"]],
                            event="completed",
                        )

    tasks = _ordered_tasks(prepared, existing_tasks, generated_tasks)
    dedupe_summary = _postprocess_completed_task_images(
        tasks,
        prepared,
        config=config,
        dry_run=dry_run,
    )
    if dedupe_summary.get("removed_count"):
        warnings.append(f"跨章节图片去重：移除 {dedupe_summary['removed_count']} 个重复图片引用。")
    chapters = [task.parsed_json for task in tasks if task.status == "completed" and task.parsed_json]
    if dry_run:
        warnings.append(f"dry-run 待生成章节数：{dry_run_pending_count}。")
    duration = time.monotonic() - started
    return ChapterGenerationRunResult(
        schema_version=BATCH_RUN_SCHEMA_VERSION,
        generated_at=generated_at,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        task_count=len(tasks),
        completed_count=sum(1 for task in tasks if task.status == "completed"),
        skipped_count=sum(1 for task in tasks if task.status == "skipped"),
        failed_count=sum(1 for task in tasks if task.status == "failed"),
        duration_seconds=duration,
        execution_mode="dry_run" if dry_run else ("parallel_resumable" if len(pending) > 1 and effective_max_workers > 1 else "serial_resumable"),
        max_workers=effective_max_workers,
        chapters=chapters,
        tasks=tasks,
        warnings=warnings,
    )


def render_chapter_generation_batch_status(
    *,
    chapter_inputs_json: str | Path,
    state_dir: str | Path,
    max_packages: int | None = None,
    chapter_title_contains: str | None = None,
) -> str:
    """渲染当前批量生成状态，便于用户查看断点续跑进度。"""

    result = run_chapter_generation_batch_from_files(
        chapter_inputs_json,
        state_dir=state_dir,
        max_packages=max_packages,
        chapter_title_contains=chapter_title_contains,
        dry_run=True,
    )
    lines = [
        "# 技术标章节批量生成状态",
        "",
        f"- 检查时间：{result.generated_at}",
        f"- 任务数：{result.task_count}",
        f"- 已完成：{result.completed_count}",
        f"- 待生成/跳过：{result.skipped_count}",
        f"- 失败：{result.failed_count}",
        "",
        "| 序号 | 章节路径 | 状态 | 缓存 | 说明 |",
        "|---:|---|---|---|---|",
    ]
    for index, task in enumerate(result.tasks, start=1):
        reason = task.resume_reason or task.error or ""
        lines.append(
            f"| {index} | {_cell(' > '.join(task.chapter_path))} | {_cell(task.status)} | "
            f"{_cell(task.cache_status)} | {_cell(reason)} |"
        )
    return "\n".join(lines)


def _generate_one(
    item: dict[str, Any],
    *,
    prompt: str,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
) -> ChapterGenerationTaskRun:
    attempts: list[dict[str, Any]] = []
    max_attempts = max(1, int(config.max_retries or 0) + 1)
    task: ChapterGenerationTaskRun | None = None
    for attempt_index in range(1, max_attempts + 1):
        result = run_chapter_generation(
            [item["package"]],
            prompt=prompt,
            max_workers=1,
            llm_config_override=config,
            llm_callable=llm_callable,
        )
        task = result.tasks[0] if result.tasks else _skipped_task(item["package"], config.model, "未生成任务结果。")
        attempts.append(
            {
                "attempt": attempt_index,
                "status": task.status,
                "failure_type": task.failure_type,
                "error": task.error,
                "duration_seconds": round(float(task.duration_seconds or 0), 3),
            }
        )
        if task.status != "failed" or not _should_auto_retry_task(task) or attempt_index >= max_attempts:
            break
        time.sleep(min(2.0 * attempt_index, 6.0))
    if task is None:
        task = _skipped_task(item["package"], config.model, "未生成任务结果。")
    _apply_resume_metadata(task, item, cache_status="miss")
    if attempts:
        task.retry_attempt_count = len(attempts) - 1
        task.retry_summary = {
            "enabled": True,
            "max_attempts": max_attempts,
            "attempts": attempts,
            "final_status": task.status,
        }
    _write_task_artifact(item, task, config=config, status=task.status)
    return task


def _should_auto_retry_task(task: ChapterGenerationTaskRun) -> bool:
    failure_type = str(task.failure_type or "")
    if failure_type in AUTO_RETRYABLE_FAILURE_TYPES:
        return True
    error = str(task.error or task.failure_reason or "").lower()
    retryable_patterns = [
        "response content is empty",
        "unterminated string",
        "jsondecodeerror",
        "expecting value",
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "temporarily",
    ]
    return any(pattern in error for pattern in retryable_patterns)


def _effective_max_workers(max_workers: int | None, config: LlmClientConfig) -> int:
    return max(1, int(max_workers if max_workers is not None else config.max_workers))


def _new_task_progress(total: int) -> dict[str, Any]:
    return {
        "total": max(0, int(total)),
        "finished": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "retrying": 0,
    }


def _notify_task_progress(
    callback: ChapterTaskProgressCallback | None,
    progress: dict[str, Any],
    *,
    task: ChapterGenerationTaskRun,
    event: str,
) -> None:
    if callback is None:
        return
    status = str(task.status or "")
    if status == "completed":
        progress["completed"] += 1
        progress["finished"] += 1
    elif status == "failed":
        progress["failed"] += 1
        progress["finished"] += 1
    elif status == "skipped":
        progress["skipped"] += 1
        progress["finished"] += 1
    else:
        progress["finished"] += 1
    payload = {
        "event": event,
        "status": status,
        "unit_id": task.unit_id,
        "chapter_path": list(task.chapter_path or []),
        "duration_seconds": task.duration_seconds,
        "error": task.error,
        **progress,
    }
    callback(payload)


def _prepare_package(package: dict[str, Any], index: int, chapter_dir: Path) -> dict[str, Any]:
    unit = package.get("generation_unit") or {}
    unit_id = str(unit.get("unit_id") or f"unit_{index + 1:04d}")
    safe_unit_id = _safe_filename(unit_id)
    return {
        "index": index,
        "package": package,
        "package_hash": _stable_hash(package),
        "cache_key": None,
        "artifact_path": chapter_dir / f"{safe_unit_id}.json",
        "legacy_artifact_pattern": f"*_{safe_unit_id}.json",
    }


def _prepare_cache_metadata(prepared: list[dict[str, Any]], *, prompt: str, config: LlmClientConfig) -> None:
    for item in prepared:
        item["cache_key"] = _chapter_cache_key(package=item["package"], prompt=prompt, config=config)


def _scheduled_pending_items(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按预计耗时从重到轻调度，结果仍按原始章节顺序汇总。"""

    return sorted(
        pending,
        key=lambda item: (-_estimated_generation_cost(item.get("package") or {}), int(item.get("index") or 0)),
    )


def _estimated_generation_cost(package: dict[str, Any]) -> int:
    policy = package.get("expanded_generation_policy") or {}
    targets = policy.get("targets") if isinstance(policy.get("targets"), dict) else {}
    unit = package.get("generation_unit") if isinstance(package.get("generation_unit"), dict) else {}
    path_text = " ".join(str(part) for part in unit.get("chapter_path") or [])
    child_count = len(unit.get("child_headings") or [])
    cost = _json_char_count_for_schedule(package) // 100
    cost += int(targets.get("min_paragraphs_total") or 0) * 12
    cost += int(targets.get("min_rich_tables") or 0) * 80
    cost += int(targets.get("min_image_refs") or 0) * 15
    cost += child_count * 40
    cost += len(package.get("excellent_bid_references") or []) * 35
    cost += len(package.get("table_references") or []) * 45
    cost += len(package.get("image_candidate_pool") or package.get("image_candidates") or []) * 4
    cost += len(package.get("image_group_candidate_pool") or package.get("image_group_candidates") or []) * 30
    if any(keyword in path_text for keyword in ["合理化建议", "危大工程", "建筑垃圾", "文明施工", "施工方案"]):
        cost += 600
    return cost


def _json_char_count_for_schedule(value: Any) -> int:
    if not value:
        return 0
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _apply_resume_metadata(task: ChapterGenerationTaskRun, item: dict[str, Any], *, cache_status: str) -> None:
    decision = item.get("resume_decision") if isinstance(item.get("resume_decision"), dict) else {}
    task.cache_status = cache_status
    task.cache_key = str(item.get("cache_key") or "")
    task.resume_action = str(decision.get("action") or "")
    task.resume_reason = str(decision.get("reason") or "")


def _postprocess_completed_task_images(
    tasks: list[ChapterGenerationTaskRun],
    prepared: list[dict[str, Any]],
    *,
    config: LlmClientConfig,
    dry_run: bool,
) -> dict[str, Any]:
    completed_pairs = [
        (index, task)
        for index, task in enumerate(tasks)
        if task.status == "completed" and isinstance(task.parsed_json, dict)
    ]
    chapters = [task.parsed_json for _, task in completed_pairs if task.parsed_json is not None]
    if not chapters:
        return {"enabled": True, "removed_count": 0, "changed_chapter_indexes": []}

    dedupe_summary = dedupe_images_across_chapters(chapters)
    changed_indexes = [int(index) for index in dedupe_summary.get("changed_chapter_indexes") or []]
    if not changed_indexes:
        return dedupe_summary

    prepared_by_index = {int(item["index"]): item for item in prepared}
    for chapter_index in changed_indexes:
        if chapter_index >= len(completed_pairs):
            continue
        prepared_index, task = completed_pairs[chapter_index]
        item = prepared_by_index.get(prepared_index)
        if item is None or task.parsed_json is None:
            continue
        task.validation = validate_chapter_output(task.parsed_json, item["package"])
        if task.validation.get("blocking"):
            task.status = "failed"
            task.error = "跨章节图片去重后章节校验失败。"
        if not dry_run:
            _write_task_artifact(item, task, config=config, status=task.status)
    return dedupe_summary


def _resume_decision(
    artifact: dict[str, Any] | None,
    package: dict[str, Any],
    package_hash: str,
    cache_key: str,
    *,
    force: bool,
    retry_failed: bool,
) -> dict[str, str]:
    if force:
        return {"action": "generate", "reason": "force 参数要求重新生成。"}
    if not artifact:
        return {"action": "generate", "reason": "未找到章节状态文件。"}
    if artifact.get("schema_version") != BATCH_ARTIFACT_SCHEMA_VERSION:
        return {"action": "generate", "reason": "章节状态文件版本不匹配。"}
    if artifact.get("package_hash") != package_hash:
        return {"action": "generate", "reason": "输入包指纹变化，需要重新生成。"}
    if artifact.get("cache_key") and artifact.get("cache_key") != cache_key:
        return {"action": "generate", "reason": "模型、提示词或生成参数变化，需要重新生成。"}
    if not _artifact_identity_matches(artifact, package):
        return {"action": "generate", "reason": "章节身份字段不匹配。"}
    status = str(artifact.get("status") or "")
    if status == "completed" and isinstance(artifact.get("chapter"), dict):
        return {"action": "skip", "reason": "章节已完成且输入包未变化。"}
    if status == "failed" and not retry_failed:
        return {"action": "skip", "reason": "章节上次失败，retry_failed=false，暂不重试。"}
    return {"action": "generate", "reason": f"章节状态为 {status or 'unknown'}，需要生成或重试。"}


def _artifact_identity_matches(artifact: dict[str, Any], package: dict[str, Any]) -> bool:
    unit = package.get("generation_unit") or {}
    return (
        str(artifact.get("unit_id") or "") == str(unit.get("unit_id") or "")
        and str(artifact.get("target_node_id") or "") == str(unit.get("target_node_id") or "")
        and list(artifact.get("chapter_path") or []) == list(unit.get("chapter_path") or [])
    )


def _write_task_artifact(
    item: dict[str, Any],
    task: ChapterGenerationTaskRun,
    *,
    config: LlmClientConfig,
    status: str,
) -> None:
    artifact_path = Path(item["artifact_path"])
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": BATCH_ARTIFACT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "unit_id": task.unit_id,
        "target_node_id": task.target_node_id,
        "chapter_path": task.chapter_path,
        "package_hash": item["package_hash"],
        "cache_key": item.get("cache_key"),
        "cache_status": task.cache_status,
        "resume_action": task.resume_action,
        "resume_reason": task.resume_reason,
        "status": status,
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "api_type": config.api_type,
        "structured_output_type": config.structured_output_type,
        "enable_thinking": config.enable_thinking,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "task": asdict(task),
        "chapter": task.parsed_json if task.status == "completed" else None,
    }
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_existing_artifact(item: dict[str, Any]) -> dict[str, Any] | None:
    """读取当前章节状态文件，兼容早期带序号前缀的临时文件名。"""

    paths = [Path(item["artifact_path"])]
    legacy_pattern = item.get("legacy_artifact_pattern")
    if legacy_pattern:
        paths.extend(sorted(Path(item["artifact_path"]).parent.glob(str(legacy_pattern))))

    matching: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            matching.append(data)
    if not matching:
        return None

    package_hash = item.get("package_hash")
    identity_matches = [artifact for artifact in matching if _artifact_identity_matches(artifact, item["package"])]
    hash_matches = [artifact for artifact in identity_matches if artifact.get("package_hash") == package_hash]
    completed = [artifact for artifact in hash_matches if artifact.get("status") == "completed"]
    if completed:
        return _best_completed_artifact(completed)
    if hash_matches:
        return hash_matches[0]
    if identity_matches:
        return identity_matches[0]
    return matching[0]


def _best_completed_artifact(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return min(artifacts, key=_artifact_quality_key)


def _artifact_quality_key(artifact: dict[str, Any]) -> tuple[int, int, str]:
    task = artifact.get("task") if isinstance(artifact.get("task"), dict) else {}
    validation = task.get("validation") if isinstance(task.get("validation"), dict) else {}
    blocking = 1 if validation.get("blocking") else 0
    issue_count = int(validation.get("issue_count") or 0)
    completed_at = str(task.get("completed_at") or artifact.get("generated_at") or "")
    return blocking, issue_count, completed_at


def _ensure_canonical_artifact(item: dict[str, Any], artifact: dict[str, Any] | None) -> None:
    if not artifact:
        return
    artifact_path = Path(item["artifact_path"])
    if artifact_path.exists():
        try:
            existing = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and _artifact_quality_key(existing) <= _artifact_quality_key(artifact):
            return
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_from_artifact(artifact: dict[str, Any] | None) -> ChapterGenerationTaskRun:
    if not artifact:
        raise ValueError("artifact is required.")
    task_data = artifact.get("task") if isinstance(artifact.get("task"), dict) else {}
    parsed_json = task_data.get("parsed_json") if isinstance(task_data.get("parsed_json"), dict) else artifact.get("chapter")
    validation = task_data.get("validation") if isinstance(task_data.get("validation"), dict) else {}
    package = artifact.get("_current_package")
    if isinstance(parsed_json, dict) and isinstance(package, dict):
        validation = validate_chapter_output(parsed_json, package)
    return ChapterGenerationTaskRun(
        unit_id=str(task_data.get("unit_id") or artifact.get("unit_id") or ""),
        target_node_id=str(task_data.get("target_node_id") or artifact.get("target_node_id") or ""),
        chapter_path=[str(part) for part in task_data.get("chapter_path") or artifact.get("chapter_path") or []],
        status=str(task_data.get("status") or artifact.get("status") or "completed"),
        duration_seconds=float(task_data.get("duration_seconds") or 0),
        started_at=task_data.get("started_at"),
        completed_at=task_data.get("completed_at") or artifact.get("generated_at"),
        model=task_data.get("model") or artifact.get("model"),
        output_text=str(task_data.get("output_text") or ""),
        parsed_json=parsed_json,
        validation=validation,
        error=task_data.get("error"),
        cache_status=str(task_data.get("cache_status") or artifact.get("cache_status") or "disabled"),
        cache_key=task_data.get("cache_key") or artifact.get("cache_key"),
        resume_action=task_data.get("resume_action") or artifact.get("resume_action"),
        resume_reason=task_data.get("resume_reason") or artifact.get("resume_reason"),
        failure_type=task_data.get("failure_type") or artifact.get("failure_type"),
        failure_reason=task_data.get("failure_reason") or artifact.get("failure_reason"),
        retry_attempt_count=int(task_data.get("retry_attempt_count") or 0),
        retry_summary=task_data.get("retry_summary") if isinstance(task_data.get("retry_summary"), dict) else {},
        repair_attempt_count=int(task_data.get("repair_attempt_count") or 0),
        repair_duration_seconds=float(task_data.get("repair_duration_seconds") or 0.0),
        repair_summary=task_data.get("repair_summary") if isinstance(task_data.get("repair_summary"), dict) else {},
    )


def _pending_task(package: dict[str, Any], model: str, reason: str) -> ChapterGenerationTaskRun:
    unit = package.get("generation_unit") or {}
    now = _now_iso()
    return ChapterGenerationTaskRun(
        unit_id=str(unit.get("unit_id") or ""),
        target_node_id=str(unit.get("target_node_id") or ""),
        chapter_path=[str(part) for part in unit.get("chapter_path") or []],
        status="skipped",
        duration_seconds=0,
        started_at=now,
        completed_at=now,
        model=model,
        validation={"valid": False, "blocking": False, "issue_count": 0, "issues": []},
        error=reason,
        failure_type="pending",
        failure_reason=reason,
    )


def _skipped_task(package: dict[str, Any], model: str, error: str) -> ChapterGenerationTaskRun:
    unit = package.get("generation_unit") or {}
    now = _now_iso()
    return ChapterGenerationTaskRun(
        unit_id=str(unit.get("unit_id") or ""),
        target_node_id=str(unit.get("target_node_id") or ""),
        chapter_path=[str(part) for part in unit.get("chapter_path") or []],
        status="skipped",
        duration_seconds=0,
        started_at=now,
        completed_at=now,
        model=model,
        validation={"valid": False, "blocking": False, "issue_count": 0, "issues": []},
        error=error,
        failure_type="configuration_skipped",
        failure_reason=error,
    )


def _ordered_tasks(
    prepared: list[dict[str, Any]],
    existing_tasks: dict[int, ChapterGenerationTaskRun],
    generated_tasks: dict[int, ChapterGenerationTaskRun],
) -> list[ChapterGenerationTaskRun]:
    tasks: list[ChapterGenerationTaskRun] = []
    for item in prepared:
        index = item["index"]
        task = existing_tasks.get(index) or generated_tasks.get(index)
        if task:
            tasks.append(task)
    return tasks


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chapter_cache_key(*, package: dict[str, Any], prompt: str, config: LlmClientConfig) -> str:
    payload = {
        "schema_version": BATCH_ARTIFACT_SCHEMA_VERSION,
        "llm_input_schema_version": LLM_INPUT_SCHEMA_VERSION,
        "llm_input_profile": LLM_INPUT_PROFILE,
        "task": TASK_KEY,
        "prompt": prompt,
        "package": package,
        "llm": {
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
        },
    }
    return _stable_hash(payload)


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    text = text.strip("._")
    return (text or "chapter")[:120]


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(timespec="seconds")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
