"""开发期模型配置文件读写。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from construction_bidding_agent.llm_config import DEFAULT_TASK_PROFILES_PATH, _read_task_profiles, llm_config

from .schemas import ModelProviderConfigRequest, ModelRuntimeConfigResponse


ENV_PATH = Path(".env")


def read_model_runtime_config() -> ModelRuntimeConfigResponse:
    config = llm_config()
    profiles = _read_task_profiles()
    return ModelRuntimeConfigResponse(
        provider=config.provider,
        api_type=config.api_type,
        base_url=config.base_url,
        model=config.model,
        api_key_masked=_mask(config.api_key),
        task_profiles_path=os.getenv("LLM_TASK_PROFILES_PATH", str(DEFAULT_TASK_PROFILES_PATH)),
        effective_default={
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
            "timeout_seconds": config.timeout_seconds,
            "max_retries": config.max_retries,
            "max_workers": config.max_workers,
            "structured_output_type": config.structured_output_type,
            "enable_thinking": config.enable_thinking,
        },
        default_profile=profiles.get("default", {}) if isinstance(profiles.get("default"), dict) else {},
        tasks=profiles.get("tasks", {}) if isinstance(profiles.get("tasks"), dict) else {},
    )


def write_model_env_config(request: ModelProviderConfigRequest, env_path: Path = ENV_PATH) -> None:
    updates: dict[str, str] = {
        "LLM_PROVIDER": request.provider,
        "API_TYPE": request.api_type,
        "BASE_URL": request.base_url,
        "MODEL": request.model,
    }
    optional_map: dict[str, Any] = {
        "API_KEY": request.api_key,
        "TEMPERATURE": request.temperature,
        "TOP_P": request.top_p,
        "MAX_TOKENS": request.max_tokens,
        "TIMEOUT_SECONDS": request.timeout_seconds,
        "MAX_RETRIES": request.max_retries,
        "MAX_WORKERS": request.max_workers,
        "ENABLE_THINKING": request.enable_thinking,
        "STRUCTURED_OUTPUT_TYPE": request.structured_output_type,
    }
    for key, value in optional_map.items():
        if value is not None:
            updates[key] = _stringify_env_value(value)
    _merge_env_file(env_path, updates)
    if request.default_profile is not None or request.tasks is not None:
        write_task_profiles(
            default_profile=request.default_profile,
            tasks=request.tasks,
            project_root=env_path.parent,
        )


def write_task_profiles(
    *,
    default_profile: dict[str, Any] | None = None,
    tasks: dict[str, dict[str, Any]] | None = None,
    project_root: Path | None = None,
) -> None:
    root = project_root or Path.cwd()
    profile_path = _task_profiles_path(root)
    profiles = _read_task_profiles()
    if not profiles:
        profiles = {"schema_version": "llm_task_profiles_v1", "default": {}, "tasks": {}}
    profiles.setdefault("schema_version", "llm_task_profiles_v1")
    if default_profile is not None:
        profiles["default"] = _clean_profile(default_profile)
    if tasks is not None:
        profiles["tasks"] = {
            str(key): _clean_profile(value)
            for key, value in tasks.items()
            if isinstance(value, dict)
        }
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _task_profiles_path(root: Path) -> Path:
    path_text = os.getenv("LLM_TASK_PROFILES_PATH")
    path = Path(path_text) if path_text else DEFAULT_TASK_PROFILES_PATH
    return path if path.is_absolute() else root / path


def _clean_profile(profile: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {
        "temperature",
        "top_p",
        "max_tokens",
        "timeout_seconds",
        "max_retries",
        "max_workers",
        "api_type",
        "structured_output_type",
        "enable_thinking",
        "reasoning_effort",
        "store_response",
    }
    cleaned: dict[str, Any] = {}
    for key in allowed_fields:
        if key not in profile:
            continue
        value = profile[key]
        if isinstance(value, str) and value.strip() == "":
            value = None
        cleaned[key] = value
    return cleaned


def _merge_env_file(env_path: Path, updates: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            merged.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            merged.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            merged.append(line)
    for key, value in updates.items():
        if key not in seen:
            merged.append(f"{key}={value}")
    env_path.write_text("\n".join(merged) + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def _stringify_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
