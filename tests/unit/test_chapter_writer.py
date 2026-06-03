import json

from construction_bidding_agent.chapter_generator.chapter_writer import (
    OUTPUT_SCHEMA_VERSION,
    _llm_call_payload,
    _llm_input,
    _text_match_score,
    apply_auto_image_reuse,
    clean_image_captions,
    dedupe_images_across_chapters,
    enrich_image_refs,
    filter_mismatched_image_refs,
    normalize_chapter_identity,
    postprocess_chapter_images,
    run_chapter_generation,
    validate_chapter_output,
)
from construction_bidding_agent.llm_client import parse_json_response_with_repair_info
from construction_bidding_agent.llm_config import LlmClientConfig


def test_run_chapter_generation_completes_with_valid_llm_output():
    package = _package()

    def fake_llm(llm_input, _config):
        unit = llm_input["generation_unit"]
        return json.dumps(_valid_output(unit), ensure_ascii=False)

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert result.completed_count == 1
    assert result.failed_count == 0
    assert result.chapters[0]["unit_id"] == "GU-N1"
    assert result.chapters[0]["sections"][0]["blocks"][1]["type"] == "rich_table"
    assert result.tasks[0].validation["valid"] is True


def test_run_chapter_generation_normalizes_wrong_chapter_path():
    package = _package()

    def fake_llm(llm_input, _config):
        output = _valid_output(llm_input["generation_unit"])
        output["chapter_path"] = ["错误章节"]
        return json.dumps(output, ensure_ascii=False)

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert result.completed_count == 1
    assert result.failed_count == 0
    assert result.chapters[0]["chapter_path"] == package["generation_unit"]["chapter_path"]
    assert not any(issue["type"] == "chapter_path_mismatch" for issue in result.tasks[0].validation["issues"])


def test_parse_json_response_reports_rule_repair():
    parsed, metadata = parse_json_response_with_repair_info('{"a": 1,}')

    assert parsed == {"a": 1}
    assert metadata["method"] == "rule"
    assert metadata["repair_count"] == 1


def test_run_chapter_generation_records_rule_json_repair_metadata():
    package = _package()
    _relax_expanded_targets(package)

    def fake_llm(llm_input, _config):
        unit = llm_input["generation_unit"]
        output_text = json.dumps(_minimal_valid_output(unit), ensure_ascii=False, separators=(",", ":"))
        return output_text.replace('"review_items":[]', '"review_items":[],')

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    task = result.tasks[0]
    assert result.completed_count == 1
    assert task.repair_attempt_count == 1
    assert task.repair_summary["method"] == "rule"
    assert task.validation["json_repair_method"] == "rule"
    assert any(issue["type"] == "json_repair_applied" for issue in task.validation["issues"])


def test_validate_chapter_output_blocks_parameter_conflict_residual():
    package = _package()
    _relax_expanded_targets(package)
    package["generation_constraints"]["parameter_conflict_scan"] = {
        "enabled": True,
        "blocking_on_output": True,
        "hard_constraints": [
            {
                "source": "score_point",
                "text": "项目负责人具有不少于8年施工管理经验。",
                "operator": ">=",
                "value": 8,
                "unit": "year",
                "category": "experience_years",
            }
        ],
        "conflicts": [],
    }
    output = _minimal_valid_output(package["generation_unit"])
    output["sections"][0]["blocks"][0]["text"] = "项目负责人具有5年施工管理经验。"

    validation = validate_chapter_output(output, package)

    assert validation["blocking"] is True
    assert any(issue["type"] == "parameter_conflict_residual" for issue in validation["issues"])


def test_run_chapter_generation_records_model_json_repair_metadata():
    package = _package()
    _relax_expanded_targets(package)
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["task_type"])
        unit = llm_input["generation_unit"]
        if llm_input["task_type"] == "repair_json_syntax_only":
            return json.dumps(_valid_output(unit), ensure_ascii=False)
        return '{"title": "未闭合"'

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    task = result.tasks[0]
    assert result.completed_count == 1
    assert calls == ["generate_technical_bid_chapter", "repair_json_syntax_only"]
    assert task.repair_attempt_count == 1
    assert task.repair_summary["method"] == "model"
    assert task.validation["json_repair_method"] == "model"


def test_run_chapter_generation_classifies_json_repair_failure():
    package = _package()
    _relax_expanded_targets(package)

    def fake_llm(_llm_input, _config):
        return '{"title": "未闭合"'

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    task = result.tasks[0]
    assert result.failed_count == 1
    assert task.failure_type == "json_parse_failed"
    assert "未闭合" in task.output_text
    assert task.failure_reason


def test_run_chapter_generation_skips_without_api_key_and_callable():
    result = run_chapter_generation(
        [_package()],
        llm_config_override=_config(api_key=None),
    )

    assert result.skipped_count == 1
    assert result.completed_count == 0
    assert result.failed_count == 0
    assert "API_KEY 未配置" in result.warnings[0]


def test_validate_chapter_output_warns_for_process_words():
    package = _package()
    output = _valid_output(package["generation_unit"])
    output["sections"][0]["blocks"][0]["text"] = "本章参考优秀标书进行编制。"

    validation = validate_chapter_output(output, package)

    assert validation["valid"] is True
    assert any(issue["type"] == "forbidden_content_risk" for issue in validation["issues"])


def test_validate_chapter_output_normalizes_image_slots():
    package = _package()
    _relax_expanded_targets(package)
    output = _minimal_valid_output(package["generation_unit"])
    output["image_slots"] = [
        {
            "section_heading": "钢筋工程施工",
            "anchor_text": "钢筋加工与连接",
            "intent": "钢筋加工、连接、绑扎流程示意图",
            "preferred_type": "施工工艺示意图",
            "min_count": "2",
            "max_count": "6",
            "group_preferred": True,
        }
    ]

    validation = validate_chapter_output(output, package)

    assert validation["valid"] is True
    assert output["image_slots"][0]["min_count"] == 2
    assert output["image_slots"][0]["max_count"] == 6
    assert output["image_slots"][0]["group_preferred"] is True


def test_validate_chapter_output_warns_for_invalid_image_slot():
    package = _package()
    _relax_expanded_targets(package)
    output = _minimal_valid_output(package["generation_unit"])
    output["image_slots"] = [{"section_heading": "", "intent": "", "min_count": 3, "max_count": 1}]

    validation = validate_chapter_output(output, package)

    assert validation["valid"] is True
    issue_types = {issue["type"] for issue in validation["issues"]}
    assert "image_slot_missing_intent" in issue_types
    assert "image_slot_missing_section_heading" in issue_types


def test_validate_chapter_output_blocks_image_slots_for_completeness_statement():
    package = _technical_bid_completeness_package()
    output = _technical_bid_completeness_output(package["generation_unit"])
    output["image_slots"] = [{"section_heading": "技术标响应范围", "intent": "响应关系示意图"}]

    validation = validate_chapter_output(output, package)

    assert validation["valid"] is False
    assert any(issue["type"] == "technical_bid_completeness_image_slots" for issue in validation["issues"])


def test_history_trace_scan_reports_residual_excellent_bid_project_name():
    package = _package()
    package["generation_constraints"]["history_trace_scan"] = {
        "enabled": True,
        "current_project_values": ["示例项目", "示例地点"],
        "candidate_terms": ["郑轨云庭01标段技术标投标文件", "示例项目"],
    }
    output = _valid_output(package["generation_unit"])
    output["sections"][0]["blocks"][0]["text"] = "郑轨云庭01标段技术标投标文件采用成熟进度管理经验。"

    validation = validate_chapter_output(output, package)

    assert validation["valid"] is True
    messages = [issue["message"] for issue in validation["issues"] if issue["type"] == "history_trace_residual"]
    assert messages
    assert "郑轨云庭01标段技术标投标文件" in messages[0]
    assert "示例项目" not in messages[0]


def test_llm_input_keeps_expanded_policy_and_reuse_levels():
    package = _package()
    captured = {}

    def fake_llm(llm_input, _config):
        captured.update(llm_input)
        unit = llm_input["generation_unit"]
        return json.dumps(_valid_output(unit), ensure_ascii=False)

    run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert captured["expanded_generation_policy"]["mode"] == "expanded"
    assert captured["generation_constraints"]["generation_mode"] == "expanded"
    assert "chapter_reuse_profile" in captured
    assert "reuse_level_policy" not in captured["generation_constraints"]
    assert "expanded_targets" not in captured["generation_constraints"]
    assert captured["excellent_bid_references"][0]["reuse_level"] == "manual_review"


def test_llm_input_uses_slim_v3_without_verbose_material_fields():
    package = _package()
    package["excellent_bid_references"] = [
        {
            "ref_id": f"REF-{index}",
            "title": f"参考章节{index}",
            "section_path": ["主要施工方案", f"小节{index}"],
            "retrieval_score": 0.9,
            "material_quality": "high",
            "primary_material_source": "body",
            "reuse_level": "rewrite_reuse",
            "reference_excerpt": "措施内容" * 500,
            "do_not_copy": [f"禁止复制{n}" for n in range(10)],
            "debug_payload": "不应进入模型",
        }
        for index in range(8)
    ]
    package["table_references"] = [
        {
            "table_id": f"T-{index}",
            "title": f"控制表{index}",
            "table_type": "measure_table",
            "columns": [{"key": f"col_{n}", "title": f"列{n}", "width": 20} for n in range(8)],
            "row_count": 12,
            "image_count": 2,
            "style_hint": {"has_image_column": True, "border_style": "grid", "debug_color": "blue"},
            "use_policy": "structure_reference",
            "material_quality": "high",
            "rows": [{"cells": {"col_1": "长内容" * 100}}],
        }
        for index in range(8)
    ]
    package["text_image_block_candidates"] = [
        {
            "block_id": f"TIB-{index}",
            "block_type": "table_image_block",
            "title": f"钢筋成熟图文块{index}",
            "section_path": ["主要施工方案", "钢筋工程"],
            "topics": ["钢筋", "质量管理", "安全管理", "模板", "混凝土", "防水", "脚手架"],
            "primary_topic": "钢筋",
            "secondary_topics": ["质量管理", "安全管理"],
            "match_level": "strong",
            "match_confidence": 0.86,
            "match_reasons": ["主主题匹配：钢筋", "标题或题注含目标强词"],
            "risk_flags": [],
            "summary": "钢筋加工、连接、绑扎及验收图文块。" * 80,
            "image_count": 8,
            "image_group_count": 1,
            "table_count": 2,
            "captions": [f"钢筋示意图{n}" for n in range(12)],
            "reuse_level": "parameterized_reuse",
            "project_specific_risk": "medium",
            "use_policy": "whole_block_preferred",
            "render_policy": {"preserve_image_order": True, "preserve_image_groups": True},
            "retrieval_score": 9.8,
            "image_asset_ids": ["不应进入模型"],
            "image_group_ids": ["不应进入模型"],
            "full_blocks": [{"type": "image_ref", "image_id": "不应进入模型"}],
        }
        for index in range(8)
    ]
    package["image_candidates"] = [
        {
            "image_id": f"IMG-{index}",
            "caption": f"钢筋加工示意图{index}",
            "semantic_text": "钢筋加工、弯曲成型、堆放标识",
            "tags": ["钢筋", "加工", "成型", "堆放", "标识"],
            "bound_section": "钢筋加工",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "image_group_id": "G-1",
            "group_title": "钢筋加工示意图",
            "group_member_index": index + 1,
            "group_member_count": 8,
            "must_keep_with_group": True,
            "image_form": "photo",
            "fit_level": "high",
            "caption_candidates": ["不应进入模型"],
            "nearby_text": "冗余上下文" * 200,
            "notes": "调试说明" * 100,
            "part_name": "word/media/image1.png",
        }
        for index in range(8)
    ]
    package["reuse_warnings"] = [f"风险提示{index}" + "很长" * 100 for index in range(8)]

    llm_input = _llm_input(package)

    assert llm_input["llm_input_schema_version"] == "chapter_llm_input_v1"
    assert llm_input["llm_input_profile"] == "slim_v3"
    assert len(llm_input["excellent_bid_references"]) == 6
    assert len(llm_input["excellent_bid_references"][0]["do_not_copy"]) == 6
    assert "debug_payload" not in llm_input["excellent_bid_references"][0]
    assert len(llm_input["table_references"]) == 6
    assert len(llm_input["table_references"][0]["columns"]) == 6
    assert set(llm_input["table_references"][0]["columns"][0]) == {"key", "title"}
    assert llm_input["table_references"][0]["style_hint"] == {"has_image_column": True, "border_style": "grid"}
    assert "rows" not in llm_input["table_references"][0]
    assert len(llm_input["text_image_block_candidates"]) == 5
    assert llm_input["text_image_block_candidates"][0]["block_id"] == "TIB-0"
    assert llm_input["text_image_block_candidates"][0]["primary_topic"] == "钢筋"
    assert llm_input["text_image_block_candidates"][0]["match_level"] == "strong"
    assert llm_input["text_image_block_candidates"][0]["match_reasons"]
    assert len(llm_input["text_image_block_candidates"][0]["topics"]) == 6
    assert len(llm_input["text_image_block_candidates"][0]["summary"]) <= 520
    assert len(llm_input["text_image_block_candidates"][0]["captions"]) == 8
    assert "image_asset_ids" not in llm_input["text_image_block_candidates"][0]
    assert "image_group_ids" not in llm_input["text_image_block_candidates"][0]
    assert "full_blocks" not in llm_input["text_image_block_candidates"][0]
    assert len(llm_input["image_candidates"]) == 6
    assert len(llm_input["image_candidates"][0]["tags"]) == 4
    assert llm_input["image_candidates"][0]["image_group"] == "grouped"
    assert llm_input["image_candidates"][0]["must_keep_with_group"] is True
    assert "image_id" not in llm_input["image_candidates"][0]
    assert "image_asset_id" not in llm_input["image_candidates"][0]
    assert "canonical_image_id" not in llm_input["image_candidates"][0]
    assert "caption_candidates" not in llm_input["image_candidates"][0]
    assert "nearby_text" not in llm_input["image_candidates"][0]
    assert "notes" not in llm_input["image_candidates"][0]
    assert "part_name" not in llm_input["image_candidates"][0]
    assert len(llm_input["reuse_warnings"]) == 5
    assert len(llm_input["reuse_warnings"][0]) <= 160
    assert llm_input["table_references_slim"] == llm_input["table_references"]
    assert llm_input["image_candidates_slim"][0]["image_id"] == "IMG-0"
    assert llm_input["image_candidates_slim"][0]["image_group_id"] == "G-1"
    assert "image_id" not in llm_input["image_candidates"][0]
    assert llm_input["llm_input_metrics"]["full_package_char_count"] > llm_input["llm_input_metrics"]["llm_input_char_count"]
    assert llm_input["llm_input_metrics"]["text_image_block_count"] == 5
    assert "text_image_block_candidates.image_asset_ids" in llm_input["llm_input_metrics"]["dropped_fields"]
    assert "image_candidates.nearby_text" in llm_input["llm_input_metrics"]["dropped_fields"]
    call_payload = _llm_call_payload(llm_input)
    assert "llm_input_metrics" not in call_payload
    assert "table_references_slim" not in call_payload
    assert "image_candidates_slim" not in call_payload
    assert call_payload["table_references"] == llm_input["table_references"]
    assert call_payload["image_candidates"] == llm_input["image_candidates"]


def test_llm_input_splits_image_groups_from_single_image_candidates():
    package = _package()
    group_members = [
        {
            "image_id": f"IMG-G-{index}",
            "image_asset_id": f"ASSET-G-{index}",
            "canonical_image_id": f"CANON-G-{index}",
            "caption": f"钢筋加工流程图{index}",
            "semantic_text": "钢筋加工、弯曲成型、半成品堆放",
            "group_member_index": index,
            "group_member_count": 3,
            "part_name": f"word/media/group{index}.png",
            "sha256": "hash",
        }
        for index in range(1, 4)
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "GROUP-STEEL",
            "group_title": "钢筋加工示意图",
            "semantic_text": "钢筋加工流程与半成品控制套图",
            "member_count": 3,
            "members": group_members,
            "captions": [member["caption"] for member in group_members],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "fit_level": "high",
            "debug_context": "不应进入模型",
        }
    ]
    package["image_candidate_pool"] = [
        *group_members,
        {
            "image_id": "IMG-SINGLE",
            "image_asset_id": "ASSET-SINGLE",
            "caption": "钢筋堆放标识牌",
            "semantic_text": "钢筋堆放与标识管理",
            "tags": ["钢筋", "堆放", "标识"],
            "bound_section": "钢筋加工",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/single.png",
        },
    ]

    llm_input = _llm_input(package)

    assert llm_input["image_groups_slim"][0]["group_title"] == "钢筋加工示意图"
    assert llm_input["image_groups_slim"][0]["image_count"] == 3
    assert "image_group_id" not in llm_input["image_groups_slim"][0]
    assert "members" not in llm_input["image_groups_slim"][0]
    assert [item["image_id"] for item in llm_input["image_candidates_slim"]] == ["IMG-SINGLE"]
    assert "image_group_candidate_pool" in llm_input["llm_input_metrics"]["dropped_fields"]
    assert "image_candidate_pool" in llm_input["llm_input_metrics"]["dropped_fields"]


def test_llm_input_metrics_show_compression_for_heavy_material_package():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": f"IMG-{index}",
            "caption": f"施工做法示意图{index}",
            "semantic_text": "模板支撑、加固节点、检查验收",
            "tags": ["模板", "支撑", "节点"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/image{index}.png",
            "sha256": "a" * 64,
            "perceptual_hash": "b" * 16,
            "nearby_text": "表格上下文" * 500,
            "caption_candidates": ["候选题注"] * 20,
            "notes": "调试说明" * 300,
        }
        for index in range(20)
    ]

    llm_input = _llm_input(package)
    metrics = llm_input["llm_input_metrics"]

    assert metrics["full_package_char_count"] > metrics["llm_input_char_count"]
    assert metrics["compression_ratio"] < 1


def test_validate_chapter_output_warns_when_expanded_volume_not_met():
    package = _package()
    output = _valid_output(package["generation_unit"])

    validation = validate_chapter_output(output, package)

    issue_types = {issue["type"] for issue in validation["issues"]}
    assert validation["valid"] is True
    assert "expanded_min_sections_not_met" in issue_types
    assert "expanded_min_paragraphs_soft_gap" in issue_types
    assert "expanded_min_tables_not_met" in issue_types
    assert "expanded_section_paragraphs_not_met" not in issue_types


def test_validate_chapter_output_does_not_count_minor_total_paragraph_gap():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    package["expanded_generation_policy"]["targets"]["min_paragraphs_total"] = 11

    validation = validate_chapter_output(output, package)

    issue_types = {issue["type"] for issue in validation["issues"]}
    assert "expanded_min_paragraphs_soft_gap" in issue_types
    assert validation["issue_count"] == 0
    assert validation["advisory_issue_count"] == 1


def test_validate_chapter_output_accepts_sections_with_tables_or_images_as_dense_enough():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    package["expanded_generation_policy"]["targets"]["min_paragraphs_per_section"] = 3
    for section in output["sections"]:
        section["blocks"] = section["blocks"][:2]
        section["blocks"].append(
            {
                "type": "rich_table",
                "title": "控制表",
                "columns": [{"key": "col_1", "title": "序号"}],
                "rows": [{"cells": {"col_1": "1"}} for _ in range(4)],
            }
        )

    validation = validate_chapter_output(output, package)

    assert not any(issue["type"] == "expanded_section_paragraphs_not_met" for issue in validation["issues"])


def test_run_chapter_generation_keeps_initial_draft_when_expanded_retry_fails():
    package = _package()
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["task_type"])
        if llm_input["task_type"] == "expand_existing_technical_bid_chapter":
            raise ValueError("retry response text missing")
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == ["generate_technical_bid_chapter", "expand_existing_technical_bid_chapter"]
    assert result.completed_count == 1
    assert result.failed_count == 0
    assert result.chapters[0]["unit_id"] == "GU-N1"
    validation = result.tasks[0].validation
    assert validation["expanded_retry_attempted"] is True
    assert validation["expanded_retry_accepted"] is False
    assert "retry response text missing" in validation["expanded_retry_error"]
    assert any(issue["type"] == "expanded_retry_failed" for issue in validation["issues"])


def test_validate_chapter_output_warns_for_missing_reusable_image_ref():
    package = _package()
    package["image_candidates"] = [
        {
            "image_id": "IMG-001",
            "caption": "施工进度计划编制依据与原则",
            "bound_section": "施工进度计划编制依据与原则",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "施工进度计划编制依据与原则", "confidence": 0.9}
            ],
            "semantic_text": "施工进度计划编制依据与原则",
            "semantic_confidence": 0.9,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image1.png",
        }
    ]
    package["expanded_generation_policy"]["targets"]["min_image_refs"] = 1
    output = _expanded_output(package["generation_unit"])

    validation = validate_chapter_output(output, package)

    assert any(issue["type"] == "expanded_reusable_images_not_used" for issue in validation["issues"])


def test_validate_chapter_output_warns_for_invalid_image_ref():
    package = _package()
    package["image_candidates"] = [
        {
            "image_id": "IMG-001",
            "caption": "施工进度计划编制依据与原则",
            "bound_section": "施工进度计划编制依据与原则",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "施工进度计划编制依据与原则", "confidence": 0.9}
            ],
            "semantic_text": "施工进度计划编制依据与原则",
            "semantic_confidence": 0.9,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image1.png",
        },
        {
            "image_id": "IMG-002",
            "caption": "需复核图片",
            "reuse_level": "manual_review",
            "risk_level": "high",
            "part_name": "word/media/image2.png",
        },
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append({"type": "image_ref", "image_id": "IMG-002", "caption": "错误引用"})
    output["sections"][1]["blocks"].append({"type": "image_ref", "image_id": "IMG-999", "caption": "未知引用"})

    validation = validate_chapter_output(output, package)
    issue_types = {issue["type"] for issue in validation["issues"]}

    assert "image_ref_requires_manual_review" in issue_types
    assert "image_ref_unknown" in issue_types


def test_validate_technical_bid_completeness_rejects_construction_template_and_images():
    package = _technical_bid_completeness_package()
    output = _technical_bid_completeness_output(package["generation_unit"])
    output["sections"][0]["heading"] = "项目概况"
    output["sections"][0]["blocks"].append({"type": "image_placeholder", "caption": "施工总平面图"})

    validation = validate_chapter_output(output, package)
    issue_types = {issue["type"] for issue in validation["issues"]}

    assert validation["valid"] is False
    assert "technical_bid_completeness_construction_template" in issue_types
    assert "technical_bid_completeness_visual_block" in issue_types


def test_validate_technical_bid_completeness_accepts_response_statement():
    package = _technical_bid_completeness_package()
    output = _technical_bid_completeness_output(package["generation_unit"])

    validation = validate_chapter_output(output, package)
    issue_types = {issue["type"] for issue in validation["issues"]}

    assert validation["valid"] is True
    assert "technical_bid_completeness_construction_template" not in issue_types
    assert "technical_bid_completeness_visual_block" not in issue_types


def test_llm_input_for_technical_bid_completeness_removes_material_pools():
    package = _technical_bid_completeness_package()
    package["image_candidate_pool"] = [{"image_id": "IMG-1", "caption": "不应进入模型"}]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "GROUP-1",
            "members": [{"image_id": "IMG-G1"}, {"image_id": "IMG-G2"}],
            "reuse_level": "candidate_reuse",
        }
    ]
    package["table_references"] = [{"table_id": "T-1", "title": "不应进入模型"}]
    package["excellent_bid_references"] = [{"ref_id": "R-1", "reference_excerpt": "不应进入模型"}]

    llm_input = _llm_input(package)

    assert llm_input["table_references"] == []
    assert llm_input["image_candidates_slim"] == []
    assert llm_input["image_groups_slim"] == []
    assert llm_input["excellent_bid_references"] == []


def test_filter_mismatched_image_refs_removes_unknown_and_manual_review_refs():
    package = _package()
    package["image_candidates"] = [
        {
            "image_id": "IMG-001",
            "caption": "施工进度计划编制依据与原则",
            "bound_section": "施工进度计划编制依据与原则",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "施工进度计划编制依据与原则", "confidence": 0.9}
            ],
            "semantic_text": "施工进度计划编制依据与原则",
            "semantic_confidence": 0.9,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image1.png",
        },
        {
            "image_id": "IMG-002",
            "caption": "需复核图片",
            "reuse_level": "manual_review",
            "risk_level": "high",
            "part_name": "word/media/image2.png",
        },
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].extend(
        [
            {"type": "image_ref", "image_id": "IMG-001", "caption": "施工进度计划编制依据与原则"},
            {"type": "image_ref", "image_id": "IMG-002", "caption": "需复核图片"},
            {"type": "image_ref", "image_id": "IMG-999", "caption": "模型编造图片"},
        ]
    )

    result = filter_mismatched_image_refs(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-001"]
    assert result["image_ref_filter"]["removed_count"] == 2
    assert {item["reason"] for item in result["image_ref_filter"]["removed"]} == {
        "unknown_or_manual_review_image_ref",
        "manual_review_image_ref",
    }


def test_validate_chapter_output_accepts_reusable_group_member_image_refs():
    package = _package()
    members = [
        {
            "image_id": f"IMG-GROUP-{index}",
            "image_asset_id": f"ASSET-GROUP-{index}",
            "caption": f"施工进度做法{index}",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/group{index}.png",
            "image_group_id": "GROUP-1",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index in range(1, 3)
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "GROUP-1",
            "group_title": "施工进度计划编制依据与原则",
            "semantic_sources": [
                {"source_type": "group_title", "text": "施工进度计划编制依据与原则", "confidence": 0.92}
            ],
            "semantic_text": "施工进度计划编制依据与原则",
            "semantic_confidence": 0.92,
            "source_section_path": ["施工进度表", "施工进度计划编制依据与原则"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": members,
            "must_keep_together": True,
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].extend(
        [
            {"type": "image_ref", "image_id": "IMG-GROUP-1", "caption": "施工进度做法1"},
            {"type": "image_ref", "image_id": "IMG-GROUP-2", "caption": "施工进度做法2"},
        ]
    )

    validation = validate_chapter_output(output, package)

    assert not any(issue["type"] == "image_ref_unknown" for issue in validation["issues"])


def test_normalize_chapter_identity_restores_input_ids():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["unit_id"] = "模型改写的ID"
    output["target_node_id"] = "模型改写的节点"
    output["chapter_path"] = ["模型改写的章节"]

    normalized = normalize_chapter_identity(output, package)

    assert normalized["unit_id"] == package["generation_unit"]["unit_id"]
    assert normalized["target_node_id"] == package["generation_unit"]["target_node_id"]
    assert normalized["chapter_path"] == package["generation_unit"]["chapter_path"]
    assert validate_chapter_output(normalized, package)["blocking"] is False


def test_enrich_image_refs_adds_rendering_metadata():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-001",
            "caption": "标准化防护做法",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image1.png",
            "material_slice_id": "SRC0001-M00001",
            "source_bid_id": "SRC0001",
            "source_slice_id": "S1",
            "bound_table_id": "T1",
            "bound_row_id": 2,
            "bound_cell_key": "col_3",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append({"type": "image_ref", "image_id": "IMG-001", "caption": "标准化防护做法"})

    enriched = enrich_image_refs(output, package)
    image_ref = enriched["sections"][0]["blocks"][-1]

    assert image_ref["source_part_name"] == "word/media/image1.png"
    assert image_ref["material_slice_id"] == "SRC0001-M00001"
    assert image_ref["source_bid_id"] == "SRC0001"
    assert image_ref["bound_cell_key"] == "col_3"
    assert enriched["image_ref_enrichment"]["enriched_count"] == 1


def test_enrich_image_refs_replaces_llm_caption_with_trusted_semantic_caption():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00068-IMG0000",
            "image_id": "IMG-STEEL-BENDING",
            "caption": "钢筋弯曲",
            "semantic_sources": [
                {"source_type": "below_cell_caption", "text": "钢筋弯曲", "confidence": 0.92}
            ],
            "semantic_text": "钢筋弯曲",
            "semantic_confidence": 0.92,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image35.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append(
        {
            "type": "image_ref",
            "image_id": "IMG-STEEL-BENDING",
            "caption": "钢筋直螺纹套筒连接施工示意图",
        }
    )

    enriched = enrich_image_refs(output, package)
    image_ref = enriched["sections"][0]["blocks"][-1]

    assert image_ref["caption"] == "钢筋弯曲"
    assert image_ref["original_caption"] == "钢筋直螺纹套筒连接施工示意图"
    assert image_ref["caption_source"] == "excellent_bid_image_semantic"
    assert image_ref["semantic_text"] == "钢筋弯曲"
    assert image_ref["semantic_confidence"] == 0.92
    assert image_ref["semantic_sources"][0]["source_type"] == "below_cell_caption"


def test_clean_image_captions_rewrites_weak_table_fragment_caption():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-TEMPLATE",
            "caption": "序号；设计说明",
            "semantic_sources": [
                {"source_type": "same_row_text", "text": "框架柱模板支设流程", "confidence": 0.74}
            ],
            "semantic_text": "框架柱模板支设流程",
            "semantic_confidence": 0.74,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/template.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "模板工程选型设计及支撑体系",
            "level": 3,
            "blocks": [{"type": "image_ref", "image_id": "IMG-TEMPLATE", "caption": "序号；设计说明"}],
        }
    ]

    result = clean_image_captions(enrich_image_refs(output, package), package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "框架柱模板支设流程做法示意图"
    assert image_ref["original_caption"] == "序号；设计说明"
    assert image_ref["caption_before_cleanup"] == "框架柱模板支设流程"
    assert image_ref["caption_source"] == "image_caption_cleanup"


def test_clean_image_captions_rewrites_long_sentence_caption_from_section_context():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "外脚手架搭设及安全防护措施",
            "level": 3,
            "blocks": [
                {
                    "type": "image_ref",
                    "image_id": "IMG-SCAFFOLD",
                    "caption": "立杆顶端栏杆宜高出女儿墙上端1m，宜高出檐口上端1.5m。",
                    "group_semantic_text": "脚手架立杆及连墙件搭设",
                    "group_title": "脚手架立杆及连墙件搭设",
                }
            ],
        }
    ]

    result = clean_image_captions(output, package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "脚手架立杆及连墙件搭设做法示意图"
    assert image_ref["caption_before_cleanup"].startswith("立杆顶端栏杆")


def test_clean_image_captions_rewrites_project_specific_measurement_sentence():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-MEASURE",
            "caption": "序号；主要内容",
            "semantic_sources": [
                {
                    "source_type": "same_row_text",
                    "text": "综合以上几点要素，结合现场情况拟布设K1～K8共个8点位",
                    "confidence": 0.78,
                }
            ],
            "semantic_text": "综合以上几点要素，结合现场情况拟布设K1～K8共个8点位",
            "semantic_confidence": 0.78,
            "bound_section": "工程测量控制网建立及监测方案",
            "source_section_path": ["土建施工方案", "工程测量控制网建立及监测方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/measure.png",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [{"type": "image_ref", "image_id": "IMG-MEASURE", "caption": "序号；主要内容"}],
        }
    ]

    result = clean_image_captions(enrich_image_refs(output, package), package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "测量控制网布设示意图"
    assert "K1" not in image_ref["caption"]
    assert "点位" not in image_ref["caption"]


def test_clean_image_captions_does_not_invent_caption_from_weak_row_item():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-WEAK-CAPTION",
            "caption": "约束条件",
            "semantic_sources": [
                {"source_type": "same_row_item", "text": "约束条件", "confidence": 0.84}
            ],
            "semantic_text": "约束条件",
            "semantic_confidence": 0.84,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/concrete.png",
            "material_slice_id": "SRC0001-M00093",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [{"type": "image_ref", "image_id": "IMG-WEAK-CAPTION", "caption": "约束条件"}],
        }
    ]

    result = clean_image_captions(enrich_image_refs(output, package), package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "约束条件"
    assert image_ref.get("caption_before_cleanup") is None
    assert result.get("image_caption_cleanup") is None


def test_clean_image_captions_keeps_parenthetical_long_caption_complete():
    package = _package()
    long_caption = "大体积混凝土浇筑后温度分布梯度示意图(红、橙、黄、绿、蓝代表温度依次递减)"
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-CONCRETE-TEMP",
            "caption": long_caption,
            "semantic_sources": [
                {"source_type": "embedded_same_cell_caption", "text": long_caption, "confidence": 0.96}
            ],
            "semantic_text": long_caption,
            "semantic_confidence": 0.96,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/concrete-temp.png",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [{"type": "image_ref", "image_id": "IMG-CONCRETE-TEMP", "caption": long_caption}],
        }
    ]

    result = clean_image_captions(enrich_image_refs(output, package), package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == long_caption
    assert image_ref["caption"].endswith(")")


def test_clean_image_captions_removes_path_and_numbering_residue():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "模板工程选型设计及支撑体系",
            "level": 3,
            "blocks": [
                {
                    "type": "image_ref",
                    "image_id": "IMG-TEMPLATE-DESIGN",
                    "caption": "模板选型及设计>2模板设计",
                    "semantic_text": "模板选型及设计>2模板设计",
                    "source_section_path": ["土建施工方案", "模板选型及设计", "2模板设计"],
                }
            ],
        }
    ]

    result = clean_image_captions(output, package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "模板支设做法示意图"
    assert ">" not in image_ref["caption"]
    assert not image_ref["caption"].startswith("2")


def test_clean_image_captions_rewrites_sentence_like_dewatering_caption():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "土方开挖与基坑排水措施",
            "level": 3,
            "blocks": [
                {
                    "type": "image_ref",
                    "image_id": "IMG-DEWATERING",
                    "caption": "如上部填土中水量较大，可用∅48钢管击入土中引水",
                    "semantic_text": "如上部填土中水量较大，可用∅48钢管击入土中引水",
                    "bound_section": "基坑排水引水",
                }
            ],
        }
    ]

    result = clean_image_captions(output, package)
    image_ref = result["sections"][0]["blocks"][0]

    assert image_ref["caption"] == "基坑排水引水做法示意图"
    assert "可用" not in image_ref["caption"]
    assert "击入" not in image_ref["caption"]


def test_auto_image_reuse_inserts_candidate_images_after_llm_output():
    package = _package()
    package["image_candidate_pool"] = [
            {
                "image_id": "IMG-001",
                "caption": "施工进度计划编制依据与原则",
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "施工进度计划编制依据与原则", "confidence": 0.9}
                ],
                "semantic_text": "施工进度计划编制依据与原则",
                "semantic_confidence": 0.9,
                "bound_section": "施工进度计划编制依据与原则",
                "source_section_path": ["安全文明", "施工进度计划编制依据与原则"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image1.png",
            "material_slice_id": "SRC0001-M00001",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "max_auto_image_refs": 3,
    }
    package["expanded_generation_policy"]["targets"]["min_image_refs"] = 1
    output = _expanded_output(package["generation_unit"])

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert len(image_refs) == 1
    assert image_refs[0]["image_id"] == "IMG-001"
    assert image_refs[0]["auto_inserted"] is True
    assert result["auto_image_reuse"]["inserted_count"] == 1
    assert validate_chapter_output(result, package)["valid"] is True


def test_auto_image_reuse_accepts_direct_reuse_images_after_llm_output():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-DIRECT",
            "caption": "进度计划纠偏流程图",
            "semantic_sources": [{"source_type": "same_cell_caption", "text": "进度计划纠偏措施", "confidence": 0.9}],
            "semantic_text": "进度计划纠偏措施",
            "semantic_confidence": 0.9,
            "bound_section": "进度计划纠偏措施",
            "source_section_path": ["工期保证", "进度计划纠偏措施"],
            "reuse_level": "direct_reuse",
            "risk_level": "low",
            "part_name": "word/media/direct.png",
            "material_slice_id": "SRC0001-MDIRECT",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
    }
    package["expanded_generation_policy"]["targets"]["min_image_refs"] = 1
    output = _expanded_output(package["generation_unit"])

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-DIRECT"]
    assert validate_chapter_output(result, package)["valid"] is True


def test_auto_image_reuse_uses_image_slots_before_generic_density_fill():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-STEEL",
            "caption": "钢筋加工连接绑扎流程示意图",
            "semantic_text": "钢筋加工、直螺纹连接、绑扎流程示意图",
            "semantic_confidence": 0.92,
            "bound_section": "钢筋工程施工",
            "source_section_path": ["主要施工方案与技术措施", "钢筋工程施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/steel.png",
            "material_slice_id": "SRC0001-MSTEEL",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-TEMPLATE",
            "caption": "模板支设示意图",
            "semantic_text": "模板支设、加固体系示意图",
            "semantic_confidence": 0.92,
            "bound_section": "模板工程施工",
            "source_section_path": ["主要施工方案与技术措施", "模板工程施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/template.png",
            "material_slice_id": "SRC0001-MTEMPLATE",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程施工",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋加工、连接和绑扎施工控制正文。"}],
        }
    ]
    output["image_slots"] = [
        {
            "section_heading": "钢筋工程施工",
            "anchor_text": "钢筋加工与连接",
            "intent": "钢筋加工、连接、绑扎流程示意图",
            "preferred_type": "施工工艺示意图",
            "min_count": 1,
            "max_count": 1,
            "group_preferred": False,
        }
    ]

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-STEEL"]
    assert result["image_slot_reuse"]["inserted_count"] == 1
    assert result["auto_image_reuse"]["slot_inserted_count"] == 1


def test_auto_image_reuse_prefers_complete_group_for_image_slot():
    package = _package()
    members = [
        {
            "image_id": f"IMG-G-STEEL-{index}",
            "image_asset_id": f"ASSET-G-STEEL-{index}",
            "caption": f"钢筋加工流程示意{index}",
            "semantic_text": "钢筋加工、连接、绑扎流程示意图",
            "semantic_confidence": 0.92,
            "bound_section": "钢筋工程施工",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/steel-group-{index}.png",
            "material_slice_id": "SRC0001-MSTEELGROUP",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "GROUP-STEEL-SLOT",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index in range(1, 3)
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "GROUP-STEEL-SLOT",
            "group_title": "钢筋加工流程示意图",
            "semantic_text": "钢筋加工、连接、绑扎流程示意图",
            "semantic_confidence": 0.92,
            "member_count": 2,
            "members": members,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 4,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程施工",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋加工、连接和绑扎施工控制正文。"}],
        }
    ]
    output["image_slots"] = [
        {
            "section_heading": "钢筋工程施工",
            "anchor_text": "钢筋加工流程",
            "intent": "钢筋加工、连接、绑扎流程示意图",
            "preferred_type": "施工工艺示意图",
            "min_count": 2,
            "max_count": 2,
            "group_preferred": True,
        }
    ]

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-G-STEEL-1", "IMG-G-STEEL-2"]
    assert {block["image_group_id"] for block in image_refs} == {"GROUP-STEEL-SLOT"}
    assert result["image_slot_reuse"]["inserted_group_member_count"] == 2


def test_auto_image_reuse_skips_low_confidence_mismatched_image_slot():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-TEMPLATE",
            "caption": "模板支设示意图",
            "semantic_text": "模板支设、加固体系示意图",
            "semantic_confidence": 0.3,
            "bound_section": "模板工程施工",
            "source_section_path": ["主要施工方案与技术措施", "模板工程施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/template.png",
            "material_slice_id": "SRC0001-MTEMPLATE",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程施工",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋加工、连接和绑扎施工控制正文。"}],
        }
    ]
    output["image_slots"] = [
        {
            "section_heading": "钢筋工程施工",
            "anchor_text": "钢筋加工与连接",
            "intent": "钢筋加工、连接、绑扎流程示意图",
            "preferred_type": "施工工艺示意图",
            "min_count": 1,
            "max_count": 1,
            "group_preferred": False,
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_postprocess_chapter_images_silently_removes_image_placeholders():
    package = _package()
    package["auto_image_reuse_policy"] = {"enabled": False, "allow_placeholders": False}
    output = _expanded_output(package["generation_unit"])

    result = postprocess_chapter_images(output, package)

    assert all(
        block.get("type") != "image_placeholder"
        for section in result["sections"]
        for block in section["blocks"]
    )
    assert result["image_placeholder_filter"]["removed_count"] == 1


def test_auto_image_reuse_fills_to_density_target_not_minimum_only():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": f"IMG-{index:03d}",
            "caption": f"工艺做法图片{index}",
            "bound_section": ["施工进度计划编制依据与原则", "总体施工部署与关键线路分析", "进度计划纠偏措施"][index % 3],
            "semantic_sources": [
                {
                    "source_type": "same_cell_caption",
                    "text": ["施工进度计划编制依据与原则", "总体施工部署与关键线路分析", "进度计划纠偏措施"][index % 3],
                    "confidence": 0.9,
                }
            ],
            "semantic_text": ["施工进度计划编制依据与原则", "总体施工部署与关键线路分析", "进度计划纠偏措施"][index % 3],
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", f"工艺做法图片{index}"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/image{index}.png",
            "material_slice_id": f"SRC0001-M{index:05d}",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
        for index in range(1, 61)
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 3,
        "target_image_refs": 18,
        "max_image_refs_total": 24,
        "max_images_per_section": 6,
    }
    package["expanded_generation_policy"]["targets"]["min_image_refs"] = 3
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append({"type": "image_ref", "image_id": "IMG-001", "caption": "模型已选图片"})

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    section_counts = [
        sum(1 for block in section["blocks"] if block.get("type") == "image_ref")
        for section in result["sections"]
    ]
    assert len(image_refs) == 18
    assert result["auto_image_reuse"]["inserted_count"] == 17
    assert result["auto_image_reuse"]["target_image_refs"] == 18
    assert max(section_counts) <= 6
    assert any(block.get("auto_inserted") for block in image_refs)


def test_auto_image_reuse_skips_images_without_section_semantic_match():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-STEEL",
            "caption": "钢筋加工成型",
            "bound_section": "钢筋加工成型",
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施", "钢筋加工成型"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/steel.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "测量控制正文"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_does_not_fill_concrete_section_with_template_image():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-TEMPLATE",
            "caption": "框架柱模板支设流程图",
            "caption_candidates": ["模板设计"],
            "tags": ["模板", "混凝土"],
            "source_section_path": ["土建施工方案", "模板工程施工方案与技术措施", "模板选型及设计"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/template.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "混凝土浇筑、振捣、测温和养护控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_does_not_fill_concrete_pouring_section_with_precast_block_group():
    package = _package()
    members = [
        {
            "image_id": f"IMG-PRECAST-GROUP-{index}",
            "image_asset_id": f"ASSET-PRECAST-GROUP-{index}",
            "caption": "穿墙套管混凝土预制块",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "穿墙套管混凝土预制块", "confidence": 0.9}
            ],
            "semantic_text": "穿墙套管混凝土预制块",
            "semantic_confidence": 0.9,
            "source_section_path": ["质量管理措施", "砌体工程质量管理措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/precast-group-{index}.png",
            "material_slice_id": "SRC0001-M00443",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00443-G0103",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index in range(1, 3)
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00443-G0103",
            "group_title": "穿墙套管混凝土预制块",
            "semantic_sources": [
                {"source_type": "group_title", "text": "穿墙套管混凝土预制块", "confidence": 0.92}
            ],
            "semantic_text": "穿墙套管混凝土预制块",
            "semantic_confidence": 0.92,
            "source_section_path": ["质量管理措施", "砌体工程质量管理措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": members,
            "material_slice_id": "SRC0001-M00443",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 2,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "混凝土浇筑、振捣、测温、温控和养护控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_covers_waterproof_and_post_pour_empty_sections():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-WATERPROOF",
            "caption": "阴角防水细部做法",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "地下室阴角防水细部做法", "confidence": 0.9}
            ],
            "semantic_text": "地下室阴角防水细部做法",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施", "地下室防水施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof.png",
            "material_slice_id": "SRC0001-M00095",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-POST-POUR",
            "caption": "基础底板后浇带防水构造",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "基础底板后浇带防水止水构造", "confidence": 0.9}
            ],
            "semantic_text": "基础底板后浇带防水止水构造",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "后浇带专项施工方案与技术措施", "后浇带方案设计概况"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/post-pour.png",
            "material_slice_id": "SRC0001-M00120",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 2,
        "max_images_per_section": 2,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "地下室及屋面防水施工技术",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "地下室防水、屋面防水、阴角附加层和卷材搭接控制正文。"}],
        },
        {
            "heading": "后浇带及变形缝处理专项方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "后浇带、变形缝、止水钢板和施工缝处理控制正文。"}],
        },
    ]

    result = apply_auto_image_reuse(output, package)
    refs_by_heading = {
        section["heading"]: [block["image_id"] for block in section["blocks"] if block.get("type") == "image_ref"]
        for section in result["sections"]
    }

    assert refs_by_heading["地下室及屋面防水施工技术"] == ["IMG-WATERPROOF"]
    assert refs_by_heading["后浇带及变形缝处理专项方案"] == ["IMG-POST-POUR"]


def test_auto_image_reuse_allows_matching_primary_topic():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-TEMPLATE",
            "caption": "框架柱模板支设流程图",
            "caption_candidates": ["模板设计"],
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "模板选型支模体系满堂架控制", "confidence": 0.9}
            ],
            "semantic_text": "模板选型支模体系满堂架控制",
            "semantic_confidence": 0.9,
            "tags": ["模板", "混凝土"],
            "source_section_path": ["土建施工方案", "模板工程施工方案与技术措施", "模板选型及设计"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/template.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "模板工程选型设计及支撑体系",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "模板选型、支模体系、对拉螺栓和满堂架控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert len(image_refs) == 1
    assert image_refs[0]["image_id"] == "IMG-TEMPLATE"


def test_auto_image_reuse_allows_same_topic_when_source_section_title_differs():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-MEASURE",
            "caption": "楼层内控点预留洞口及激光引测示意图",
            "bound_section": "2 地上施工测量控制",
            "semantic_sources": [
                {"source_type": "below_cell_caption", "text": "楼层内控点预留洞口及激光引测示意图", "confidence": 0.92}
            ],
            "semantic_text": "楼层内控点预留洞口及激光引测示意图",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案", "场区施工测量", "2 地上施工测量控制"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/measure.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "建立平面控制网，采用内控点、激光铅垂仪和轴线监测进行复核。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-MEASURE"]


def test_auto_image_reuse_uses_structured_semantic_caption_and_deduplicates_legacy_id():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00068-IMG0000",
            "image_id": "EBIMG_SRC0001_SRC0001_M00068_rId49_68_0_0",
            "caption": "钢筋加工成型做法示意",
            "caption_candidates": ["钢筋弯曲", "箍筋加工控制"],
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "钢筋弯曲", "confidence": 0.9},
                {"source_type": "section_heading", "text": "钢筋加工成型", "confidence": 0.46},
            ],
            "semantic_text": "钢筋弯曲",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施", "钢筋加工成型"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image35.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 2,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "钢筋加工成型、箍筋制作和安装连接控制正文。"},
                {
                    "type": "image_ref",
                    "image_id": "EBIMG_SRC0001_M00068_rId49_68_0_0",
                    "caption": "钢筋直螺纹套筒连接施工示意图",
                    "source_part_name": "word/media/image35.png",
                },
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert len(image_refs) == 1
    assert image_refs[0]["source_part_name"] == "word/media/image35.png"


def test_auto_image_reuse_generated_caption_prefers_structured_semantic_text():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00077-IMG0011",
            "image_id": "IMG-WALL-TEMPLATE",
            "caption": "模板设计做法示意",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "剪力墙模板支设平立剖面设计节点图", "confidence": 0.9}
            ],
            "semantic_text": "剪力墙模板支设平立剖面设计节点图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "模板工程施工方案与技术措施", "模板选型及设计"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image93.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "模板工程选型设计及支撑体系",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "模板选型、支模体系、墙柱梁板节点控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert len(image_refs) == 1
    assert image_refs[0]["caption"] == "剪力墙模板支设平立剖面设计节点图"


def test_auto_image_reuse_rejects_process_images_for_elevator_section():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-STEEL-GROUP",
            "caption": "绑扎楼板钢筋",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "绑扎楼板钢筋", "confidence": 0.9}
            ],
            "semantic_text": "绑扎楼板钢筋",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程制作安装及连接技术"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/steel.png",
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-MEASURE-GROUP",
            "caption": "内控点预留洞口安置示意图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "内控点预留洞口安置示意图", "confidence": 0.9}
            ],
            "semantic_text": "内控点预留洞口安置示意图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量控制网建立及监测方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/measure.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 2,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "电梯井道验收及预埋件复核",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "电梯井道、导轨、层门和预埋件复核控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_allows_elevator_image_for_elevator_section():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-ELEVATOR",
            "caption": "电梯导轨安装校正示意图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "电梯导轨安装校正示意图", "confidence": 0.9}
            ],
            "semantic_text": "电梯导轨安装校正示意图",
            "semantic_confidence": 0.9,
            "source_section_path": ["电梯工程施工方案", "导轨安装及轿厢组装工艺"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/elevator.png",
            "material_slice_id": "SRC0001-M00688",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "导轨安装及轿厢组装工艺",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "电梯导轨安装、轿厢组装和层门安装控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-ELEVATOR"]


def test_auto_image_reuse_rejects_process_image_for_management_goal_section():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-REBAR",
            "caption": "竖向梯子筋应用示意",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "竖向梯子筋应用示意", "confidence": 0.9}
            ],
            "semantic_text": "竖向梯子筋应用示意",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程制作安装及连接技术"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/rebar.png",
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程质量目标及承诺",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "项目管理目标、质量目标、责任分工和检查闭环正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_skips_section_heading_only_image_semantics():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-WEAK-TEMPLATE",
            "caption": "模板设计",
            "semantic_sources": [
                {"source_type": "section_heading", "text": "2 模板设计", "confidence": 0.46}
            ],
            "semantic_text": "2 模板设计",
            "semantic_confidence": 0.46,
            "source_section_path": ["土建施工方案", "模板选型及设计", "2 模板设计"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/weak-template.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "模板工程选型设计及支撑体系",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "模板选型、支模体系、墙柱梁板节点控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_inserts_complete_image_group_and_skips_single_members():
    package = _package()
    members = [
        {
            "image_id": f"IMG-GROUP-{index}",
            "image_asset_id": f"ASSET-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/group{index}.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00068-G0000",
            "group_title": "钢筋加工示意图",
            "group_semantic_text": "钢筋加工示意图",
            "group_member_index": index,
            "group_member_count": 3,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["钢筋调直", "钢筋切断", "钢筋弯曲"], start=1)
    ]
    package["image_candidate_pool"] = members
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00068-G0000",
            "group_title": "钢筋加工示意图",
            "caption": "钢筋加工示意图",
            "semantic_sources": [
                {"source_type": "group_title", "text": "钢筋加工示意图", "confidence": 0.92}
            ],
            "semantic_text": "钢筋加工示意图",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 3,
            "members": members,
            "captions": ["钢筋调直", "钢筋切断", "钢筋弯曲"],
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 3,
        "max_image_refs_total": 6,
        "max_images_per_section": 6,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "钢筋加工示意图包括调直、切断、弯曲等加工流程控制。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-GROUP-1", "IMG-GROUP-2", "IMG-GROUP-3"]
    assert {block["image_group_id"] for block in image_refs} == {"SRC0001-M00068-G0000"}
    assert [block["group_member_index"] for block in image_refs] == [1, 2, 3]
    assert result["auto_image_reuse"]["inserted_group_count"] == 1


def test_auto_image_reuse_completes_llm_selected_partial_image_group():
    package = _package()
    members = [
        {
            "image_id": f"IMG-GROUP-{index}",
            "image_asset_id": f"ASSET-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "基坑降水方案及措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/group{index}.png",
            "material_slice_id": "SRC0001-M00055",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00055-G0002",
            "group_title": "基坑明排水做法",
            "group_semantic_text": "基坑明排水做法",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["基坑明排水做法一", "基坑明排水做法二"], start=1)
    ]
    package["image_candidate_pool"] = members
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00055-G0002",
            "group_title": "基坑明排水做法",
            "semantic_sources": [
                {"source_type": "group_title", "text": "基坑明排水做法", "confidence": 0.92}
            ],
            "semantic_text": "基坑明排水做法",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "基坑降水方案及措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": members,
            "captions": ["基坑明排水做法一", "基坑明排水做法二"],
            "material_slice_id": "SRC0001-M00055",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 2,
        "max_images_per_section": 2,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "土方开挖及基坑支护施工方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "基坑开挖、支护、降水和明排水施工控制。"},
                {
                    "type": "image_ref",
                    "image_id": "IMG-GROUP-1",
                    "image_asset_id": "ASSET-GROUP-1",
                    "caption": "基坑明排水做法一",
                    "image_group_id": "SRC0001-M00055-G0002",
                    "group_member_index": 1,
                    "group_member_count": 2,
                    "material_slice_id": "SRC0001-M00055",
                    "must_keep_with_group": True,
                },
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-GROUP-1", "IMG-GROUP-2"]
    assert [block["group_member_index"] for block in image_refs] == [1, 2]
    assert image_refs[1]["auto_completed_group"] is True
    assert result["auto_image_reuse"]["completed_existing_group_count"] == 1


def test_auto_image_reuse_skips_same_material_single_image_after_group_inserted():
    package = _package()
    members = [
        {
            "image_id": f"IMG-GROUP-{index}",
            "image_asset_id": f"ASSET-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/group{index}.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00030-G0000",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["内控点预留洞口安置示意图", "第一次接收激光点"], start=1)
    ]
    package["image_candidate_pool"] = [
        *members,
        {
            "image_id": "IMG-SINGLE-SAME-MATERIAL",
            "image_asset_id": "ASSET-SINGLE-SAME-MATERIAL",
            "caption": "内控点预留洞口安置示意图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "内控点预留洞口安置示意图", "confidence": 0.9}
            ],
            "semantic_text": "内控点预留洞口安置示意图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/single.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00030-G0000",
            "group_title": "平面控制网的引测与精度控制措施",
            "semantic_sources": [
                {"source_type": "group_title", "text": "平面控制网的引测与精度控制措施", "confidence": 0.92}
            ],
            "semantic_text": "平面控制网的引测与精度控制措施",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": members,
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 3,
        "max_image_refs_total": 3,
        "max_images_per_section": 5,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "平面控制网引测和内控点预留洞口控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-GROUP-1", "IMG-GROUP-2"]
    assert "IMG-SINGLE-SAME-MATERIAL" not in {block["image_id"] for block in image_refs}


def test_auto_image_reuse_removes_llm_chosen_same_material_single_after_group_inserted():
    package = _package()
    members = [
        {
            "image_id": f"IMG-GROUP-{index}",
            "image_asset_id": f"ASSET-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/group{index}.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00030-G0000",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["内控点预留洞口安置示意图", "第一次接收激光点"], start=1)
    ]
    package["image_candidate_pool"] = [
        *members,
        {
            "image_id": "IMG-SINGLE-SAME-MATERIAL",
            "image_asset_id": "ASSET-SINGLE-SAME-MATERIAL",
            "caption": "内控点预留洞口安置示意图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "内控点预留洞口安置示意图", "confidence": 0.9}
            ],
            "semantic_text": "内控点预留洞口安置示意图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/single.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00030-G0000",
            "group_title": "平面控制网的引测与精度控制措施",
            "semantic_sources": [
                {"source_type": "group_title", "text": "平面控制网的引测与精度控制措施", "confidence": 0.92}
            ],
            "semantic_text": "平面控制网的引测与精度控制措施",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": members,
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 3,
        "max_image_refs_total": 3,
        "max_images_per_section": 5,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "平面控制网引测和内控点预留洞口控制正文。"},
                {"type": "image_ref", "image_id": "IMG-SINGLE-SAME-MATERIAL", "caption": "模型已选散图"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-GROUP-1", "IMG-GROUP-2"]
    assert "IMG-SINGLE-SAME-MATERIAL" not in {block["image_id"] for block in image_refs}


def test_auto_image_reuse_keeps_large_group_complete_even_over_section_soft_limit():
    package = _package()
    members = [
        {
            "image_id": f"IMG-FLOW-{index}",
            "image_asset_id": f"ASSET-FLOW-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施", "钢筋安装流程"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/flow{index}.png",
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00069-GFLOW",
            "group_member_index": index,
            "group_member_count": 4,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["封梁侧模板", "铺板底模板并弹板筋控制线", "绑扎楼板钢筋", "混凝土浇筑"], start=1)
    ]
    package["image_candidate_pool"] = members
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00069-GFLOW",
            "group_title": "钢筋安装流程示意图",
            "semantic_sources": [
                {"source_type": "group_title", "text": "钢筋安装流程示意图", "confidence": 0.92}
            ],
            "semantic_text": "钢筋安装流程示意图",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施", "钢筋安装流程"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 4,
            "members": members,
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 4,
        "max_image_refs_total": 6,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "钢筋安装流程包括梁板模板、板筋绑扎和混凝土浇筑衔接控制。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == [f"IMG-FLOW-{index}" for index in range(1, 5)]
    assert {block["image_group_id"] for block in image_refs} == {"SRC0001-M00069-GFLOW"}


def test_auto_image_reuse_replaces_existing_same_material_single_with_large_group():
    package = _package()
    members = [
        {
            "image_id": f"IMG-MEASURE-GROUP-{index}",
            "image_asset_id": f"ASSET-MEASURE-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/measure-group-{index}.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00030-GCONTROL",
            "group_member_index": index,
            "group_member_count": 9,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(
            [
                "平面控制网布设示意",
                "控制点引测示意",
                "内控点预留洞口安置示意",
                "激光铅垂仪投测示意",
                "轴线传递示意",
                "高程控制点复核示意",
                "控制网闭合复测示意",
                "沉降观测点布设示意",
                "测量成果复核示意",
            ],
            start=1,
        )
    ]
    package["image_candidate_pool"] = [
        *members,
        {
            "image_id": "IMG-MEASURE-SINGLE",
            "image_asset_id": "ASSET-MEASURE-SINGLE",
            "caption": "内控点预留洞口安置示意",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "内控点预留洞口安置示意", "confidence": 0.9}
            ],
            "semantic_text": "内控点预留洞口安置示意",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/measure-single.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00030-GCONTROL",
            "group_title": "平面控制网的引测与精度控制措施",
            "semantic_sources": [
                {"source_type": "group_title", "text": "平面控制网的引测与精度控制措施", "confidence": 0.92}
            ],
            "semantic_text": "平面控制网的引测与精度控制措施",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 9,
            "members": members,
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 6,
        "max_image_refs_total": 12,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "平面控制网引测、轴线传递、高程复核和监测控制正文。"},
                {"type": "image_ref", "image_id": "IMG-MEASURE-SINGLE", "caption": "模型已选散图"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == [
        f"IMG-MEASURE-GROUP-{index}" for index in range(1, 10)
    ]
    assert "IMG-MEASURE-SINGLE" not in {block["image_id"] for block in image_refs}
    assert {block["image_group_id"] for block in image_refs} == {"SRC0001-M00030-GCONTROL"}


def test_auto_image_reuse_covers_empty_matching_section_even_when_total_target_met():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-MEASURE",
            "caption": "内控点预留洞口安置示意图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "内控点预留洞口安置示意图", "confidence": 0.9}
            ],
            "semantic_text": "内控点预留洞口安置示意图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "工程测量及监测施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/measure.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-WATERPROOF",
            "caption": "阴角防水细部做法",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "阴角防水细部做法", "confidence": 0.9}
            ],
            "semantic_text": "阴角防水细部做法",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施", "地下室防水施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof.png",
            "material_slice_id": "SRC0001-M00095",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 3,
        "max_images_per_section": 2,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "测量控制网和内控点预留洞口控制正文。"},
                {"type": "image_ref", "image_id": "IMG-MEASURE", "caption": "已有测量图片"},
            ],
        },
        {
            "heading": "地下室及屋面防水施工技术",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "地下室及屋面防水施工应加强阴角、节点和细部构造控制。"}],
        },
    ]

    result = apply_auto_image_reuse(output, package)
    waterproof_blocks = result["sections"][1]["blocks"]

    assert any(block.get("image_id") == "IMG-WATERPROOF" for block in waterproof_blocks)
    assert result["auto_image_reuse"]["coverage_inserted_count"] == 1


def test_auto_image_reuse_covers_empty_sections_before_group_burst_hits_total_limit():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-WATERPROOF",
            "caption": "阴角防水细部做法",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "地下室阴角防水细部做法", "confidence": 0.9}
            ],
            "semantic_text": "地下室阴角防水细部做法",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施", "地下室防水施工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof.png",
            "material_slice_id": "SRC0001-M00095",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    members = [
        {
            "image_id": f"IMG-STEEL-GROUP-{index}",
            "image_asset_id": f"ASSET-STEEL-GROUP-{index}",
            "caption": caption,
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
            ],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/steel-group-{index}.png",
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "SRC0001-M00069-GFLOW",
            "group_member_index": index,
            "group_member_count": 4,
            "must_keep_with_group": True,
        }
        for index, caption in enumerate(["封梁侧模板", "铺板底模板并弹板筋控制线", "绑扎楼板钢筋", "浇筑混凝土"], start=1)
    ]
    package["image_group_candidate_pool"] = [
        {
            "image_group_id": "SRC0001-M00069-GFLOW",
            "group_title": "钢筋安装流程示意图",
            "semantic_sources": [
                {"source_type": "group_title", "text": "钢筋安装流程示意图", "confidence": 0.92}
            ],
            "semantic_text": "钢筋安装流程示意图",
            "semantic_confidence": 0.92,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 4,
            "members": members,
            "material_slice_id": "SRC0001-M00069",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "must_keep_together": True,
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 4,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋安装流程包括梁板模板、板筋绑扎和混凝土浇筑衔接控制。"}],
        },
        {
            "heading": "地下室及屋面防水施工技术",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "地下室防水、屋面防水、阴角附加层和卷材搭接控制正文。"}],
        },
    ]

    result = apply_auto_image_reuse(output, package)
    refs_by_heading = {
        section["heading"]: [block["image_id"] for block in section["blocks"] if block.get("type") == "image_ref"]
        for section in result["sections"]
    }

    assert refs_by_heading["地下室及屋面防水施工技术"] == ["IMG-WATERPROOF"]
    assert refs_by_heading["钢筋工程制作安装及连接技术"] == [
        f"IMG-STEEL-GROUP-{index}" for index in range(1, 5)
    ]


def test_auto_image_reuse_prefers_concrete_pouring_over_generic_precast_block():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-PRECAST-BLOCK",
            "caption": "穿墙套管混凝土预制块",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "穿墙套管混凝土预制块", "confidence": 0.9}
            ],
            "semantic_text": "穿墙套管混凝土预制块",
            "semantic_confidence": 0.9,
            "source_section_path": ["质量管理措施", "砌体工程质量管理措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/precast.png",
            "material_slice_id": "SRC0001-M00443",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-CONCRETE-POURING",
            "caption": "混凝土的振捣",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "混凝土浇筑振捣控制", "confidence": 0.9}
            ],
            "semantic_text": "混凝土浇筑振捣控制",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "大体积混凝土施工方法"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/pouring.png",
            "material_slice_id": "SRC0001-M00092",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 2,
        "max_images_per_section": 2,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "混凝土浇筑、振捣、测温、温控和养护控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-CONCRETE-POURING"]


def test_auto_image_reuse_expands_sparse_concrete_and_waterproof_sections():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-CONCRETE-TEMP",
            "caption": "测温点布置",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "大体积混凝土测温点布置", "confidence": 0.9}
            ],
            "semantic_text": "大体积混凝土测温点布置",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "大体积混凝土施工方法"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/concrete-temp.png",
            "material_slice_id": "SRC0001-M00091",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-CONCRETE-VIBRATE",
            "caption": "混凝土的振捣",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "混凝土浇筑振捣控制", "confidence": 0.9}
            ],
            "semantic_text": "混凝土浇筑振捣控制",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "大体积混凝土施工方法"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/concrete-vibrate.png",
            "material_slice_id": "SRC0001-M00092",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-CONCRETE-SLUMP",
            "caption": "混凝土坍落度检测",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "混凝土浇筑坍落度检测", "confidence": 0.9}
            ],
            "semantic_text": "混凝土浇筑坍落度检测",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "混凝土浇筑质量控制"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/concrete-slump.png",
            "material_slice_id": "SRC0001-M00093",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-WATERPROOF-CORNER",
            "caption": "阴角防水细部做法",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "地下室阴角防水细部做法", "confidence": 0.9}
            ],
            "semantic_text": "地下室阴角防水细部做法",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof-corner.png",
            "material_slice_id": "SRC0001-M00095",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-WATERPROOF-LAP",
            "caption": "卷材搭接细部做法",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "屋面防水卷材搭接细部做法", "confidence": 0.9}
            ],
            "semantic_text": "屋面防水卷材搭接细部做法",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof-lap.png",
            "material_slice_id": "SRC0001-M00096",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
        {
            "image_id": "IMG-WATERPROOF-MEMBRANE",
            "caption": "防水卷材铺贴",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "地下室防水卷材铺贴施工", "confidence": 0.9}
            ],
            "semantic_text": "地下室防水卷材铺贴施工",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "防水施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/waterproof-membrane.png",
            "material_slice_id": "SRC0001-M00097",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        },
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 2,
        "target_image_refs": 2,
        "max_image_refs_total": 8,
        "max_images_per_section": 4,
        "sparse_section_image_refs": 3,
        "sparse_section_min_candidates": 2,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "混凝土浇筑及大体积温控措施",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "混凝土浇筑、振捣、测温、温控、坍落度和养护控制正文。"},
                {"type": "image_ref", "image_id": "IMG-CONCRETE-TEMP", "caption": "测温点布置"},
            ],
        },
        {
            "heading": "地下室及屋面防水施工技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "地下室防水、屋面防水、阴角附加层、卷材搭接和铺贴控制正文。"},
                {"type": "image_ref", "image_id": "IMG-WATERPROOF-CORNER", "caption": "阴角防水细部做法"},
            ],
        },
    ]

    result = apply_auto_image_reuse(output, package)

    refs_by_heading = {
        section["heading"]: [block["image_id"] for block in section["blocks"] if block.get("type") == "image_ref"]
        for section in result["sections"]
    }
    assert len(refs_by_heading["混凝土浇筑及大体积温控措施"]) == 3
    assert len(refs_by_heading["地下室及屋面防水施工技术"]) == 3
    assert result["auto_image_reuse"]["sparse_inserted_count"] == 4


def test_validate_chapter_output_warns_when_image_group_is_split():
    package = _package()
    output = _valid_output(package["generation_unit"])
    output["sections"][0]["blocks"].append(
        {
            "type": "image_ref",
            "image_id": "IMG-GROUP-1",
            "image_group_id": "SRC0001-M00068-G0000",
            "group_member_index": 1,
            "group_member_count": 3,
            "caption": "钢筋调直",
        }
    )

    validation = validate_chapter_output(output, package)

    assert any(issue["type"] == "image_group_split" for issue in validation["issues"])


def test_filter_mismatched_image_refs_removes_llm_chosen_wrong_image():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-STEEL",
            "caption": "钢筋加工成型",
            "bound_section": "钢筋加工成型",
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施", "钢筋加工成型"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/steel.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "测量控制正文"},
                {"type": "image_ref", "image_id": "IMG-STEEL", "caption": "工程测量控制网建立及监测方案做法示意"},
            ],
        }
    ]

    result = filter_mismatched_image_refs(output, package)

    blocks = result["sections"][0]["blocks"]
    assert all(block.get("type") != "image_ref" for block in blocks)
    assert result["image_ref_filter"]["removed_count"] == 1


def test_filter_mismatched_image_refs_matches_legacy_image_id_and_part_name():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00030-IMG0001",
            "image_id": "EBIMG_SRC0001_SRC0001_M00030_rId20_27_5_0",
            "caption": "第二步",
            "caption_candidates": ["激光铅垂仪投测步骤"],
            "nearby_text": "主体结构测量控制点竖向传递",
            "tags": ["测量"],
            "source_section_path": ["土建施工方案", "测量放线"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image6.png",
            "material_slice_id": "SRC0001-M00030",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "外脚手架搭设及安全防护措施",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "外脚手架搭设正文"},
                {
                    "type": "image_ref",
                    "image_id": "EBIMG_SRC0001_M00030_rId20_27_5_0",
                    "caption": "外脚手架连墙件及剪刀撑设置示意图",
                    "source_part_name": "word/media/image6.png",
                },
            ],
        }
    ]

    result = filter_mismatched_image_refs(output, package)

    blocks = result["sections"][0]["blocks"]
    assert all(block.get("type") != "image_ref" for block in blocks)
    assert result["image_ref_filter"]["removed_count"] == 1
    assert result["image_ref_filter"]["removed"][0]["source_part_name"] == "word/media/image6.png"


def test_enrich_image_refs_matches_legacy_image_id_and_updates_to_asset_id():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00068-IMG0000",
            "image_id": "EBIMG_SRC0001_SRC0001_M00068_rId49_68_0_0",
            "caption": "钢筋弯曲",
            "tags": ["钢筋", "箍筋"],
            "source_section_path": ["土建施工方案", "钢筋工程"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image35.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append(
        {
            "type": "image_ref",
            "image_id": "EBIMG_SRC0001_M00068_rId49_68_0_0",
            "caption": "钢筋直螺纹套筒连接施工示意图",
        }
    )

    enriched = enrich_image_refs(output, package)
    image_ref = enriched["sections"][0]["blocks"][-1]

    assert image_ref["image_id"] == "EBIMG_SRC0001_SRC0001_M00068_rId49_68_0_0"
    assert image_ref["image_asset_id"] == "SRC0001-M00068-IMG0000"
    assert image_ref["source_part_name"] == "word/media/image35.png"
    assert image_ref["source_bid_id"] == "SRC0001"


def test_enrich_image_refs_replaces_heading_like_source_part_name():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_asset_id": "SRC0001-M00092-IMG0000",
            "image_id": "EBIMG_SRC0001_SRC0001_M00092_rId134_112_2_2",
            "caption": "混凝土振捣",
            "semantic_text": "混凝土振捣",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/image134.png",
            "material_slice_id": "SRC0001-M00092",
            "source_bid_id": "SRC0001",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append(
        {
            "type": "image_ref",
            "image_id": "EBIMG_SRC0001_SRC0001_M00092_rId134_112_2_2",
            "caption": "混凝土振捣做法示意图",
            "source_part_name": "混凝土浇筑及大体积温控措施",
        }
    )

    enriched = enrich_image_refs(output, package)
    image_ref = enriched["sections"][0]["blocks"][-1]

    assert image_ref["source_part_name"] == "word/media/image134.png"


def test_text_match_score_handles_chinese_domain_terms():
    assert _text_match_score("地上施工测量控制", "工程测量控制网建立及监测方案") > 0
    assert _text_match_score("钢筋加工成型", "工程测量控制网建立及监测方案") == 0
    assert _text_match_score("钢筋工程施工方案与技术措施 钢筋加工成型", "钢筋工程制作安装及连接技术") > 0


def test_auto_image_reuse_distributes_multiple_images_across_paragraph_anchors():
    package = _package()
    package["image_candidate_pool"] = [
            {
                "image_id": f"IMG-DIST-{index}",
                "caption": f"纠偏措施图片{index}",
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "进度计划纠偏措施", "confidence": 0.9}
                ],
                "semantic_text": "进度计划纠偏措施",
                "semantic_confidence": 0.9,
                "bound_section": "进度计划纠偏措施",
            "source_section_path": ["工期保证", "纠偏措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/dist{index}.png",
            "material_slice_id": f"SRC0001-MD{index}",
            "source_bid_id": "SRC0001",
        }
        for index in range(1, 4)
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 3,
        "target_image_refs": 3,
        "max_image_refs_total": 3,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])

    result = apply_auto_image_reuse(output, package)
    blocks = result["sections"][2]["blocks"]
    image_indexes = [index for index, block in enumerate(blocks) if block.get("type") == "image_ref"]
    previous_types = [blocks[index - 1]["type"] for index in image_indexes]

    assert len(image_indexes) == 3
    assert previous_types.count("paragraph") >= 2


def test_auto_image_reuse_inserts_bound_image_after_matching_table():
    package = _package()
    package["image_candidate_pool"] = [
            {
                "image_id": "IMG-TABLE",
                "caption": "钢筋加工示意图",
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "总体施工部署与关键线路分析", "confidence": 0.9}
                ],
                "semantic_text": "总体施工部署与关键线路分析",
                "semantic_confidence": 0.9,
                "bound_section": "总体施工部署与关键线路分析",
            "source_section_path": ["土建施工方案", "钢筋加工"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/table.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "bound_table_id": "TABLE-STEEL",
        }
    ]
    package["auto_image_reuse_policy"] = {"enabled": True, "min_image_refs": 1, "target_image_refs": 1, "max_image_refs_total": 1}
    output = _expanded_output(package["generation_unit"])
    section = output["sections"][1]
    section["blocks"][2]["table_id"] = "TABLE-STEEL"

    result = apply_auto_image_reuse(output, package)
    blocks = result["sections"][1]["blocks"]
    table_index = next(index for index, block in enumerate(blocks) if block.get("table_id") == "TABLE-STEEL")

    assert blocks[table_index + 1]["type"] == "image_ref"
    assert blocks[table_index + 1]["image_id"] == "IMG-TABLE"


def test_auto_image_reuse_inserts_unbound_image_after_matching_paragraph():
    package = _package()
    package["image_candidate_pool"] = [
            {
                "image_id": "IMG-PARA",
                "caption": "纠偏措施流程图",
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "进度计划纠偏措施", "confidence": 0.9}
                ],
                "semantic_text": "进度计划纠偏措施",
                "semantic_confidence": 0.9,
                "bound_section": "进度计划纠偏措施",
            "source_section_path": ["工期保证", "纠偏措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/para.png",
            "material_slice_id": "SRC0001-M00088",
            "source_bid_id": "SRC0001",
        }
    ]
    package["auto_image_reuse_policy"] = {"enabled": True, "min_image_refs": 1, "target_image_refs": 1, "max_image_refs_total": 1}
    output = _expanded_output(package["generation_unit"])

    result = apply_auto_image_reuse(output, package)
    blocks = result["sections"][2]["blocks"]
    image_index = next(index for index, block in enumerate(blocks) if block.get("type") == "image_ref")

    assert image_index < len(blocks) - 1
    assert blocks[image_index - 1]["type"] == "paragraph"
    assert blocks[image_index]["image_id"] == "IMG-PARA"


def test_auto_image_reuse_places_image_near_specific_semantic_paragraph():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-STIRRUP",
            "caption": "钢筋加工成型做法示意",
            "semantic_sources": [
                {"source_type": "previous_row_1_item", "text": "箍筋加工控制", "confidence": 0.66}
            ],
            "semantic_text": "箍筋加工控制",
            "semantic_confidence": 0.66,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/stirrup.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {"enabled": True, "min_image_refs": 1, "target_image_refs": 1, "max_image_refs_total": 1}
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "钢筋原材进场后按批次验收，复试合格后方可使用。"},
                {"type": "paragraph", "text": "箍筋加工控制应重点检查弯钩角度、平直段长度和成型尺寸。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    blocks = result["sections"][0]["blocks"]
    image_index = next(index for index, block in enumerate(blocks) if block.get("type") == "image_ref")

    assert blocks[image_index - 1]["text"].startswith("箍筋加工控制")
    assert blocks[image_index]["caption"] == "箍筋加工控制"


def test_auto_image_reuse_skips_when_only_section_matches_but_paragraphs_do_not():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-REBAR-BENDING",
            "caption": "钢筋弯曲",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "钢筋弯曲", "confidence": 0.9}
            ],
            "semantic_text": "钢筋弯曲",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/rebar-bending.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {"enabled": True, "min_image_refs": 1, "target_image_refs": 1, "max_image_refs_total": 1}
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程制作安装及连接技术",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "钢筋原材进场后按批次验收，复试合格后方可使用。"},
                {"type": "paragraph", "text": "直螺纹套筒连接施工应检查丝头长度、拧紧力矩和外露丝扣。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_does_not_treat_rebar_axis_as_measurement_axis():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-REBAR-AXIS",
            "caption": "钢筋切口控制",
            "semantic_sources": [
                {
                    "source_type": "same_row_text",
                    "text": "钢筋应先调直，保证切口断面与钢筋轴线垂直。",
                    "confidence": 0.74,
                }
            ],
            "semantic_text": "钢筋应先调直，保证切口断面与钢筋轴线垂直。",
            "semantic_confidence": 0.74,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/rebar-axis.png",
            "material_slice_id": "SRC0001-M00068",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "工程测量控制网建立及监测方案",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "施工测量控制网、轴线控制点和高程控制点应定期复核。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_skips_weak_single_rebar_image_in_formwork_section():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-WEAK-REBAR",
            "caption": "方法做法示意图",
            "semantic_sources": [
                {"source_type": "same_row_text", "text": "序号；方法", "confidence": 0.62}
            ],
            "semantic_text": "序号；方法",
            "semantic_confidence": 0.62,
            "source_section_path": ["土建施工方案", "钢筋工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/rebar-method.png",
            "material_slice_id": "SRC0001-M00080",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "主要构件模板施工方法",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "墙柱梁板模板支设前应完成模板选型、支撑体系复核和对拉螺栓布置。"},
                {"type": "paragraph", "text": "模板安装应控制标高、垂直度、拼缝严密性和支撑稳定性。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_keeps_strong_formwork_single_image():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-FORMWORK",
            "caption": "剪力墙模板支设节点图",
            "semantic_sources": [
                {"source_type": "same_cell_caption", "text": "剪力墙模板支设节点图", "confidence": 0.9}
            ],
            "semantic_text": "剪力墙模板支设节点图",
            "semantic_confidence": 0.9,
            "source_section_path": ["土建施工方案", "模板工程施工方案与技术措施"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": "word/media/formwork.png",
            "material_slice_id": "SRC0001-M00077",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
        }
    ]
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 1,
        "max_image_refs_total": 1,
        "max_images_per_section": 3,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "主要构件模板施工方法",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "剪力墙模板支设应重点控制模板拼缝、木方间距和对拉螺栓布置。"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-FORMWORK"]


def test_auto_image_reuse_skips_manual_review_images():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-002",
            "caption": "施工总平面图",
            "bound_section": "施工总平面图",
            "reuse_level": "manual_review",
            "risk_level": "high",
            "part_name": "word/media/image2.png",
        }
    ]
    package["auto_image_reuse_policy"] = {"enabled": True, "min_image_refs": 1, "max_auto_image_refs": 3}
    output = _expanded_output(package["generation_unit"])

    result = apply_auto_image_reuse(output, package)

    assert all(
        block.get("type") != "image_ref"
        for section in result["sections"]
        for block in section["blocks"]
    )


def test_auto_image_reuse_dedupes_same_caption_single_images_in_section():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "single image dedupe",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "section text"},
                {"type": "image_ref", "image_id": "IMG-A", "source_part_name": "word/media/same.png", "caption": "Same caption"},
                {"type": "paragraph", "text": "more section text"},
                {"type": "image_ref", "image_id": "IMG-B", "source_part_name": "word/media/same.png", "caption": "Same caption"},
                {"type": "image_ref", "image_id": "IMG-C", "source_part_name": "word/media/other.png", "caption": "Same caption"},
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-A", "IMG-C"]


def test_auto_image_reuse_keeps_same_caption_image_group_members():
    package = _package()
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "group image preserve",
            "level": 3,
            "blocks": [
                {"type": "paragraph", "text": "section text"},
                {
                    "type": "image_ref",
                    "image_id": "IMG-G1",
                    "caption": "Group caption",
                    "image_group_id": "GROUP-KEEP",
                    "group_member_index": 1,
                    "group_member_count": 2,
                    "must_keep_with_group": True,
                },
                {
                    "type": "image_ref",
                    "image_id": "IMG-G2",
                    "caption": "Group caption",
                    "image_group_id": "GROUP-KEEP",
                    "group_member_index": 2,
                    "group_member_count": 2,
                    "must_keep_with_group": True,
                },
            ],
        }
    ]

    result = apply_auto_image_reuse(output, package)

    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]
    assert [block["image_id"] for block in image_refs] == ["IMG-G1", "IMG-G2"]


def test_auto_image_reuse_does_not_stack_multiple_groups_over_section_limit():
    package = _package()
    groups = []
    for group_index in range(1, 3):
        members = [
            {
                "image_id": f"IMG-SCAF-{group_index}-{member_index}",
                "image_asset_id": f"ASSET-SCAF-{group_index}-{member_index}",
                "caption": f"脚手架连墙件做法{group_index}-{member_index}",
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "脚手架连墙件搭设做法", "confidence": 0.9}
                ],
                "semantic_text": "脚手架连墙件搭设做法",
                "semantic_confidence": 0.9,
                "source_section_path": ["土建施工方案", "脚手架工程安全措施"],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "part_name": f"word/media/scaffold-{group_index}-{member_index}.png",
                "material_slice_id": f"SRC0001-SCAF-{group_index}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
                "image_group_id": f"G-SCAF-{group_index}",
                "group_member_index": member_index,
                "group_member_count": 4,
                "must_keep_with_group": True,
            }
            for member_index in range(1, 5)
        ]
        groups.append(
            {
                "image_group_id": f"G-SCAF-{group_index}",
                "group_title": "脚手架连墙件搭设做法",
                "semantic_sources": [
                    {"source_type": "group_title", "text": "脚手架连墙件搭设做法", "confidence": 0.92}
                ],
                "semantic_text": "脚手架连墙件搭设做法",
                "semantic_confidence": 0.92,
                "source_section_path": ["土建施工方案", "脚手架工程安全措施"],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "member_count": 4,
                "members": members,
                "material_slice_id": f"SRC0001-SCAF-{group_index}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
                "must_keep_together": True,
            }
        )
    package["image_group_candidate_pool"] = groups
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 8,
        "max_image_refs_total": 12,
        "max_images_per_section": 5,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "外脚手架搭设及安全防护措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "外脚手架搭设、连墙件、剪刀撑、安全网和立杆间距控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert len(image_refs) == 4
    assert {block["image_group_id"] for block in image_refs} == {"G-SCAF-1"}


def test_auto_image_reuse_dedupes_equivalent_groups_from_different_sources():
    package = _package()
    groups = []
    for source_index in range(1, 3):
        members = [
            {
                "image_id": f"IMG-FIRE-{source_index}-{member_index}",
                "image_asset_id": f"ASSET-FIRE-{source_index}-{member_index}",
                "caption": caption,
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": caption, "confidence": 0.92}
                ],
                "semantic_text": caption,
                "semantic_confidence": 0.92,
                "source_section_path": ["安全管理措施", "消防安全管理措施"],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "part_name": f"word/media/fire-{source_index}-{member_index}.png",
                "material_slice_id": f"SRC000{source_index}-M00350",
                "source_bid_id": f"SRC000{source_index}",
                "material_quality": "high",
                "image_group_id": f"G-FIRE-{source_index}",
                "group_title": "消防泵房保证措施",
                "group_semantic_text": "消防泵房保证措施",
                "group_member_index": member_index,
                "group_member_count": 2,
                "must_keep_with_group": True,
            }
            for member_index, caption in enumerate(["消防泵防外部示意图", "消防泵防内部示意图"], start=1)
        ]
        groups.append(
            {
                "image_group_id": f"G-FIRE-{source_index}",
                "group_title": "消防泵房保证措施",
                "semantic_text": "消防泵房保证措施",
                "semantic_sources": [
                    {"source_type": "group_title", "text": "消防泵房保证措施", "confidence": 0.92}
                ],
                "semantic_confidence": 0.92,
                "source_section_path": ["安全管理措施", "消防安全管理措施"],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "member_count": 2,
                "members": members,
                "material_slice_id": f"SRC000{source_index}-M00350",
                "source_bid_id": f"SRC000{source_index}",
                "material_quality": "high",
                "must_keep_together": True,
            }
        )
    package["image_group_candidate_pool"] = groups
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 4,
        "max_image_refs_total": 6,
        "max_images_per_section": 6,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "消防安全管理措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "消防安全、消防泵房、动火审批和消防器材管理正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-FIRE-1-1", "IMG-FIRE-1-2"]
    assert {block["image_group_id"] for block in image_refs} == {"G-FIRE-1"}
    assert result["auto_image_reuse"]["deduped_equivalent_image_count"] == 0


def test_auto_image_reuse_filters_environment_subtopic_mismatch():
    package = _package()
    groups = []
    for group_id, title, captions in [
        ("G-WATER", "水污染控制措施", ["三级沉淀池循环用水示意图", "油水分离器设置示意图"]),
        ("G-AIR", "大气污染控制措施", ["道路喷淋降尘示意图", "出入口洗车机示意图"]),
    ]:
        members = [
            {
                "image_id": f"IMG-{group_id}-{member_index}",
                "image_asset_id": f"ASSET-{group_id}-{member_index}",
                "caption": caption,
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}
                ],
                "semantic_text": caption,
                "semantic_confidence": 0.9,
                "source_section_path": ["环境保护措施", title],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "part_name": f"word/media/{group_id}-{member_index}.png",
                "material_slice_id": f"SRC0001-{group_id}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
                "image_group_id": group_id,
                "group_title": title,
                "group_semantic_text": title,
                "group_member_index": member_index,
                "group_member_count": 2,
                "must_keep_with_group": True,
            }
            for member_index, caption in enumerate(captions, start=1)
        ]
        groups.append(
            {
                "image_group_id": group_id,
                "group_title": title,
                "semantic_text": title,
                "semantic_sources": [{"source_type": "group_title", "text": title, "confidence": 0.92}],
                "semantic_confidence": 0.92,
                "source_section_path": ["环境保护措施", title],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "member_count": 2,
                "members": members,
                "material_slice_id": f"SRC0001-{group_id}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
                "must_keep_together": True,
            }
        )
    package["image_group_candidate_pool"] = groups
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 4,
        "max_image_refs_total": 6,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "水污染防治措施",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "污水、废水、沉淀池、洗车槽和油水分离器控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-G-WATER-1", "IMG-G-WATER-2"]
    assert {block["image_group_id"] for block in image_refs} == {"G-WATER"}


def test_auto_image_reuse_uses_strong_text_image_block_group_as_whole():
    package = _package()
    members = [
        {
            "image_id": f"IMG-TIB-REBAR-{index}",
            "image_asset_id": f"ASSET-TIB-REBAR-{index}",
            "caption": caption,
            "semantic_sources": [{"source_type": "same_cell_caption", "text": caption, "confidence": 0.92}],
            "semantic_text": caption,
            "semantic_confidence": 0.92,
            "source_section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "part_name": f"word/media/tib-rebar-{index}.png",
            "material_slice_id": "SRC0001-M01000",
            "source_bid_id": "SRC0001",
            "material_quality": "high",
            "image_group_id": "G-TIB-REBAR",
            "group_title": "钢筋加工示意图",
            "group_semantic_text": "钢筋加工、连接、绑扎流程示意图",
            "group_member_index": index,
            "group_member_count": 3,
            "must_keep_with_group": True,
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-SRC0001-M01000",
            "text_image_block_match_level": "strong",
            "text_image_block_match_confidence": 0.86,
            "reuse_priority": "text_image_block_strong",
        }
        for index, caption in enumerate(["钢筋加工示意图", "钢筋连接示意图", "钢筋绑扎示意图"], start=1)
    ]
    package["text_image_block_reuse_candidates"] = [
        {
            "block_id": "TIB-SRC0001-M01000",
            "block_type": "image_group_block",
            "material_slice_id": "SRC0001-M01000",
            "title": "钢筋加工成熟图文块",
            "topics": ["钢筋"],
            "primary_topic": "钢筋",
            "match_level": "strong",
            "match_confidence": 0.86,
            "risk_flags": [],
            "project_specific_risk": "medium",
            "reuse_level": "parameterized_reuse",
            "image_group_candidates": [
                {
                    "image_group_id": "G-TIB-REBAR",
                    "group_title": "钢筋加工示意图",
                    "semantic_text": "钢筋加工、连接、绑扎流程示意图",
                    "semantic_sources": [{"source_type": "group_title", "text": "钢筋加工示意图", "confidence": 0.92}],
                    "semantic_confidence": 0.92,
                    "source_section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                    "reuse_level": "candidate_reuse",
                    "risk_level": "low",
                    "member_count": 3,
                    "members": members,
                    "material_slice_id": "SRC0001-M01000",
                    "source_bid_id": "SRC0001",
                    "material_quality": "high",
                    "must_keep_together": True,
                    "source_reuse_mode": "text_image_block",
                    "text_image_block_id": "TIB-SRC0001-M01000",
                    "text_image_block_match_level": "strong",
                    "text_image_block_match_confidence": 0.86,
                    "reuse_priority": "text_image_block_strong",
                }
            ],
            "image_candidates": [],
        }
    ]
    package["image_group_candidate_pool"] = []
    package["image_candidate_pool"] = []
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 3,
        "max_image_refs_total": 6,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["image_slots"] = [
        {
            "section_heading": "钢筋工程施工方案",
            "intent": "钢筋加工、连接、绑扎流程示意图",
            "preferred_type": "施工工艺示意图",
            "max_count": 3,
            "group_preferred": True,
        }
    ]
    output["sections"] = [
        {
            "heading": "钢筋工程施工方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋加工、连接、绑扎及成品保护控制正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert [block["image_id"] for block in image_refs] == ["IMG-TIB-REBAR-1", "IMG-TIB-REBAR-2", "IMG-TIB-REBAR-3"]
    assert {block["image_group_id"] for block in image_refs} == {"G-TIB-REBAR"}
    assert {block["text_image_block_id"] for block in image_refs} == {"TIB-SRC0001-M01000"}
    assert result["auto_image_reuse"]["slot_group_inserted_count"] == 3


def test_auto_image_reuse_ignores_risky_text_image_block_candidates():
    package = _package()
    package["text_image_block_reuse_candidates"] = [
        {
            "block_id": "TIB-RISKY",
            "block_type": "image_group_block",
            "material_slice_id": "SRC0001-M01001",
            "title": "仅题注命中的钢筋图文块",
            "topics": ["钢筋"],
            "primary_topic": "钢筋",
            "match_level": "strong",
            "match_confidence": 0.86,
            "risk_flags": ["primary_topic_only_from_caption"],
            "project_specific_risk": "medium",
            "reuse_level": "parameterized_reuse",
            "image_group_candidates": [
                {
                    "image_group_id": "G-RISKY",
                    "group_title": "钢筋加工示意图",
                    "semantic_text": "钢筋加工示意图",
                    "semantic_confidence": 0.92,
                    "source_section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                    "reuse_level": "candidate_reuse",
                    "risk_level": "low",
                    "member_count": 2,
                    "members": [
                        {
                            "image_id": "IMG-RISKY-1",
                            "image_asset_id": "ASSET-RISKY-1",
                            "caption": "钢筋加工示意图",
                            "semantic_text": "钢筋加工",
                            "semantic_confidence": 0.92,
                            "part_name": "word/media/risky1.png",
                            "reuse_level": "candidate_reuse",
                            "risk_level": "low",
                        }
                    ],
                    "source_reuse_mode": "text_image_block",
                    "text_image_block_id": "TIB-RISKY",
                    "text_image_block_match_level": "strong",
                    "text_image_block_match_confidence": 0.86,
                    "reuse_priority": "text_image_block_strong",
                }
            ],
            "image_candidates": [],
        }
    ]
    package["image_group_candidate_pool"] = []
    package["image_candidate_pool"] = []
    package["auto_image_reuse_policy"] = {
        "enabled": True,
        "min_image_refs": 1,
        "target_image_refs": 2,
        "max_image_refs_total": 4,
        "max_images_per_section": 4,
    }
    output = _expanded_output(package["generation_unit"])
    output["sections"] = [
        {
            "heading": "钢筋工程施工方案",
            "level": 3,
            "blocks": [{"type": "paragraph", "text": "钢筋加工、连接和绑扎施工正文。"}],
        }
    ]

    result = apply_auto_image_reuse(output, package)
    image_refs = [
        block
        for section in result["sections"]
        for block in section["blocks"]
        if block.get("type") == "image_ref"
    ]

    assert image_refs == []


def test_run_chapter_generation_retries_expanded_volume_warnings():
    package = _package()
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["task_type"])
        unit = llm_input["generation_unit"]
        if llm_input["task_type"] == "expand_existing_technical_bid_chapter":
            return json.dumps(_expanded_output(unit), ensure_ascii=False)
        return json.dumps(_valid_output(unit), ensure_ascii=False)

    result = run_chapter_generation(
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == ["generate_technical_bid_chapter", "expand_existing_technical_bid_chapter"]
    assert result.completed_count == 1
    assert result.tasks[0].validation["expanded_retry_attempted"] is True
    assert result.tasks[0].validation["expanded_retry_accepted"] is True
    assert len(result.chapters[0]["sections"]) == 3


def test_dedupe_images_across_chapters_removes_later_duplicate_single_image():
    first = _expanded_output(_package()["generation_unit"])
    second = _expanded_output(_package()["generation_unit"])
    second["unit_id"] = "GU-N2"
    second["target_node_id"] = "N2"
    second["chapter_path"] = ["主要施工方案与技术措施", "模板工程"]
    image_ref = {
        "type": "image_ref",
        "image_id": "IMG-SAME",
        "image_asset_id": "ASSET-SAME",
        "source_bid_id": "SRC0001",
        "source_part_name": "word/media/image1.png",
        "caption": "模板支设示意图",
    }
    first["sections"][0]["blocks"].append(dict(image_ref))
    second["sections"][0]["blocks"].append(dict(image_ref))

    summary = dedupe_images_across_chapters([first, second])

    assert summary["removed_duplicate_asset_count"] == 1
    assert _image_ids(first) == ["IMG-SAME"]
    assert _image_ids(second) == []
    assert second["cross_chapter_image_dedup"]["removed_count"] == 1


def test_dedupe_images_across_chapters_removes_duplicate_group_as_whole():
    first = _expanded_output(_package()["generation_unit"])
    second = _expanded_output(_package()["generation_unit"])
    second["unit_id"] = "GU-N2"
    second["target_node_id"] = "N2"
    second["chapter_path"] = ["主要施工方案与技术措施", "钢筋工程"]
    members = [
        {
            "type": "image_ref",
            "image_id": f"IMG-G{index}",
            "image_asset_id": f"ASSET-G{index}",
            "image_group_id": "GROUP-STEEL",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
            "source_bid_id": "SRC0001",
            "source_part_name": f"word/media/group{index}.png",
            "caption": f"钢筋加工示意{index}",
        }
        for index in range(1, 3)
    ]
    first["sections"][0]["blocks"].extend(dict(member) for member in members)
    second["sections"][0]["blocks"].extend(dict(member) for member in members)

    summary = dedupe_images_across_chapters([first, second])

    assert summary["removed_duplicate_group_count"] == 2
    assert _image_ids(first) == ["IMG-G1", "IMG-G2"]
    assert _image_ids(second) == []


def test_dedupe_images_across_chapters_prefers_group_over_later_single_member():
    first = _expanded_output(_package()["generation_unit"])
    second = _expanded_output(_package()["generation_unit"])
    second["unit_id"] = "GU-N2"
    second["target_node_id"] = "N2"
    second["chapter_path"] = ["主要施工方案与技术措施", "钢筋工程"]
    first["sections"][0]["blocks"].extend(
        [
            {
                "type": "image_ref",
                "image_id": f"IMG-G{index}",
                "image_asset_id": f"ASSET-G{index}",
                "image_group_id": "GROUP-STEEL",
                "group_member_index": index,
                "group_member_count": 2,
                "must_keep_with_group": True,
                "caption": f"钢筋加工示意{index}",
            }
            for index in range(1, 3)
        ]
    )
    second["sections"][0]["blocks"].append(
        {
            "type": "image_ref",
            "image_id": "IMG-G2",
            "image_asset_id": "ASSET-G2",
            "caption": "钢筋加工示意2",
        }
    )

    summary = dedupe_images_across_chapters([first, second])

    assert summary["removed_single_covered_by_group_count"] == 1
    assert _image_ids(first) == ["IMG-G1", "IMG-G2"]
    assert _image_ids(second) == []


def test_enrich_image_refs_fills_source_id_and_image_fingerprints():
    package = _package()
    package["image_candidate_pool"] = [
        {
            "image_id": "IMG-001",
            "image_asset_id": "ASSET-001",
            "canonical_image_id": "CANON-001",
            "sha256": "abc123",
            "perceptual_hash": "ffff0000",
            "source_id": "SRC0009",
            "part_name": "word/media/image1.png",
            "caption": "测量控制网布设示意图",
            "semantic_text": "测量控制网布设示意图",
            "semantic_confidence": 0.9,
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
        }
    ]
    output = _expanded_output(package["generation_unit"])
    output["sections"][0]["blocks"].append({"type": "image_ref", "image_id": "IMG-001"})

    result = enrich_image_refs(output, package)
    image_ref = result["sections"][0]["blocks"][-1]

    assert image_ref["source_id"] == "SRC0009"
    assert image_ref["source_bid_id"] == "SRC0009"
    assert image_ref["canonical_image_id"] == "CANON-001"
    assert image_ref["sha256"] == "abc123"
    assert image_ref["perceptual_hash"] == "ffff0000"


def _package():
    return {
        "task_type": "generate_technical_bid_chapter",
        "schema_version": "chapter_generation_input_v1",
        "project_info": {
            "project_name": "示例项目",
            "location": "示例地点",
            "duration": "365日历天",
            "quality": "合格",
        },
        "generation_unit": {
            "unit_id": "GU-N1",
            "target_node_id": "N1",
            "chapter_path": ["施工进度表"],
            "child_headings": ["施工进度计划编制依据与原则", "总体施工部署与关键线路分析"],
            "domain": "construction",
            "category": "施工进度",
        },
        "score_point": {
            "score_point_raw": "施工进度表",
            "score_standard_raw": "关键线路清晰、准确、完整，计划编制合理、可行。",
        },
        "technical_requirements": [],
        "excellent_bid_references": [
            {
                "ref_id": "SRC0002-M00366",
                "title": "计划开、竣工日期和施工进度网络图",
                "reuse_level": "manual_review",
                "reference_excerpt": "计划开、竣工日期和施工进度网络图。",
            }
        ],
        "table_references": [
            {
                "table_id": "T1",
                "title": "施工进度计划表",
                "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "控制内容"}],
            }
        ],
        "image_candidates": [],
        "generation_constraints": {
            "generation_mode": "expanded",
            "forbidden_content": ["历史项目名称", "历史建设单位"],
        },
        "expanded_generation_policy": {
            "mode": "expanded",
            "section_type": "project_specific",
            "targets": {
                "min_sections": 3,
                "min_paragraphs_per_section": 2,
                "min_paragraphs_total": 8,
                "min_rich_tables": 2,
                "min_rows_per_rich_table": 4,
                "min_image_refs": 0,
                "min_image_placeholders": 1,
            },
            "reuse_level_policy": {
                "manual_review": "不得作为正文主素材自动写入，只能作为占位、候选说明或人工复核项。",
            },
            "writing_requirements": ["输出必须是 expanded 详稿。"],
        },
    }


def _valid_output(unit):
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": unit["chapter_path"][-1],
        "sections": [
            {
                "heading": "施工进度计划编制依据与原则",
                "level": 2,
                "blocks": [
                    {"type": "paragraph", "text": "本工程施工进度计划以招标工期、施工图纸和现场条件为依据。"},
                    {
                        "type": "rich_table",
                        "title": "施工进度控制要点表",
                        "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "控制内容"}],
                        "rows": [{"cells": {"col_1": "1", "col_2": "明确关键线路并实施动态纠偏。"}}],
                    },
                ],
            }
        ],
        "score_response_check": {
            "score_point_raw": "施工进度表",
            "response_summary": "围绕关键线路、计划可行性和工期目标进行响应。",
            "covered": True,
            "evidence_headings": ["施工进度计划编制依据与原则"],
        },
        "source_usage": [{"ref_id": "SRC0002-M00366", "usage": "结构参考", "rewrite_required": True}],
        "review_items": [{"severity": "medium", "type": "manual_check", "message": "需人工补充最终进度计划图。"}],
    }


def _minimal_valid_output(unit):
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": unit["chapter_path"][-1],
        "sections": [
            {
                "heading": "施工组织",
                "level": 3,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "本节围绕施工组织、技术准备和现场实施要求展开，形成可执行的正文内容。",
                    }
                ],
            }
        ],
        "score_response_check": {
            "score_point_raw": "主要施工方案与技术措施",
            "response_summary": "已响应。",
            "covered": True,
            "evidence_headings": ["施工组织"],
        },
        "source_usage": [],
        "review_items": [],
    }


def _relax_expanded_targets(package):
    package["expanded_generation_policy"]["targets"] = {
        "min_sections": 1,
        "min_paragraphs_per_section": 1,
        "min_paragraphs_total": 1,
        "min_rich_tables": 0,
        "min_rows_per_rich_table": 0,
        "min_image_refs": 0,
        "min_image_placeholders": 0,
    }


def _expanded_output(unit):
    sections = []
    for index, heading in enumerate(["施工进度计划编制依据与原则", "总体施工部署与关键线路分析", "进度计划纠偏措施"], start=1):
        sections.append(
            {
                "heading": heading,
                "level": 2,
                "blocks": [
                    {"type": "paragraph", "text": f"{heading}正文一，结合本工程工期目标、现场条件和资源组织进行详细安排，明确控制原则、执行方法和检查要求。"},
                    {"type": "paragraph", "text": f"{heading}正文二，建立动态检查机制，按周分析进度偏差，及时调整劳动力、材料供应和机械投入。"},
                    {
                        "type": "rich_table",
                        "title": f"{heading}控制表",
                        "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "控制内容"}],
                        "rows": [
                            {"cells": {"col_1": "1", "col_2": "明确控制目标。"}},
                            {"cells": {"col_1": "2", "col_2": "落实责任分工。"}},
                            {"cells": {"col_1": "3", "col_2": "开展过程检查。"}},
                            {"cells": {"col_1": "4", "col_2": "实施纠偏闭环。"}},
                        ],
                    },
                ],
            }
        )
    sections[0]["blocks"].append({"type": "image_placeholder", "caption": "施工进度计划图", "reason": "需结合本项目计划人工补充"})
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": unit["chapter_path"][-1],
        "sections": sections,
        "score_response_check": {
            "score_point_raw": "施工进度表",
            "response_summary": "围绕关键线路、计划可行性和工期目标进行响应。",
            "covered": True,
            "evidence_headings": [section["heading"] for section in sections],
        },
        "source_usage": [{"ref_id": "SRC0002-M00366", "usage": "结构参考", "rewrite_required": True}],
        "review_items": [{"severity": "medium", "type": "manual_check", "message": "需人工补充最终进度计划图。"}],
    }


def _technical_bid_completeness_package():
    package = _package()
    package["generation_unit"] = {
        "unit_id": "GU-G1",
        "target_node_id": "G1",
        "chapter_path": ["内容完整性"],
        "child_headings": ["技术标响应范围", "章节完整性组织", "响应依据与编制原则", "技术标完整性承诺"],
        "domain": "general",
        "category": "技术标完整性说明",
    }
    package["score_point"] = {
        "score_point_raw": "内容完整性",
        "score_standard_raw": "技术标内容完整，章节齐全，响应招标文件要求。",
    }
    package["technical_requirements"] = [
        {
            "requirement_id": "GENERAL-COMPLETENESS-001",
            "type": "generation_guidance",
            "category": "技术标完整性说明",
            "raw_clause": "本章用于说明技术标完整响应关系，不展开具体施工工艺。",
        }
    ]
    package["excellent_bid_references"] = []
    package["table_references"] = []
    package["image_candidates"] = []
    package["image_candidate_pool"] = []
    package["auto_image_reuse_policy"] = {"enabled": False}
    package["expanded_generation_policy"] = {
        "mode": "expanded",
        "section_type": "technical_bid_response_statement",
        "targets": {
            "min_sections": 4,
            "min_paragraphs_per_section": 2,
            "min_paragraphs_total": 8,
            "min_rich_tables": 0,
            "min_rows_per_rich_table": 0,
            "min_image_refs": 0,
            "min_image_placeholders": 0,
        },
        "reuse_level_policy": {},
        "writing_requirements": ["本章是技术标完整性说明，不是施工方案章节，不输出内部复核表格。"],
    }
    return package


def _technical_bid_completeness_output(unit):
    headings = unit["child_headings"]
    sections = []
    for heading in headings:
        sections.append(
            {
                "heading": heading,
                "level": 2,
                "blocks": [
                    {"type": "paragraph", "text": f"{heading}正文一，围绕招标文件技术要求和技术标目录章节组织进行完整说明，明确本技术标覆盖范围。"},
                    {"type": "paragraph", "text": f"{heading}正文二，说明编制成果与技术标准、施工组织安排和章节内容之间的衔接关系，保证正文完整连贯。"},
                ],
            }
        )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": unit["chapter_path"][-1],
        "sections": sections,
        "score_response_check": {
            "score_point_raw": "内容完整性",
            "response_summary": "说明技术标章节完整覆盖招标文件评分点和技术要求。",
            "covered": True,
            "evidence_headings": headings,
        },
        "source_usage": [],
        "review_items": [],
    }


def _image_ids(chapter):
    return [
        block.get("image_id")
        for section in chapter["sections"]
        for block in section.get("blocks") or []
        if block.get("type") == "image_ref"
    ]


def _config(api_key="key"):
    return LlmClientConfig(
        provider="test",
        api_key=api_key,
        base_url="https://compatible.example.com/v1",
        model="test-model",
        temperature=0.35,
        top_p=0.9,
        max_tokens=12000,
        timeout_seconds=300,
        max_retries=2,
        api_type="responses",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
    )
