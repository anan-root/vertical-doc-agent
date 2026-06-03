"""OpenAI 兼容 LLM 调用工具。"""

from __future__ import annotations

import json
import re
from typing import Any

from construction_bidding_agent.llm_config import LlmClientConfig, llm_config
from construction_bidding_agent.llm_gateway import finish_llm_audit_call, start_llm_audit_call


def call_openai_json(
    *,
    config: LlmClientConfig | None = None,
    task_key: str | None = None,
    model: str | None = None,
    system_prompt: str,
    user_input: str,
) -> str:
    """按配置调用 Responses 或 Chat Completions API，并返回文本内容。"""

    from openai import OpenAI

    final_config = config or llm_config(task_key=task_key, model_override=model)
    audit_context = start_llm_audit_call(
        config=final_config,
        task_key=task_key,
        system_prompt=system_prompt,
        user_input=user_input,
    )
    client = OpenAI(
        api_key=final_config.api_key,
        base_url=final_config.base_url,
        timeout=final_config.timeout_seconds,
        max_retries=final_config.max_retries,
    )
    try:
        api_type = normalize_api_type(final_config.api_type)
        if api_type == "responses":
            content = _call_responses_api(client, final_config, system_prompt, user_input)
        elif api_type == "chat_completions":
            content = _call_chat_completions_api(client, final_config, system_prompt, user_input)
        else:
            raise ValueError(
                "Unsupported API_TYPE "
                f"{final_config.api_type!r}. Supported values: responses, chat_completions, chat."
            )
        if not content:
            raise ValueError("LLM response content is empty.")
        finish_llm_audit_call(audit_context, output_text=content)
        return content
    except Exception as exc:
        finish_llm_audit_call(audit_context, error=exc)
        raise


def normalize_api_type(api_type: str | None) -> str:
    """归一化 .env 中的 API_TYPE，允许常见简写。"""

    normalized = (api_type or "responses").strip().lower().replace("-", "_")
    if normalized in {"responses", "response"}:
        return "responses"
    if normalized in {"chat", "chat_completion", "chat_completions"}:
        return "chat_completions"
    return normalized


def _call_responses_api(
    client: Any,
    config: LlmClientConfig,
    system_prompt: str,
    user_input: str,
) -> str:
    request: dict[str, Any] = {
        "model": config.model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "temperature": config.temperature,
        "store": config.store_response,
    }
    if config.structured_output_type:
        request["text"] = {"format": {"type": config.structured_output_type}}
    if config.top_p is not None:
        request["top_p"] = config.top_p
    if config.max_tokens is not None:
        request["max_output_tokens"] = config.max_tokens
    request["reasoning"] = {"effort": effective_reasoning_effort(config)}
    response = client.responses.create(**request)
    return response_output_text(response)


def _call_chat_completions_api(
    client: Any,
    config: LlmClientConfig,
    system_prompt: str,
    user_input: str,
) -> str:
    request: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "temperature": config.temperature,
    }
    if config.top_p is not None:
        request["top_p"] = config.top_p
    if config.max_tokens is not None:
        request["max_tokens"] = config.max_tokens
    if config.structured_output_type:
        request["response_format"] = {"type": config.structured_output_type}
    response = client.chat.completions.create(**request)
    return chat_completion_output_text(response)


def parse_json_response(response_text: str) -> dict[str, Any]:
    """解析模型返回的 JSON 对象，兼容常见的轻微格式噪声。"""

    parsed, _metadata = parse_json_response_with_repair_info(response_text)
    return parsed


def parse_json_response_with_repair_info(response_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """解析 JSON，并返回是否经过低风险规则修复的元数据。"""

    stripped = response_text.strip()
    candidates = _json_parse_candidates(stripped)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            repaired = _repair_common_json_noise(candidate)
            if repaired != candidate:
                try:
                    parsed = json.loads(repaired)
                except json.JSONDecodeError as repaired_exc:
                    last_error = repaired_exc
                    continue
                if not isinstance(parsed, dict):
                    raise ValueError("LLM response is not a JSON object.")
                return parsed, {
                    "method": "rule",
                    "repair_count": 1,
                    "original_error": str(exc),
                    "repaired_text_changed": True,
                }
            else:
                continue
        if not isinstance(parsed, dict):
            raise ValueError("LLM response is not a JSON object.")
        return parsed, {
            "method": "none",
            "repair_count": 0,
            "original_error": None,
            "repaired_text_changed": False,
        }
    if last_error:
        raise last_error
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object.")
    return parsed, {
        "method": "none",
        "repair_count": 0,
        "original_error": None,
        "repaired_text_changed": False,
    }


def _json_parse_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    if not text:
        return candidates
    candidates.append(text)
    if text.startswith("```"):
        fenced = text.strip("`")
        fenced = fenced.removeprefix("json").strip()
        candidates.append(fenced)
    object_text = _extract_first_json_object(text)
    if object_text and object_text not in candidates:
        candidates.append(object_text)
    return candidates


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    in_string = False
    escaped = False
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _repair_common_json_noise(text: str) -> str:
    """修复模型输出中最常见且低风险的 JSON 语法噪声。"""

    repaired = text.strip()
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
    return repaired


def effective_reasoning_effort(config: LlmClientConfig) -> str:
    if not config.enable_thinking:
        return "none"
    effort = (config.reasoning_effort or "").strip().lower()
    if effort in {"minimal", "low", "medium", "high", "xhigh"}:
        return effort
    return "medium"


def response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    output = _get_response_field(response, "output")
    if isinstance(output, list):
        for item in output:
            content_items = _get_response_field(item, "content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                text = _get_response_field(content_item, "text")
                if isinstance(text, str):
                    chunks.append(text)
    if chunks:
        return "".join(chunks)

    text = _get_response_field(response, "text")
    if isinstance(text, str):
        return text
    raise ValueError("Unable to read text from Responses API output.")


def chat_completion_output_text(response: Any) -> str:
    choices = _get_response_field(response, "choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Unable to read choices from Chat Completions API output.")

    message = _get_response_field(choices[0], "message")
    content = _get_response_field(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            text = _get_response_field(item, "text")
            if isinstance(text, str):
                chunks.append(text)
        if chunks:
            return "".join(chunks)
    raise ValueError("Unable to read text from Chat Completions API output.")


def _get_response_field(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)
