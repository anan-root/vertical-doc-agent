from construction_bidding_agent.chapter_generator.parameter_conflict_guard import (
    apply_parameter_conflict_guard,
    extract_hard_constraints,
    find_output_parameter_conflict_residuals,
)


def test_extracts_experience_year_hard_constraint():
    constraints = extract_hard_constraints(
        parse_result={
            "technical_score_points": [
                {
                    "original_text": "项目负责人配置",
                    "score_rule": "项目负责人具有不少于8年施工管理经验。",
                }
            ]
        }
    )

    assert constraints[0]["category"] == "experience_years"
    assert constraints[0]["operator"] == ">="
    assert constraints[0]["value"] == 8


def test_apply_guard_downgrades_lower_experience_material():
    materials = [
        {
            "material_slice_id": "M1",
            "title": "项目负责人配置",
            "reuse_level": "direct_reuse",
            "paragraphs": [{"text_preview": "项目负责人具有5年施工管理经验。"}],
            "tables": [],
        }
    ]

    scan = apply_parameter_conflict_guard(
        materials,
        parse_result={
            "technical_score_points": [
                {"score_rule": "项目负责人具有不少于8年施工管理经验。"}
            ]
        },
    )

    assert scan["conflicts"]
    assert materials[0]["reuse_level"] == "manual_review"
    assert materials[0]["review_required"] is True
    assert materials[0]["parameter_conflicts"][0]["category"] == "experience_years"


def test_apply_guard_keeps_higher_experience_material():
    materials = [
        {
            "material_slice_id": "M1",
            "title": "项目负责人配置",
            "reuse_level": "direct_reuse",
            "paragraphs": [{"text_preview": "项目负责人具有10年施工管理经验。"}],
            "tables": [],
        }
    ]

    scan = apply_parameter_conflict_guard(
        materials,
        parse_result={
            "technical_score_points": [
                {"score_rule": "项目负责人具有不少于8年施工管理经验。"}
            ]
        },
    )

    assert scan["conflicts"] == []
    assert materials[0]["reuse_level"] == "direct_reuse"


def test_apply_guard_flags_duration_and_performance_count():
    materials = [
        {
            "material_slice_id": "M1",
            "title": "工期及类似业绩",
            "reuse_level": "direct_reuse",
            "paragraphs": [{"text_preview": "计划工期90日历天，具有2个类似业绩。"}],
            "tables": [],
        }
    ]

    scan = apply_parameter_conflict_guard(
        materials,
        parse_result={
            "technical_bid_requirements": [
                {"content": "计划工期不超过60日历天。"},
                {"content": "投标人具有不少于3个类似业绩。"},
            ]
        },
    )

    categories = {item["category"] for item in scan["conflicts"]}
    assert {"duration_days", "similar_performance_count"} <= categories
    assert materials[0]["reuse_level"] == "manual_review"


def test_apply_guard_flags_qualification_grade():
    materials = [
        {
            "material_slice_id": "M1",
            "title": "企业资质",
            "reuse_level": "direct_reuse",
            "paragraphs": [{"text_preview": "企业具备建筑工程施工总承包二级资质。"}],
            "tables": [],
        }
    ]

    scan = apply_parameter_conflict_guard(
        materials,
        parse_result={
            "technical_bid_requirements": [
                {"content": "企业须具备建筑工程施工总承包一级资质。"}
            ]
        },
    )

    assert scan["conflicts"][0]["category"] == "qualification_grade"
    assert materials[0]["reuse_level"] == "manual_review"


def test_output_residual_detects_lower_experience():
    residuals = find_output_parameter_conflict_residuals(
        {
            "sections": [
                {
                    "blocks": [
                        {"type": "paragraph", "text": "项目负责人具有5年施工管理经验。"}
                    ]
                }
            ]
        },
        {
            "generation_constraints": {
                "parameter_conflict_scan": {
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
                }
            }
        },
    )

    assert residuals[0]["type"] == "parameter_conflict_residual"
