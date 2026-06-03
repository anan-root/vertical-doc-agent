"""LLM 连接配置与任务级参数 profile。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "qwen3.6-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_API_TYPE = "responses"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_WORKERS = 1
DEFAULT_STRUCTURED_OUTPUT_TYPE = "json_object"
DEFAULT_ENABLE_THINKING = False
DEFAULT_STORE_RESPONSE = False
DEFAULT_REASONING_EFFORT = "none"
DEFAULT_TASK_PROFILES_PATH = Path("configs") / "llm-task-profiles.json"


@dataclass(frozen=True, slots=True)
class LlmClientConfig:
    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    temperature: float
    top_p: float | None
    max_tokens: int | None
    timeout_seconds: float
    max_retries: int
    api_type: str = DEFAULT_API_TYPE
    structured_output_type: str | None = DEFAULT_STRUCTURED_OUTPUT_TYPE
    enable_thinking: bool = DEFAULT_ENABLE_THINKING
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT
    store_response: bool = DEFAULT_STORE_RESPONSE
    max_workers: int = DEFAULT_MAX_WORKERS


def llm_config(
    *,
    task_key: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    base_url_override: str | None = None,
) -> LlmClientConfig:
    """合成最终 LLM 配置。

    优先级：代码默认值 < profile.default < .env 全局参数 < profile.tasks[task_key]。
    连接类字段仍由 .env 或显式覆盖控制，任务 profile 只负责生成与运行参数。
    """

    load_dotenv(Path.cwd() / ".env")
    default_profile, task_profile = _task_profiles(task_key)
    generation = _default_generation_values()
    _apply_profile(generation, default_profile)
    _apply_env_generation_values(generation)
    _apply_profile(generation, task_profile)

    provider = provider_override or _env("LLM_PROVIDER", "PROVIDER") or _infer_provider()
    base_url = base_url_override or _env(
        "BASE_URL",
        "LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "DASHSCOPE_BASE_URL",
        "base_url",
    )
    model = model_override or _env(
        "MODEL",
        "LLM_MODEL",
        "OPENAI_MODEL",
        "DEEPSEEK_MODEL",
        "DASHSCOPE_MODEL",
        "model",
    )

    return LlmClientConfig(
        provider=provider,
        api_key=_api_key(),
        base_url=base_url or DEFAULT_BASE_URL,
        model=model or DEFAULT_MODEL,
        temperature=_as_float(generation["temperature"], "temperature"),
        top_p=_as_optional_float(generation["top_p"], "top_p"),
        max_tokens=_as_optional_int(generation["max_tokens"], "max_tokens"),
        timeout_seconds=_as_float(generation["timeout_seconds"], "timeout_seconds"),
        max_retries=_as_int(generation["max_retries"], "max_retries"),
        api_type=str(generation["api_type"] or DEFAULT_API_TYPE),
        structured_output_type=_as_optional_string(generation["structured_output_type"]),
        enable_thinking=_as_bool(generation["enable_thinking"], "enable_thinking"),
        reasoning_effort=_as_optional_string(generation["reasoning_effort"]) or DEFAULT_REASONING_EFFORT,
        store_response=_as_bool(generation["store_response"], "store_response"),
        max_workers=max(1, _as_int(generation["max_workers"], "max_workers")),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _default_generation_values() -> dict[str, Any]:
    return {
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": DEFAULT_TOP_P,
        "max_tokens": None,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_retries": DEFAULT_MAX_RETRIES,
        "max_workers": DEFAULT_MAX_WORKERS,
        "api_type": DEFAULT_API_TYPE,
        "structured_output_type": DEFAULT_STRUCTURED_OUTPUT_TYPE,
        "enable_thinking": DEFAULT_ENABLE_THINKING,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "store_response": DEFAULT_STORE_RESPONSE,
    }


def _apply_env_generation_values(generation: dict[str, Any]) -> None:
    generation["temperature"] = _env_float("TEMPERATURE", "LLM_TEMPERATURE", default=generation["temperature"])
    generation["top_p"] = _env_optional_float("TOP_P", "LLM_TOP_P", default=generation["top_p"])
    generation["max_tokens"] = _env_optional_int("MAX_TOKENS", "LLM_MAX_TOKENS", default=generation["max_tokens"])
    generation["timeout_seconds"] = _env_float(
        "TIMEOUT_SECONDS",
        "LLM_TIMEOUT_SECONDS",
        default=generation["timeout_seconds"],
    )
    generation["max_retries"] = _env_int("MAX_RETRIES", "LLM_MAX_RETRIES", default=generation["max_retries"])
    generation["max_workers"] = _env_int("MAX_WORKERS", "LLM_MAX_WORKERS", default=generation["max_workers"])
    generation["api_type"] = _env("API_TYPE", "LLM_API_TYPE") or generation["api_type"]
    generation["structured_output_type"] = (
        _env("STRUCTURED_OUTPUT_TYPE", "LLM_STRUCTURED_OUTPUT_TYPE") or generation["structured_output_type"]
    )
    generation["enable_thinking"] = _env_bool(
        "ENABLE_THINKING",
        "LLM_ENABLE_THINKING",
        default=generation["enable_thinking"],
    )
    generation["reasoning_effort"] = _env("REASONING_EFFORT", "LLM_REASONING_EFFORT") or generation["reasoning_effort"]
    generation["store_response"] = _env_bool(
        "STORE_RESPONSE",
        "LLM_STORE_RESPONSE",
        default=generation["store_response"],
    )


def _task_profiles(task_key: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _read_task_profiles()
    default_profile = data.get("default") if isinstance(data.get("default"), dict) else {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
    task_profile = tasks.get(task_key) if task_key and isinstance(tasks.get(task_key), dict) else {}
    return default_profile, task_profile


def _read_task_profiles() -> dict[str, Any]:
    path_text = _env("LLM_TASK_PROFILES_PATH", "TASK_LLM_PROFILES_PATH")
    path = Path(path_text) if path_text else DEFAULT_TASK_PROFILES_PATH
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"LLM task profiles must be a JSON object: {path}")
    return data


def _apply_profile(generation: dict[str, Any], profile: dict[str, Any]) -> None:
    field_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "max_tokens": "max_tokens",
        "timeout_seconds": "timeout_seconds",
        "max_retries": "max_retries",
        "max_workers": "max_workers",
        "api_type": "api_type",
        "structured_output_type": "structured_output_type",
        "enable_thinking": "enable_thinking",
        "reasoning_effort": "reasoning_effort",
        "store_response": "store_response",
    }
    for source_key, target_key in field_map.items():
        if source_key in profile:
            generation[target_key] = profile[source_key]


def _infer_provider() -> str:
    base_url = _env("BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL", "DASHSCOPE_BASE_URL") or ""
    lowered = base_url.lower()
    if "dashscope" in lowered or _env("DASHSCOPE_API_KEY"):
        return "dashscope"
    if "deepseek" in lowered or _env("DEEPSEEK_API_KEY"):
        return "deepseek"
    return "openai_compatible"


def _api_key() -> str | None:
    return _env("API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY")


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def _env_float(*names: str, default: Any) -> float:
    value = _env(*names)
    return _as_float(default if value is None else value, names[0])


def _env_optional_float(*names: str, default: Any = None) -> float | None:
    value = _env(*names)
    return _as_optional_float(default if value is None else value, names[0])


def _env_int(*names: str, default: Any) -> int:
    value = _env(*names)
    return _as_int(default if value is None else value, names[0])


def _env_bool(*names: str, default: Any) -> bool:
    value = _env(*names)
    return _as_bool(default if value is None else value, names[0])


def _env_optional_int(*names: str, default: Any = None) -> int | None:
    value = _env(*names)
    return _as_optional_int(default if value is None else value, names[0])


def _as_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a float, got {value!r}.") from exc


def _as_optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"none", "null", ""}:
        return None
    return _as_float(value, field_name)


def _as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer, got {value!r}.") from exc


def _as_optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"none", "null", ""}:
        return None
    return _as_int(value, field_name)


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean, got {value!r}.")


def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "none", "null"}:
        return None
    return text
