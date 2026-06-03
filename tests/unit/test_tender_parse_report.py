from construction_bidding_agent.document_parser.tender_parse_report import (
    build_tender_parse_result,
    render_tender_parse_report,
)


def test_build_tender_parse_result_merges_llm_outputs_and_review_items():
    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        _score_run(),
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["schema_version"] == "tender_parse_result_v0.1"
    assert result["parse_job"]["status"] == "completed_with_warnings"
    assert result["execution"]["mode"] == "parallel"
    assert result["execution"]["task_count"] == 3
    assert result["execution"]["completed_task_count"] == 3
    assert result["execution"]["timing"]["llm_wall_clock_seconds"] == 12
    assert result["project_type"]["value"] == "construction"
    assert result["project_info"]["project_name"]["value"] == "固始县轴承厂家属院棚户区改造项目"
    assert result["project_info"]["construction_scale"]["value"] == "未明确"
    assert result["project_info"]["construction_scale"]["review_required"] is True
    assert result["technical_score_points"][0]["original_text"] == "主要施工方案 与技术措施"
    assert result["technical_score_points"][0]["catalog_level_1_title"] == "主要施工方案与技术措施"
    assert result["technical_score_points"][0]["source_refs"][0]["row_index"] == 0
    assert result["technical_standards"][0]["generation_impact"] == "生成“质量管理措施”相关章节时应响应该要求。"
    assert any(item["item"] == "复核建设规模" for item in result["review_items"])


def test_render_tender_parse_report_contains_core_sections():
    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        _score_run(),
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    report = render_tender_parse_report(result)

    assert "# 招标文件解析报告" in report
    assert "## 一、项目信息" in report
    assert "## 二、技术要求信息" in report
    assert "| 项目名称 | 固始县轴承厂家属院棚户区改造项目 |" in report
    assert "质量应达到合格标准" in report
    assert "复核建设规模" in report


def test_build_tender_parse_result_dedupes_same_parallel_run_execution_timing():
    run = _combined_parallel_run()

    result = build_tender_parse_result(
        _input_packages(),
        run,
        run,
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["execution"]["task_count"] == 3
    assert result["execution"]["completed_task_count"] == 3
    assert result["execution"]["timing"]["llm_wall_clock_seconds"] == 10
    assert result["execution"]["timing"]["llm_task_duration_sum_seconds"] == 12


def test_score_point_task_failure_blocks_outline_generation_but_keeps_report_data():
    score_run = _score_run()
    score_run["tasks"][0]["status"] = "failed"
    score_run["tasks"][0]["error"] = "model timeout"
    score_run["tasks"][0]["parsed_json"] = {}

    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        score_run,
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["parse_job"]["status"] == "failed"
    assert result["execution"]["failed_task_count"] == 1
    assert result["execution"]["has_blocking_failure"] is True
    assert result["execution"]["can_generate_outline"] is False
    assert result["technical_score_points"] == []
    assert any(warning["level"] == "blocking" for warning in result["warnings"])
    assert any(item["priority"] == "blocking" for item in result["review_items"])
    assert "# 招标文件解析报告" in render_tender_parse_report(result)


def test_fallback_completed_score_task_keeps_outline_generation_allowed():
    score_run = _score_run()
    score_run["tasks"][0]["status"] = "fallback_completed"
    score_run["tasks"][0]["cache_status"] = "rule_fallback"
    score_run["tasks"][0]["fallback_reason"] = "LLM JSON 解析失败后规则兜底。"

    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        score_run,
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["parse_job"]["status"] == "completed_with_warnings"
    assert result["execution"]["completed_task_count"] == 3
    assert result["execution"]["llm_completed_task_count"] == 2
    assert result["execution"]["fallback_task_count"] == 1
    assert result["execution"]["failed_task_count"] == 0
    assert result["execution"]["can_generate_outline"] is True
    assert result["technical_score_points"][0]["original_text"] == "主要施工方案 与技术措施"


def test_score_point_quality_gate_blocks_outline_generation_and_keeps_reviewable_points():
    score_run = _score_run()
    score_task = score_run["tasks"][0]
    score_task["status"] = "failed"
    score_task["validation"] = {
        "fatal": True,
        "issues": ["第 1 个评分点未被确认为技术标评分点，不能直接进入一级目录。"],
        "quality_gate": {
            "blocking": True,
            "issue_count": 1,
            "blocking_issue_count": 1,
            "warning_issue_count": 0,
            "score_point_count": 1,
            "issues": [
                {
                    "severity": "blocking",
                    "type": "not_technical_bid_score_point",
                    "message": "第 1 个评分点未被确认为技术标评分点，不能直接进入一级目录。",
                }
            ],
        },
    }
    score_task["parsed_json"].pop("quality_gate", None)

    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        score_run,
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["parse_job"]["status"] == "failed"
    assert result["execution"]["has_blocking_failure"] is True
    assert result["execution"]["can_generate_outline"] is False
    assert result["technical_score_points"][0]["blocks_outline_generation"] is True
    assert result["technical_score_points"][0]["review_required"] is True
    assert any(warning["level"] == "blocking" for warning in result["warnings"])
    assert any(item["priority"] == "blocking" and "复核评分点" in item["item"] for item in result["review_items"])


def test_project_info_task_failure_keeps_partial_report_with_high_review_item():
    project_run = _project_technical_run()
    project_run["tasks"][0]["status"] = "failed"
    project_run["tasks"][0]["error"] = "invalid json"
    project_run["tasks"][0]["parsed_json"] = {}

    result = build_tender_parse_result(
        _input_packages(),
        project_run,
        _score_run(),
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    assert result["parse_job"]["status"] == "completed_with_warnings"
    assert result["execution"]["has_blocking_failure"] is False
    assert result["execution"]["can_generate_outline"] is True
    assert result["technical_score_points"]
    assert any(warning["level"] == "high" for warning in result["warnings"])
    assert any(item["priority"] == "high" for item in result["review_items"])


def test_missing_llm_task_is_visible_in_execution_summary():
    score_run = {"execution_mode": "parallel", "duration_seconds": 1, "tasks": []}

    result = build_tender_parse_result(
        _input_packages(),
        _project_technical_run(),
        score_run,
        generated_at="2026-05-04T10:00:00+08:00",
        job_id="job_001",
    )

    missing_task = next(
        task
        for task in result["execution"]["tasks"]
        if task["task_key"] == "score_points_extraction_input"
    )
    assert missing_task["status"] == "missing"
    assert result["parse_job"]["status"] == "failed"
    assert result["execution"]["can_generate_outline"] is False


def _input_packages():
    return {
        "schema_version": "tender_extraction_inputs_v0.1",
        "source_path": "data/raw/sample.docx",
        "file_id": "file_001",
        "file_name": "sample.docx",
        "file_type": "docx",
        "package_count": 3,
        "warnings": [],
        "packages": [
            {
                "task_key": "project_info_extraction_input",
                "regions": [
                    {
                        "region_key": "chapter_1_notice",
                        "region_title": "第一章 招标公告",
                        "source_refs": [],
                    }
                ],
                "block_refs": [
                    {
                        "block_index": 1,
                        "block_type": "paragraph",
                        "text_preview": "招标公告",
                    },
                    {
                        "block_index": 10,
                        "block_type": "table",
                        "table_index": 2,
                        "row_count": 1,
                        "max_column_count": 2,
                        "text_preview": "项目名称 | 固始县轴承厂家属院棚户区改造项目",
                    },
                ],
                "cell_refs": [
                    {
                        "cell_id": "B10_R0_C1",
                        "text_raw": "固始县轴承厂家属院棚户区改造项目",
                        "block_index": 10,
                        "table_index": 2,
                        "row_index": 0,
                        "cell_index": 1,
                    }
                ],
            },
            {
                "task_key": "score_points_extraction_input",
                "regions": [
                    {
                        "region_key": "evaluation_method_preface_table",
                        "region_title": "评标办法前附表",
                        "source_refs": [],
                    }
                ],
                "block_refs": [
                    {
                        "block_index": 20,
                        "block_type": "table",
                        "table_index": 4,
                        "row_count": 1,
                        "max_column_count": 2,
                        "text_preview": "主要施工方案 与技术措施 | 评分标准",
                    }
                ],
                "cell_refs": [
                    {
                        "cell_id": "B20_R0_C0",
                        "text_raw": "主要施工方案 与技术措施",
                        "block_index": 20,
                        "table_index": 4,
                        "row_index": 0,
                        "cell_index": 0,
                    },
                    {
                        "cell_id": "B20_R0_C1",
                        "text_raw": "评分标准",
                        "block_index": 20,
                        "table_index": 4,
                        "row_index": 0,
                        "cell_index": 1,
                    },
                ],
            },
            {
                "task_key": "technical_requirements_extraction_input",
                "regions": [],
                "block_refs": [],
                "cell_refs": [],
            },
        ],
    }


def _project_technical_run():
    return {
        "execution_mode": "parallel",
        "duration_seconds": 7,
        "tasks": [
            {
                "task_key": "project_info_extraction_input",
                "task_title": "项目信息",
                "status": "completed",
                "started_at": "2026-05-04T10:00:00+08:00",
                "completed_at": "2026-05-04T10:00:03+08:00",
                "duration_seconds": 3,
                "input_estimated_tokens": 100,
                "validation": {"issues": []},
                "parsed_json": {
                    "project_type": "construction",
                    "contains_design_task": False,
                    "project_type_confidence": 0.9,
                    "project_type_needs_confirmation": False,
                    "fields": {
                        "project_name": {
                            "field_ref": {"type": "cell", "id": "B10_R0_C1"},
                            "value": "固始县轴承厂家属院棚户区改造项目",
                            "confidence": 0.99,
                            "needs_confirmation": False,
                            "confirmation_reason": None,
                        },
                        "scale": {
                            "field_ref": {"type": "block", "id": "B1"},
                            "value": None,
                            "confidence": 0,
                            "needs_confirmation": True,
                            "confirmation_reason": "未找到明确建设规模。",
                        },
                    },
                    "warnings": [],
                },
            },
            {
                "task_key": "technical_requirements_extraction_input",
                "task_title": "技术要求",
                "status": "completed",
                "started_at": "2026-05-04T10:00:00+08:00",
                "completed_at": "2026-05-04T10:00:04+08:00",
                "duration_seconds": 4,
                "input_estimated_tokens": 100,
                "validation": {"issues": []},
                "parsed_json": {
                    "requirements": [],
                    "technical_standards": [
                        {
                            "standard_ref": {"type": "block", "id": "B1"},
                            "model_observed_text": "质量应达到合格标准。",
                            "standard_type": "quality",
                            "target_section_hint": "质量管理措施",
                            "confidence": 0.9,
                            "needs_confirmation": False,
                        }
                    ],
                    "technical_risks": [],
                    "warnings": [],
                },
            },
        ]
    }


def _score_run():
    return {
        "execution_mode": "parallel",
        "duration_seconds": 5,
        "tasks": [
            {
                "task_key": "score_points_extraction_input",
                "task_title": "评分点",
                "status": "completed",
                "started_at": "2026-05-04T10:00:00+08:00",
                "completed_at": "2026-05-04T10:00:05+08:00",
                "duration_seconds": 5,
                "input_estimated_tokens": 100,
                "validation": {"issues": []},
                "parsed_json": {
                    "system_final_score_points": [
                        {
                            "score_point_raw": "主要施工方案 与技术措施",
                            "level_1_heading_text": "主要施工方案与技术措施",
                            "score_point_ref": {"type": "cell", "id": "B20_R0_C0"},
                            "score": None,
                            "description": "评分标准",
                            "confidence": 0.9,
                            "needs_confirmation": False,
                            "confirmation_reason": None,
                        }
                    ],
                    "warnings": [
                        {
                            "type": "no_score_value",
                            "message": "技术标采用定性评审，无具体分值。",
                            "ref": {"type": "table", "id": "B20"},
                        }
                    ],
                },
            }
        ]
    }


def _combined_parallel_run():
    run = _project_technical_run()
    score_task = _score_run()["tasks"][0]
    run["duration_seconds"] = 10
    run["tasks"].append(score_task)
    return run
