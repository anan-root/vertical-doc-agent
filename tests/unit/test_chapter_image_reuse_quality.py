from construction_bidding_agent.chapter_generator.image_reuse_quality import (
    build_chapter_image_reuse_quality_report,
    render_chapter_image_reuse_quality_report,
)


def test_image_reuse_quality_accepts_complete_grouped_images():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section(
                    "工程测量控制网建立及监测方案",
                    [
                        _image("IMG-1", "内控点预留洞口安置示意图", group_id="G-1", member_index=1, member_count=2),
                        _image("IMG-2", "楼层轴线引测示意图", group_id="G-1", member_index=2, member_count=2),
                    ],
                )
            ]
        ),
        _chapter_inputs(),
    )

    assert report["summary"]["image_count"] == 2
    assert report["summary"]["image_group_count"] == 1
    assert report["summary"]["split_group_count"] == 0
    assert report["summary"]["duplicate_image_count"] == 0
    assert report["summary"]["high_risk_count"] == 0
    assert report["summary"]["medium_risk_count"] == 0
    assert report["chapter_reviews"][0]["section_reviews"][0]["conclusion"] == "图片复用基本合理"


def test_image_reuse_quality_flags_split_group():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section(
                    "工程测量控制网建立及监测方案",
                    [_image("IMG-1", "内控点预留洞口安置示意图", group_id="G-1", member_index=1, member_count=3)],
                )
            ]
        ),
        _chapter_inputs(),
    )

    chapter_issues = report["chapter_reviews"][0]["chapter_issues"]

    assert report["summary"]["split_group_count"] == 1
    assert any(issue["type"] == "split_image_group" for issue in chapter_issues)
    assert report["summary"]["high_risk_count"] >= 1


def test_image_reuse_quality_flags_duplicate_images():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section("模板支撑体系搭设措施", [_image("IMG-1", "梁板模板支撑节点示意图")]),
                _section("砌体工程施工及防裂技术措施", [_image("IMG-1", "梁板模板支撑节点示意图")]),
            ]
        ),
        _chapter_inputs(),
    )

    chapter_issues = report["chapter_reviews"][0]["chapter_issues"]

    assert report["summary"]["duplicate_image_count"] == 1
    assert any(issue["type"] == "duplicate_image" for issue in chapter_issues)
    assert report["summary"]["high_risk_count"] >= 1


def test_image_reuse_quality_flags_concrete_topic_conflict():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section(
                    "混凝土浇筑及大体积温控措施",
                    [_image("IMG-1", "穿墙套管混凝土预制块做法示意图", semantic_text="穿墙套管混凝土预制块")],
                )
            ]
        ),
        _chapter_inputs(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]
    image_issue_types = {issue["type"] for issue in section["images"][0]["issues"]}

    assert "topic_conflict" in image_issue_types
    assert any(issue["type"] == "section_has_high_risk_images" for issue in section["issues"])
    assert section["conclusion"] == "需人工复核"


def test_image_reuse_quality_accepts_management_flow_image():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section(
                    "质量管理体系与检查闭环措施",
                    [_image("IMG-1", "质量检查整改闭环流程图", semantic_text="质量管理 检查 整改 闭环 流程")],
                )
            ]
        ),
        _chapter_inputs(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["issues"] == []
    assert section["images"][0]["issues"] == []
    assert section["conclusion"] == "图片复用基本合理"


def test_image_reuse_quality_warns_management_section_with_unrelated_photo():
    report = build_chapter_image_reuse_quality_report(
        _generation_result(
            [
                _section(
                    "质量管理体系与责任分工措施",
                    [_image("IMG-1", "钢筋绑扎样板照片", semantic_text="钢筋绑扎 样板 照片")],
                )
            ]
        ),
        _chapter_inputs(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]
    image_issue_types = {issue["type"] for issue in section["images"][0]["issues"]}

    assert "management_image_type" in image_issue_types
    assert any(issue["type"] == "section_has_medium_risk_images" for issue in section["issues"])
    assert section["conclusion"] == "建议抽查"


def test_image_reuse_quality_warns_missing_expected_process_image():
    report = build_chapter_image_reuse_quality_report(
        _generation_result([_section("地下室及屋面防水施工技术", [])]),
        _chapter_inputs(
            image_candidates=[
                {
                    "image_id": "IMG-WP-1",
                    "caption": "地下室防水卷材搭接做法",
                    "semantic_text": "地下室 防水 卷材 搭接",
                }
            ]
        ),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert any(issue["type"] == "missing_expected_image" for issue in section["issues"])
    assert section["conclusion"] == "建议抽查"
    assert any("地下室及屋面防水施工技术" in item["message"] for item in report["recommendations"])


def test_render_image_reuse_quality_report_contains_readable_rows():
    report = build_chapter_image_reuse_quality_report(
        _generation_result([_section("工程测量控制网建立及监测方案", [_image("IMG-1", "内控点示意图")])]),
        _chapter_inputs(),
    )

    markdown = render_chapter_image_reuse_quality_report(report)

    assert "# 章节图片复用质量报告" in markdown
    assert "工程测量控制网建立及监测方案" in markdown
    assert "内控点示意图" in markdown
    assert "| 序号 | 章节 | 小节 | 图片 | 套图 | 散图 | 风险 | 结论 |" in markdown


def _generation_result(sections):
    return {
        "schema_version": "chapter_generation_run_v0.1",
        "chapters": [
            {
                "unit_id": "GU-N1",
                "target_node_id": "N1",
                "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                "sections": sections,
            }
        ],
    }


def _chapter_inputs(image_candidates=None):
    return {
        "packages": [
            {
                "generation_unit": {"unit_id": "GU-N1"},
                "image_candidates": image_candidates or [],
                "image_candidate_pool": image_candidates or [],
                "image_group_candidate_pool": [],
            }
        ]
    }


def _section(heading, images):
    return {
        "heading": heading,
        "level": 2,
        "blocks": [
            {"type": "paragraph", "text": f"{heading}正文。"},
            *images,
        ],
    }


def _image(
    image_id,
    caption,
    *,
    semantic_text=None,
    group_id=None,
    member_index=None,
    member_count=None,
):
    return {
        "type": "image_ref",
        "image_id": image_id,
        "caption": caption,
        "source_part_name": f"word/media/{image_id}.png",
        "source_section_path": ["土建施工方案", "示例小节"],
        "semantic_text": semantic_text or caption,
        "semantic_confidence": 0.9,
        "reuse_level": "candidate_reuse",
        "risk_level": "low",
        "image_group_id": group_id,
        "group_title": "示例套图" if group_id else None,
        "group_member_index": member_index,
        "group_member_count": member_count,
        "must_keep_with_group": bool(group_id),
    }
