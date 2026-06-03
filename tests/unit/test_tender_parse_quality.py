from construction_bidding_agent.document_parser.tender_parse_quality import (
    build_quality_baseline,
    compare_quality_to_baseline,
    extract_quality_metrics,
)


def test_extract_quality_metrics_keeps_core_quality_signals():
    metrics = extract_quality_metrics(_parse_result())

    assert metrics.file_name == "sample.docx"
    assert metrics.project_type == "construction"
    assert metrics.can_generate_outline is True
    assert metrics.technical_score_point_count == 2
    assert metrics.score_point_source_ref_count == 2
    assert metrics.score_point_titles_from_original_count == 2
    assert metrics.project_info_missing_fields == ["construction_scale"]


def test_compare_quality_to_baseline_flags_regressions():
    baseline = build_quality_baseline([_parse_result()])
    regressed = _parse_result()
    regressed["technical_score_points"] = regressed["technical_score_points"][:1]
    regressed["project_info"]["quality_requirement"]["value"] = "未明确"

    comparison = compare_quality_to_baseline(baseline, [regressed])

    assert comparison["status"] == "failed"
    issues = comparison["comparisons"][0]["issues"]
    assert any("技术评分点数量下降" in issue for issue in issues)
    assert any("项目基础信息新增缺失字段" in issue for issue in issues)


def test_compare_quality_to_baseline_passes_same_result():
    baseline = build_quality_baseline([_parse_result()])

    comparison = compare_quality_to_baseline(baseline, [_parse_result()])

    assert comparison["status"] == "passed"
    assert comparison["failed_count"] == 0


def _parse_result():
    return {
        "parse_job": {"status": "completed_with_warnings"},
        "execution": {"can_generate_outline": True},
        "input_files": [{"file_name": "sample.docx"}],
        "project_type": {"value": "construction"},
        "project_info": {
            "project_name": {"value": "样例项目"},
            "construction_location": {"value": "河南"},
            "construction_scale": {"value": "未明确"},
            "tender_scope": {"value": "施工图范围"},
            "duration_requirement": {"value": "180日历天"},
            "quality_requirement": {"value": "合格"},
            "safety_civilization_requirement": {"value": "达到安全文明标准"},
        },
        "technical_score_points": [
            {
                "original_text": "主要施工方案与技术措施",
                "catalog_level_1_title": "主要施工方案与技术措施",
                "source_refs": [{"id": "B1_R0_C0"}],
            },
            {
                "original_text": "质量管理体系与措施",
                "catalog_level_1_title": "质量管理体系与措施",
                "source_refs": [{"id": "B2_R0_C0"}],
            },
        ],
        "technical_bid_requirements": [{"id": "r1"}],
        "technical_standards": [{"id": "s1"}],
        "review_items": [{"id": "review1"}],
        "warnings": [{"level": "medium"}],
    }
