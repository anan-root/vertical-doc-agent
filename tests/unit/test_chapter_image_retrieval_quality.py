from construction_bidding_agent.chapter_generator.image_retrieval_quality import (
    build_chapter_image_retrieval_quality_report,
    render_chapter_image_retrieval_quality_report,
)


def test_image_retrieval_quality_flags_library_hit_missing_from_candidate_pool():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result([_section("地下室及屋面防水施工技术", [])]),
        _chapter_inputs(),
        _library([_slice("SRC0001-M00095", "地下室防水施工", "地下室 防水 卷材", image_count=5)]),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["library_reusable_hit_count"] == 1
    assert section["candidate_image_count"] == 0
    assert any(issue["type"] == "candidate_pool_miss" for issue in section["issues"])
    assert report["summary"]["candidate_pool_miss_section_count"] == 1
    assert report["summary"]["high_risk_count"] == 1


def test_image_retrieval_quality_flags_candidate_not_used():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result([_section("工程测量控制网建立及监测方案", [])]),
        _chapter_inputs(
            image_candidates=[
                _image_candidate("IMG-1", "内控点预留洞口安置示意图", "工程测量 控制网 内控点 引测"),
            ]
        ),
        _library(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["candidate_image_count"] == 1
    assert section["used_image_count"] == 0
    assert any(issue["type"] == "candidate_not_used" for issue in section["issues"])
    assert report["summary"]["candidate_unused_section_count"] == 1


def test_image_retrieval_quality_accepts_used_candidate_images():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result(
            [
                _section(
                    "工程测量控制网建立及监测方案",
                    [
                        _image_ref("IMG-1", "内控点预留洞口安置示意图"),
                        _image_ref("IMG-2", "轴线竖向引测示意图"),
                    ],
                )
            ]
        ),
        _chapter_inputs(
            image_candidates=[
                _image_candidate("IMG-1", "内控点预留洞口安置示意图", "工程测量 控制网 内控点 引测"),
            ]
        ),
        _library(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["used_image_count"] == 2
    assert section["issues"] == []
    assert section["conclusion"] == "召回与使用基本合理"


def test_image_retrieval_quality_does_not_match_concrete_precast_block_as_pouring_candidate():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result([_section("混凝土浇筑及大体积温控措施", [])]),
        _chapter_inputs(
            image_candidates=[
                _image_candidate("IMG-1", "门窗洞口混凝土预制块做法", "砌体 门窗洞口 混凝土预制块 套管"),
            ]
        ),
        _library(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["candidate_image_count"] == 0
    assert not any(issue["type"] == "candidate_not_used" for issue in section["issues"])


def test_image_retrieval_quality_warns_when_usage_is_too_conservative():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result([_section("地下室及屋面防水施工技术", [_image_ref("IMG-USED", "阴角防水细部做法")])]),
        _chapter_inputs(
            image_candidates=[
                _image_candidate(f"IMG-{index}", f"地下室防水卷材节点做法{index}", "地下室 防水 卷材 阴角 止水")
                for index in range(1, 7)
            ]
        ),
        _library(),
    )

    section = report["chapter_reviews"][0]["section_reviews"][0]

    assert section["used_image_count"] == 1
    assert section["candidate_image_count"] == 6
    assert any(issue["type"] == "image_usage_too_conservative" for issue in section["issues"])
    assert report["summary"]["low_usage_section_count"] == 1


def test_render_image_retrieval_quality_report_contains_section_rows():
    report = build_chapter_image_retrieval_quality_report(
        _generation_result([_section("后浇带及变形缝处理专项方案", [])]),
        _chapter_inputs(),
        _library([_slice("SRC0001-M00120", "后浇带方案设计概况", "后浇带 变形缝 止水", image_count=4)]),
    )

    markdown = render_chapter_image_retrieval_quality_report(report)

    assert "# 章节图片素材召回质量报告" in markdown
    assert "后浇带及变形缝处理专项方案" in markdown
    assert "候选池漏召回小节数" in markdown
    assert "| 序号 | 章节 | 小节 | 已用图 | 候选图 | 候选套图 | 素材摘要 | 全库可用素材 | 问题 | 结论 |" in markdown


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
                "image_group_candidates": [],
                "image_group_candidate_pool": [],
                "material_retrieval_summary": {"image_group_summary": []},
                "excellent_bid_references": [],
            }
        ]
    }


def _library(slices=None):
    return {
        "schema_version": "excellent_bid_material_library_v1",
        "slices": slices or [],
    }


def _slice(material_slice_id, title, search_text, *, image_count):
    return {
        "material_slice_id": material_slice_id,
        "title": title,
        "clean_title": title,
        "section_path": ["主要施工方案与技术措施", title],
        "search_text": search_text,
        "image_count": image_count,
        "image_group_count": 0,
        "material_quality": "high",
        "reuse_level": "parameterized_reuse",
        "project_specific_risk": "medium",
        "primary_material_source": "docx",
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


def _image_ref(image_id, caption):
    return {"type": "image_ref", "image_id": image_id, "caption": caption}


def _image_candidate(image_id, caption, semantic_text):
    return {
        "image_id": image_id,
        "image_asset_id": f"{image_id}-ASSET",
        "caption": caption,
        "semantic_text": semantic_text,
        "semantic_confidence": 0.9,
        "source_section_path": ["主要施工方案与技术措施", "优秀素材"],
        "reuse_level": "candidate_reuse",
        "risk_level": "low",
        "material_quality": "high",
        "use_policy": "candidate_reuse",
    }
