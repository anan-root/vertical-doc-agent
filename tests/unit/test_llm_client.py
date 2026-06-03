import json
import sys
import types

from construction_bidding_agent.llm_client import call_openai_json, parse_json_response
from construction_bidding_agent.llm_config import LlmClientConfig
from construction_bidding_agent.llm_gateway import llm_audit_context, summarize_llm_audit_for_job


def test_parse_json_response_accepts_fenced_json_object():
    parsed = parse_json_response('```json\n{"title": "测试"}\n```')

    assert parsed == {"title": "测试"}


def test_parse_json_response_extracts_json_object_from_extra_text():
    parsed = parse_json_response('说明文字\n{"title": "测试", "sections": []}\n结束')

    assert parsed["title"] == "测试"


def test_parse_json_response_repairs_common_json_noise():
    parsed = parse_json_response('{title: "测试", "sections": [],}')

    assert parsed == {"title": "测试", "sections": []}


def test_parse_json_response_rejects_non_object_json():
    try:
        parse_json_response(json.dumps(["不是对象"], ensure_ascii=False))
    except ValueError as exc:
        assert "not a JSON object" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_call_openai_json_writes_redacted_audit_log(tmp_path, monkeypatch):
    audit_path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("LLM_AUDIT_LOG_PATH", str(audit_path))

    class FakeResponse:
        output_text = '{"ok": true}'

    class FakeResponses:
        def create(self, **_kwargs):
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    config = LlmClientConfig(
        provider="deepseek",
        api_key="sk-secret-value",
        base_url="https://api.deepseek.com",
        model="deepseek-v4",
        temperature=0,
        top_p=1,
        max_tokens=1024,
        timeout_seconds=30,
        max_retries=1,
        api_type="responses",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
    )

    result = call_openai_json(
        config=config,
        task_key="score_points_extraction_input",
        system_prompt="系统提示 sk-should-not-leak",
        user_input="用户输入包含敏感正文",
    )

    assert result == '{"ok": true}'
    record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["task_key"] == "score_points_extraction_input"
    assert record["provider"] == "deepseek"
    assert record["model"] == "deepseek-v4"
    assert record["status"] == "succeeded"
    assert record["prompt_hash"]
    assert record["estimated_input_tokens"] > 0
    assert record["estimated_output_tokens"] > 0
    assert "用户输入包含敏感正文" not in json.dumps(record, ensure_ascii=False)
    assert "sk-secret-value" not in json.dumps(record, ensure_ascii=False)
    assert "sk-should-not-leak" not in json.dumps(record, ensure_ascii=False)


def test_llm_audit_context_adds_project_and_job_ids(tmp_path, monkeypatch):
    audit_path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("LLM_AUDIT_LOG_PATH", str(audit_path))

    class FakeResponse:
        output_text = '{"ok": true}'

    class FakeResponses:
        def create(self, **_kwargs):
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    config = LlmClientConfig(
        provider="deepseek",
        api_key="sk-secret-value",
        base_url="https://api.deepseek.com",
        model="deepseek-v4",
        temperature=0,
        top_p=1,
        max_tokens=1024,
        timeout_seconds=30,
        max_retries=1,
        api_type="responses",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
    )

    with llm_audit_context(project_id="P-001", job_id="JOB-001", tool_name="tender_parse"):
        call_openai_json(
            config=config,
            task_key="score_points_extraction_input",
            system_prompt="system",
            user_input="user",
        )

    record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["project_id"] == "P-001"
    assert record["job_id"] == "JOB-001"
    assert record["tool_name"] == "tender_parse"
    assert record["task_key"] == "score_points_extraction_input"


def test_summarize_llm_audit_for_job_groups_usage(tmp_path):
    audit_path = tmp_path / "llm_calls.jsonl"
    records = [
        {
            "project_id": "P-001",
            "job_id": "JOB-001",
            "task_key": "score_points_extraction_input",
            "provider": "deepseek",
            "model": "deepseek-v4",
            "status": "succeeded",
            "estimated_input_tokens": 100,
            "estimated_output_tokens": 40,
            "duration_ms": 1200,
            "started_at": "2026-05-20T01:00:00+00:00",
            "ended_at": "2026-05-20T01:00:02+00:00",
        },
        {
            "project_id": "P-001",
            "job_id": "JOB-001",
            "task_key": "technical_bid_chapter_generation",
            "provider": "deepseek",
            "model": "deepseek-v4",
            "status": "timeout",
            "estimated_input_tokens": 80,
            "estimated_output_tokens": 0,
            "duration_ms": 3000,
            "started_at": "2026-05-20T01:00:03+00:00",
            "ended_at": "2026-05-20T01:00:06+00:00",
        },
        {
            "project_id": "P-002",
            "job_id": "JOB-002",
            "task_key": "other",
            "status": "succeeded",
            "estimated_input_tokens": 999,
            "estimated_output_tokens": 999,
        },
    ]
    audit_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")

    summary = summarize_llm_audit_for_job("JOB-001", project_id="P-001", audit_log_path=audit_path)

    assert summary["call_count"] == 2
    assert summary["succeeded_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["timeout_count"] == 1
    assert summary["estimated_input_tokens"] == 180
    assert summary["estimated_output_tokens"] == 40
    assert summary["estimated_total_tokens"] == 220
    assert summary["duration_ms"] == 4200
    assert summary["providers"] == ["deepseek"]
    assert summary["models"] == ["deepseek-v4"]
    assert summary["task_keys"] == ["score_points_extraction_input", "technical_bid_chapter_generation"]
