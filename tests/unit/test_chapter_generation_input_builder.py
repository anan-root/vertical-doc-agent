from construction_bidding_agent.chapter_generator.input_builder import (
    build_chapter_generation_inputs,
    render_chapter_generation_input_report,
)


def test_core_construction_node_is_split_by_level_2_and_keeps_level_1_raw_title():
    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
    )

    assert len(packages) == 2
    first = packages[0]
    assert first["schema_version"] == "chapter_generation_input_v1"
    assert first["generation_unit"]["unit_type"] == "level2_section_group"
    assert first["generation_unit"]["chapter_path"] == ["主要施工方案与技术措施", "项目概况"]
    assert first["generation_unit"]["child_headings"] == ["工程基本情况", "现场条件分析"]
    assert first["score_point"]["score_point_raw"] == "主要施工方案与技术措施"
    assert first["score_point"]["must_use_original_text_as_heading"] is True


def test_large_civil_level2_node_is_split_into_level3_units():
    outline = _outline()
    civil_node = {
        "node_id": "N1_003",
        "level": 2,
        "number": "1.3",
        "title": "土建施工方案与技术措施",
        "domain": "construction",
        "category": "施工方案",
        "template_refs": [],
        "children": [
            {"node_id": "N1_003_001", "level": 3, "title": "测量放线施工方案"},
            {"node_id": "N1_003_002", "level": 3, "title": "钢筋工程施工方案"},
            {"node_id": "N1_003_003", "level": 3, "title": "模板工程施工方案"},
            {"node_id": "N1_003_004", "level": 3, "title": "混凝土工程施工方案"},
        ],
    }
    outline["nodes"][0]["children"] = [civil_node]

    packages = build_chapter_generation_inputs(outline, _parse_result())

    assert len(packages) == 4
    first = packages[0]["generation_unit"]
    assert first["unit_type"] == "level3_subsection_unit"
    assert first["split_from_unit_type"] == "level2_section_group"
    assert first["parent_level_2_node_id"] == "N1_003"
    assert first["parent_level_2_title"] == "土建施工方案与技术措施"
    assert first["chapter_path"] == ["主要施工方案与技术措施", "土建施工方案与技术措施", "测量放线施工方案"]


def test_large_split_uses_level2_title_not_level1_title():
    outline = _outline()
    general_node = {
        "node_id": "N1_003",
        "level": 2,
        "number": "1.3",
        "title": "工程重点难点分析及对策",
        "domain": "construction",
        "category": "施工方案",
        "template_refs": [],
        "children": [
            {"node_id": f"N1_003_{index:03d}", "level": 3, "title": f"重点难点对策{index}"}
            for index in range(1, 7)
        ],
    }
    outline["nodes"][0]["children"] = [general_node]

    packages = build_chapter_generation_inputs(outline, _parse_result())

    assert len(packages) == 1
    unit = packages[0]["generation_unit"]
    assert unit["unit_type"] == "level2_section_group"
    assert unit["chapter_path"] == ["主要施工方案与技术措施", "工程重点难点分析及对策"]
    assert len(unit["child_headings"]) == 6


def test_large_civil_level2_split_is_bounded_to_key_process_units():
    outline = _outline()
    child_titles = [
        "测量放线施工方案",
        "土方开挖施工方案",
        "钢筋工程施工方案",
        "模板工程施工方案",
        "混凝土工程施工方案",
        "防水工程施工方案",
        "砌体工程施工方案",
        "脚手架工程施工方案",
        "成品保护措施",
        "资料管理措施",
    ]
    civil_node = {
        "node_id": "N1_003",
        "level": 2,
        "number": "1.3",
        "title": "土建施工方案与技术措施",
        "domain": "construction",
        "category": "施工方案",
        "template_refs": [],
        "children": [
            {"node_id": f"N1_003_{index:03d}", "level": 3, "title": title}
            for index, title in enumerate(child_titles, start=1)
        ],
    }
    outline["nodes"][0]["children"] = [civil_node]

    packages = build_chapter_generation_inputs(outline, _parse_result())

    units = [package["generation_unit"] for package in packages]
    assert len(units) == 8
    assert {unit["unit_type"] for unit in units} == {"level3_subsection_unit"}
    assert "成品保护措施" not in [unit["chapter_path"][-1] for unit in units]
    assert "资料管理措施" not in [unit["chapter_path"][-1] for unit in units]


def test_content_completeness_node_is_not_split_even_when_category_is_construction_plan():
    outline = _outline()
    outline["nodes"][0]["title"] = "内容完整性"
    packages = build_chapter_generation_inputs(
        outline,
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
    )

    assert len(packages) == 1
    assert packages[0]["generation_unit"]["unit_type"] == "level1_chapter"
    assert packages[0]["generation_unit"]["chapter_path"] == ["内容完整性"]
    assert packages[0]["excellent_bid_references"] == []
    assert packages[0]["table_references"] == []
    assert packages[0]["image_candidates"] == []


def test_content_completeness_uses_general_response_policy_and_ignores_retrieval_materials():
    outline = _outline()
    outline["nodes"][0].update(
        {
            "title": "内容完整性",
            "domain": "general",
            "category": "技术标完整性说明",
            "children": [
                {"node_id": "N1_001", "level": 2, "title": "技术标响应范围"},
                {"node_id": "N1_002", "level": 2, "title": "评分点逐项响应说明"},
                {"node_id": "N1_003", "level": 2, "title": "章节完整性组织"},
                {"node_id": "N1_004", "level": 2, "title": "响应依据与编制原则"},
                {"node_id": "N1_005", "level": 2, "title": "技术标完整性承诺"},
            ],
        }
    )
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["target_section"].update(
        {
            "target_node_id": "N1",
            "domain": "general",
            "category": "技术标完整性说明",
            "chapter_path": ["内容完整性"],
        }
    )
    retrieval_inputs["packages"][0]["image_candidate_pool"] = [
        {
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "image_id": "IMG-001",
            "part_name": "word/media/image1.png",
            "caption": "钢筋加工示意图",
            "use_policy": "candidate_reuse",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
        }
    ]

    packages = build_chapter_generation_inputs(
        outline,
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    package = packages[0]
    assert package["generation_unit"]["domain"] == "general"
    assert package["generation_unit"]["category"] == "技术标完整性说明"
    assert package["generation_unit"]["child_headings"] == [
        "技术标响应范围",
        "评分点逐项响应说明",
        "章节完整性组织",
        "响应依据与编制原则",
        "技术标完整性承诺",
    ]
    assert package["excellent_bid_references"] == []
    assert package["table_references"] == []
    assert package["image_candidates"] == []
    assert package["image_candidate_pool"] == []
    assert package["image_group_candidates"] == []
    assert package["text_image_block_candidates"] == []
    assert package["material_retrieval_summary"] is None
    assert package["expanded_generation_policy"]["section_type"] == "technical_bid_response_statement"
    assert package["expanded_generation_policy"]["targets"]["min_sections"] == 5
    assert package["expanded_generation_policy"]["targets"]["min_rich_tables"] == 2
    assert package["expanded_generation_policy"]["targets"]["min_image_refs"] == 0
    assert package["expanded_generation_policy"]["targets"]["min_image_placeholders"] == 0
    assert package["auto_image_reuse_policy"]["enabled"] is False
    assert package["technical_requirements"][0]["type"] == "generation_guidance"
    assert any(item["type"] == "score_point_coverage" for item in package["technical_requirements"])


def test_include_domain_filters_design_without_blocking_construction():
    outline = _outline()
    outline["nodes"].append(
        {
            "node_id": "N2",
            "level": 1,
            "number": "2",
            "title": "设计方案及优化建议",
            "score_point_id": "SP2",
            "domain": "design",
            "category": "设计方案",
            "children": [],
        }
    )

    packages = build_chapter_generation_inputs(
        outline,
        _parse_result(project_type="epc"),
        include_domains={"construction"},
    )

    assert packages
    assert {package["generation_unit"]["domain"] for package in packages} == {"construction"}


def test_references_include_table_rows_and_image_policy():
    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
    )

    package = packages[0]
    assert package["excellent_bid_references"][0]["title"] == "项目概况"
    assert package["table_references"][0]["columns"][0]["title"] == "分类"
    assert package["row_examples"][0]["cell_blocks"]["col_1"][0]["text"] == "项目名称"
    assert package["image_candidates"][0]["reuse_level"] == "manual_review"
    assert package["image_candidates"][0]["part_name"] == "word/media/image1.png"


def test_material_retrieval_inputs_override_legacy_excellent_bid_index():
    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
        material_retrieval_inputs=_material_retrieval_inputs(),
    )

    package = packages[0]
    assert package["material_retrieval_summary"]["matched_material_count"] == 1
    assert package["excellent_bid_references"][0]["material_slice_id"] == "SRC0001-M00001"
    assert package["excellent_bid_references"][0]["title"] == "项目概况素材"
    assert package["excellent_bid_references"][0]["reuse_level"] == "rewrite_reuse"
    assert package["table_references"][0]["source_slice_id"] == "SRC0001-M00001"
    assert package["table_references"][0]["columns"][0]["title"] == "分类"


def test_material_retrieval_text_image_block_reuse_candidates_are_preserved_outside_llm_summary():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["text_image_block_reuse_candidates"] = [
        {
            "block_id": "TIB-SRC0001-M00001",
            "block_type": "image_group_block",
            "source_id": "SRC0001",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋加工成熟图文块",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "topics": ["钢筋"],
            "primary_topic": "钢筋",
            "secondary_topics": [],
            "match_level": "strong",
            "match_confidence": 0.82,
            "match_reasons": ["主主题匹配：钢筋"],
            "risk_flags": [],
            "retrieval_score": 23.5,
            "reuse_level": "parameterized_reuse",
            "project_specific_risk": "medium",
            "use_policy": "whole_block_preferred",
            "image_asset_ids": ["ASSET-1", "ASSET-2"],
            "image_group_ids": ["GROUP-1"],
            "image_group_candidates": [
                {
                    "image_group_id": "GROUP-1",
                    "material_slice_id": "SRC0001-M00001",
                    "source_id": "SRC0001",
                    "group_title": "钢筋加工示意图",
                    "semantic_text": "钢筋加工、连接、绑扎流程示意图",
                    "semantic_confidence": 0.9,
                    "source_section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                    "reuse_level": "candidate_reuse",
                    "use_policy": "candidate_reuse",
                    "risk_level": "low",
                    "member_count": 2,
                    "members": [
                        {
                            "image_id": "IMG-1",
                            "image_asset_id": "ASSET-1",
                            "caption": "钢筋加工示意图一",
                            "semantic_text": "钢筋加工",
                            "semantic_confidence": 0.9,
                            "part_name": "word/media/rebar1.png",
                            "reuse_level": "candidate_reuse",
                            "use_policy": "candidate_reuse",
                            "risk_level": "low",
                        },
                        {
                            "image_id": "IMG-2",
                            "image_asset_id": "ASSET-2",
                            "caption": "钢筋加工示意图二",
                            "semantic_text": "钢筋绑扎",
                            "semantic_confidence": 0.9,
                            "part_name": "word/media/rebar2.png",
                            "reuse_level": "candidate_reuse",
                            "use_policy": "candidate_reuse",
                            "risk_level": "low",
                        },
                    ],
                    "source_reuse_mode": "text_image_block",
                    "text_image_block_id": "TIB-SRC0001-M00001",
                    "text_image_block_match_level": "strong",
                    "text_image_block_match_confidence": 0.82,
                    "reuse_priority": "text_image_block_strong",
                }
            ],
            "image_candidates": [],
        }
    ]

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    package = packages[0]
    reuse = package["text_image_block_reuse_candidates"][0]
    group = reuse["image_group_candidates"][0]
    assert reuse["image_group_ids"] == ["GROUP-1"]
    assert group["source_reuse_mode"] == "text_image_block"
    assert group["text_image_block_id"] == "TIB-SRC0001-M00001"
    assert group["members"][0]["text_image_block_id"] == "TIB-SRC0001-M00001"
    assert package["text_image_block_candidates"][0]["block_id"] == "TIB-SRC0001-M00001"
    assert package["image_candidates"][0]["material_slice_id"] == "SRC0001-M00001"
    assert package["image_candidates"][0]["reuse_level"] == "manual_review"
    assert package["text_image_block_candidates"][0]["block_id"] == "TIB-SRC0001-M00001"
    assert package["text_image_block_candidates"][0]["primary_topic"] == "质量管理"
    assert package["text_image_block_candidates"][0]["match_level"] == "moderate"
    assert package["generation_constraints"]["text_image_block_policy"]["enabled"] is True
    assert package["reuse_warnings"][0]["risk_level"] == "medium"


def test_generation_input_carries_full_image_candidate_pool():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["matched_materials"][0]["material_quality"] = "high"
    retrieval_inputs["packages"][0]["image_candidate_pool"] = [
        {
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "rel_id": "rId1",
            "part_name": "word/media/image1.png",
            "context": "table_cell",
            "table_index": 1,
            "row_index": 1,
            "cell_index": 1,
            "use_policy": "candidate_reuse",
            "material_quality": "high",
            "material_title": "项目概况素材",
            "source_section_path": ["主要施工方案与技术措施", "项目概况"],
        },
        {
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "rel_id": "rId2",
            "part_name": "word/media/image2.png",
            "context": "table_cell",
            "table_index": 1,
            "row_index": 2,
            "cell_index": 1,
            "use_policy": "candidate_reuse",
            "material_quality": "high",
            "material_title": "项目概况素材",
            "source_section_path": ["主要施工方案与技术措施", "项目概况"],
        },
    ]

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    package = packages[0]
    assert len(package["image_candidates"]) == 2
    assert len(package["image_candidate_pool"]) == 2
    assert package["auto_image_reuse_policy"]["enabled"] is True
    assert package["auto_image_reuse_policy"]["target_image_refs"] >= package["auto_image_reuse_policy"]["min_image_refs"]
    assert package["material_retrieval_summary"]["image_candidate_pool_count"] == 2


def test_generation_input_counts_direct_reuse_images_as_auto_reusable():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["matched_materials"][0]["material_quality"] = "high"
    retrieval_inputs["packages"][0]["image_candidate_pool"] = [
        {
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "image_id": "IMG-DIRECT-1",
            "part_name": "word/media/direct1.png",
            "caption": "进度计划纠偏流程图",
            "semantic_text": "进度计划纠偏措施",
            "semantic_confidence": 0.9,
            "semantic_sources": [{"source_type": "same_cell_caption", "text": "进度计划纠偏措施", "confidence": 0.9}],
            "use_policy": "direct_reuse",
            "reuse_level": "direct_reuse",
            "risk_level": "low",
            "material_quality": "high",
            "source_section_path": ["主要施工方案与技术措施", "施工部署", "进度计划纠偏措施"],
        }
    ]

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    package = packages[0]
    assert package["image_candidate_pool"][0]["reuse_level"] == "direct_reuse"
    assert package["expanded_generation_policy"]["reuse_profile"]["reusable_image_count"] == 1
    assert package["auto_image_reuse_policy"]["enabled"] is True
    assert package["auto_image_reuse_policy"]["llm_selects_images"] is False
    assert package["auto_image_reuse_policy"]["allow_placeholders"] is False
    assert package["auto_image_reuse_policy"]["missing_image_behavior"] == "silent_skip"


def test_generation_input_balances_representative_images_by_topic():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["matched_materials"][0]["material_quality"] = "high"
    retrieval_inputs["packages"][0]["image_references"] = []
    refs = []
    for index in range(12):
        refs.append(
            {
                "material_slice_id": "SRC0001-M00001",
                "source_id": "SRC0001",
                "rel_id": f"rId-measure-{index}",
                "part_name": f"word/media/measure{index}.png",
                "context": "table_cell",
                "caption": "测量控制网示意图",
                "source_section_path": ["主要施工方案与技术措施", "工程测量控制网建立及监测方案"],
                "use_policy": "candidate_reuse",
                "material_quality": "high",
            }
        )
    refs.append(
        {
            "material_slice_id": "SRC0001-M00120",
            "source_id": "SRC0001",
            "rel_id": "rId-post-pour",
            "part_name": "word/media/post-pour.png",
            "context": "table_cell",
            "caption": "后浇带模板独立支设示意图",
            "source_section_path": ["主要施工方案与技术措施", "后浇带及变形缝处理专项方案"],
            "use_policy": "candidate_reuse",
            "material_quality": "high",
        }
    )
    retrieval_inputs["packages"][0]["image_candidate_pool"] = refs

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    captions = [item["caption"] for item in packages[0]["image_candidates"]]
    assert "后浇带模板独立支设示意图" in captions
    assert len(packages[0]["image_candidates"]) == 12


def test_generation_input_preserves_image_asset_semantics_from_retrieval():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["matched_materials"][0]["material_quality"] = "high"
    retrieval_inputs["packages"][0]["image_references"] = [
        {
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_STABLE_001",
            "rel_id": "rId1",
            "part_name": "word/media/image1.png",
            "caption": "底板马凳筋",
            "caption_candidates": ["底板马凳筋", "钢筋定位措施"],
            "nearby_text": "底板马凳筋；楼板钢筋定位控制",
            "tags": ["钢筋"],
            "source_section_path": ["主要施工方案与技术措施", "项目概况"],
            "table_index": 1,
            "row_index": 1,
            "cell_index": 1,
            "use_policy": "candidate_reuse",
            "material_quality": "high",
            "risk_level": "low",
        }
    ]

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    image = packages[0]["image_candidates"][0]
    assert image["image_id"] == "EBIMG_STABLE_001"
    assert image["caption"] == "底板马凳筋"
    assert image["caption_candidates"] == ["底板马凳筋", "钢筋定位措施"]
    assert image["nearby_text"] == "底板马凳筋；楼板钢筋定位控制"
    assert image["tags"] == ["钢筋"]


def test_generation_input_preserves_image_group_candidates_from_retrieval():
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["matched_materials"][0]["material_quality"] = "high"
    retrieval_inputs["packages"][0]["image_group_references"] = [
        {
            "image_group_id": "SRC0001-M00001-G0000",
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "source_section_path": ["主要施工方案与技术措施", "项目概况"],
            "group_title": "钢筋加工示意图",
            "semantic_text": "钢筋加工示意图",
            "semantic_confidence": 0.92,
            "captions": ["钢筋调直", "钢筋切断"],
            "member_count": 2,
            "use_policy": "candidate_reuse",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "material_quality": "high",
            "must_keep_together": True,
            "members": [
                {
                    "material_slice_id": "SRC0001-M00001",
                    "source_id": "SRC0001",
                    "image_asset_id": "SRC0001-M00001-IMG0001",
                    "image_id": "EBIMG-GROUP-1",
                    "part_name": "word/media/group1.png",
                    "caption": "钢筋调直",
                    "source_section_path": ["主要施工方案与技术措施", "项目概况"],
                    "use_policy": "candidate_reuse",
                    "risk_level": "low",
                },
                {
                    "material_slice_id": "SRC0001-M00001",
                    "source_id": "SRC0001",
                    "image_asset_id": "SRC0001-M00001-IMG0002",
                    "image_id": "EBIMG-GROUP-2",
                    "part_name": "word/media/group2.png",
                    "caption": "钢筋切断",
                    "source_section_path": ["主要施工方案与技术措施", "项目概况"],
                    "use_policy": "candidate_reuse",
                    "risk_level": "low",
                },
            ],
        }
    ]
    retrieval_inputs["packages"][0]["image_group_candidate_pool"] = retrieval_inputs["packages"][0]["image_group_references"]

    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    group = packages[0]["image_group_candidates"][0]
    assert group["image_group_id"] == "SRC0001-M00001-G0000"
    assert group["must_keep_together"] is True
    assert [member["image_id"] for member in group["members"]] == ["EBIMG-GROUP-1", "EBIMG-GROUP-2"]
    assert all(member["must_keep_with_group"] is True for member in group["members"])
    assert packages[0]["material_retrieval_summary"]["image_group_reference_count"] == 1


def test_generation_input_adds_expanded_policy():
    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
        material_retrieval_inputs=_material_retrieval_inputs(),
    )

    policy = packages[0]["expanded_generation_policy"]

    assert policy["mode"] == "expanded"
    assert packages[0]["generation_constraints"]["generation_mode"] == "expanded"
    assert policy["targets"]["min_sections"] >= 3
    assert "min_image_refs" in policy["targets"]
    assert "manual_review" in policy["reuse_level_policy"]


def test_common_management_chapter_prefers_direct_reuse_and_group_assets():
    outline = _outline()
    outline["nodes"][0].update({"title": "质量管理体系与措施", "category": "质量管理"})
    outline["nodes"][0]["children"] = [
        {
            "node_id": "N1_010",
            "level": 2,
            "number": "1.1",
            "title": "质量管理体系",
            "domain": "construction",
            "category": "质量管理",
            "children": [],
        }
    ]
    retrieval_inputs = _material_retrieval_inputs()
    retrieval_inputs["packages"][0]["target_section"].update(
        {
            "target_node_id": "N1_010",
            "category": "质量管理",
            "chapter_path": ["质量管理体系与措施", "质量管理体系"],
        }
    )
    retrieval_inputs["packages"][0]["matched_materials"][0].update(
        {
            "title": "质量管理体系素材",
            "section_path": ["质量管理体系与措施", "质量管理体系"],
            "material_quality": "high",
            "reuse_level": "direct_reuse",
            "project_specific_risk": "low",
        }
    )
    retrieval_inputs["packages"][0]["image_group_references"] = [
        {
            "image_group_id": "SRC0001-M00001-G0001",
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "group_title": "质量管理流程图",
            "semantic_text": "质量管理流程图",
            "source_section_path": ["质量管理体系与措施", "质量管理体系"],
            "use_policy": "candidate_reuse",
            "reuse_level": "candidate_reuse",
            "risk_level": "low",
            "member_count": 2,
            "members": [],
        }
    ]

    packages = build_chapter_generation_inputs(
        outline,
        _parse_result(),
        material_retrieval_inputs=retrieval_inputs,
    )

    package = packages[0]
    profile = package["chapter_reuse_profile"]
    assert profile["profile"] == "direct_reuse_preferred"
    assert profile["allow_direct_text_reuse"] is True
    assert profile["allow_table_group_reuse"] is True
    assert profile["allow_image_group_reuse"] is True
    assert "direct_reuse" in profile["preferred_material_reuse_levels"]
    assert package["expanded_generation_policy"]["section_type"] == "management_measure"
    assert package["generation_constraints"]["chapter_reuse_profile"] == profile


def test_project_specific_chapter_blocks_direct_reuse_and_requires_trace_scan():
    packages = build_chapter_generation_inputs(
        _outline(),
        _parse_result(),
        excellent_bid_index=_excellent_bid_index(),
        material_retrieval_inputs=_material_retrieval_inputs(),
    )

    package = packages[0]
    profile = package["chapter_reuse_profile"]
    scan = package["generation_constraints"]["history_trace_scan"]

    assert profile["profile"] == "manual_review"
    assert profile["allow_direct_text_reuse"] is False
    assert profile["allow_table_group_reuse"] is False
    assert profile["allow_image_group_reuse"] is False
    assert "项目概况" in profile["blocked_direct_reuse_topics"]
    assert scan["enabled"] is True
    assert "示例项目" in scan["current_project_values"]
    assert any("历史项目名称" in term for term in scan["candidate_terms"])


def test_report_summarizes_package_counts():
    packages = build_chapter_generation_inputs(_outline(), _parse_result())

    report = render_chapter_generation_input_report(packages)

    assert "# 技术标章节正文生成输入包报告" in report
    assert "输入包数量：2" in report
    assert "level2_section_group=2" in report


def _outline():
    return {
        "outline_id": "outline_001",
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "number": "1",
                "title": "主要施工方案与技术措施",
                "score_point_id": "SP1",
                "score": "10分",
                "score_rule": "施工方案总体安排合理，技术措施完整。",
                "domain": "construction",
                "category": "施工方案",
                "template_refs": [
                    {
                        "source_bid_id": "excellent_bid_001",
                        "slice_id": "S0",
                        "section_title": "针对本项目施工管理提出总体施工方案",
                        "section_path": ["针对本项目施工管理提出总体施工方案"],
                    }
                ],
                "children": [
                    {
                        "node_id": "N1_001",
                        "level": 2,
                        "number": "1.1",
                        "title": "项目概况",
                        "domain": "construction",
                        "category": "施工方案",
                        "template_refs": [],
                        "children": [
                            {"node_id": "N1_001_001", "level": 3, "title": "工程基本情况"},
                            {"node_id": "N1_001_002", "level": 3, "title": "现场条件分析"},
                        ],
                    },
                    {
                        "node_id": "N1_002",
                        "level": 2,
                        "number": "1.2",
                        "title": "施工部署",
                        "domain": "construction",
                        "category": "施工方案",
                        "template_refs": [],
                        "children": [],
                    },
                ],
            }
        ],
    }


def _parse_result(project_type="construction"):
    return {
        "project_type": {"value": project_type},
        "project_info": {
            "project_name": {"value": "示例项目"},
            "construction_location": {"value": "示例地点"},
            "construction_scale": {"value": "总建筑面积约50000平方米"},
            "tender_scope": {"value": "施工图纸及工程量清单范围"},
            "duration_requirement": {"value": "365日历天"},
            "quality_requirement": {"value": "合格"},
            "safety_civilization_requirement": {"value": "安全文明施工"},
        },
        "technical_score_points": [
            {
                "score_point_id": "SP1",
                "original_text": "主要施工方案与技术措施",
                "score_rule": "施工方案总体安排合理，技术措施完整。",
                "score_value": "10分",
                "source_refs": [{"file_id": "F1", "block_index": 10, "text_excerpt": "主要施工方案与技术措施"}],
            }
        ],
        "technical_bid_requirements": [
            {
                "requirement_id": "TBR1",
                "category": "construction_scope",
                "content": "施工内容包括土建、装饰、安装等工程。",
                "source_refs": [{"file_id": "F1", "block_index": 20}],
                "confidence": 0.9,
                "review_required": False,
            }
        ],
        "technical_standards": [
            {
                "standard_id": "TS1",
                "category": "construction_standard",
                "summary": "施工质量应达到国家现行验收规范合格标准。",
                "original_excerpt": "施工质量应达到国家现行验收规范合格标准。",
                "source_refs": [{"file_id": "F1", "block_index": 30}],
                "confidence": 0.95,
                "review_required": False,
            }
        ],
    }


def _excellent_bid_index():
    return {
        "slices": [
            {
                "slice_id": "S0",
                "level": 1,
                "section_path": ["针对本项目施工管理提出总体施工方案"],
                "paragraphs": [{"text_preview": "总体施工方案参考段落。"}],
                "tables": [],
                "image_bindings": [],
            },
            {
                "slice_id": "S1",
                "level": 2,
                "section_path": ["针对本项目施工管理提出总体施工方案", "项目概况"],
                "paragraphs": [{"text_preview": "项目概况采用表格化表达。"}],
                "tables": [
                    {
                        "table_index": 1,
                        "nearest_heading_text": "项目概况",
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 0,
                        "header_preview": ["分类", "概况内容"],
                        "row_previews": [
                            {
                                "row_index": 0,
                                "cells": [
                                    {"cell_index": 0, "text_preview": "分类", "image_count": 0},
                                    {"cell_index": 1, "text_preview": "概况内容", "image_count": 0},
                                ],
                            },
                            {
                                "row_index": 1,
                                "cells": [
                                    {"cell_index": 0, "text_preview": "项目名称", "image_count": 0},
                                    {"cell_index": 1, "text_preview": "历史项目名称", "image_count": 0},
                                ],
                            },
                        ],
                    }
                ],
                "image_bindings": [
                    {
                        "rel_id": "rId1",
                        "part_name": "word/media/image1.png",
                        "context": "table_cell",
                        "table_index": 1,
                        "row_index": 1,
                        "cell_index": 1,
                    }
                ],
            },
        ]
    }


def _material_retrieval_inputs():
    return {
        "schema_version": "chapter_material_retrieval_input_index_v1",
        "package_count": 1,
        "packages": [
            {
                "schema_version": "chapter_material_retrieval_input_v1",
                "target_section": {
                    "target_node_id": "N1_001",
                    "parent_level_1_node_id": "N1",
                    "domain": "construction",
                    "category": "施工方案",
                    "chapter_path": ["主要施工方案与技术措施", "项目概况"],
                },
                "matched_materials": [
                    {
                        "rank": 1,
                        "score": 1.0,
                        "match_reasons": ["section_path_exact"],
                        "material_slice_id": "SRC0001-M00001",
                        "source_id": "SRC0001",
                        "source_type": "docx_only",
                        "source_slice_id": "S1",
                        "title": "项目概况素材",
                        "section_path": ["主要施工方案与技术措施", "项目概况"],
                        "material_quality": "review_required",
                        "primary_material_source": "docx",
                        "reuse_level": "light_rewrite",
                        "paragraphs": [
                            {
                                "paragraph_index": 1,
                                "text_preview": "项目概况参考段落",
                            }
                        ],
                    }
                ],
                "paragraph_references": [
                    {
                        "material_slice_id": "SRC0001-M00001",
                        "source_id": "SRC0001",
                        "paragraph_index": 1,
                        "text_preview": "项目概况参考段落",
                        "use_policy": "rewrite_reference",
                        "material_quality": "review_required",
                    }
                ],
                "table_references": [
                    {
                        "material_slice_id": "SRC0001-M00001",
                        "source_id": "SRC0001",
                        "table_index": 1,
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 1,
                        "header_preview": ["分类", "概况内容"],
                        "use_policy": "reuse_structure_rewrite_content",
                        "material_quality": "review_required",
                    }
                ],
                "text_image_block_candidates": [
                    {
                        "block_id": "TIB-SRC0001-M00001",
                        "block_type": "table_image_block",
                        "source_id": "SRC0001",
                        "material_slice_id": "SRC0001-M00001",
                        "title": "项目概况成熟图文块",
                        "section_path": ["主要施工方案与技术措施", "项目概况"],
                        "topics": ["质量管理"],
                        "primary_topic": "质量管理",
                        "secondary_topics": [],
                        "match_level": "moderate",
                        "match_confidence": 0.62,
                        "match_reasons": ["主主题匹配：质量管理"],
                        "risk_flags": [],
                        "summary": "项目概况章节的表格与配图组织方式。",
                        "image_count": 1,
                        "image_group_count": 0,
                        "table_count": 1,
                        "captions": ["项目概况示意图"],
                        "reuse_level": "rewrite_reuse",
                        "project_specific_risk": "medium",
                        "use_policy": "whole_block_preferred",
                        "render_policy": {"preserve_image_order": True},
                        "retrieval_score": 8.5,
                    }
                ],
                "image_references": [
                    {
                        "material_slice_id": "SRC0001-M00001",
                        "source_id": "SRC0001",
                        "rel_id": "rId1",
                        "part_name": "word/media/image1.png",
                        "context": "table_cell",
                        "table_index": 1,
                        "row_index": 1,
                        "cell_index": 1,
                        "use_policy": "manual_review",
                        "material_quality": "review_required",
                    }
                ],
                "reuse_warnings": [
                    {
                        "material_slice_id": "SRC0001-M00001",
                        "risk_level": "medium",
                        "reason": "需复核",
                    }
                ],
                "retrieval_policy": {
                    "top_k": 5,
                    "allowed_qualities": ["high", "usable", "review_required", "pdf_fallback"],
                    "exclude_pdf_reference_material": True,
                },
            }
        ],
    }
