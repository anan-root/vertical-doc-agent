from construction_bidding_agent.outline_generator.generator import build_outline_tree


def test_outline_uses_score_point_display_title_as_level_1_title_and_keeps_raw_source():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "主要施工方案 与技术措施",
                    "catalog_level_1_title": "主要施工方案与技术措施",
                    "source_refs": [{"type": "cell", "id": "B1_R0_C0"}],
                    "score_rule": "施工方案总体安排合理，针对施工难点提出建议。",
                    "review_required": False,
                }
            ]
        ),
        excellent_bid_index=_excellent_bid_index(),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    assert result["status"] == "completed"
    assert result["can_generate_chapters"] is True
    assert result["nodes"][0]["title"] == "主要施工方案与技术措施"
    assert result["nodes"][0]["title_source"] == "score_point_normalized"
    assert result["nodes"][0]["score_point_original_title"] == "主要施工方案 与技术措施"
    assert result["nodes"][0]["children"][0]["title"] == "项目概况"
    assert result["nodes"][0]["template_source"] == "excellent_bid_template"
    assert result["quality_checks"][1]["status"] == "passed"


def test_outline_blocks_when_parse_result_disallows_outline_generation():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "施工组织设计",
                    "catalog_level_1_title": "施工组织设计",
                    "source_refs": [{"type": "cell", "id": "B1_R0_C0"}],
                    "blocks_outline_generation": True,
                    "review_required": True,
                }
            ],
            can_generate_outline=False,
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    assert result["status"] == "blocked"
    assert result["can_generate_chapters"] is False
    assert result["nodes"][0]["requires_review"] is True
    assert any(item["priority"] == "blocking" for item in result["review_items"])
    assert result["quality_checks"][-1]["status"] == "failed"


def test_outline_keeps_epc_design_score_point_but_does_not_use_construction_template():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "设计方案及优化建议",
                    "catalog_level_1_title": "设计方案及优化建议",
                    "source_refs": [{"type": "cell", "id": "B2_R0_C0"}],
                    "score_rule": "设计方案合理，绿色建筑与限额设计措施完善。",
                    "review_required": False,
                },
                {
                    "score_point_id": "TSP002",
                    "original_text": "施工组织设计",
                    "catalog_level_1_title": "施工组织设计",
                    "source_refs": [{"type": "cell", "id": "B3_R0_C0"}],
                    "score_rule": "施工部署合理。",
                    "review_required": False,
                },
            ],
            project_type="epc",
        ),
        excellent_bid_index=_excellent_bid_index(),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    design_node = result["nodes"][0]
    construction_node = result["nodes"][1]
    assert design_node["domain"] == "design"
    assert design_node["template_source"] == "generated_from_requirement"
    assert design_node["generation_status"] == "design_pending"
    assert design_node["requires_review"] is True
    assert construction_node["domain"] == "construction"
    assert result["can_export_construction_only"] is True
    assert result["quality_checks"][3]["status"] == "passed"


def test_outline_classification_prefers_score_point_title_over_score_rule_noise():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "拟投入资源配备计划",
                    "catalog_level_1_title": "拟投入资源配备计划",
                    "source_refs": [{"type": "cell", "id": "B4_R0_C0"}],
                    "score_rule": "计划合理，质量、安全、进度保证措施完善。",
                    "review_required": False,
                }
            ]
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    node = result["nodes"][0]
    assert node["category"] == "资源投入"
    assert [child["title"] for child in node["children"]] == [
        "劳动力投入计划",
        "主要机械设备投入计划",
        "材料供应计划",
        "资源保障措施",
    ]


def test_outline_content_completeness_is_response_explanation_not_construction_plan():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "内容完整性",
                    "catalog_level_1_title": "内容完整性",
                    "source_refs": [{"type": "cell", "id": "B10_R0_C0"}],
                    "score_rule": "技术标的主要内容具有完整性，符合招标文件要求。",
                    "review_required": False,
                }
            ]
        ),
        excellent_bid_index=_excellent_bid_index(),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    node = result["nodes"][0]
    child_titles = [child["title"] for child in node["children"]]
    assert node["domain"] == "general"
    assert node["category"] == "技术标完整性说明"
    assert node["generation_status"] == "general_ready"
    assert node["template_source"] == "generated"
    assert child_titles == [
        "技术标响应范围",
        "评分点逐项响应说明",
        "章节完整性组织",
        "响应依据与编制原则",
        "技术标完整性承诺",
    ]
    assert "项目概况" not in child_titles
    assert "编制依据" not in child_titles
    assert "施工部署" not in child_titles


def test_outline_classifies_new_technology_before_resource_material_keyword():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "采用新工艺、新技术、新设备、新材料、BIM等的程度",
                    "catalog_level_1_title": "采用新工艺、新技术、新设备、新材料、BIM等的程度",
                    "source_refs": [{"type": "cell", "id": "B5_R0_C0"}],
                    "score_rule": "应用程度先进合理。",
                    "review_required": False,
                }
            ]
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    node = result["nodes"][0]
    assert node["category"] == "技术创新"
    assert "BIM 应用措施" in [child["title"] for child in node["children"]]


def test_outline_project_understanding_uses_project_understanding_children():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "对招标项目的理解",
                    "catalog_level_1_title": "对招标项目的理解",
                    "source_refs": [{"type": "cell", "id": "B6_R0_C0"}],
                    "score_rule": "对设计方案、施工方案理解全面。",
                    "review_required": False,
                }
            ],
            project_type="epc",
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    node = result["nodes"][0]
    assert node["domain"] == "management"
    assert node["category"] == "项目理解"
    assert [child["title"] for child in node["children"]][:2] == ["项目概况理解", "招标范围与建设目标理解"]


def test_outline_confirmation_model_locks_level_1_and_allows_child_edits():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "施工总平面布置图",
                    "catalog_level_1_title": "施工总平面布置图",
                    "source_refs": [{"type": "cell", "id": "B7_R0_C0"}],
                    "score_rule": "平面布置合理。",
                    "review_required": False,
                }
            ]
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    parent = result["nodes"][0]
    child = parent["children"][0]
    assert parent["confirmation_state"]["title_locked"] is True
    assert parent["confirmation_state"]["delete_forbidden"] is True
    assert "edit_title" not in parent["confirmation_state"]["allowed_actions"]
    assert child["confirmation_state"]["title_locked"] is False
    assert "edit_title" in child["confirmation_state"]["allowed_actions"]
    assert result["confirmation"]["rules"]["level_1_title_locked"] is True
    assert result["confirmation"]["summary"]["locked_level_1_count"] == 1


def test_outline_confirmation_review_queue_targets_review_nodes():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "风险管理措施",
                    "catalog_level_1_title": "风险管理措施",
                    "source_refs": [{"type": "cell", "id": "B8_R0_C0"}],
                    "score_rule": "风险识别和应急措施完善。",
                    "review_required": True,
                }
            ]
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )

    queue = result["confirmation"]["review_queue"]
    assert result["confirmation"]["status"] == "pending_review"
    assert queue[0]["target_node_id"] == result["nodes"][0]["node_id"]
    assert queue[0]["status"] == "pending"
    assert result["nodes"][0]["confirmation_state"]["review_status"] == "pending_review"


def test_outline_confirmation_flattens_level_3_nodes():
    result = build_outline_tree(
        _parse_result(
            [
                {
                    "score_point_id": "TSP001",
                    "original_text": "施工组织设计",
                    "catalog_level_1_title": "施工组织设计",
                    "source_refs": [{"type": "cell", "id": "B9_R0_C0"}],
                    "score_rule": "施工部署合理。",
                    "review_required": False,
                }
            ]
        ),
        generated_at="2026-05-04T10:00:00+08:00",
        outline_id="outline_test",
    )
    child = result["nodes"][0]["children"][0]
    child["children"] = [
        {
            "node_id": "outline_test_001_001_001",
            "level": 3,
            "number": "1.1.1",
            "title": "总体施工思路",
            "title_source": "generated",
            "domain": "construction",
            "category": "施工方案",
            "children": [],
            "requires_review": False,
            "generation_status": "construction_ready",
        }
    ]
    from construction_bidding_agent.outline_generator.generator import refresh_outline_confirmation

    refresh_outline_confirmation(result)

    flat_nodes = result["confirmation"]["flat_nodes"]
    assert any(item["level"] == 3 and item["title"] == "总体施工思路" for item in flat_nodes)
    assert result["nodes"][0]["children"][0]["children"][0]["confirmation_state"]["review_status"] == "auto_checked"


def _parse_result(points, *, project_type="construction", can_generate_outline=True):
    return {
        "parse_job": {"job_id": "job_001"},
        "execution": {"can_generate_outline": can_generate_outline},
        "project_type": {"value": project_type},
        "technical_score_points": points,
    }


def _excellent_bid_index():
    return {
        "slices": [
            {
                "slice_id": "S0",
                "heading_index": 0,
                "level": 1,
                "section_path": ["针对本项目施工管理提出总体施工方案"],
            },
            {
                "slice_id": "S1",
                "heading_index": 1,
                "level": 2,
                "section_path": ["针对本项目施工管理提出总体施工方案", "项目概况"],
            },
            {
                "slice_id": "S2",
                "heading_index": 2,
                "level": 2,
                "section_path": ["针对本项目施工管理提出总体施工方案", "编制依据"],
            },
        ]
    }
