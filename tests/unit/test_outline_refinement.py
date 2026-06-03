from construction_bidding_agent.outline_generator.refinement import (
    build_outline_refinement_inputs,
    validate_outline_refinement_output,
)


def test_build_outline_refinement_inputs_refines_content_completeness_node():
    packages = build_outline_refinement_inputs(
        {
            "nodes": [
                {
                    "node_id": "N1",
                    "title": "内容完整性",
                    "domain": "general",
                    "category": "技术标完整性说明",
                    "children": [],
                }
            ]
        },
        _parse_result(),
    )

    assert len(packages) == 1
    assert packages[0]["target_outline_node"]["level_1_title"] == "内容完整性"
    assert packages[0]["granularity_rule"]["chapter_type"] == "technical_bid_completeness_statement"
    assert packages[0]["granularity_rule"]["min_level_2_count"] == 3
    assert packages[0]["granularity_rule"]["max_level_2_count"] == 5
    assert packages[0]["granularity_rule"]["level_3_allowed"] is False
    assert "技术标完整性说明需按评分点专门生成目录" in packages[0]["trigger_reasons"]


def test_build_outline_refinement_inputs_sets_construction_method_limits():
    packages = build_outline_refinement_inputs(
        {
            "nodes": [
                {
                    "node_id": "N1",
                    "title": "主要施工方案与技术措施",
                    "domain": "construction",
                    "category": "施工方案",
                    "children": [],
                    "template_source": "generated_from_requirement",
                }
            ]
        },
        _parse_result(),
    )

    rule = packages[0]["granularity_rule"]

    assert rule["chapter_type"] == "construction_method_and_technical_measures"
    assert rule["min_level_2_count"] == 8
    assert rule["max_level_2_count"] == 12
    assert rule["max_level_3_per_level_2"] == 8
    assert rule["max_total_level_3_count"] == 70
    assert rule["level_3_allowed"] is True


def test_validate_outline_refinement_rejects_construction_plan_children_for_content_completeness():
    package = {
        "target_outline_node": {
            "node_id": "N1",
            "level_1_title": "内容完整性",
            "category": "技术标完整性说明",
        },
        "granularity_rule": {
            "min_level_2_count": 3,
            "max_level_2_count": 5,
            "level_3_required": False,
            "level_3_allowed": False,
        },
    }
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "内容完整性",
        "level_1_title_unchanged": True,
        "domain": "general",
        "category": "技术标完整性说明",
        "refined_children": [
            {"level": 2, "title": "项目概况", "title_source": "generated", "children": []},
            {"level": 2, "title": "编制依据", "title_source": "generated", "children": []},
            {"level": 2, "title": "施工部署", "title_source": "generated", "children": []},
            {"level": 2, "title": "主要施工方法", "title_source": "generated", "children": []},
        ],
        "quality_self_check": {},
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is False
    assert any(issue["type"] == "completeness_misclassified_as_construction" for issue in validation["issues"])


def test_validate_outline_refinement_auto_removes_level_3_for_content_completeness():
    package = {
        "target_outline_node": {
            "node_id": "N1",
            "level_1_title": "内容完整性",
            "category": "技术标完整性说明",
        },
        "granularity_rule": {
            "chapter_type": "technical_bid_completeness_statement",
            "min_level_2_count": 3,
            "max_level_2_count": 5,
            "level_3_required": False,
            "level_3_allowed": False,
            "max_level_3_per_level_2": 0,
            "max_total_level_3_count": 0,
        },
    }
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "内容完整性",
        "level_1_title_unchanged": True,
        "refined_children": [
            {"title": "技术标响应范围", "children": ["响应范围说明"]},
            {"title": "章节完整性说明", "children": []},
            {"title": "完整性承诺", "children": []},
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert any(
        issue["type"] == "level_3_forbidden" and issue["severity"] == "warning"
        for issue in validation["issues"]
    )
    assert output["refined_children"][0]["children"] == []


def test_build_outline_refinement_inputs_triggers_for_thin_core_node():
    outline = {
        "outline_id": "outline_001",
        "nodes": [
            {
                "node_id": "N1",
                "title": "安全管理体系与措施",
                "domain": "construction",
                "category": "安全管理",
                "score_rule": "安全措施完善。",
                "template_source": "generated_from_requirement",
                "children": [{"level": 2, "title": "安全保证措施", "children": []}],
            }
        ],
    }

    packages = build_outline_refinement_inputs(outline, _parse_result())

    assert len(packages) == 1
    package = packages[0]
    assert package["schema_version"] == "outline_refinement_input_v1"
    assert package["target_outline_node"]["level_1_title"] == "安全管理体系与措施"
    assert package["granularity_rule"]["min_level_2_count"] == 6
    assert package["granularity_rule"]["max_level_2_count"] == 8
    assert package["granularity_rule"]["max_level_3_per_level_2"] == 4
    assert package["granularity_rule"]["level_3_required"] is True
    assert "目录过薄" in package["trigger_reasons"]
    assert "核心章节缺少三级目录" in package["trigger_reasons"]


def test_build_outline_refinement_inputs_can_start_from_empty_llm_seed_outline():
    outline = {
        "outline_id": "outline_001",
        "nodes": [
            {
                "node_id": "N1",
                "title": "安全管理体系与措施",
                "domain": "construction",
                "category": "安全管理",
                "score_rule": "安全措施完善。",
                "template_source": "llm_required",
                "children": [],
            }
        ],
    }

    packages = build_outline_refinement_inputs(outline, _parse_result())

    assert len(packages) == 1
    assert packages[0]["target_outline_node"]["existing_children"] == []
    assert packages[0]["excellent_bid_candidates"] == []
    assert "目录过薄" in packages[0]["trigger_reasons"]
    assert "未完全匹配优秀标书范式" in packages[0]["trigger_reasons"]


def test_build_outline_refinement_inputs_keeps_rule_skeleton_as_draft_context():
    outline = {
        "outline_id": "outline_001",
        "nodes": [
            {
                "node_id": "N1",
                "title": "风险管理措施",
                "domain": "construction",
                "category": "风险管理",
                "score_rule": "风险防控管理措施齐全。",
                "template_source": "rule_skeleton_for_llm",
                "children": [
                    {"level": 2, "title": "风险识别", "children": []},
                    {"level": 2, "title": "风险评估", "children": []},
                    {"level": 2, "title": "风险控制措施", "children": []},
                    {"level": 2, "title": "应急预案", "children": []},
                    {"level": 2, "title": "风险动态管理", "children": []},
                ],
            }
        ],
    }

    packages = build_outline_refinement_inputs(outline, _parse_result())

    assert len(packages) == 1
    assert [item["title"] for item in packages[0]["target_outline_node"]["existing_children"]] == [
        "风险识别",
        "风险评估",
        "风险控制措施",
        "应急预案",
        "风险动态管理",
    ]
    assert "核心章节缺少三级目录" in packages[0]["trigger_reasons"]


def test_validate_outline_refinement_output_accepts_valid_result():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {
                "temp_id": "L2_001",
                "level": 2,
                "title": "安全管理目标",
                "title_source": "generated",
                "reason": "补充安全管理目标",
                "requires_review": False,
                "children": [{"temp_id": "L3_001", "level": 3, "title": "安全目标分解", "title_source": "generated"}],
            },
            {"temp_id": "L2_002", "level": 2, "title": "安全管理体系", "title_source": "generated", "children": []},
            {"temp_id": "L2_003", "level": 2, "title": "安全生产责任制", "title_source": "generated", "children": []},
            {"temp_id": "L2_004", "level": 2, "title": "危险源辨识与控制", "title_source": "generated", "children": []},
            {"temp_id": "L2_005", "level": 2, "title": "安全防护措施", "title_source": "generated", "children": []},
            {"temp_id": "L2_006", "level": 2, "title": "应急管理措施", "title_source": "generated", "children": []},
        ],
        "quality_self_check": {},
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert validation["level_2_count"] == 6
    assert validation["level_3_count"] == 1
    assert validation["has_level_3"] is True


def test_validate_outline_refinement_auto_trims_over_expanded_management_outline():
    package = _input_package()
    package["granularity_rule"].update(
        {
            "max_level_2_count": 8,
            "level_3_allowed": True,
            "max_level_3_per_level_2": 4,
            "max_total_level_3_count": 24,
        }
    )
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {"title": f"安全管理子项{i}", "children": [f"三级{j}" for j in range(1, 7)]}
            for i in range(1, 10)
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert validation["level_2_count"] == 8
    assert validation["max_level_3_per_level_2"] == 4
    assert any(
        issue["type"] == "level_2_count_above_max" and issue["severity"] == "warning"
        for issue in validation["issues"]
    )
    assert any(
        issue["type"] == "level_3_per_level_2_above_max" and issue["severity"] == "warning"
        for issue in validation["issues"]
    )
    assert len(output["refined_children"]) == 8
    assert all(len(child["children"]) <= 4 for child in output["refined_children"])


def test_validate_outline_refinement_output_normalizes_mechanical_fields():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {"title": "安全管理目标", "children": [{"title": "安全目标分解"}]},
            {"title": "安全管理体系", "children": []},
            {"title": "安全生产责任制", "children": []},
            {"title": "危险源辨识与控制", "children": []},
            {"title": "安全防护措施", "children": []},
            {"title": "应急管理措施", "children": []},
        ],
        "quality_self_check": {},
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert output["refined_children"][0]["level"] == 2
    assert output["refined_children"][0]["title_source"] == "generated"
    assert output["refined_children"][0]["children"][0]["level"] == 3
    assert output["refined_children"][0]["children"][0]["title_source"] == "generated"


def test_validate_outline_refinement_output_normalizes_string_children():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {"title": "安全管理目标", "children": ["安全目标分解"]},
            {"title": "安全管理体系", "children": []},
            {"title": "安全生产责任制", "children": []},
            {"title": "危险源辨识与控制", "children": []},
            {"title": "安全防护措施", "children": []},
            {"title": "应急管理措施", "children": []},
        ],
        "quality_self_check": {},
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert output["refined_children"][0]["children"][0] == {
        "title": "安全目标分解",
        "level": 3,
        "title_source": "generated",
    }


def test_validate_outline_refinement_output_rejects_modified_level_1_title():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理措施",
        "level_1_title_unchanged": False,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [],
        "quality_self_check": {},
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is False
    assert validation["blocking"] is True
    assert any(issue["type"] == "level_1_modified" for issue in validation["issues"])


def test_validate_outline_refinement_output_accepts_level_1_whitespace_difference():
    package = {
        "target_outline_node": {
            "node_id": "N1",
            "level_1_title": "施工现场实施信息 化监控和数据处理",
        },
        "granularity_rule": {
            "min_level_2_count": 4,
            "level_3_required": False,
        },
    }
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "施工现场实施信息化监控和数据处理",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "信息化与BIM",
        "refined_children": [
            {"title": "BIM应用目标", "children": ["模型与数据关联"]},
            {"title": "信息化监控措施", "children": ["视频监控系统部署"]},
            {"title": "施工数据采集与处理", "children": ["数据统计分析"]},
            {"title": "信息化协同管理", "children": ["移动端协同应用"]},
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert any(issue["type"] == "level_1_whitespace_normalized" for issue in validation["issues"])
    assert output["level_1_title"] == "施工现场实施信息 化监控和数据处理"


def test_validate_outline_refinement_output_normalizes_title_aliases():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {"level_2_title": "安全管理目标", "children": [{"level_3_title": "安全目标分解"}]},
            {"level_2_title": "安全管理体系", "children": []},
            {"level_2_title": "安全生产责任制", "children": []},
            {"level_2_title": "危险源辨识与控制", "children": []},
            {"level_2_title": "安全防护措施", "children": []},
            {"level_2_title": "应急管理措施", "children": []},
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert output["refined_children"][0]["title"] == "安全管理目标"
    assert output["refined_children"][0]["children"][0]["title"] == "安全目标分解"


def test_validate_outline_refinement_output_normalizes_children_aliases():
    package = _input_package()
    output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": "N1",
        "level_1_title": "安全管理体系与措施",
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "安全管理",
        "refined_children": [
            {"level2_title": "安全管理目标", "level3_titles": ["安全目标分解"]},
            {"level2_title": "安全管理体系", "level3_children": []},
            {"level2_title": "安全生产责任制", "grandchildren": []},
            {"level2_title": "危险源辨识与控制", "subsections": []},
            {"level2_title": "安全防护措施", "items": []},
            {"level2_title": "应急管理措施", "children": []},
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert output["refined_children"][0]["title"] == "安全管理目标"
    assert output["refined_children"][0]["children"][0]["title"] == "安全目标分解"


def test_validate_outline_refinement_output_fills_missing_envelope_fields():
    package = _input_package()
    output = {
        "refined_children": [
            {"title": "安全管理目标", "children": [{"title": "安全目标分解"}]},
            {"title": "安全管理体系", "children": []},
            {"title": "安全生产责任制", "children": []},
            {"title": "危险源辨识与控制", "children": []},
            {"title": "安全防护措施", "children": []},
            {"title": "应急管理措施", "children": []},
        ],
    }

    validation = validate_outline_refinement_output(output, package)

    assert validation["valid"] is True
    assert output["schema_version"] == "outline_refinement_v1"
    assert output["target_node_id"] == "N1"
    assert output["level_1_title"] == "安全管理体系与措施"
    assert output["level_1_title_unchanged"] is True


def _parse_result():
    return {
        "project_type": {"value": "construction"},
        "project_info": {
            "project_name": {"value": "示例项目"},
            "construction_location": {"value": "示例地点"},
            "construction_scale": {"value": "示例规模"},
            "tender_scope": {"value": "施工图纸及工程量清单范围"},
            "duration_requirement": {"value": "365日历天"},
            "quality_requirement": {"value": "合格"},
            "safety_civilization_requirement": {"value": "安全文明施工"},
        },
    }


def _input_package():
    return {
        "target_outline_node": {
            "node_id": "N1",
            "level_1_title": "安全管理体系与措施",
        },
        "granularity_rule": {
            "min_level_2_count": 6,
            "level_3_required": True,
        },
    }
