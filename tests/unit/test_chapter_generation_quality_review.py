from construction_bidding_agent.chapter_generator.quality_review import (
    build_chapter_generation_quality_review,
    render_chapter_generation_quality_review,
)


def test_quality_review_scores_complete_chapter_ready_for_full_generation():
    review = build_chapter_generation_quality_review(_generation_result(), _chapter_inputs())

    assert review["chapter_count"] == 1
    assert review["summary"]["ready_for_full_generation"] is True
    assert review["chapter_reviews"][0]["quality_score"] >= 90
    assert review["chapter_reviews"][0]["metrics"]["heading_coverage_ratio"] == 1
    assert "完整覆盖输入目录小节" in review["chapter_reviews"][0]["strengths"]


def test_quality_review_flags_missing_heading_and_table_underuse():
    generation_result = _generation_result()
    generation_result["chapters"][0]["sections"] = [
        {"heading": "施工进度计划编制依据", "level": 2, "blocks": [{"type": "paragraph", "text": "正文。"}]}
    ]

    review = build_chapter_generation_quality_review(generation_result, _chapter_inputs())

    issues = review["chapter_reviews"][0]["issues"]
    assert any(issue["type"] == "heading_coverage" for issue in issues)
    assert any(issue["type"] == "table_underuse" for issue in issues)
    assert review["summary"]["ready_for_full_generation"] is False


def test_render_quality_review_contains_summary_and_chapter_detail():
    review = build_chapter_generation_quality_review(_generation_result(), _chapter_inputs())

    report = render_chapter_generation_quality_review(review)

    assert "# 章节生成质量评审报告" in report
    assert "施工进度表" in report
    assert "平均质量分" in report


def _generation_result():
    return {
        "schema_version": "chapter_generation_run_v0.1",
        "failed_count": 0,
        "chapters": [
            {
                "schema_version": "technical_bid_chapter_draft_v1",
                "unit_id": "GU-N1",
                "target_node_id": "N1",
                "chapter_path": ["施工进度表"],
                "title": "施工进度表",
                "sections": [
                    {
                        "heading": "施工进度计划编制依据",
                        "level": 2,
                        "blocks": [
                            {"type": "paragraph", "text": "根据招标工期和施工图纸编制进度计划。"},
                            {
                                "type": "rich_table",
                                "title": "控制要点表",
                                "columns": [{"key": "col_1", "title": "序号"}],
                                "rows": [{"cells": {"col_1": "1"}}],
                            },
                        ],
                    },
                    {
                        "heading": "关键线路控制措施",
                        "level": 2,
                        "blocks": [{"type": "paragraph", "text": "围绕关键线路实施动态纠偏。"}],
                    },
                    {
                        "heading": "进度保障措施",
                        "level": 2,
                        "blocks": [{"type": "paragraph", "text": "配置劳动力、材料和机械资源。"}],
                    },
                    {
                        "heading": "节点纠偏措施",
                        "level": 2,
                        "blocks": [{"type": "paragraph", "text": "出现偏差时及时调整流水段。"}],
                    },
                ],
                "score_response_check": {
                    "covered": True,
                    "response_summary": "围绕关键线路和工期目标响应评分点。",
                },
                "source_usage": [{"ref_id": "SRC1", "usage": "结构参考"}],
                "review_items": [{"message": "人工补充最终进度图。"}],
            }
        ],
        "tasks": [
            {
                "unit_id": "GU-N1",
                "chapter_path": ["施工进度表"],
                "status": "completed",
                "validation": {"issue_count": 0, "issues": []},
            }
        ],
    }


def _chapter_inputs():
    return {
        "packages": [
            {
                "generation_unit": {
                    "unit_id": "GU-N1",
                    "chapter_path": ["施工进度表"],
                    "child_headings": ["施工进度计划编制依据", "关键线路控制措施", "进度保障措施", "节点纠偏措施"],
                },
                "score_point": {"score_standard_raw": "关键线路清晰、准确、完整。"},
                "excellent_bid_references": [{"ref_id": "SRC1"}],
                "table_references": [{"table_id": "T1"}, {"table_id": "T2"}, {"table_id": "T3"}],
                "image_candidates": [],
                "reuse_warnings": [],
            }
        ]
    }
