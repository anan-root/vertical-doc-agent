from construction_bidding_agent.chapter_generator.outline_package_consistency import (
    build_outline_package_consistency,
    render_outline_package_consistency_report,
)


def test_outline_package_consistency_accepts_split_and_excluded_management_nodes():
    outline = {
        "outline_id": "O1",
        "nodes": [
            _node("L1", "主要施工方案与技术措施", "construction", [_node("L1-1", "项目概况"), _node("L1-2", "土建施工方案")]),
            _node("L2", "质量管理体系与措施", "management", [_node("L2-1", "质量目标")]),
        ],
    }
    chapter_inputs = {
        "packages": [
            _package("U1", "L1-1", "L1", ["主要施工方案与技术措施", "项目概况"], []),
            _package("U2", "L1-2", "L1", ["主要施工方案与技术措施", "土建施工方案"], []),
        ]
    }

    result = build_outline_package_consistency(outline, chapter_inputs)

    assert result["status"] == "pass"
    assert result["outline_level1_count"] == 2
    assert result["package_count"] == 2
    assert result["packaged_level1_count"] == 1
    assert result["unpackaged_level1_count"] == 1
    assert result["issue_counts"]["error"] == 0
    assert result["issue_counts"]["warning"] == 0
    assert result["top_level_mappings"][0]["status"] == "scheduled"
    assert result["top_level_mappings"][1]["status"] == "not_scheduled"
    assert "非 construction 领域" in result["top_level_mappings"][1]["notes"][1]


def test_outline_package_consistency_flags_score_point_heading_mismatch_and_child_mismatch():
    outline = {
        "nodes": [
            _node(
                "L1",
                "工期保证措施",
                "construction",
                [_node("L1-1", "工期目标"), _node("L1-2", "资源保障")],
            )
        ]
    }
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "L1",
                "L1",
                ["工期保障措施"],
                ["工期目标"],
                unit_type="level1_chapter",
                score_point_raw="工期保证措施",
            )
        ]
    }

    result = build_outline_package_consistency(outline, chapter_inputs)
    issue_types = {issue["type"] for issue in result["issues"]}

    assert result["status"] == "fail"
    assert "score_point_heading_mismatch" in issue_types
    assert "chapter_path_mismatch" in issue_types
    assert "child_headings_mismatch" in issue_types

    report = render_outline_package_consistency_report(result)
    assert "技术标目录与正文生成单元一致性检查报告" in report
    assert "评分点原文" in report


def _node(node_id, title, domain="construction", children=None):
    return {"node_id": node_id, "title": title, "domain": domain, "children": children or []}


def _package(unit_id, target_id, parent_id, chapter_path, child_headings, unit_type="level2_section_group", score_point_raw=None):
    return {
        "generation_unit": {
            "unit_id": unit_id,
            "target_node_id": target_id,
            "parent_level_1_node_id": parent_id,
            "unit_type": unit_type,
            "chapter_path": chapter_path,
            "child_headings": child_headings,
        },
        "score_point": {"score_point_raw": score_point_raw or chapter_path[0]},
    }
