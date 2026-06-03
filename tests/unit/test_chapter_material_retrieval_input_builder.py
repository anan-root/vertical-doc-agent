from construction_bidding_agent.chapter_generator.material_retrieval_input_builder import (
    _filter_material_hits_for_target,
    build_chapter_material_retrieval_inputs,
    render_chapter_material_retrieval_report,
)
from construction_bidding_agent.chapter_generator.input_builder import build_chapter_generation_inputs


def test_builds_material_retrieval_package_for_level2_chapter():
    packages = build_chapter_material_retrieval_inputs(_outline(), _library(), top_k=3)

    assert len(packages) == 2
    first = packages[0]
    assert first["schema_version"] == "chapter_material_retrieval_input_v1"
    assert first["target_section"]["chapter_path"] == ["主要施工方案与技术措施", "钢筋工程施工方案"]
    assert first["matched_materials"][0]["title"] == "钢筋工程施工方案"
    assert first["paragraph_references"][0]["text_preview"] == "钢筋工程施工正文"
    assert first["table_references"][0]["header_preview"] == ["工序", "措施"]
    assert first["image_references"][0]["use_policy"] == "candidate_reuse"
    assert first["text_image_block_candidates"]
    assert first["text_image_block_candidates"][0]["block_id"].startswith("TIB-")
    assert "钢筋" in first["text_image_block_candidates"][0]["topics"]


def test_text_image_block_reuse_preserves_row_metadata():
    library = _library()
    first_binding = library["image_assets"][0] if library.get("image_assets") else library["slices"][0]["image_bindings"][0]
    library["slices"][0]["title"] = "梁柱接头模板支设施工方法"
    library["slices"][0]["section_path"] = ["主要施工方案与技术措施", "模板工程施工方案"]
    library["slices"][0]["search_text"] = "模板 梁柱接头 模板支设 模板拼缝 节点做法"
    library["slices"][0]["image_count"] = 1
    library["slices"][0]["docx_image_count"] = 1
    library["slices"][0]["tables"][0]["table_index"] = 3
    library["slices"][0]["tables"][0]["max_column_count"] = 3
    library["slices"][0]["tables"][0]["header_preview"] = ["宸ュ簭", "閮ㄤ綅", "鍋氭硶"]
    library["slices"][0]["tables"][0]["row_previews"] = [
        {
            "row_index": 0,
            "cells": [
                {"cell_index": 0, "text_preview": "宸ュ簭", "image_count": 0},
                {"cell_index": 1, "text_preview": "閮ㄤ綅", "image_count": 0},
                {"cell_index": 2, "text_preview": "鍋氭硶", "image_count": 0},
            ],
        },
        {
            "row_index": 8,
            "cells": [
                {"cell_index": 0, "text_preview": "1", "image_count": 0},
                {"cell_index": 1, "text_preview": "姊佹煴鎺ュご", "image_count": 0},
                {"cell_index": 2, "text_preview": "姊佹煴鎺ュご妯℃澘鏀鑺傜偣鍋氭硶", "image_count": 1},
            ],
        },
    ]
    library["image_assets"] = [
        {
            **first_binding,
            "image_asset_id": "SRC0001-M00001-IMG-TPL",
            "image_id": "EBIMG_TEMPLATE_ROW",
            "material_slice_id": "SRC0001-M00001",
            "source_id": "SRC0001",
            "source_slice_id": "S1",
            "section_path": ["主要施工方案与技术措施", "模板工程施工方案"],
            "table_index": 3,
            "row_index": 8,
            "cell_index": 2,
            "caption_actual": "梁柱接头模板支设节点做法",
            "semantic_text": "梁柱接头模板支设节点做法",
            "cell_text": "梁柱接头模板支设节点做法",
            "row_text": "模板工程 | 梁柱接头 | 梁柱接头模板支设节点做法",
            "nearby_text": "模板工程 梁柱接头 模板支设 模板拼缝",
            "caption_candidates": ["梁柱接头模板支设节点做法"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        }
    ]
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "模板工程施工方案",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [{"node_id": "N1_1_1", "level": 3, "title": "梁柱接头模板支设节点做法"}],
                    }
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=5)

    reuse = packages[0]["text_image_block_reuse_candidates"][0]
    assert reuse["block_type"] == "method_row_block"
    assert reuse["render_policy"]["row_level_context"] is True
    assert reuse["row_scope"] == {"table_index": 3, "start_row_index": 8, "end_row_index": 8}
    assert reuse["image_candidates"][0]["render_policy"]["row_level_context"] is True
    assert reuse["image_candidates"][0]["row_scope"] == reuse["row_scope"]


def test_review_required_and_pdf_fallback_materials_create_warnings():
    packages = build_chapter_material_retrieval_inputs(_outline(), _library(), top_k=5)

    warnings = packages[0]["reuse_warnings"]

    assert any(item["risk_level"] == "medium" for item in warnings)


def test_parameter_conflict_downgrades_material_and_excludes_reuse_pools():
    outline = _outline()
    outline["nodes"][0]["score_rule"] = "项目负责人具有不少于8年施工管理经验。"
    library = _library()
    library["slices"][0]["reuse_level"] = "direct_reuse"
    library["slices"][0]["paragraphs"][0]["text_preview"] = "项目负责人具有5年施工管理经验。"
    library["slices"][0]["tables"][0]["row_previews"] = [
        {"cells": [{"text_preview": "项目负责人5年施工管理经验"}]}
    ]
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_STABLE_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId1",
            "target": "media/image1.png",
            "part_name": "word/media/image1.png",
            "context": "table_cell",
            "caption_actual": "钢筋加工示意图",
            "nearby_text": "钢筋加工 工艺 做法",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(
        outline,
        library,
        parse_result={"technical_score_points": [{"score_rule": "项目负责人具有不少于8年施工管理经验。"}]},
        top_k=3,
    )

    package = packages[0]
    material = package["matched_materials"][0]
    assert material["reuse_level"] == "manual_review"
    assert material["review_required"] is True
    assert material["parameter_conflicts"][0]["category"] == "experience_years"
    assert package["paragraph_references"] == []
    assert package["table_references"] == []
    assert package["image_candidate_pool"] == []
    assert any(item.get("reason_type") == "parameter_conflict" for item in package["reuse_warnings"])


def test_reuse_level_controls_warnings_and_manual_review_images():
    outline = {
        "nodes": [
            {
                "node_id": "N3",
                "level": 1,
                "title": "施工总平面布置图",
                "domain": "construction",
                "category": "施工总平面",
                "children": [],
            }
        ]
    }
    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=5)

    package = packages[0]

    assert any(item["risk_level"] == "high" for item in package["reuse_warnings"])
    assert package["image_references"][0]["use_policy"] == "manual_review"


def test_practice_site_photo_can_be_candidate_reuse():
    outline = {
        "nodes": [
            {
                "node_id": "N4",
                "level": 1,
                "title": "安全文明标准化防护措施",
                "domain": "construction",
                "category": "文明环保",
                "children": [],
            }
        ]
    }
    library = _library()
    library["slices"][0]["title"] = "标准化防护现场照片"
    library["slices"][0]["section_path"] = ["安全文明标准化防护措施", "标准化防护现场照片"]
    library["slices"][0]["search_text"] = "安全文明 标准化防护 现场照片 优秀做法"
    library["slices"][0]["reuse_level"] = "direct_reuse"
    library["slices"][0]["project_specific_risk"] = "low"

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=3)

    assert packages[0]["image_references"][0]["use_policy"] == "candidate_reuse"


def test_image_pool_keeps_images_from_later_materials():
    library = _library()
    library["slices"][0]["image_bindings"] = [
        {
            "rel_id": f"rId{i}",
            "target": f"media/image{i}.png",
            "part_name": f"word/media/image{i}.png",
            "context": "table_cell",
            "block_index": 2,
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "table_index": 1,
            "row_index": i,
            "cell_index": 1,
        }
        for i in range(1, 11)
    ]
    library["slices"][1]["material_quality"] = "high"
    library["slices"][1]["image_count"] = 1
    library["slices"][1]["image_bindings"] = [
        {
            "rel_id": "rId-later",
            "target": "media/later.png",
            "part_name": "word/media/later.png",
            "context": "table_cell",
            "block_index": 3,
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案", "钢筋加工"],
            "table_index": 2,
            "row_index": 1,
            "cell_index": 1,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    image_material_ids = {item["material_slice_id"] for item in packages[0]["image_references"]}
    pool_material_ids = {item["material_slice_id"] for item in packages[0]["image_candidate_pool"]}
    assert "SRC0002-M00068" in image_material_ids
    assert "SRC0002-M00068" in pool_material_ids
    assert len([item for item in packages[0]["image_references"] if item["material_slice_id"] == "SRC0001-M00001"]) <= 4


def test_material_retrieval_supplements_materials_for_child_headings():
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "土建施工方案与技术措施",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [
                            {"node_id": "N1_1_1", "level": 3, "title": "工程测量控制网建立及监测方案"},
                            {"node_id": "N1_1_2", "level": 3, "title": "混凝土浇筑及大体积温控措施"},
                            {"node_id": "N1_1_3", "level": 3, "title": "地下室及屋面防水施工技术"},
                        ],
                    }
                ],
            }
        ]
    }
    library = _library()
    library["slices"].extend(
        [
            _material_slice(
                "SRC0001-M00030",
                "工程测量及监测施工方案与技术措施",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "工程测量及监测施工方案与技术措施"],
                "测量 控制网 轴线",
                "measure.png",
            ),
            _material_slice(
                "SRC0001-M00086",
                "混凝土施工方案与技术措施",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "混凝土施工方案与技术措施"],
                "混凝土 浇筑 大体积 温控",
                "concrete.png",
            ),
            _material_slice(
                "SRC0001-M00095",
                "防水施工方案与技术措施",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "防水施工方案与技术措施"],
                "地下室 屋面 防水 卷材",
                "waterproof.png",
            ),
        ]
    )

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=1)

    material_ids = {item["material_slice_id"] for item in packages[0]["matched_materials"]}
    pool_material_ids = {item["material_slice_id"] for item in packages[0]["image_candidate_pool"]}
    assert {"SRC0001-M00086", "SRC0001-M00095"} <= material_ids
    assert {"SRC0001-M00086", "SRC0001-M00095"} <= pool_material_ids
    assert packages[0]["retrieval_policy"]["child_heading_supplement_hit_count"] >= 2


def test_child_heading_supplement_prefers_specific_theme_over_generic_construction_plan():
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "土建施工方案与技术措施",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [
                            {"node_id": "N1_1_1", "level": 3, "title": "后浇带及变形缝处理专项方案"},
                        ],
                    }
                ],
            }
        ]
    }
    library = _library()
    library["slices"].extend(
        [
            _material_slice(
                "SRC0001-M00030",
                "工程测量及监测施工方案与技术措施",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "工程测量及监测施工方案与技术措施"],
                "测量 控制网 轴线 施工方案",
                "measure.png",
            ),
            _material_slice(
                "SRC0001-M00120",
                "后浇带方案设计概况",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "后浇带专项施工方案与技术措施"],
                "后浇带 变形缝 施工方案",
                "post-pour.png",
            ),
        ]
    )

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=1)

    material_ids = [item["material_slice_id"] for item in packages[0]["matched_materials"]]
    assert "SRC0001-M00120" in material_ids
    assert packages[0]["retrieval_policy"]["child_heading_supplement_hit_count"] >= 1


def test_child_heading_supplement_keeps_image_rich_earthwork_foundation_materials():
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "土建施工方案与技术措施",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [
                            {"node_id": "N1_1_1", "level": 3, "title": "土方开挖及基坑支护专项方案"},
                        ],
                    }
                ],
            }
        ]
    }
    library = _library()
    library["slices"] = [
        _material_slice(
            "SRC0002-M00037",
            "土方开挖原则",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", "土方开挖原则"],
            "土方 开挖 原则 施工方案",
            "unused-earthwork.png",
        ),
        _material_slice(
            "SRC0002-M00038",
            "土方开挖施工方案",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", "土方开挖施工方案"],
            "土方 开挖 施工方案",
            "unused-excavation.png",
        ),
        _material_slice(
            "SRC0002-M00032",
            "基坑支护专项施工方案与技术措施",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", "基坑支护专项施工方案与技术措施"],
            "基坑 支护 边坡 护坡 降水 排水",
            "foundation-support.png",
        ),
        _material_slice(
            "SRC0001-M00057",
            "降水井工艺流程与要求",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", "基坑降水施工"],
            "基坑 降水 排水 降水井 工艺流程",
            "dewatering.png",
        ),
    ]
    for item in library["slices"][:2]:
        item["image_count"] = 0
        item["docx_image_count"] = 0
        item["image_bindings"] = []

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=1)

    material_ids = {item["material_slice_id"] for item in packages[0]["matched_materials"]}
    pool_material_ids = {item["material_slice_id"] for item in packages[0]["image_candidate_pool"]}
    assert {"SRC0002-M00032", "SRC0001-M00057"} <= material_ids
    assert {"SRC0002-M00032", "SRC0001-M00057"} <= pool_material_ids


def test_promotion_image_slice_filter_ignores_score_rule_safety_words():
    from construction_bidding_agent.document_parser.models import (
        ExcellentBidMaterialSearchHit,
        ExcellentBidMaterialSlice,
    )

    safety_slice = ExcellentBidMaterialSlice(
        material_slice_id="SRC0003-PMS00021",
        source_id="SRC0003",
        source_type="docx_image_promotion",
        source_slice_id="SRC0003-PMS00021",
        title="7 污水排放",
        clean_title="7 污水排放",
        section_path=["7 污水排放"],
        search_text="安全 高处坠落 基坑支护结构处坠落 塔吊倾覆 采光井洞口坠落",
        primary_material_source="docx_image_promotion",
        material_quality="high",
        image_count=8,
        docx_image_count=8,
        reuse_level="direct_reuse",
        project_specific_risk="low",
    )
    target = {
        "chapter_path": ["质量管理体系与措施", "主体结构施工质量保证措施"],
        "child_headings": ["钢筋工程加工与安装质量控制"],
        "category": "质量管理",
        "query": "施工方案完整，安全措施合理，质量保证措施完善。",
    }

    filtered = _filter_material_hits_for_target(
        [ExcellentBidMaterialSearchHit(material_slice_id=safety_slice.material_slice_id, score=1, slice=safety_slice)],
        target,
    )

    assert filtered == []


def test_promotion_image_slice_filter_requires_primary_bim_topic_for_bim_target():
    from construction_bidding_agent.document_parser.models import (
        ExcellentBidMaterialSearchHit,
        ExcellentBidMaterialSlice,
    )

    mixed_slice = ExcellentBidMaterialSlice(
        material_slice_id="SRC0003-PMS00021",
        source_id="SRC0003",
        source_type="docx_image_promotion",
        source_slice_id="SRC0003-PMS00021",
        title="7 污水排放",
        clean_title="7 污水排放",
        section_path=["7 污水排放"],
        search_text="BIM 平台 数据 监控 安全 高处坠落 塔吊倾覆 生活区 污水排放",
        primary_material_source="docx_image_promotion",
        material_quality="high",
        image_count=8,
        docx_image_count=8,
        reuse_level="direct_reuse",
        project_specific_risk="low",
    )
    target = {
        "chapter_path": ["采用新工艺、新技术、新设备、新材料、BIM等的程度"],
        "child_headings": ["BIM模型建模管理", "BIM深化应用"],
        "category": "施工方案",
    }

    filtered = _filter_material_hits_for_target(
        [ExcellentBidMaterialSearchHit(material_slice_id=mixed_slice.material_slice_id, score=1, slice=mixed_slice)],
        target,
    )

    assert filtered == []


def test_image_candidate_pool_round_robins_across_materials():
    library = _library()
    library["slices"][0]["image_bindings"] = [
        {
            "rel_id": f"rId-rich-{index}",
            "target": f"media/rich{index}.png",
            "part_name": f"word/media/rich{index}.png",
            "context": "table_cell",
            "block_index": 2,
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "table_index": 1,
            "row_index": index,
            "cell_index": 1,
        }
        for index in range(1, 21)
    ]
    later = _material_slice(
        "SRC0001-M00086",
        "混凝土施工方案",
        ["主要施工方案与技术措施", "钢筋工程施工方案", "混凝土施工方案"],
        "钢筋 混凝土 施工",
        "concrete.png",
    )
    library["slices"].append(later)

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    pool = packages[0]["image_candidate_pool"]
    rich_indexes = [index for index, item in enumerate(pool) if item["material_slice_id"] == "SRC0001-M00001"]
    later_indexes = [index for index, item in enumerate(pool) if item["material_slice_id"] == "SRC0001-M00086"]
    assert rich_indexes
    assert later_indexes
    assert later_indexes[0] < rich_indexes[min(5, len(rich_indexes) - 1)]


def test_image_candidate_pool_keeps_late_unique_theme_when_pool_is_full():
    library = _library()
    library["slices"] = []
    for material_index, theme in enumerate(["测量", "钢筋", "模板", "混凝土", "防水", "脚手架"], start=1):
        item = _material_slice(
            f"SRC0001-M{material_index:05d}",
            f"{theme}施工方案",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", f"{theme}施工方案"],
            f"{theme} 施工方案",
            f"{theme}.png",
        )
        item["image_bindings"] = [
            {
                "rel_id": f"rId-{theme}-{index}",
                "target": f"media/{theme}{index}.png",
                "part_name": f"word/media/{theme}{index}.png",
                "context": "table_cell",
                "block_index": 2,
                "section_path": item["section_path"],
                "table_index": 1,
                "row_index": index,
                "cell_index": 1,
            }
            for index in range(20)
        ]
        library["slices"].append(item)
    library["slices"].append(
        _material_slice(
            "SRC0001-M00120",
            "后浇带专项施工方案",
            ["主要施工方案与技术措施", "土建施工方案与技术措施", "后浇带专项施工方案"],
            "后浇带 变形缝",
            "post-pour.png",
        )
    )
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "土建施工方案与技术措施",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [
                            {"node_id": "N1_1_1", "level": 3, "title": "后浇带及变形缝处理专项方案"},
                        ],
                    }
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=6)

    pool_material_ids = {item["material_slice_id"] for item in packages[0]["image_candidate_pool"]}
    assert "SRC0001-M00120" in pool_material_ids


def test_material_retrieval_prefers_image_assets_over_section_bindings():
    library = _library()
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_STABLE_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
            "rel_id": "rId1",
            "target": "media/image1.png",
            "part_name": "word/media/image1.png",
            "context": "table_cell",
            "table_index": 1,
            "row_index": 1,
            "cell_index": 1,
            "caption_actual": "底板马凳筋",
            "caption_candidates": ["底板马凳筋", "钢筋定位措施"],
            "nearby_text": "底板马凳筋；楼板钢筋定位控制",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    image_ref = packages[0]["image_references"][0]
    assert image_ref["image_id"] == "EBIMG_STABLE_001"
    assert image_ref["caption"] == "底板马凳筋"
    assert image_ref["caption_candidates"] == ["底板马凳筋", "钢筋定位措施"]
    assert image_ref["nearby_text"] == "底板马凳筋；楼板钢筋定位控制"
    assert image_ref["use_policy"] == "candidate_reuse"
    assert image_ref["primary_category"] == "construction_process"
    assert image_ref["fit_level"] in {"preferred", "candidate"}


def test_caption_governance_rewrite_is_used_for_image_candidates():
    library = _library()
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_STABLE_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId1",
            "target": "media/image1.png",
            "part_name": "word/media/image1.png",
            "context": "table_cell",
            "caption_actual": "3 钢筋安装",
            "caption_candidates": ["基础钢筋绑扎示意图"],
            "semantic_text": "钢筋 绑扎",
            "nearby_text": "基础钢筋绑扎示意图",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
            "caption_governance": {
                "action": "rewrite",
                "suggested_caption": "基础钢筋绑扎示意图",
                "confidence": 0.72,
            },
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    image_ref = packages[0]["image_candidate_pool"][0]
    assert image_ref["caption"] == "基础钢筋绑扎示意图"
    assert image_ref["caption_original"] == "3 钢筋安装"
    assert image_ref["caption_governance"]["action"] == "rewrite"


def test_caption_governance_manual_review_blocks_auto_candidate_pool():
    library = _library()
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_PDF_WEAK_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId1",
            "target": "media/image1.png",
            "part_name": "word/media/image1.png",
            "context": "table_cell",
            "caption_actual": "3 钢筋安装",
            "semantic_text": "钢筋 施工",
            "nearby_text": "钢筋 施工",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
            "caption_governance": {
                "action": "manual_review",
                "suggested_caption": "3 钢筋安装",
                "reason": "原题注偏弱且无更好候选",
            },
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    assert packages[0]["image_candidate_pool"] == []
    assert packages[0]["image_references"][0]["use_policy"] == "manual_review"


def test_generic_practice_photo_overrides_overbroad_high_risk_flag():
    outline = {
        "nodes": [
            {
                "node_id": "N4",
                "level": 1,
                "title": "文明施工、环境保护管理体系及施工现场扬尘治理措施",
                "domain": "management",
                "category": "文明环保",
                "children": [
                    {
                        "node_id": "N4_1",
                        "level": 2,
                        "title": "文明施工保证措施",
                        "domain": "management",
                        "category": "文明环保",
                        "children": [],
                    }
                ],
            }
        ]
    }
    library = _library()
    library["slices"][0]["title"] = "文明施工保证措施"
    library["slices"][0]["section_path"] = ["文明施工保证措施", "材料堆放"]
    library["slices"][0]["search_text"] = "文明施工 材料堆放 标准化做法 安全文明"
    library["slices"][0]["reuse_level"] = "direct_reuse"
    library["slices"][0]["project_specific_risk"] = "high"
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_SITE_PRACTICE_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "文明施工保证措施",
            "section_path": ["文明施工保证措施", "材料堆放"],
            "rel_id": "rId-site-practice",
            "target": "media/site-practice.png",
            "part_name": "word/media/site-practice.png",
            "context": "table_cell",
            "caption_actual": "不同钢筋分规格堆放整齐",
            "semantic_text": "材料堆放 标准化做法 安全文明",
            "nearby_text": "材料堆放 标准化做法 安全文明",
            "tags": ["文明施工"],
            "reuse_level": "direct_reuse",
            "project_specific_risk": "high",
            "review_required": True,
            "caption_governance": {"action": "keep", "suggested_caption": "不同钢筋分规格堆放整齐"},
        }
    ]

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=3)

    image_ids = {item["image_id"] for item in packages[0]["image_candidate_pool"]}
    assert "EBIMG_SITE_PRACTICE_001" in image_ids
    assert packages[0]["image_candidate_pool"][0]["use_policy"] == "candidate_reuse"


def test_image_adaptation_filters_conflicting_process_images():
    library = _library()
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_REBAR_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId-rebar",
            "target": "media/rebar.png",
            "part_name": "word/media/rebar.png",
            "context": "table_cell",
            "caption_actual": "钢筋马凳筋做法示意图",
            "nearby_text": "钢筋 马凳筋 绑扎 定位",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        },
        {
            "image_asset_id": "SRC0001-M00001-IMG0001",
            "image_id": "EBIMG_WATERPROOF_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId-waterproof",
            "target": "media/waterproof.png",
            "part_name": "word/media/waterproof.png",
            "context": "table_cell",
            "caption_actual": "地下室防水卷材铺贴示意图",
            "nearby_text": "地下室 防水 卷材 铺贴",
            "tags": ["防水"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        },
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    image_ids = {item["image_id"] for item in packages[0]["image_candidate_pool"]}
    assert "EBIMG_REBAR_001" in image_ids
    assert "EBIMG_WATERPROOF_001" not in image_ids


def test_management_chapter_does_not_auto_use_unrelated_rebar_process_image():
    outline = {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "安全管理体系与措施",
                "domain": "management",
                "category": "安全管理",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "安全生产保障体系",
                        "domain": "management",
                        "category": "安全管理",
                        "children": [],
                    }
                ],
            }
        ]
    }
    library = _library()
    library["slices"][0]["title"] = "钢筋工程施工方案"
    library["slices"][0]["search_text"] = "安全管理体系与措施 安全生产保障体系 钢筋 施工"
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0000",
            "image_id": "EBIMG_REBAR_001",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId-rebar",
            "target": "media/rebar.png",
            "part_name": "word/media/rebar.png",
            "context": "table_cell",
            "caption_actual": "钢筋马凳筋做法示意图",
            "nearby_text": "钢筋 马凳筋 绑扎 定位",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(outline, library, top_k=3)

    assert packages[0]["chapter_image_profile"]["chapter_type"] == "safety_management_section"
    assert packages[0]["image_candidate_pool"] == []


def test_generation_inputs_do_not_fallback_to_manual_review_images():
    outline = {
        "schema_version": "technical_bid_outline_v0.1",
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "施工总平面布置图",
                "domain": "construction",
                "category": "施工总平面",
                "children": [],
            }
        ],
    }
    retrieval_packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=5)

    assert retrieval_packages[0]["image_candidate_pool"] == []
    assert retrieval_packages[0]["image_references"]

    generation_packages = build_chapter_generation_inputs(
        outline,
        {"project_info": {}, "technical_requirements": [], "score_points": []},
        material_retrieval_inputs={"packages": retrieval_packages},
    )

    assert generation_packages[0]["image_candidates"] == []
    assert generation_packages[0]["image_candidate_pool"] == []


def test_material_retrieval_outputs_image_group_candidates():
    library = _library()
    library["image_assets"] = [
        {
            "image_asset_id": f"SRC0001-M00001-IMG000{index}",
            "image_id": f"EBIMG-GROUP-{index}",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
            "rel_id": f"rId{index}",
            "target": f"media/group{index}.png",
            "part_name": f"word/media/group{index}.png",
            "context": "table_cell",
            "table_index": 1,
            "row_index": index,
            "cell_index": 1,
            "caption_actual": caption,
            "caption_candidates": [caption],
            "semantic_text": caption,
            "semantic_confidence": 0.9,
            "image_group_id": "SRC0001-M00001-G0000",
            "group_title": "钢筋加工示意图",
            "group_semantic_text": "钢筋加工示意图",
            "group_member_index": index,
            "group_member_count": 2,
            "must_keep_with_group": True,
            "nearby_text": "钢筋加工示意图",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        }
        for index, caption in enumerate(["钢筋调直", "钢筋切断"], start=1)
    ]
    library["image_groups"] = [
        {
            "image_group_id": "SRC0001-M00001-G0000",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "group_title": "钢筋加工示意图",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
            "table_index": 1,
            "member_count": 2,
            "image_asset_ids": ["SRC0001-M00001-IMG0001", "SRC0001-M00001-IMG0002"],
            "image_ids": ["EBIMG-GROUP-1", "EBIMG-GROUP-2"],
            "captions": ["钢筋调直", "钢筋切断"],
            "semantic_text": "钢筋加工示意图",
            "semantic_confidence": 0.92,
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
            "must_keep_together": True,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    group_ref = packages[0]["image_group_references"][0]
    assert group_ref["image_group_id"] == "SRC0001-M00001-G0000"
    assert group_ref["use_policy"] == "candidate_reuse"
    assert group_ref["must_keep_together"] is True
    assert [member["image_id"] for member in group_ref["members"]] == ["EBIMG-GROUP-1", "EBIMG-GROUP-2"]
    assert packages[0]["image_references"][0]["must_keep_with_group"] is True


def test_image_group_members_are_not_lost_when_material_image_assets_are_limited():
    library = _library()
    assets = []
    for index in range(1, 26):
        assets.append(
            {
                "image_asset_id": f"SRC0001-M00001-IMG{index:04d}",
                "image_id": f"EBIMG-GROUP-LATE-{index}",
                "source_id": "SRC0001",
                "source_type": "docx_only",
                "source_slice_id": "S1",
                "material_slice_id": "SRC0001-M00001",
                "title": "钢筋工程施工方案",
                "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
                "rel_id": f"rId{index}",
                "target": f"media/group-late-{index}.png",
                "part_name": f"word/media/group-late-{index}.png",
                "context": "table_cell",
                "table_index": 1,
                "row_index": index,
                "cell_index": 1,
                "caption_actual": f"钢筋加工流程{index}",
                "caption_candidates": [f"钢筋加工流程{index}"],
                "semantic_text": f"钢筋加工流程{index}",
                "semantic_confidence": 0.9,
                "image_group_id": "SRC0001-M00001-G-LATE",
                "group_title": "钢筋加工流程示意图",
                "group_semantic_text": "钢筋加工流程示意图",
                "group_member_index": index,
                "group_member_count": 4,
                "must_keep_with_group": index in {1, 21, 22, 23},
                "nearby_text": "钢筋加工流程示意图",
                "tags": ["钢筋"],
                "reuse_level": "candidate_reuse",
                "project_specific_risk": "low",
                "review_required": False,
            }
        )
    library["image_assets"] = assets
    library["image_groups"] = [
        {
            "image_group_id": "SRC0001-M00001-G-LATE",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "group_title": "钢筋加工流程示意图",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
            "table_index": 1,
            "member_count": 4,
            "image_asset_ids": [
                "SRC0001-M00001-IMG0001",
                "SRC0001-M00001-IMG0021",
                "SRC0001-M00001-IMG0022",
                "SRC0001-M00001-IMG0023",
            ],
            "image_ids": [
                "EBIMG-GROUP-LATE-1",
                "EBIMG-GROUP-LATE-21",
                "EBIMG-GROUP-LATE-22",
                "EBIMG-GROUP-LATE-23",
            ],
            "captions": ["钢筋加工流程1", "钢筋加工流程21", "钢筋加工流程22", "钢筋加工流程23"],
            "semantic_text": "钢筋加工流程示意图",
            "semantic_confidence": 0.92,
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
            "must_keep_together": True,
        }
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    group_ref = next(
        item
        for item in packages[0]["image_group_candidate_pool"]
        if item["image_group_id"] == "SRC0001-M00001-G-LATE"
    )
    assert group_ref["member_count"] == 4
    assert [member["image_id"] for member in group_ref["members"]] == [
        "EBIMG-GROUP-LATE-1",
        "EBIMG-GROUP-LATE-21",
        "EBIMG-GROUP-LATE-22",
        "EBIMG-GROUP-LATE-23",
    ]


def test_image_candidate_pool_dedupes_by_canonical_fingerprint():
    library = _library()
    for index, material in enumerate(library["slices"][:2], start=1):
        material["title"] = "钢筋工程施工方案"
        material["section_path"] = ["主要施工方案与技术措施", "钢筋工程施工方案"]
        material["search_text"] = "钢筋 工程 施工 方案"
        material["material_quality"] = "high"
        material["reuse_level"] = "direct_reuse"
        material["project_specific_risk"] = "low"
        material["image_count"] = 1
    library["image_assets"] = [
        {
            "image_asset_id": "SRC0001-M00001-IMG0001",
            "image_id": "EBIMG-DUP-A",
            "source_id": "SRC0001",
            "source_type": "docx_only",
            "source_slice_id": "S1",
            "material_slice_id": "SRC0001-M00001",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId1",
            "target": "media/a.png",
            "part_name": "word/media/a.png",
            "canonical_image_id": "sha256:dup",
            "sha256": "dup",
            "caption_actual": "钢筋加工示意图",
            "semantic_text": "钢筋加工示意图",
            "semantic_confidence": 0.9,
            "nearby_text": "钢筋加工示意图",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        },
        {
            "image_asset_id": "SRC0002-M00068-IMG0001",
            "image_id": "EBIMG-DUP-B",
            "source_id": "SRC0002",
            "source_type": "docx_only",
            "source_slice_id": "S2",
            "material_slice_id": "SRC0002-M00068",
            "title": "钢筋工程施工方案",
            "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
            "rel_id": "rId2",
            "target": "media/b.png",
            "part_name": "word/media/b.png",
            "canonical_image_id": "sha256:dup",
            "sha256": "dup",
            "caption_actual": "钢筋加工示意图",
            "semantic_text": "钢筋加工示意图",
            "semantic_confidence": 0.9,
            "nearby_text": "钢筋加工示意图",
            "tags": ["钢筋"],
            "reuse_level": "candidate_reuse",
            "project_specific_risk": "low",
            "review_required": False,
        },
    ]

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    dup_refs = [
        item
        for item in packages[0]["image_candidate_pool"]
        if item.get("canonical_image_id") == "sha256:dup"
    ]
    assert len(dup_refs) == 1


def test_image_group_candidate_pool_dedupes_by_member_fingerprints():
    library = _library()
    library["image_assets"] = []
    library["image_groups"] = []
    for material_index, material in enumerate(library["slices"][:2], start=1):
        material["title"] = "钢筋工程施工方案"
        material["section_path"] = ["主要施工方案与技术措施", "钢筋工程施工方案"]
        material["search_text"] = "钢筋 工程 施工 方案"
        material["material_quality"] = "high"
        material["reuse_level"] = "direct_reuse"
        material["project_specific_risk"] = "low"
        material_id = material["material_slice_id"]
        group_id = f"{material_id}-G-DUP"
        assets = []
        for index, sha in enumerate(["same-1", "same-2"], start=1):
            asset_id = f"{material_id}-IMG{index:04d}"
            assets.append(asset_id)
            library["image_assets"].append(
                {
                    "image_asset_id": asset_id,
                    "image_id": f"EBIMG-GROUP-DUP-{material_index}-{index}",
                    "source_id": material["source_id"],
                    "source_type": "docx_only",
                    "source_slice_id": material["source_slice_id"],
                    "material_slice_id": material_id,
                    "title": "钢筋工程施工方案",
                    "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                    "rel_id": f"rId{material_index}-{index}",
                    "target": f"media/{material_index}-{index}.png",
                    "part_name": f"word/media/{material_index}-{index}.png",
                    "canonical_image_id": f"sha256:{sha}",
                    "sha256": sha,
                    "caption_actual": f"钢筋加工{index}",
                    "semantic_text": f"钢筋加工{index}",
                    "semantic_confidence": 0.9,
                    "image_group_id": group_id,
                    "group_title": "钢筋加工示意图",
                    "group_semantic_text": "钢筋加工示意图",
                    "group_member_index": index,
                    "group_member_count": 2,
                    "must_keep_with_group": True,
                    "nearby_text": "钢筋加工示意图",
                    "tags": ["钢筋"],
                    "reuse_level": "candidate_reuse",
                    "project_specific_risk": "low",
                    "review_required": False,
                }
            )
        library["image_groups"].append(
            {
                "image_group_id": group_id,
                "source_id": material["source_id"],
                "source_type": "docx_only",
                "source_slice_id": material["source_slice_id"],
                "material_slice_id": material_id,
                "title": "钢筋工程施工方案",
                "group_title": "钢筋加工示意图",
                "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                "table_index": 1,
                "member_count": 2,
                "image_asset_ids": assets,
                "canonical_image_ids": ["sha256:same-1", "sha256:same-2"],
                "sha256_values": ["same-1", "same-2"],
                "group_canonical_image_key": "sha256-set:same",
                "captions": ["钢筋加工1", "钢筋加工2"],
                "semantic_text": "钢筋加工示意图",
                "semantic_confidence": 0.92,
                "tags": ["钢筋"],
                "reuse_level": "candidate_reuse",
                "project_specific_risk": "low",
                "review_required": False,
                "must_keep_together": True,
            }
        )

    packages = build_chapter_material_retrieval_inputs(_outline(), library, top_k=3)

    dup_groups = [
        item
        for item in packages[0]["image_group_candidate_pool"]
        if item.get("group_canonical_image_key") == "sha256-set:same"
    ]
    assert len(dup_groups) == 1


def test_include_domain_filters_packages():
    outline = _outline()
    outline["nodes"].append(
        {
            "node_id": "N2",
            "level": 1,
            "title": "设计方案",
            "domain": "design",
            "category": "设计方案",
            "children": [],
        }
    )

    packages = build_chapter_material_retrieval_inputs(outline, _library(), include_domains={"design"})

    assert len(packages) == 1
    assert packages[0]["target_section"]["domain"] == "design"


def test_non_core_category_stays_level1_and_uses_child_headings_in_query():
    outline = {
        "nodes": [
            {
                "node_id": "N3",
                "level": 1,
                "title": "施工总平面布置图",
                "domain": "construction",
                "category": "施工总平面",
                "children": [
                    {
                        "node_id": "N3_1",
                        "level": 2,
                        "title": "施工总平面图",
                        "domain": "construction",
                        "category": "施工总平面",
                        "children": [],
                    }
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=5)

    assert len(packages) == 1
    assert packages[0]["target_section"]["chapter_path"] == ["施工总平面布置图"]
    assert "施工总平面图" in packages[0]["target_section"]["query"]


def test_content_completeness_skips_history_construction_materials():
    outline = {
        "nodes": [
            {
                "node_id": "N0",
                "level": 1,
                "title": "内容完整性",
                "domain": "construction",
                "category": "施工方案",
                "children": [],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=5)

    assert packages[0]["matched_materials"] == []
    assert packages[0]["retrieval_policy"]["skip_reason"]


def test_chapter_image_profile_keeps_schedule_management_ahead_of_new_technology_keywords():
    outline = {
        "nodes": [
            {
                "node_id": "N5",
                "level": 1,
                "title": "工期保证措施",
                "domain": "management",
                "category": "工期管理",
                "children": [
                    {
                        "node_id": "N5_1",
                        "level": 2,
                        "title": "工期保证技术措施",
                        "domain": "management",
                        "category": "工期管理",
                        "children": [
                            {"node_id": "N5_1_1", "level": 3, "title": "新技术、新工艺应用提速方案"},
                        ],
                    }
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=1)

    assert packages[0]["chapter_image_profile"]["chapter_type"] == "schedule_management_section"


def test_chapter_image_profile_keeps_technical_innovation_ahead_of_green_keywords():
    outline = {
        "nodes": [
            {
                "node_id": "N6",
                "level": 1,
                "title": "技术创新的应用实施措施",
                "domain": "construction",
                "category": "技术创新",
                "children": [
                    {"node_id": "N6_1", "level": 2, "title": "绿色施工技术应用"},
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=1)

    assert packages[0]["chapter_image_profile"]["chapter_type"] == "bim_information_section"


def test_chapter_image_profile_keeps_civilized_creation_plan_ahead_of_progress_child_items():
    outline = {
        "nodes": [
            {
                "node_id": "N7",
                "level": 1,
                "title": "文明施工、环境保护管理体系及施工现场扬尘治理措施",
                "domain": "management",
                "category": "文明环保",
                "children": [
                    {
                        "node_id": "N7_1",
                        "level": 2,
                        "title": "安全文明标准化工地创建计划",
                        "domain": "management",
                        "category": "文明环保",
                        "children": [
                            {"node_id": "N7_1_1", "level": 3, "title": "创建实施进度计划"},
                        ],
                    }
                ],
            }
        ]
    }

    packages = build_chapter_material_retrieval_inputs(outline, _library(), top_k=1)

    assert packages[0]["chapter_image_profile"]["chapter_type"] == "civilized_environment_section"


def test_report_summarizes_material_counts():
    packages = build_chapter_material_retrieval_inputs(_outline(), _library(), top_k=2)

    report = render_chapter_material_retrieval_report(packages)

    assert "# 章节生成素材检索输入包报告" in report
    assert "输入包数量：2" in report
    assert "命中素材" in report


def _outline():
    return {
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "score_rule": "施工方案完整，技术措施合理。",
                "children": [
                    {
                        "node_id": "N1_1",
                        "level": 2,
                        "title": "钢筋工程施工方案",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [],
                    },
                    {
                        "node_id": "N1_2",
                        "level": 2,
                        "title": "施工总平面图",
                        "domain": "construction",
                        "category": "施工方案",
                        "children": [],
                    },
                ],
            }
        ]
    }


def _library():
    return {
        "schema_version": "excellent_bid_material_library_v1",
        "slices": [
            {
                "material_slice_id": "SRC0001-M00001",
                "source_id": "SRC0001",
                "source_type": "docx_only",
                "source_slice_id": "S1",
                "title": "钢筋工程施工方案",
                "clean_title": "钢筋工程施工方案",
                "level": 2,
                "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                "section_key": "主要施工方案与技术措施 > 钢筋工程施工方案",
                "search_text": "主要施工方案与技术措施 钢筋工程施工方案 钢筋工程施工正文 工序 措施",
                "keywords": ["钢筋工程", "施工方案"],
                "primary_material_source": "docx",
                "material_quality": "high",
                "reuse_level": "parameterized_reuse",
                "project_specific_risk": "medium",
                "paragraph_count": 1,
                "paragraph_char_count": 20,
                "table_count": 1,
                "image_count": 1,
                "docx_table_count": 1,
                "docx_image_count": 1,
                "paragraphs": [
                    {
                        "paragraph_index": 1,
                        "block_index": 1,
                        "style": None,
                        "char_count": 20,
                        "text_preview": "钢筋工程施工正文",
                        "image_count": 0,
                    }
                ],
                "tables": [
                    {
                        "table_index": 1,
                        "block_index": 2,
                        "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                        "section_level": 2,
                        "nearest_heading_index": 1,
                        "nearest_heading_text": "钢筋工程施工方案",
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 1,
                        "header_preview": ["工序", "措施"],
                        "row_previews": [],
                    }
                ],
                "image_bindings": [
                    {
                        "rel_id": "rId1",
                        "target": "media/image1.png",
                        "part_name": "word/media/image1.png",
                        "context": "table_cell",
                        "block_index": 2,
                        "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案"],
                        "table_index": 1,
                        "row_index": 1,
                        "cell_index": 1,
                    }
                ],
            },
            {
                "material_slice_id": "SRC0002-M00068",
                "source_id": "SRC0002",
                "source_type": "pdf_docx_fusion",
                "source_slice_id": "FUS-PDFS0068",
                "title": "钢筋工程施工方法",
                "clean_title": "钢筋工程施工方法",
                "level": 3,
                "section_path": ["主要施工方案与技术措施", "钢筋工程施工方案", "钢筋工程施工方法"],
                "search_text": "钢筋工程施工方法 钢筋加工",
                "primary_material_source": "docx",
                "material_quality": "review_required",
                "reuse_level": "parameterized_reuse",
                "project_specific_risk": "medium",
                "paragraph_count": 1,
                "paragraph_char_count": 10,
                "table_count": 1,
                "image_count": 0,
                "match_status": "fallback",
                "paragraphs": [],
                "tables": [],
                "image_bindings": [],
            },
            {
                "material_slice_id": "SRC0002-M00367",
                "source_id": "SRC0002",
                "source_type": "pdf_docx_fusion",
                "source_slice_id": "FUS-PDFS0367",
                "title": "施工总平面图",
                "clean_title": "施工总平面图",
                "level": 1,
                "section_path": ["施工总平面图"],
                "search_text": "施工总平面图 平面布置图",
                "primary_material_source": "pdf",
                "material_quality": "pdf_fallback",
                "reuse_level": "manual_review",
                "project_specific_risk": "high",
                "paragraph_count": 1,
                "paragraph_char_count": 10,
                "table_count": 0,
                "image_count": 1,
                "pdf_image_count": 1,
                "paragraphs": [],
                "tables": [],
                "image_bindings": [
                    {
                        "rel_id": "rId3",
                        "target": "media/site-plan.png",
                        "part_name": "word/media/site-plan.png",
                        "context": "table_cell",
                        "block_index": 9,
                        "section_path": ["施工总平面图"],
                        "table_index": 9,
                        "row_index": 1,
                        "cell_index": 1,
                    }
                ],
            },
        ],
    }


def _material_slice(
    material_slice_id: str,
    title: str,
    section_path: list[str],
    search_text: str,
    image_name: str,
) -> dict:
    return {
        "material_slice_id": material_slice_id,
        "source_id": "SRC0001",
        "source_type": "docx_only",
        "source_slice_id": material_slice_id,
        "title": title,
        "clean_title": title,
        "level": len(section_path),
        "section_path": section_path,
        "section_key": " > ".join(section_path),
        "search_text": " ".join([*section_path, search_text]),
        "keywords": [],
        "primary_material_source": "docx",
        "material_quality": "high",
        "reuse_level": "parameterized_reuse",
        "project_specific_risk": "medium",
        "paragraph_count": 1,
        "paragraph_char_count": 20,
        "table_count": 1,
        "image_count": 1,
        "docx_table_count": 1,
        "docx_image_count": 1,
        "paragraphs": [
            {
                "paragraph_index": 1,
                "block_index": 1,
                "style": None,
                "char_count": 20,
                "text_preview": search_text,
                "image_count": 0,
            }
        ],
        "tables": [],
        "image_bindings": [
            {
                "rel_id": f"rId-{image_name}",
                "target": f"media/{image_name}",
                "part_name": f"word/media/{image_name}",
                "context": "table_cell",
                "block_index": 2,
                "section_path": section_path,
                "table_index": 1,
                "row_index": 1,
                "cell_index": 1,
            }
        ],
    }
