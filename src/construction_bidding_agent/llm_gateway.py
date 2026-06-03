"""轻量 LLM Gateway 审计能力。

第一版只记录脱敏元数据和耗时，不保存完整 prompt、完整输出或 API Key。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from construction_bidding_agent.llm_config import LlmClientConfig


LLM_AUDIT_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("llm_audit_context", default=None)


@contextmanager
def llm_audit_context(
    *,
    project_id: str | None = None,
    job_id: str | None = None,
    tool_name: str | None = None,
):
    """给当前执行上下文中的 LLM 调用补充项目和任务信息。"""

    current = dict(LLM_AUDIT_CONTEXT.get() or {})
    if project_id:
        current["project_id"] = project_id
    if job_id:
        current["job_id"] = job_id
    if tool_name:
        current["tool_name"] = tool_name
    token = LLM_AUDIT_CONTEXT.set(current)
    try:
        yield
    finally:
        LLM_AUDIT_CONTEXT.reset(token)


def start_llm_audit_call(
    *,
    config: LlmClientConfig,
    task_key: str | None = None,
    tool_name: str | None = None,
    system_prompt: str = "",
    user_input: str = "",
    project_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """创建一次 LLM 调用审计上下文。"""

    ambient = LLM_AUDIT_CONTEXT.get() or {}
    started_at = _now_iso()
    prompt_hash = _hash_text(f"{system_prompt}\n\n{user_input}")
    return {
        "_start_monotonic": time.monotonic(),
        "trace_id": uuid4().hex,
        "project_id": project_id or ambient.get("project_id") or _env("LLM_AUDIT_PROJECT_ID"),
        "job_id": job_id or ambient.get("job_id") or _env("LLM_AUDIT_JOB_ID"),
        "task_key": task_key,
        "tool_name": tool_name or ambient.get("tool_name") or task_key or "llm_client.call_openai_json",
        "provider": config.provider,
        "model": config.model,
        "api_type": config.api_type,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "structured_output_type": config.structured_output_type,
        "prompt_hash": prompt_hash,
        "system_prompt_hash": _hash_text(system_prompt),
        "user_input_hash": _hash_text(user_input),
        "prompt_summary": {
            "system_prompt_chars": len(system_prompt or ""),
            "user_input_chars": len(user_input or ""),
            "total_chars": len(system_prompt or "") + len(user_input or ""),
        },
        "estimated_input_tokens": estimate_tokens(system_prompt) + estimate_tokens(user_input),
        "estimated_output_tokens": None,
        "started_at": started_at,
        "ended_at": None,
        "duration_ms": None,
        "status": "running",
        "error_type": None,
        "error_summary": None,
        "result_summary": None,
    }


def finish_llm_audit_call(
    context: dict[str, Any] | None,
    *,
    output_text: str | None = None,
    error: BaseException | None = None,
) -> None:
    """完成并写入一次 LLM 调用审计。审计失败不能影响业务调用。"""

    if not context:
        return
    try:
        ended_at = _now_iso()
        duration_ms = int((time.monotonic() - float(context.get("_start_monotonic") or time.monotonic())) * 1000)
        event = {key: value for key, value in context.items() if not key.startswith("_")}
        event["ended_at"] = ended_at
        event["duration_ms"] = max(0, duration_ms)
        if error is None:
            event["status"] = "succeeded"
            event["estimated_output_tokens"] = estimate_tokens(output_text or "")
            event["result_summary"] = {
                "output_chars": len(output_text or ""),
                "looks_like_json": _looks_like_json(output_text or ""),
            }
        else:
            event["status"] = _error_status(error)
            event["error_type"] = type(error).__name__
            event["error_summary"] = _redact_sensitive(str(error))[:500]
        write_llm_audit_event(event)
    except Exception:
        return


def write_llm_audit_event(event: dict[str, Any]) -> None:
    if not _audit_enabled():
        return
    path = _audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def summarize_llm_audit_for_job(
    job_id: str,
    *,
    project_id: str | None = None,
    audit_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """按 job 汇总脱敏 LLM 审计日志，供任务结果和小助手展示。"""

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return _empty_job_summary(job_id=normalized_job_id, project_id=project_id)
    path = Path(audit_log_path) if audit_log_path else _audit_log_path()
    if not path.exists():
        return _empty_job_summary(job_id=normalized_job_id, project_id=project_id)

    summary = _empty_job_summary(job_id=normalized_job_id, project_id=project_id)
    providers: set[str] = set()
    models: set[str] = set()
    task_keys: set[str] = set()
    statuses: dict[str, int] = {}
    first_started_at: str | None = None
    last_ended_at: str | None = None

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = _parse_jsonl_record(line)
                if not record:
                    continue
                if str(record.get("job_id") or "") != normalized_job_id:
                    continue
                record_project_id = str(record.get("project_id") or "")
                if project_id and record_project_id and record_project_id != str(project_id):
                    continue

                summary["call_count"] += 1
                status = str(record.get("status") or "unknown")
                statuses[status] = statuses.get(status, 0) + 1
                if status == "succeeded":
                    summary["succeeded_count"] += 1
                else:
                    summary["failed_count"] += 1
                if status == "timeout":
                    summary["timeout_count"] += 1

                summary["estimated_input_tokens"] += _safe_int(record.get("estimated_input_tokens"))
                summary["estimated_output_tokens"] += _safe_int(record.get("estimated_output_tokens"))
                summary["duration_ms"] += _safe_int(record.get("duration_ms"))
                _add_nonempty(providers, record.get("provider"))
                _add_nonempty(models, record.get("model"))
                _add_nonempty(task_keys, record.get("task_key"))
                first_started_at = _min_iso(first_started_at, record.get("started_at"))
                last_ended_at = _max_iso(last_ended_at, record.get("ended_at"))
    except Exception:
        return _empty_job_summary(job_id=normalized_job_id, project_id=project_id)

    summary["estimated_total_tokens"] = int(summary["estimated_input_tokens"]) + int(summary["estimated_output_tokens"])
    summary["duration_seconds"] = round(int(summary["duration_ms"]) / 1000, 3)
    summary["providers"] = sorted(providers)
    summary["models"] = sorted(models)
    summary["task_keys"] = sorted(task_keys)
    summary["statuses"] = statuses
    summary["has_failures"] = bool(summary["failed_count"])
    summary["first_started_at"] = first_started_at
    summary["last_ended_at"] = last_ended_at
    return summary


def estimate_tokens(text: str | None) -> int:
    """粗略 token 估算，只用于趋势和排障，不作为计费口径。"""

    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _audit_enabled() -> bool:
    raw = _env("LLM_AUDIT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _audit_log_path() -> Path:
    explicit = _env("LLM_AUDIT_LOG_PATH")
    if explicit:
        return Path(explicit)
    storage_root = Path(_env("APP_STORAGE_ROOT") or "data")
    return storage_root / "app" / "llm_calls.jsonl"


def _hash_text(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("{") or stripped.startswith("```json")


def _error_status(error: BaseException) -> str:
    text = f"{type(error).__name__} {error}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _redact_sensitive(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", text)
    redacted = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[A-Za-z0-9_\-\.]+",
        r"\1=***",
        redacted,
    )
    return redacted


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _empty_job_summary(*, job_id: str, project_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "llm_audit_job_summary_v0.1",
        "source": "llm_audit_jsonl",
        "job_id": job_id,
        "project_id": project_id,
        "call_count": 0,
        "succeeded_count": 0,
        "failed_count": 0,
        "timeout_count": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
        "estimated_total_tokens": 0,
        "duration_ms": 0,
        "duration_seconds": 0.0,
        "providers": [],
        "models": [],
        "task_keys": [],
        "statuses": {},
        "has_failures": False,
        "first_started_at": None,
        "last_ended_at": None,
    }


def _parse_jsonl_record(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _add_nonempty(target: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        target.add(text)


def _min_iso(current: str | None, candidate: Any) -> str | None:
    text = str(candidate or "").strip()
    if not text:
        return current
    if current is None or text < current:
        return text
    return current


def _max_iso(current: str | None, candidate: Any) -> str | None:
    text = str(candidate or "").strip()
    if not text:
        return current
    if current is None or text > current:
        return text
    return current
