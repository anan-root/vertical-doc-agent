"""项目全链路耗时画像生成器。

本模块只读取已有任务记录和落盘产物，不改变解析、目录或正文生成逻辑。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROFILE_SCHEMA_VERSION = "project_timing_profile_v0.1"
DEFAULT_TIMEZONE = "Asia/Shanghai"
PROFILE_JSON_NAME = "tender_to_word_timing_profile.json"
PROFILE_MD_NAME = "tender_to_word_timing_profile.md"


def write_project_timing_profile(
    *,
    storage_root: str | Path,
    project: dict[str, Any],
    files: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成并写入项目耗时画像 JSON 和 Markdown。"""

    profile = build_project_timing_profile(
        storage_root=storage_root,
        project=project,
        files=files,
        jobs=jobs,
    )
    reports_dir = Path(storage_root) / "projects" / str(project.get("project_id") or "") / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / PROFILE_JSON_NAME).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports_dir / PROFILE_MD_NAME).write_text(render_project_timing_profile(profile), encoding="utf-8")
    return profile


def build_project_timing_profile(
    *,
    storage_root: str | Path,
    project: dict[str, Any],
    files: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """从现有产物构建机器可读耗时画像。"""

    storage = Path(storage_root)
    project_id = str(project.get("project_id") or "")
    project_dir = storage / "projects" / project_id
    artifacts = _artifact_paths(project_dir)
    artifact_stats = _artifact_stats(artifacts)
    parse_profile = _parse_profile(artifacts)
    outline_profile = _outline_profile(artifacts)
    chapter_profile = _chapter_profile(artifacts)
    word_profile = _word_profile(artifacts)
    chapter_job_history = _chapter_job_history(jobs, chapter_profile)
    stage_metrics = _stage_metrics(
        jobs=jobs,
        parse_profile=parse_profile,
        outline_profile=outline_profile,
        chapter_profile=chapter_profile,
        word_profile=word_profile,
    )
    upload_profile = _upload_profile(files)
    observed = _observed_window(upload_profile, artifacts)
    bottlenecks = _bottlenecks(stage_metrics, chapter_profile)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "project": {
            "project_id": project_id,
            "name": project.get("name"),
            "project_type": project.get("project_type"),
        },
        "upload": upload_profile,
        "observed_window": observed,
        "stage_metrics": stage_metrics,
        "llm_tasks": {
            "tender_parse": parse_profile.get("tasks", []),
            "outline_refinement": outline_profile.get("tasks", []),
        },
        "chapter_generation": chapter_profile,
        "chapter_job_history": chapter_job_history,
        "word_refresh": word_profile,
        "artifacts": artifact_stats,
        "bottlenecks": bottlenecks,
        "warnings": _profile_warnings(parse_profile, outline_profile, chapter_profile, word_profile),
    }


def render_project_timing_profile(profile: dict[str, Any]) -> str:
    """渲染给人阅读的耗时画像报告。"""

    project = profile.get("project") or {}
    observed = profile.get("observed_window") or {}
    chapter = profile.get("chapter_generation") or {}
    chapter_history = profile.get("chapter_job_history") or {}
    word = profile.get("word_refresh") or {}
    lines = [
        "# 从上传招标文件到下载 Word 初稿耗时画像报告",
        "",
        f"- 项目 ID：`{project.get('project_id') or ''}`",
        f"- 项目名称：{project.get('name') or ''}",
        f"- 报告生成时间：{profile.get('generated_at') or ''}",
        "- 统计口径：以后端任务记录和项目产物为准；本报告自动生成，不包含 API Key。",
        "",
        "## 一、结论摘要",
        "",
    ]
    if observed.get("upload_to_latest_word_seconds") is not None:
        lines.append(
            f"- 从最早上传文件到最新 Word 初稿产物的自然时间："
            f"{_duration_text(observed.get('upload_to_latest_word_seconds'))}。"
        )
    machine_seconds = sum(
        float(stage.get("duration_seconds") or 0)
        for stage in profile.get("stage_metrics") or []
        if stage.get("status") in {"succeeded", "failed", "completed", "available"}
    )
    lines.append(f"- 已记录阶段机器耗时合计：{_duration_text(machine_seconds)}。")
    if chapter.get("task_count"):
        lines.append(
            f"- 章节状态数：{chapter.get('task_count')}，平均单章耗时："
            f"{chapter.get('duration_stats', {}).get('avg_seconds', 0):.2f} 秒，"
            f"最慢章节：{chapter.get('duration_stats', {}).get('max_seconds', 0):.2f} 秒。"
        )
    if chapter_history.get("run_count"):
        latest_success = chapter_history.get("latest_successful_run") or {}
        if latest_success.get("duration_seconds") is not None:
            lines.append(f"- 最近一次成功正文生成任务耗时：{_duration_text(latest_success.get('duration_seconds'))}。")
        lines.append(
            f"- 正文生成任务累计活跃耗时：{_duration_text(chapter_history.get('cumulative_active_seconds'))}"
            f"（含中断/续跑/失败任务）；任务自然跨度：{_duration_text(chapter_history.get('natural_span_seconds'))}。"
        )
        if chapter_history.get("chapter_package_duration_sum_seconds") is not None:
            lines.append(
                f"- 章节包耗时合计：{_duration_text(chapter_history.get('chapter_package_duration_sum_seconds'))}"
                "，该值为并发任务内各小节包耗时相加，不等同于用户等待时间。"
            )
    if word.get("duration_seconds") is not None:
        lines.append(f"- 最近 Word 初稿刷新耗时：{word.get('duration_seconds'):.2f} 秒。")
    lines.extend(["", "## 二、阶段耗时", "", "| 阶段 | 状态 | 耗时 | 任务/产物 | 说明 |", "|---|---|---:|---|---|"])
    for stage in profile.get("stage_metrics") or []:
        lines.append(
            f"| {_cell(stage.get('label'))} | {_cell(stage.get('status'))} | "
            f"{_duration_text(stage.get('duration_seconds'))} | {_cell(stage.get('source'))} | {_cell(stage.get('note'))} |"
        )

    lines.extend(["", "## 三、LLM 抽取与目录任务", ""])
    for group_title, tasks in [
        ("招标文件解析 LLM 任务", (profile.get("llm_tasks") or {}).get("tender_parse") or []),
        ("目录补强 LLM 任务", (profile.get("llm_tasks") or {}).get("outline_refinement") or []),
    ]:
        lines.extend([f"### {group_title}", "", "| 任务 | 状态 | 输入字符 | 输出字符 | 估算 tokens | 耗时 | 缓存 | 错误 |", "|---|---|---:|---:|---:|---:|---|---|"])
        if tasks:
            for task in tasks:
                lines.append(
                    f"| {_cell(task.get('title') or task.get('task_key'))} | {_cell(task.get('status'))} | "
                    f"{int(task.get('input_char_count') or 0)} | {int(task.get('output_char_count') or 0)} | "
                    f"{int(task.get('input_estimated_tokens') or 0)} | "
                    f"{float(task.get('duration_seconds') or 0):.2f} | "
                    f"{_cell(task.get('cache_status'))} | {_cell(task.get('error'))} |"
                )
        else:
            lines.append("| 暂无 |  | 0 | 0 | 0 | 0 |  |  |")
        lines.append("")

    lines.extend(["## 四、章节生成画像", ""])
    stats = chapter.get("duration_stats") or {}
    lines.extend(
        [
            f"- 章节数：{chapter.get('task_count') or 0}",
            f"- 已完成：{chapter.get('completed_count') or 0}",
            f"- 失败：{chapter.get('failed_count') or 0}",
            f"- LLM 实际输入字符合计：{chapter.get('input_char_total') or 0}",
            f"- 完整素材包字符合计：{chapter.get('full_package_char_total') or 0}",
            f"- 输出字符合计：{chapter.get('output_char_total') or 0}",
            f"- 平均耗时：{stats.get('avg_seconds', 0):.2f} 秒",
            f"- 中位数耗时：{stats.get('median_seconds', 0):.2f} 秒",
            "",
            "### 正文生成任务口径",
            "",
        ]
    )
    if chapter_history.get("runs"):
        lines.extend(
            [
                f"- 正文生成任务次数：{chapter_history.get('run_count') or 0}",
                f"- 成功任务：{chapter_history.get('succeeded_count') or 0}",
                f"- 中断任务：{chapter_history.get('interrupted_count') or 0}",
                f"- 失败任务：{chapter_history.get('failed_count') or 0}",
                f"- 最近一次成功任务耗时：{_duration_text((chapter_history.get('latest_successful_run') or {}).get('duration_seconds'))}",
                f"- 累计活跃耗时：{_duration_text(chapter_history.get('cumulative_active_seconds'))}",
                f"- 任务自然跨度：{_duration_text(chapter_history.get('natural_span_seconds'))}",
                f"- 章节包耗时合计：{_duration_text(chapter_history.get('chapter_package_duration_sum_seconds'))}",
                "",
                "| 任务 | 状态 | 进度 | 开始时间 | 结束时间 | 耗时 | 说明 |",
                "|---|---|---:|---|---|---:|---|",
            ]
        )
        for run in chapter_history.get("runs") or []:
            progress = f"{run.get('progress_completed') or 0}/{run.get('progress_total') or 0}"
            lines.append(
                f"| {_cell(run.get('job_id'))} | {_cell(run.get('status'))} | {progress} | "
                f"{_cell(run.get('started_at'))} | {_cell(run.get('ended_at'))} | "
                f"{_duration_text(run.get('duration_seconds'))} | {_cell(run.get('message'))} |"
            )
    else:
        lines.append("- 暂无正文生成任务历史。")
    lines.extend(
        [
            "",
            "### 慢章节 Top 10",
            "",
            "| 排名 | 章节 | 状态 | 模型 | LLM 输入字符 | 完整包字符 | 输出字符 | 表格候选 | 图片候选 | 耗时 | 错误 |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for index, item in enumerate(chapter.get("slow_chapters_top") or [], start=1):
        lines.append(
            f"| {index} | {_cell(item.get('chapter_path_text'))} | {_cell(item.get('status'))} | "
            f"{_cell(item.get('model'))} | {int(item.get('input_char_count') or 0)} | "
            f"{int(item.get('full_package_char_count') or 0)} | "
            f"{int(item.get('output_char_count') or 0)} | {int(item.get('table_reference_count') or 0)} | "
            f"{int(item.get('image_candidate_count') or 0)} | "
            f"{float(item.get('duration_seconds') or 0):.2f} | {_cell(item.get('error'))} |"
        )

    lines.extend(["", "## 五、Word 初稿刷新", "", "| 指标 | 数值 |", "|---|---:|"])
    lines.append(f"| 刷新耗时 | {_duration_text(word.get('duration_seconds'))} |")
    lines.append(f"| Word 文件大小 | {_size_text(word.get('docx_size'))} |")
    lines.append(f"| Word JSON 大小 | {_size_text(word.get('json_size'))} |")
    lines.append(f"| 章节覆盖率 | {word.get('coverage_ratio', '')} |")
    lines.append(f"| 生成章节数 | {word.get('generated_package_count', '')} |")
    lines.append(f"| 占位章节数 | {word.get('placeholder_package_count', '')} |")
    lines.append(f"| 刷新是否调用 LLM | {'是' if (word.get('refresh_timing') or {}).get('llm_called') else '否'} |")
    render_stats = word.get("render_stats") or {}
    lines.append(f"| 图片引用数 | {render_stats.get('image_ref_count', 0)} |")
    lines.append(f"| 已渲染图片数 | {render_stats.get('rendered_image_count', 0)} |")
    lines.append(f"| 缺失图片数 | {render_stats.get('missing_image_count', 0)} |")
    lines.append(f"| 图片处理耗时 | {_duration_text(render_stats.get('image_processing_duration_seconds'))} |")
    lines.extend(["", "### Word 刷新子阶段", "", "| 子阶段 | 耗时 |", "|---|---:|"])
    sub_stages = []
    sub_stages.extend((word.get("refresh_timing") or {}).get("stages") or [])
    sub_stages.extend((word.get("word_refresh_timing") or {}).get("stages") or [])
    if sub_stages:
        for stage in sub_stages:
            lines.append(f"| {_cell(stage.get('label') or stage.get('key'))} | {_duration_text(stage.get('duration_seconds'))} |")
    else:
        lines.append("| 暂无子阶段计时 | - |")

    lines.extend(["", "## 六、产物体积", "", "| 产物 | 大小 |", "|---|---:|"])
    for artifact in profile.get("artifacts") or []:
        lines.append(f"| `{artifact.get('relative_path')}` | {_size_text(artifact.get('size'))} |")

    bottlenecks = profile.get("bottlenecks") or []
    lines.extend(["", "## 七、瓶颈提示", ""])
    if bottlenecks:
        for item in bottlenecks:
            lines.append(f"- {item}")
    else:
        lines.append("- 暂无明显瓶颈提示。")

    warnings = profile.get("warnings") or []
    if warnings:
        lines.extend(["", "## 八、数据缺口", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def _parse_profile(artifacts: dict[str, Path]) -> dict[str, Any]:
    data = _read_json(artifacts["llm_extraction"])
    inputs = _read_json(artifacts["extraction_inputs"])
    packages = {str(item.get("task_key") or ""): item for item in inputs.get("packages") or [] if isinstance(item, dict)}
    tasks = []
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_key = str(task.get("task_key") or "")
        package = packages.get(task_key) or {}
        input_text = str(package.get("input_text") or "")
        output_text = str(task.get("output_text") or "")
        tasks.append(
            {
                "task_key": task_key,
                "title": task.get("task_title") or task_key,
                "status": task.get("status"),
                "duration_seconds": _float(task.get("duration_seconds")),
                "input_char_count": len(input_text),
                "output_char_count": len(output_text),
                "input_estimated_tokens": int(task.get("input_estimated_tokens") or package.get("estimated_tokens") or 0),
                "cache_status": task.get("cache_status"),
                "error": task.get("error"),
            }
        )
    return {
        "available": bool(data),
        "duration_seconds": _float(data.get("duration_seconds")),
        "model": data.get("model"),
        "execution_mode": data.get("execution_mode"),
        "max_workers": data.get("max_workers"),
        "task_count": data.get("task_count") or len(tasks),
        "completed_count": data.get("completed_task_count"),
        "failed_count": data.get("failed_task_count"),
        "tasks": tasks,
    }


def _outline_profile(artifacts: dict[str, Path]) -> dict[str, Any]:
    data = _read_json(artifacts["outline_refinement_result"])
    inputs = _read_json(artifacts["outline_refinement_inputs"])
    packages: dict[str, dict[str, Any]] = {}
    for item in inputs.get("packages") or []:
        if not isinstance(item, dict):
            continue
        node = item.get("target_outline_node") if isinstance(item.get("target_outline_node"), dict) else {}
        for key in (
            str(item.get("target_node_id") or ""),
            str(item.get("level_1_title") or ""),
            str(node.get("node_id") or ""),
            str(node.get("title") or ""),
        ):
            if key:
                packages[key] = item
    tasks = []
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        key = str(task.get("target_node_id") or task.get("level_1_title") or "")
        package = packages.get(key) or packages.get(str(task.get("level_1_title") or "")) or {}
        output_text = str(task.get("output_text") or "")
        tasks.append(
            {
                "task_key": key,
                "title": task.get("level_1_title") or key,
                "status": task.get("status"),
                "duration_seconds": _float(task.get("duration_seconds")),
                "input_char_count": _json_char_count(package),
                "output_char_count": len(output_text),
                "input_estimated_tokens": int(package.get("estimated_tokens") or 0),
                "cache_status": None,
                "error": task.get("error"),
            }
        )
    return {
        "available": bool(data),
        "duration_seconds": _float(data.get("duration_seconds")),
        "model": data.get("model"),
        "execution_mode": data.get("execution_mode"),
        "max_workers": data.get("max_workers"),
        "task_count": data.get("task_count") or len(tasks),
        "completed_count": data.get("applied_count"),
        "failed_count": data.get("failed_count"),
        "tasks": tasks,
    }


def _chapter_profile(artifacts: dict[str, Path]) -> dict[str, Any]:
    inputs = _read_json(artifacts["chapter_inputs"])
    packages = {
        str((package.get("generation_unit") or {}).get("unit_id") or ""): package
        for package in inputs.get("packages") or []
        if isinstance(package, dict)
    }
    state_dir = artifacts["chapter_state_dir"]
    rows: list[dict[str, Any]] = []
    if state_dir.exists():
        for path in sorted((state_dir / "chapters").glob("*.json")):
            artifact = _read_json(path)
            task = artifact.get("task") if isinstance(artifact.get("task"), dict) else {}
            unit_id = str(task.get("unit_id") or artifact.get("unit_id") or "")
            package = packages.get(unit_id) or {}
            chapter = artifact.get("chapter") if isinstance(artifact.get("chapter"), dict) else task.get("parsed_json")
            counts = _chapter_content_counts(chapter if isinstance(chapter, dict) else {})
            material = _package_material_counts(package)
            row = {
                "unit_id": unit_id,
                "chapter_path": task.get("chapter_path") or artifact.get("chapter_path") or [],
                "chapter_path_text": " > ".join(str(part) for part in task.get("chapter_path") or artifact.get("chapter_path") or []),
                "status": task.get("status") or artifact.get("status"),
                "duration_seconds": _float(task.get("duration_seconds")),
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "model": task.get("model") or artifact.get("model"),
                "input_char_count": int(task.get("llm_input_char_count") or 0) or _json_char_count(package),
                "full_package_char_count": int(task.get("full_package_char_count") or 0) or _json_char_count(package),
                "llm_input_char_count": int(task.get("llm_input_char_count") or 0),
                "llm_input_profile": task.get("llm_input_profile"),
                "llm_input_compression_ratio": _float(task.get("llm_input_compression_ratio")),
                "output_char_count": len(str(task.get("output_text") or "")),
                "validation_issue_count": (task.get("validation") or {}).get("issue_count"),
                "error": task.get("error"),
                "package_hash_match": bool(package) and artifact.get("package_hash") == _stable_hash(package),
                **material,
                **counts,
            }
            rows.append(row)
    durations = [float(row.get("duration_seconds") or 0) for row in rows if float(row.get("duration_seconds") or 0) > 0]
    return {
        "available": bool(rows),
        "task_count": len(rows),
        "completed_count": sum(1 for row in rows if row.get("status") == "completed"),
        "failed_count": sum(1 for row in rows if row.get("status") == "failed"),
        "input_char_total": sum(int(row.get("input_char_count") or 0) for row in rows),
        "full_package_char_total": sum(int(row.get("full_package_char_count") or 0) for row in rows),
        "llm_input_char_total": sum(int(row.get("llm_input_char_count") or 0) for row in rows),
        "output_char_total": sum(int(row.get("output_char_count") or 0) for row in rows),
        "duration_stats": _duration_stats(durations),
        "duration_buckets": _duration_buckets(durations),
        "slow_chapters_top": sorted(rows, key=lambda item: float(item.get("duration_seconds") or 0), reverse=True)[:10],
        "chapters": rows,
    }


def _word_profile(artifacts: dict[str, Path]) -> dict[str, Any]:
    summary = _read_json(artifacts["llm_generation_summary"])
    word = summary.get("word_review") if isinstance(summary.get("word_review"), dict) else {}
    full_summary = word.get("summary") if isinstance(word.get("summary"), dict) else {}
    docx = artifacts["word_draft_docx"]
    output_json = artifacts["word_draft_json"]
    refresh_timing = summary.get("refresh_timing") or full_summary.get("workflow_refresh_timing") or {}
    word_refresh_timing = full_summary.get("word_refresh_timing") or {}
    duration = _float((refresh_timing or {}).get("duration_seconds"))
    if duration is None:
        duration = _float((word_refresh_timing or {}).get("duration_seconds"))
    return {
        "available": docx.exists() or bool(word),
        "duration_seconds": duration,
        "refresh_only": bool(summary.get("refresh_only")),
        "status": word.get("status"),
        "docx_size": docx.stat().st_size if docx.exists() else None,
        "json_size": output_json.stat().st_size if output_json.exists() else None,
        "coverage_ratio": full_summary.get("coverage_ratio"),
        "generated_package_count": full_summary.get("generated_package_count"),
        "placeholder_package_count": full_summary.get("placeholder_package_count"),
        "render_stats": full_summary.get("render_stats") or {},
        "refresh_timing": refresh_timing,
        "word_refresh_timing": word_refresh_timing,
    }


def _chapter_job_history(jobs: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> dict[str, Any]:
    """汇总正文生成任务历史，避免只看最近一次成功任务造成耗时误读。"""

    runs = []
    for job in jobs:
        if job.get("job_type") != "chapter_llm_generation":
            continue
        started = _parse_dt(job.get("started_at"))
        ended = _parse_dt(job.get("ended_at"))
        duration = _job_duration_seconds(job)
        status = _historical_job_status(job)
        runs.append(
            {
                "job_id": job.get("job_id"),
                "status": status,
                "raw_status": job.get("status"),
                "progress_completed": job.get("progress_completed"),
                "progress_total": job.get("progress_total"),
                "progress_failed": job.get("progress_failed"),
                "started_at": started.isoformat() if started else job.get("started_at"),
                "ended_at": ended.isoformat() if ended else job.get("ended_at"),
                "duration_seconds": None if duration is None else round(duration, 2),
                "message": job.get("message"),
            }
        )
    runs.sort(key=lambda item: str(item.get("started_at") or ""))
    first_started = _first_dt(run.get("started_at") for run in runs)
    last_ended = _last_dt(run.get("ended_at") for run in runs)
    latest_successful = next((run for run in reversed(runs) if run.get("status") == "succeeded"), None)
    active_seconds = sum(float(run.get("duration_seconds") or 0) for run in runs)
    chapter_duration_sum = (chapter_profile.get("duration_stats") or {}).get("sum_seconds")
    return {
        "available": bool(runs),
        "run_count": len(runs),
        "succeeded_count": sum(1 for run in runs if run.get("status") == "succeeded"),
        "interrupted_count": sum(1 for run in runs if run.get("status") == "interrupted"),
        "failed_count": sum(1 for run in runs if run.get("status") == "failed"),
        "cancelled_count": sum(1 for run in runs if run.get("status") == "cancelled"),
        "cumulative_active_seconds": round(active_seconds, 2),
        "natural_span_seconds": round((last_ended - first_started).total_seconds(), 2) if first_started and last_ended else None,
        "latest_successful_run": latest_successful,
        "chapter_package_duration_sum_seconds": chapter_duration_sum,
        "chapter_package_duration_note": "章节包耗时合计为各小节包独立耗时相加；并发执行时不等同于用户自然等待时间。",
        "runs": runs,
    }


def _historical_job_status(job: dict[str, Any]) -> str:
    """将重启后残留的 running/pending 历史任务归为 interrupted。"""

    status = str(job.get("status") or "")
    if status in {"pending", "running"} and job.get("ended_at"):
        return "interrupted"
    return status


def _stage_metrics(
    *,
    jobs: list[dict[str, Any]],
    parse_profile: dict[str, Any],
    outline_profile: dict[str, Any],
    chapter_profile: dict[str, Any],
    word_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    latest = _latest_jobs_by_type(jobs)
    return [
        _stage("upload", "资料上传", None, "uploaded_files", "上传本身近实时，耗时按文件记录。", "available"),
        _job_stage("tender_parse", "招标文件解析", latest, parse_profile, "tender_llm_extraction.json"),
        _job_stage("outline_generation", "技术标目录生成", latest, outline_profile, "outline_refinement_result.json"),
        _job_stage("chapter_generation", "章节输入包准备", latest, {}, "chapter_generation_inputs.json"),
        _job_stage("chapter_llm_generation", "正文分章节生成", latest, chapter_profile, "chapter_llm_generation_result.json"),
        _job_stage("chapter_aggregate_refresh", "Word 初稿刷新", latest, word_profile, "technical_bid_draft.docx"),
    ]


def _job_stage(
    job_type: str,
    label: str,
    latest_jobs: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    job = latest_jobs.get(job_type) or {}
    duration = _job_duration_seconds(job)
    if duration is None:
        duration = profile.get("duration_seconds")
    status = job.get("status") or ("available" if profile.get("available") else "missing")
    note = str(job.get("message") or "")
    if profile.get("model"):
        note = f"{note} 模型：{profile.get('model')}".strip()
    return _stage(job_type, label, duration, source, note, status)


def _stage(key: str, label: str, duration: Any, source: str, note: str, status: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "duration_seconds": None if duration is None else round(_float(duration), 2),
        "source": source,
        "note": note,
    }


def _upload_profile(files: list[dict[str, Any]]) -> dict[str, Any]:
    tender_files = [item for item in files if item.get("business_type") == "tender_document"]
    created_values = [_parse_dt(item.get("created_at")) for item in files]
    created_values = [item for item in created_values if item is not None]
    return {
        "file_count": len(files),
        "tender_file_count": len(tender_files),
        "total_size": sum(int(item.get("file_size") or 0) for item in files),
        "first_uploaded_at": min(created_values).isoformat() if created_values else None,
        "files": [
            {
                "file_name": item.get("file_name"),
                "business_type": item.get("business_type"),
                "file_size": item.get("file_size"),
                "created_at": item.get("created_at"),
            }
            for item in files
        ],
    }


def _observed_window(upload: dict[str, Any], artifacts: dict[str, Path]) -> dict[str, Any]:
    first_upload = _parse_dt(upload.get("first_uploaded_at"))
    latest_word = _mtime_dt(artifacts["word_draft_docx"])
    return {
        "first_uploaded_at": first_upload.isoformat() if first_upload else None,
        "latest_word_draft_at": latest_word.isoformat() if latest_word else None,
        "upload_to_latest_word_seconds": (latest_word - first_upload).total_seconds() if first_upload and latest_word else None,
    }


def _artifact_paths(project_dir: Path) -> dict[str, Path]:
    return {
        "llm_extraction": project_dir / "parse" / "tender_llm_extraction.json",
        "extraction_inputs": project_dir / "parse" / "tender_extraction_inputs.json",
        "outline_refinement_result": project_dir / "outline" / "outline_refinement_result.json",
        "outline_refinement_inputs": project_dir / "outline" / "outline_refinement_inputs.json",
        "chapter_inputs": project_dir / "generation" / "chapter_generation_inputs.json",
        "chapter_state_dir": project_dir / "generation" / "chapter_llm_state",
        "chapter_llm_generation_result": project_dir / "generation" / "chapter_llm_generation_result.json",
        "llm_generation_summary": project_dir / "generation" / "chapter_llm_generation_summary.json",
        "word_draft_docx": project_dir / "documents" / "technical_bid_draft.docx",
        "word_draft_json": project_dir / "documents" / "technical_bid_draft.json",
    }


def _artifact_stats(artifacts: dict[str, Path]) -> list[dict[str, Any]]:
    rows = []
    for key, path in artifacts.items():
        if key == "chapter_state_dir" or not path.exists() or not path.is_file():
            continue
        rows.append(
            {
                "key": key,
                "relative_path": str(path).replace("\\", "/"),
                "size": path.stat().st_size,
                "modified_at": _mtime_dt(path).isoformat() if _mtime_dt(path) else None,
            }
        )
    return rows


def _latest_jobs_by_type(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_type = str(job.get("job_type") or "")
        if not job_type:
            continue
        current = result.get(job_type)
        if current is None or str(job.get("created_at") or "") >= str(current.get("created_at") or ""):
            result[job_type] = job
    return result


def _job_duration_seconds(job: dict[str, Any]) -> float | None:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    if metadata.get("duration_seconds") is not None:
        return _float(metadata.get("duration_seconds"))
    started = _parse_dt(job.get("started_at"))
    ended = _parse_dt(job.get("ended_at"))
    if started and ended:
        return (ended - started).total_seconds()
    return None


def _package_material_counts(package: dict[str, Any]) -> dict[str, int]:
    return {
        "technical_requirement_count": len(package.get("technical_requirements") or []),
        "excellent_bid_reference_count": len(package.get("excellent_bid_references") or []),
        "table_reference_count": len(package.get("table_references") or []),
        "image_candidate_count": len(package.get("image_candidates") or package.get("image_candidate_pool") or []),
        "image_group_candidate_count": len(package.get("image_group_candidates") or package.get("image_group_candidate_pool") or []),
    }


def _chapter_content_counts(chapter: dict[str, Any]) -> dict[str, int]:
    counts = {
        "output_section_count": len(chapter.get("sections") or []),
        "output_paragraph_count": 0,
        "output_table_count": 0,
        "output_image_ref_count": 0,
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            block_type = value.get("type")
            if block_type == "paragraph":
                counts["output_paragraph_count"] += 1
            if block_type in {"table", "rich_table"}:
                counts["output_table_count"] += 1
            if block_type == "image":
                counts["output_image_ref_count"] += 1
            for key in ("images", "image_refs", "image_assets"):
                if isinstance(value.get(key), list):
                    counts["output_image_ref_count"] += len(value[key])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(chapter.get("sections") or [])
    return counts


def _duration_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"sum_seconds": 0, "avg_seconds": 0, "median_seconds": 0, "min_seconds": 0, "max_seconds": 0}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "sum_seconds": round(sum(ordered), 2),
        "avg_seconds": round(sum(ordered) / len(ordered), 2),
        "median_seconds": round(median, 2),
        "min_seconds": round(min(ordered), 2),
        "max_seconds": round(max(ordered), 2),
    }


def _duration_buckets(values: list[float]) -> dict[str, int]:
    return {
        "<=90s": sum(1 for value in values if value <= 90),
        "90-180s": sum(1 for value in values if 90 < value <= 180),
        "180-300s": sum(1 for value in values if 180 < value <= 300),
        ">300s": sum(1 for value in values if value > 300),
    }


def _bottlenecks(stage_metrics: list[dict[str, Any]], chapter_profile: dict[str, Any]) -> list[str]:
    tips = []
    stages = [item for item in stage_metrics if item.get("duration_seconds") is not None]
    if stages:
        slowest = max(stages, key=lambda item: float(item.get("duration_seconds") or 0))
        tips.append(f"当前最慢阶段是“{slowest.get('label')}”，耗时 {_duration_text(slowest.get('duration_seconds'))}。")
    slow_chapters = chapter_profile.get("slow_chapters_top") or []
    if slow_chapters:
        first = slow_chapters[0]
        tips.append(f"当前最慢章节是“{first.get('chapter_path_text')}”，耗时 {float(first.get('duration_seconds') or 0):.2f} 秒。")
    buckets = chapter_profile.get("duration_buckets") or {}
    if buckets.get(">300s"):
        tips.append(f"超过 300 秒的慢章节数量：{buckets.get('>300s')}。")
    return tips


def _profile_warnings(*profiles: dict[str, Any]) -> list[str]:
    warnings = []
    labels = ["招标解析", "目录补强", "章节生成", "Word 刷新"]
    for label, profile in zip(labels, profiles):
        if not profile.get("available"):
            warnings.append(f"{label}产物尚不存在，相关耗时为空。")
    return warnings


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _stable_hash(value: Any) -> str:
    import hashlib

    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_char_count(value: Any) -> int:
    if not value:
        return 0
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


def _first_dt(values: Any) -> datetime | None:
    parsed = [_parse_dt(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return min(parsed) if parsed else None


def _last_dt(values: Any) -> datetime | None:
    parsed = [_parse_dt(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _mtime_dt(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo(DEFAULT_TIMEZONE))


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(timespec="seconds")


def _duration_text(value: Any) -> str:
    if value is None:
        return "暂无"
    seconds = _float(value)
    if seconds >= 3600:
        return f"{seconds / 3600:.2f} 小时"
    if seconds >= 60:
        return f"{seconds / 60:.2f} 分钟"
    return f"{seconds:.2f} 秒"


def _size_text(value: Any) -> str:
    if value is None:
        return "暂无"
    size = _float(value)
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size:.0f} B"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
