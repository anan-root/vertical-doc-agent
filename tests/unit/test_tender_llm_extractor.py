import json
import sys
import types
import uuid
from pathlib import Path

from construction_bidding_agent.document_parser.tender_llm_extractor import (
    LlmClientConfig,
    TenderLlmTaskRun,
    _collect_invalid_ref_ids,
    _llm_config,
    _load_prompt,
    _parse_json_response,
    _postprocess_project_info,
    _postprocess_score_points,
    _run_task_packages,
    _validate_task_output,
    _validate_output_refs,
    run_tender_llm_extraction_from_file,
)


LLM_ENV_KEYS = [
    "API_KEY",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "BASE_URL",
    "LLM_BASE_URL",
    "OPENAI_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "DASHSCOPE_BASE_URL",
    "MODEL",
    "LLM_MODEL",
    "OPENAI_MODEL",
    "DEEPSEEK_MODEL",
    "DASHSCOPE_MODEL",
    "LLM_PROVIDER",
    "PROVIDER",
    "TEMPERATURE",
    "LLM_TEMPERATURE",
    "TOP_P",
    "LLM_TOP_P",
    "MAX_TOKENS",
    "LLM_MAX_TOKENS",
    "TIMEOUT_SECONDS",
    "LLM_TIMEOUT_SECONDS",
    "MAX_RETRIES",
    "LLM_MAX_RETRIES",
    "MAX_WORKERS",
    "LLM_MAX_WORKERS",
    "API_TYPE",
    "LLM_API_TYPE",
    "STRUCTURED_OUTPUT_TYPE",
    "LLM_STRUCTURED_OUTPUT_TYPE",
    "ENABLE_THINKING",
    "LLM_ENABLE_THINKING",
    "REASONING_EFFORT",
    "LLM_REASONING_EFFORT",
    "STORE_RESPONSE",
    "LLM_STORE_RESPONSE",
    "LLM_TASK_PROFILES_PATH",
    "TASK_LLM_PROFILES_PATH",
]


def test_parse_json_response_accepts_plain_and_fenced_json():
    assert _parse_json_response('{"schema_version":"x"}') == {"schema_version": "x"}
    assert _parse_json_response('```json\n{"schema_version":"x"}\n```') == {"schema_version": "x"}


def test_validate_score_points_flags_empty_points():
    validation = _validate_task_output(
        "score_points_extraction_input",
        {
            "schema_version": "score_points_v1",
            "is_score_region": True,
            "score_points": [],
        },
    )

    assert validation["issue_count"] == 1
    assert "score_points is empty." in validation["issues"]


def test_postprocess_score_points_repairs_shifted_cell_refs_and_backfills_original_text():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B312_R26_C1"},
                "model_observed_text": "主要施工方案与技术措施",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B312_R26_C2"},
                "parent_ref": {"type": "cell", "id": "B312_R25_C1"},
                "belongs_to_technical_bid": True,
                "confidence": 0.91,
                "needs_confirmation": False,
                "confirmation_reason": None,
            }
        ],
    }
    package = {
        "block_refs": [{"block_index": 312}],
        "cell_refs": [
            {
                "cell_id": "B312_R25_C1",
                "text_raw": "技术标评审标准",
                "block_index": 312,
                "table_index": 2,
                "row_index": 25,
                "cell_index": 1,
            },
            {
                "cell_id": "B312_R26_C0",
                "text_raw": "主要施工方案与技术措施",
                "block_index": 312,
                "table_index": 2,
                "row_index": 26,
                "cell_index": 0,
            },
            {
                "cell_id": "B312_R26_C1",
                "text_raw": "施工方案总体安排合理，对施工难点有先进和合理的建议。",
                "block_index": 312,
                "table_index": 2,
                "row_index": 26,
                "cell_index": 1,
            },
        ],
    }
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)
    _validate_output_refs(data, package, validation)

    point = data["score_points"][0]
    final_point = data["system_final_score_points"][0]
    assert point["score_point_ref"]["id"] == "B312_R26_C0"
    assert point["description_ref"]["id"] == "B312_R26_C1"
    assert final_point["score_point_raw"] == "主要施工方案与技术措施"
    assert final_point["level_1_heading_text"] == "主要施工方案与技术措施"
    assert final_point["description"] == "施工方案总体安排合理，对施工难点有先进和合理的建议。"
    assert final_point["parent_text"] == "技术标评审标准"
    assert final_point["used_as_level_1_heading"] is True
    assert validation["issue_count"] == 0
    assert len(validation["ref_corrections"]) == 2


def test_postprocess_score_points_keeps_raw_text_and_normalizes_pdf_heading_spaces():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B1222_R0_C0"},
                "model_observed_text": "主要施工方案与技术措施",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B1222_R0_C1"},
                "parent_ref": None,
                "belongs_to_technical_bid": True,
                "confidence": 0.91,
                "needs_confirmation": False,
                "confirmation_reason": None,
            }
        ],
    }
    package = {
        "block_refs": [{"block_index": 1222}],
        "cell_refs": [
            {
                "cell_id": "B1222_R0_C0",
                "text_raw": "主要施工方案 与技术措施",
                "block_index": 1222,
                "table_index": 8,
                "row_index": 0,
                "cell_index": 0,
            },
            {
                "cell_id": "B1222_R0_C1",
                "text_raw": "内容完整、针对性强。",
                "block_index": 1222,
                "table_index": 8,
                "row_index": 0,
                "cell_index": 1,
            },
        ],
    }
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    final_point = data["system_final_score_points"][0]
    assert final_point["score_point_raw"] == "主要施工方案 与技术措施"
    assert final_point["level_1_heading_text"] == "主要施工方案与技术措施"


def test_postprocess_score_points_quality_gate_blocks_non_technical_point():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B1_R0_C0"},
                "model_observed_text": "施工组织设计",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B1_R0_C1"},
                "parent_ref": None,
                "belongs_to_technical_bid": False,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            }
        ],
    }
    package = _score_point_package(
        [
            ("B1_R0_C0", "施工组织设计", 0, 0),
            ("B1_R0_C1", "内容完整、针对性强。", 0, 1),
        ]
    )
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    quality_gate = data["quality_gate"]
    assert quality_gate == validation["quality_gate"]
    assert quality_gate["blocking"] is True
    assert validation["fatal"] is True
    assert quality_gate["blocking_issue_count"] == 1
    assert {issue["type"] for issue in quality_gate["issues"]} == {"score_points_empty"}
    assert data["system_final_score_points"] == []
    assert data["system_removed_score_points"][0]["reason"] == "not_confirmed_as_technical_bid"


def test_postprocess_score_points_quality_gate_warns_possible_business_or_price_point():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B2_R0_C0"},
                "model_observed_text": "投标报价评分",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B2_R0_C1"},
                "parent_ref": None,
                "belongs_to_technical_bid": True,
                "confidence": 0.85,
                "needs_confirmation": False,
                "confirmation_reason": None,
            }
        ],
    }
    package = _score_point_package(
        [
            ("B2_R0_C0", "投标报价评分", 0, 0),
            ("B2_R0_C1", "按报价偏差率计算。", 0, 1),
        ],
        block_index=2,
    )
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    quality_gate = data["quality_gate"]
    assert quality_gate["blocking"] is True
    assert validation["fatal"] is True
    assert data["system_final_score_points"] == []
    assert data["system_removed_score_points"][0]["reason"] == "business_price_or_credit_score_point"


def test_postprocess_score_points_filters_llm_regression_and_recovers_technical_table_rows():
    business_text = (
        "（1）商务标评审采用合格制。（2）评审内容主要包括实质性内容是否响应招标文件、"
        "是否可能低于成本或者影响履约的异常低价投标情况。"
    )
    technical_titles = [
        "内容完整性",
        "主要施工方案与技术措施",
        "质量管理体系与措施",
        "安全管理体系与措施",
        "文明施工、环境保护管理体系及施工现场扬尘治理措施",
        "工期保证措施",
        "拟投入资源配备计划",
        "施工进度表",
        "施工总平面布置图",
        "技术创新的应用实施措施",
        "采用新工艺、新技术、新设备、新材料、BIM等的程度",
        "施工现场实施信息化监控和数据处理",
        "风险管理措施",
    ]
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B312_R24_C2"},
                "model_observed_text": business_text,
                "score_ref": None,
                "description_ref": None,
                "parent_ref": {"type": "cell", "id": "B312_R24_C1"},
                "belongs_to_technical_bid": True,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            },
            {
                "score_point_ref": {"type": "cell", "id": "B312_R25_C0"},
                "model_observed_text": "2.2.2（2）",
                "score_ref": None,
                "description_ref": None,
                "parent_ref": {"type": "cell", "id": "B312_R25_C1"},
                "belongs_to_technical_bid": True,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            },
            *[
                {
                    "score_point_ref": {"type": "cell", "id": f"B312_R{row}_C0"},
                    "model_observed_text": title,
                    "score_ref": None,
                    "description_ref": {"type": "cell", "id": f"B312_R{row}_C1"},
                    "parent_ref": {"type": "cell", "id": "B312_R25_C1"},
                    "belongs_to_technical_bid": True,
                    "confidence": 0.9,
                    "needs_confirmation": False,
                    "confirmation_reason": None,
                }
                for row, title in zip(range(26, 37), technical_titles[1:12], strict=True)
            ],
        ],
    }
    package = _score_point_package(
        [
            ("B312_R24_C0", "2.2.1（1）", 24, 0),
            ("B312_R24_C1", "商务标评审标准）", 24, 1),
            ("B312_R24_C2", business_text, 24, 2),
            ("B312_R25_C0", "2.2.2（2）", 25, 0),
            ("B312_R25_C1", "技术标评审标准", 25, 1),
            ("B312_R25_C2", "内容完整性", 25, 2),
            ("B312_R25_C3", "技术标的主要内容具有完整性，符合招标文件的要求。", 25, 3),
            *[
                (f"B312_R{row}_C0", title, row, 0)
                for row, title in zip(range(26, 38), technical_titles[1:], strict=True)
            ],
            *[
                (f"B312_R{row}_C1", f"{title}评分标准。若不提供则判为不合格。", row, 1)
                for row, title in zip(range(26, 38), technical_titles[1:], strict=True)
            ],
            ("B312_R38_C0", "2.2.3（3）", 38, 0),
            ("B312_R38_C1", "综合标评审标准", 38, 1),
            ("B312_R38_C2", "企业业绩", 38, 2),
            ("B312_R38_C3", "是否合格，投标人提供近三年房屋建筑工程施工业绩。", 38, 3),
        ],
        block_index=312,
    )
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    titles = [point["level_1_heading_text"] for point in data["system_final_score_points"]]
    assert titles == technical_titles
    assert len(data["system_removed_score_points"]) == 2
    assert {item["reason"] for item in data["system_removed_score_points"]} == {
        "business_price_or_credit_score_point",
        "structural_or_clause_number",
    }
    assert [point["level_1_heading_text"] for point in data["system_recovered_score_points"]] == [
        "内容完整性",
        "风险管理措施",
    ]
    assert data["quality_gate"]["blocking"] is False
    assert validation["fatal"] is False


def test_postprocess_score_points_repairs_cross_row_shift_inside_technical_table():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B312_R25_C0"},
                "model_observed_text": "主要施工方案与技术措施",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B312_R25_C1"},
                "parent_ref": {"type": "cell", "id": "B312_R25_C1"},
                "belongs_to_technical_bid": True,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            }
        ],
    }
    package = _score_point_package(
        [
            ("B312_R25_C0", "2.2.2（2）", 25, 0),
            ("B312_R25_C1", "技术标评审标准", 25, 1),
            ("B312_R25_C2", "内容完整性", 25, 2),
            ("B312_R25_C3", "技术标的主要内容具有完整性。", 25, 3),
            ("B312_R26_C0", "主要施工方案与技术措施", 26, 0),
            ("B312_R26_C1", "施工方案总体安排合理。", 26, 1),
            ("B312_R27_C0", "质量管理体系与措施", 27, 0),
            ("B312_R27_C1", "质量管理措施完整。", 27, 1),
            ("B312_R28_C0", "2.2.3（3）", 28, 0),
            ("B312_R28_C1", "综合标评审标准", 28, 1),
        ],
        block_index=312,
    )
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    final_point = next(
        point
        for point in data["system_final_score_points"]
        if point["level_1_heading_text"] == "主要施工方案与技术措施"
    )
    assert final_point["score_point_ref"] == {"type": "cell", "id": "B312_R26_C0"}
    assert final_point["description_ref"] == {"type": "cell", "id": "B312_R26_C1"}
    assert final_point["needs_confirmation"] is False
    assert data["quality_gate"]["blocking"] is False


def test_postprocess_score_points_quality_gate_warns_duplicate_titles():
    data = {
        "schema_version": "score_points_v1",
        "is_score_region": True,
        "score_points": [
            {
                "score_point_ref": {"type": "cell", "id": "B3_R0_C0"},
                "model_observed_text": "质量管理体系与措施",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B3_R0_C1"},
                "parent_ref": None,
                "belongs_to_technical_bid": True,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            },
            {
                "score_point_ref": {"type": "cell", "id": "B3_R1_C0"},
                "model_observed_text": "质量管理体系与措施",
                "score_ref": None,
                "description_ref": {"type": "cell", "id": "B3_R1_C1"},
                "parent_ref": None,
                "belongs_to_technical_bid": True,
                "confidence": 0.9,
                "needs_confirmation": False,
                "confirmation_reason": None,
            },
        ],
    }
    package = _score_point_package(
        [
            ("B3_R0_C0", "质量管理体系与措施", 0, 0),
            ("B3_R0_C1", "第一处评分标准。", 0, 1),
            ("B3_R1_C0", "质量管理体系与措施", 1, 0),
            ("B3_R1_C1", "第二处评分标准。", 1, 1),
        ],
        block_index=3,
    )
    validation = _validate_task_output("score_points_extraction_input", data)

    _postprocess_score_points(data, package, validation)

    quality_gate = data["quality_gate"]
    assert quality_gate["blocking"] is False
    assert any(issue["type"] == "duplicate_score_point_title" for issue in quality_gate["issues"])


def test_collect_invalid_ref_ids_ignores_plain_business_ids():
    data = {
        "document_id": "project_info_candidate_001",
        "region_id": "project_info_combined",
        "field_ref": {"type": "cell", "id": "B20_R0_C1"},
        "bad_ref": {"type": "cell", "id": "B20_R0_C9"},
    }

    invalid = _collect_invalid_ref_ids(data, valid_block_ids={"B20"}, valid_cell_ids={"B20_R0_C1"})

    assert invalid == {"B20_R0_C9"}


def test_postprocess_project_info_removes_invalid_empty_field_ref():
    data = {
        "fields": {
            "safety_civilized": {
                "field_ref": {"type": "block", "id": "B0"},
                "value": None,
                "confidence": 0,
                "needs_confirmation": True,
                "confirmation_reason": "未找到安全文明要求。",
            },
            "project_name": {
                "field_ref": {"type": "cell", "id": "B10_R0_C1"},
                "value": "项目名称",
                "confidence": 0.99,
                "needs_confirmation": False,
            },
        }
    }
    package = {
        "block_refs": [{"block_index": 10}],
        "cell_refs": [{"cell_id": "B10_R0_C1"}],
    }

    _postprocess_project_info(data, package)

    assert data["fields"]["safety_civilized"]["field_ref"] is None
    assert "无法回填" in data["fields"]["safety_civilized"]["confirmation_reason"]
    assert data["fields"]["project_name"]["field_ref"]["id"] == "B10_R0_C1"
    assert "duration" in data["fields"]
    assert data["fields"]["duration"]["needs_confirmation"] is True


def test_run_task_packages_parallel_preserves_package_order(monkeypatch):
    def fake_run_single_task(package, *, prompt_dir, llm_config=None, model=None, **_kwargs):
        return TenderLlmTaskRun(
            task_key=package["task_key"],
            task_title=package["task_title"],
            model=(llm_config.model if llm_config else model),
            status="completed",
            input_estimated_tokens=package["estimated_tokens"],
            duration_seconds=0.01,
            started_at="2026-05-04T10:00:00+08:00",
            completed_at="2026-05-04T10:00:01+08:00",
        )

    monkeypatch.setattr(
        "construction_bidding_agent.document_parser.tender_llm_extractor._run_single_task",
        fake_run_single_task,
    )
    packages = [
        {"task_key": "project_info_extraction_input", "task_title": "项目", "estimated_tokens": 1},
        {"task_key": "score_points_extraction_input", "task_title": "评分", "estimated_tokens": 2},
        {"task_key": "technical_requirements_extraction_input", "task_title": "技术", "estimated_tokens": 3},
    ]

    tasks = _run_task_packages(
        packages,
        prompt_dir=Path("docs/prompts"),
        model="test-model",
        execution_mode="parallel",
        max_workers=3,
    )

    assert [task.task_key for task in tasks] == [package["task_key"] for package in packages]
    assert all(task.started_at for task in tasks)
    assert all(task.completed_at for task in tasks)


def test_run_task_packages_uses_task_specific_llm_profiles(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    tmp_dir = Path("outputs") / "tmp_tests" / f"llm_profiles_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True)
    profile_path = tmp_dir / "llm-task-profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "default": {
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": 2048,
                    "timeout_seconds": 180,
                    "structured_output_type": "json_object",
                    "enable_thinking": False,
                    "reasoning_effort": "none",
                },
                "tasks": {
                    "project_info_extraction_input": {
                        "max_tokens": 4096,
                        "timeout_seconds": 240,
                    },
                    "score_points_extraction_input": {
                        "max_tokens": 4096,
                        "timeout_seconds": 180,
                    },
                    "technical_requirements_extraction_input": {
                        "max_tokens": 8192,
                        "timeout_seconds": 300,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_TASK_PROFILES_PATH", str(profile_path))

    captured = {}

    def fake_run_single_task(package, *, prompt_dir, llm_config=None, model=None, provider=None, base_url=None, **_kwargs):
        config = llm_config or _llm_config(
            task_key=package["task_key"],
            model_override=model,
            provider_override=provider,
            base_url_override=base_url,
        )
        captured[package["task_key"]] = {
            "max_tokens": config.max_tokens,
            "timeout_seconds": config.timeout_seconds,
        }
        return TenderLlmTaskRun(
            task_key=package["task_key"],
            task_title=package["task_title"],
            model=config.model,
            status="completed",
            input_estimated_tokens=package["estimated_tokens"],
            duration_seconds=0.01,
        )

    monkeypatch.setattr(
        "construction_bidding_agent.document_parser.tender_llm_extractor._run_single_task",
        fake_run_single_task,
    )
    packages = [
        {"task_key": "project_info_extraction_input", "task_title": "项目", "estimated_tokens": 1},
        {"task_key": "score_points_extraction_input", "task_title": "评分", "estimated_tokens": 2},
        {"task_key": "technical_requirements_extraction_input", "task_title": "技术", "estimated_tokens": 3},
    ]

    _run_task_packages(
        packages,
        prompt_dir=Path("docs/prompts"),
        model="test-model",
        execution_mode="serial",
        max_workers=1,
    )

    assert captured["project_info_extraction_input"] == {"max_tokens": 4096, "timeout_seconds": 240.0}
    assert captured["score_points_extraction_input"] == {"max_tokens": 4096, "timeout_seconds": 180.0}
    assert captured["technical_requirements_extraction_input"] == {"max_tokens": 8192, "timeout_seconds": 300.0}


def test_run_task_packages_uses_task_cache_and_force_refresh(monkeypatch):
    tmp_dir = Path("outputs") / "tmp_tests" / f"llm_cache_{uuid.uuid4().hex}"
    prompt_dir = tmp_dir / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "score-point-extraction-prompt.md").write_text("prompt", encoding="utf-8")
    call_count = {"count": 0}

    def fake_call_openai_json(*, llm_config=None, task_key=None, model=None, system_prompt, user_input):
        call_count["count"] += 1
        return json.dumps(
            {
                "schema_version": "score_points_v1",
                "is_score_region": True,
                "score_points": [
                    {
                        "score_point_ref": {"type": "cell", "id": "B1_R0_C0"},
                        "model_observed_text": "施工组织设计",
                        "score_ref": None,
                        "description_ref": {"type": "cell", "id": "B1_R0_C1"},
                        "parent_ref": None,
                        "belongs_to_technical_bid": True,
                        "confidence": 0.9,
                        "needs_confirmation": False,
                        "confirmation_reason": None,
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "construction_bidding_agent.document_parser.tender_llm_extractor._call_openai_json",
        fake_call_openai_json,
    )
    package = {
        "task_key": "score_points_extraction_input",
        "task_title": "评分点",
        "estimated_tokens": 12,
        "input_profile": "full",
        "input_text": "施工组织设计 | 内容完整",
        "block_refs": [{"block_index": 1}],
        "cell_refs": [
            {
                "cell_id": "B1_R0_C0",
                "text_raw": "施工组织设计",
                "block_index": 1,
                "table_index": 1,
                "row_index": 0,
                "cell_index": 0,
            },
            {
                "cell_id": "B1_R0_C1",
                "text_raw": "内容完整",
                "block_index": 1,
                "table_index": 1,
                "row_index": 0,
                "cell_index": 1,
            },
        ],
    }

    first = _run_task_packages(
        [package],
        prompt_dir=prompt_dir,
        model="deepseek-v4-pro",
        execution_mode="serial",
        max_workers=1,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        cache_dir=tmp_dir / "cache",
    )[0]
    second = _run_task_packages(
        [package],
        prompt_dir=prompt_dir,
        model="deepseek-v4-pro",
        execution_mode="serial",
        max_workers=1,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        cache_dir=tmp_dir / "cache",
    )[0]
    refreshed = _run_task_packages(
        [package],
        prompt_dir=prompt_dir,
        model="deepseek-v4-pro",
        execution_mode="serial",
        max_workers=1,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        cache_dir=tmp_dir / "cache",
        force_refresh=True,
    )[0]

    assert call_count["count"] == 2
    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert refreshed.cache_status == "miss"
    assert second.parsed_json == first.parsed_json


def test_load_prompt_prefers_production_prompt_and_falls_back_to_design_prompt(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    design_prompt = prompt_dir / "score-point-extraction-prompt.md"
    production_prompt = prompt_dir / "score-point-extraction-production.md"
    design_prompt.write_text("design prompt", encoding="utf-8")
    production_prompt.write_text("production prompt", encoding="utf-8")

    prompt = _load_prompt("score_points_extraction_input", prompt_dir)

    assert prompt.startswith("production prompt")
    production_prompt.unlink()
    fallback_prompt = _load_prompt("score_points_extraction_input", prompt_dir)
    assert fallback_prompt.startswith("design prompt")


def test_llm_config_prefers_generic_env_vars(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("API_KEY", "generic-key")
    monkeypatch.setenv("BASE_URL", "https://compatible.example.com/v1")
    monkeypatch.setenv("MODEL", "generic-model")
    monkeypatch.setenv("LLM_PROVIDER", "custom-compatible")
    monkeypatch.setenv("TEMPERATURE", "0.2")
    monkeypatch.setenv("TOP_P", "0.8")
    monkeypatch.setenv("MAX_TOKENS", "4096")
    monkeypatch.setenv("TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("MAX_RETRIES", "4")
    monkeypatch.setenv("MAX_WORKERS", "5")
    monkeypatch.setenv("API_TYPE", "responses")
    monkeypatch.setenv("STRUCTURED_OUTPUT_TYPE", "json_object")
    monkeypatch.setenv("ENABLE_THINKING", "true")
    monkeypatch.setenv("REASONING_EFFORT", "medium")
    monkeypatch.setenv("STORE_RESPONSE", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "legacy-model")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://legacy.example.com")

    config = _llm_config()

    assert config.provider == "custom-compatible"
    assert config.api_key == "generic-key"
    assert config.base_url == "https://compatible.example.com/v1"
    assert config.model == "generic-model"
    assert config.temperature == 0.2
    assert config.top_p == 0.8
    assert config.max_tokens == 4096
    assert config.timeout_seconds == 240
    assert config.max_retries == 4
    assert config.max_workers == 5
    assert config.api_type == "responses"
    assert config.structured_output_type == "json_object"
    assert config.enable_thinking is True
    assert config.reasoning_effort == "medium"
    assert config.store_response is True


def test_llm_config_allows_task_profile_to_override_generation_parameters(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    tmp_dir = Path("outputs") / "tmp_tests" / f"llm_profile_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True)
    profile_path = tmp_dir / "profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "default": {
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": 2048,
                    "timeout_seconds": 180,
                    "max_workers": 2,
                    "structured_output_type": "json_object",
                    "enable_thinking": False,
                    "reasoning_effort": "none",
                },
                "tasks": {
                    "outline_refinement": {
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "max_tokens": 4096,
                        "timeout_seconds": 120,
                        "max_workers": 4,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_TASK_PROFILES_PATH", str(profile_path))
    monkeypatch.setenv("TEMPERATURE", "0")
    monkeypatch.setenv("TOP_P", "1")
    monkeypatch.setenv("MAX_TOKENS", "1024")
    monkeypatch.setenv("TIMEOUT_SECONDS", "200")

    config = _llm_config(task_key="outline_refinement")

    assert config.temperature == 0.2
    assert config.top_p == 0.9
    assert config.max_tokens == 4096
    assert config.timeout_seconds == 120
    assert config.max_workers == 4
    assert config.structured_output_type == "json_object"


def test_call_openai_json_uses_env_driven_generation_parameters(monkeypatch):
    from construction_bidding_agent.document_parser.tender_llm_extractor import _call_openai_json

    captured = {}

    class FakeResponse:
        output_text = '{"ok": true}'

    class FakeResponses:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    fake_openai_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)
    config = LlmClientConfig(
        provider="custom",
        api_key="key",
        base_url="https://compatible.example.com/v1",
        model="custom-model",
        temperature=0.15,
        top_p=0.7,
        max_tokens=2048,
        timeout_seconds=123,
        max_retries=5,
        api_type="responses",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
    )

    assert _call_openai_json(
        llm_config=config,
        system_prompt="system",
        user_input="user",
    ) == '{"ok": true}'
    assert captured["client"] == {
        "api_key": "key",
        "base_url": "https://compatible.example.com/v1",
        "timeout": 123,
        "max_retries": 5,
    }
    assert captured["request"]["model"] == "custom-model"
    assert captured["request"]["input"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert captured["request"]["temperature"] == 0.15
    assert captured["request"]["top_p"] == 0.7
    assert captured["request"]["max_output_tokens"] == 2048
    assert captured["request"]["text"] == {"format": {"type": "json_object"}}
    assert captured["request"]["reasoning"] == {"effort": "none"}
    assert captured["request"]["store"] is False


def test_call_openai_json_supports_chat_completions(monkeypatch):
    from construction_bidding_agent.document_parser.tender_llm_extractor import _call_openai_json

    captured = {}

    class FakeMessage:
        content = '{"ok": true}'

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = FakeChat()

    fake_openai_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)
    config = LlmClientConfig(
        provider="dashscope",
        api_key="key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="kimi-k2.6",
        temperature=0,
        top_p=0.9,
        max_tokens=4096,
        timeout_seconds=180,
        max_retries=2,
        api_type="chat",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
    )

    assert _call_openai_json(
        llm_config=config,
        system_prompt="system",
        user_input="user",
    ) == '{"ok": true}'
    assert captured["client"] == {
        "api_key": "key",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "timeout": 180,
        "max_retries": 2,
    }
    assert captured["request"] == {
        "model": "kimi-k2.6",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "temperature": 0,
        "top_p": 0.9,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }


def test_run_tender_llm_extraction_skips_without_api_key(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com")
    tmp_path = Path("outputs") / "tmp_tests"
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / "inputs.json"
    input_path.write_text(
        json.dumps(
            {
                "file_name": "sample.pdf",
                "packages": [
                    {
                        "task_key": "score_points_extraction_input",
                        "task_title": "技术标评分点抽取输入包",
                        "estimated_tokens": 123,
                        "input_text": "sample",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_tender_llm_extraction_from_file(
        input_path,
        prompt_dir=tmp_path,
    )

    assert result.task_count == 1
    assert result.completed_task_count == 0
    assert result.failed_task_count == 1
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-pro"
    assert result.base_url == "https://api.deepseek.com"
    assert result.execution_mode == "parallel"
    assert result.duration_seconds >= 0
    assert result.started_at
    assert result.completed_at
    assert result.tasks[0].status == "skipped"
    assert result.tasks[0].started_at
    assert result.tasks[0].completed_at
    assert "API_KEY" in result.tasks[0].error


def _score_point_package(
    cells: list[tuple[str, str, int, int]],
    *,
    block_index: int = 1,
) -> dict[str, object]:
    return {
        "block_refs": [{"block_index": block_index}],
        "cell_refs": [
            {
                "cell_id": cell_id,
                "text_raw": text,
                "block_index": block_index,
                "table_index": 1,
                "row_index": row_index,
                "cell_index": cell_index,
            }
            for cell_id, text, row_index, cell_index in cells
        ],
    }
