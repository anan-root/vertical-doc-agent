from construction_bidding_agent.document_parser.excellent_bid_material_library import (
    build_excellent_bid_material_library,
    render_excellent_bid_material_library_report,
    search_excellent_bid_materials,
)
from construction_bidding_agent.document_parser.excellent_bid_source_filter import (
    filter_excellent_bid_material_library,
)
from construction_bidding_agent.document_parser.excellent_bid_text_image_block_index import (
    build_text_image_block_index,
    search_text_image_blocks,
)
from construction_bidding_agent.document_parser.image_fingerprints import (
    enrich_material_library_image_fingerprints,
)


def test_library_normalizes_docx_and_fusion_indexes():
    result = build_excellent_bid_material_library(
        [
            ("docx.json", _docx_index()),
            ("fusion.json", _fusion_index()),
        ],
        library_id="test_library",
    )

    assert result.library_id == "test_library"
    assert result.source_count == 2
    assert result.slice_count == 3
    assert result.table_count == 4
    assert result.image_count == 2
    assert result.docx_table_count == 3
    assert result.docx_image_count == 2
    assert result.pdf_fallback_table_count == 1
    assert result.pdf_fallback_image_count == 0
    assert result.pdf_reference_table_like_count == 3
    assert result.pdf_reference_image_count == 2
    assert result.image_asset_count == 2
    assert len(result.image_assets) == 2
    assert result.source_type_counts == {"docx_only": 1, "pdf_docx_fusion": 1}
    assert result.material_quality_counts["high"] == 2
    assert result.material_quality_counts["pdf_fallback"] == 1

    docx_slice = result.slices[0]
    fusion_slice = result.slices[1]
    unmatched_slice = result.slices[2]
    assert docx_slice.source_type == "docx_only"
    assert docx_slice.primary_material_source == "docx"
    assert docx_slice.section_key
    assert docx_slice.table_count == 1
    assert docx_slice.tables[0].row_previews
    assert docx_slice.tables[0].row_previews[0].cells[0].text_preview == "分类"
    assert fusion_slice.match_status == "matched"
    assert fusion_slice.docx_table_count == 2
    assert unmatched_slice.material_quality == "pdf_fallback"
    assert unmatched_slice.primary_material_source == "pdf"


def test_source_filter_keeps_two_word_sources_and_drops_fusion_source():
    result = build_excellent_bid_material_library(
        [
            ("docx.json", _docx_index()),
            ("fusion.json", _fusion_index()),
            ("zhenggui.json", _docx_index()),
        ]
    ).to_dict()
    result["sources"][2]["source_id"] = "SRC0003"
    result["sources"][2]["source_name"] = "郑轨·云庭01标段技术标投标文件"
    for item in result["slices"]:
        if item["source_id"] == "SRC0001" and item["material_slice_id"].startswith("SRC0001"):
            duplicate = dict(item)
            duplicate["source_id"] = "SRC0003"
            duplicate["material_slice_id"] = duplicate["material_slice_id"].replace("SRC0001", "SRC0003", 1)
            result["slices"].append(duplicate)
            break

    filtered = filter_excellent_bid_material_library(result, enabled_source_ids={"SRC0001", "SRC0003"})

    assert {source["source_id"] for source in filtered["sources"]} == {"SRC0001", "SRC0003"}
    assert {item["source_id"] for item in filtered["slices"]} == {"SRC0001", "SRC0003"}
    assert {item["source_id"] for item in filtered["image_assets"]} <= {"SRC0001", "SRC0003"}
    assert filtered["source_count"] == 2
    assert filtered["pdf_fallback_image_count"] == 0
    assert filtered["source_filter"]["disabled_sources"][0]["source_id"] == "SRC0002"


def test_text_image_block_index_searches_mature_text_image_blocks():
    index = _docx_index()
    slice_ = index["slices"][0]
    slice_["section_path"] = ["1 施工方案与技术措施", "1.4 钢筋工程"]
    slice_["paragraphs"][0]["text_preview"] = "钢筋加工、连接、绑扎和验收流程控制。"
    slice_["image_bindings"][0].update(
        {
            "section_path": ["1 施工方案与技术措施", "1.4 钢筋工程"],
            "cell_text": "钢筋加工绑扎流程示意图",
            "row_text": "工序 | 钢筋加工绑扎流程示意图",
            "nearby_text": "钢筋加工绑扎流程示意图；钢筋连接；钢筋验收",
            "caption_candidates": ["钢筋加工绑扎流程示意图"],
        }
    )
    library = build_excellent_bid_material_library([("docx.json", index)]).to_dict()

    block_index = build_text_image_block_index(library)
    hits = search_text_image_blocks(
        block_index,
        query="钢筋加工绑扎流程示意图",
        section_path=["主要施工方案与技术措施", "钢筋工程施工方案"],
        top_k=3,
    )

    assert block_index["block_count"] >= 1
    assert hits
    assert hits[0]["block_id"].startswith(("TIB-", "TIBR-"))
    assert hits[0]["image_count"] >= 1
    assert "钢筋" in hits[0]["topics"]
    assert hits[0]["primary_topic"] == "钢筋"
    assert hits[0]["match_level"] in {"moderate", "strong"}
    assert hits[0]["match_reasons"]


def test_text_image_block_index_builds_row_blocks_for_mixed_topic_tables():
    index = _docx_index()
    first = index["slices"][0]["image_bindings"][0]
    index["slices"][0]["section_path"] = ["施工方案", "模板、防水混合做法表"]
    index["slices"][0]["image_count"] = 2
    index["slices"][0]["docx_image_count"] = 2
    index["slices"][0]["image_bindings"] = [
        {
            **first,
            "rel_id": "rId-formwork",
            "target": "media/formwork.png",
            "part_name": "word/media/formwork.png",
            "table_index": 7,
            "row_index": 2,
            "cell_index": 2,
            "cell_text": "梁柱接头模板支设施工示意图",
            "row_text": "模板工程 | 梁柱接头模板支设 | 梁柱接头模板支设施工示意图",
            "nearby_text": "模板工程；梁柱接头模板支设；模板拼缝加固",
            "caption_candidates": ["梁柱接头模板支设施工示意图"],
        },
        {
            **first,
            "rel_id": "rId-waterproof",
            "target": "media/waterproof.png",
            "part_name": "word/media/waterproof.png",
            "table_index": 7,
            "row_index": 5,
            "cell_index": 2,
            "cell_text": "阴阳角防水附加层施工示意图",
            "row_text": "防水工程 | 阴阳角防水附加层 | 阴阳角防水附加层施工示意图",
            "nearby_text": "防水工程；卷材铺贴；阴阳角防水附加层",
            "caption_candidates": ["阴阳角防水附加层施工示意图"],
        },
    ]
    library = build_excellent_bid_material_library([("docx.json", index)]).to_dict()

    block_index = build_text_image_block_index(library)
    hits = search_text_image_blocks(
        block_index,
        query="梁柱接头模板支设及模板拼缝加固",
        section_path=["主要施工方案与技术措施", "模板工程施工方案"],
        top_k=5,
    )

    assert hits
    assert hits[0]["block_type"] == "method_row_block"
    assert hits[0]["primary_topic"] == "模板"
    assert hits[0]["image_count"] == 1
    assert any("模板" in caption for caption in hits[0]["captions"])
    assert not any("防水" in caption for caption in hits[0]["captions"])


def test_text_image_block_search_rejects_row_block_without_specific_subtopic():
    block_index = {
        "blocks": [
            {
                **_text_image_block(
                    "WRONG_ROW",
                    "后浇带未独立支设缺陷",
                    "后浇带未独立支设缺陷",
                    "模板",
                    ["模板"],
                ),
                "block_type": "method_row_block",
                "image_count": 1,
                "image_group_count": 0,
                "use_policy": "row_block_preferred",
                "row_scope": {"table_index": 1, "start_row_index": 10, "end_row_index": 10},
            },
            {
                **_text_image_block(
                    "RIGHT_ROW",
                    "梁柱接头模板支设及模板拼缝加固",
                    "梁柱接头模板支设平面图及模板拼缝节点大样",
                    "模板",
                    ["模板"],
                ),
                "block_type": "method_row_block",
                "image_count": 1,
                "image_group_count": 0,
                "use_policy": "row_block_preferred",
                "row_scope": {"table_index": 1, "start_row_index": 12, "end_row_index": 12},
            },
        ]
    }

    hits = search_text_image_blocks(
        block_index,
        query="模板拆除 梁柱接头模板支设 模板拼缝",
        section_path=["主要施工方案与技术措施", "模板工程施工方案"],
        top_k=5,
    )

    assert [hit["block_id"] for hit in hits] == ["RIGHT_ROW"]
    assert "row_block_missing_strong_specific_terms" not in hits[0]["risk_flags"]


def test_text_image_block_search_rejects_cross_process_primary_topic():
    block_index = {
        "blocks": [
            _text_image_block("STEEL", "钢筋加工成型", "钢筋加工、连接、绑扎流程示意图", "钢筋", ["钢筋"]),
            _text_image_block("CONCRETE", "混凝土浇筑控制", "混凝土浇筑、振捣、养护和温控示意图", "混凝土", ["混凝土"]),
        ]
    }

    hits = search_text_image_blocks(
        block_index,
        query="混凝土浇筑及大体积温控措施",
        section_path=["主要施工方案与技术措施", "混凝土浇筑及大体积温控措施"],
        top_k=5,
    )

    assert [hit["block_id"] for hit in hits] == ["CONCRETE"]
    assert hits[0]["primary_topic"] == "混凝土"


def test_text_image_block_search_rejects_process_blocks_for_general_analysis():
    block_index = {
        "blocks": [
            _text_image_block("STEEL", "钢筋加工成型", "钢筋加工、连接、绑扎流程示意图", "钢筋", ["钢筋"]),
        ]
    }

    hits = search_text_image_blocks(
        block_index,
        query="工程重点难点分析及对策",
        section_path=["主要施工方案与技术措施", "工程重点难点分析及对策"],
        top_k=5,
    )

    assert hits == []


def test_text_image_block_search_prefers_title_subtopic_over_caption_only_match():
    block_index = {
        "blocks": [
            _text_image_block("CAPTION_ONLY", "装饰装修工程质量管理措施", "屋面防水节点做法", "防水", ["防水", "质量管理"]),
            _text_image_block("WATERPROOF", "地下室防水施工", "阴阳角防水节点、卷材搭接和穿墙套管防水细部做法", "防水", ["防水"]),
        ]
    }

    hits = search_text_image_blocks(
        block_index,
        query="地下室及屋面防水施工技术",
        section_path=["主要施工方案与技术措施", "地下室及屋面防水施工技术"],
        top_k=5,
    )

    assert [hit["block_id"] for hit in hits] == ["WATERPROOF", "CAPTION_ONLY"]
    assert "标题/路径子主题命中：地下室" in hits[0]["match_reasons"]
    assert "subtopic_only_from_caption" in hits[1]["risk_flags"]


def test_library_builds_image_assets_with_nearby_text_and_caption_candidates():
    result = build_excellent_bid_material_library([("docx.json", _docx_index())])

    assert result.image_asset_count == 1
    asset = result.image_assets[0]
    assert asset.image_id.startswith("EBIMG_SRC0001")
    assert asset.part_name == "word/media/image1.png"
    assert asset.caption_actual == "标准化防护示意图"
    assert "标准化防护示意图" in asset.caption_candidates
    assert "上一行说明" in asset.nearby_text
    assert asset.semantic_text == "标准化防护示意图"
    assert asset.semantic_confidence >= 0.9
    assert asset.semantic_sources[0]["source_type"] in {"below_cell_caption", "same_cell_caption"}
    assert asset.reuse_level == "direct_reuse"
    assert asset.review_required is False


def test_library_builds_image_semantics_from_previous_table_rows_when_caption_missing():
    index = _docx_index()
    binding = index["slices"][0]["image_bindings"][0]
    binding["cell_text"] = ""
    binding["below_cell_text"] = ""
    binding["row_text"] = " | | "
    binding["previous_row_text"] = "工序 | 钢筋马凳筋设置控制"
    binding["previous_row_texts"] = [
        "工序 | 钢筋马凳筋设置控制",
        "措施 | 按板厚与保护层厚度设置马凳筋间距",
    ]
    binding["nearby_text"] = ""
    binding["caption_candidates"] = []

    result = build_excellent_bid_material_library([("docx.json", index)])
    asset = result.image_assets[0]
    source_types = {item["source_type"] for item in asset.semantic_sources}

    assert asset.semantic_text == "工序；钢筋马凳筋设置控制"
    assert "previous_row_1_item" in source_types
    assert any("马凳筋" in item["text"] for item in asset.semantic_sources)


def test_library_builds_image_groups_and_marks_members_keep_together():
    index = _docx_index()
    first = index["slices"][0]["image_bindings"][0]
    index["slices"][0]["image_count"] = 3
    index["slices"][0]["docx_image_count"] = 3
    index["slices"][0]["image_bindings"] = [
        {
            **first,
            "rel_id": f"rId{idx}",
            "target": f"media/image{idx}.png",
            "part_name": f"word/media/image{idx}.png",
            "row_index": idx,
            "cell_index": 1,
            "previous_row_text": "钢筋加工示意图",
            "cell_text": "",
            "below_cell_text": caption,
            "caption_candidates": [caption],
        }
        for idx, caption in enumerate(["钢筋调直", "钢筋切断", "钢筋弯曲"], start=1)
    ]

    result = build_excellent_bid_material_library([("docx.json", index)])

    assert result.image_group_count == 1
    group = result.image_groups[0]
    assert group.member_count == 3
    assert group.group_title == "钢筋加工示意图"
    assert group.must_keep_together is True
    assert [asset.image_group_id for asset in result.image_assets] == [group.image_group_id] * 3
    assert all(asset.must_keep_with_group for asset in result.image_assets)
    assert [asset.group_member_index for asset in result.image_assets] == [1, 2, 3]


def test_material_library_image_fingerprint_enrichment_updates_assets_and_groups(tmp_path):
    from PIL import Image

    source_docx = tmp_path / "demo.docx"
    image_buffer = tmp_path / "image1.png"
    Image.new("RGB", (12, 12), "red").save(image_buffer)
    import zipfile

    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(image_buffer, "word/media/image1.png")

    library = {
        "library_id": "test_library",
        "sources": [
            {
                "source_id": "SRC0001",
                "source_name": "demo",
                "source_type": "docx_only",
                "source_paths": [str(source_docx)],
            }
        ],
        "image_assets": [
            {
                "image_asset_id": "SRC0001-M00001-IMG0001",
                "image_id": "EBIMG-1",
                "source_id": "SRC0001",
                "part_name": "word/media/image1.png",
                "caption_actual": "钢筋加工示意图",
            }
        ],
        "image_groups": [
            {
                "image_group_id": "SRC0001-M00001-G0001",
                "image_asset_ids": ["SRC0001-M00001-IMG0001"],
            }
        ],
    }

    stats = enrich_material_library_image_fingerprints(library, raw_root=tmp_path)

    asset = library["image_assets"][0]
    group = library["image_groups"][0]
    assert stats["fingerprinted_asset_count"] == 1
    assert stats["missing_count"] == 0
    assert asset["sha256"]
    assert asset["canonical_image_id"] == f"sha256:{asset['sha256']}"
    assert asset["perceptual_hash"]
    assert group["canonical_image_ids"] == [asset["canonical_image_id"]]
    assert group["sha256_values"] == [asset["sha256"]]


def test_library_merges_same_table_flow_image_groups_into_one_group():
    index = _docx_index()
    first = index["slices"][0]["image_bindings"][0]
    index["slices"][0]["section_path"] = ["施工方案", "钢筋工程施工方案", "典型梁板钢筋绑扎流程示意图"]
    index["slices"][0]["image_count"] = 4
    index["slices"][0]["docx_image_count"] = 4
    index["slices"][0]["image_bindings"] = [
        {
            **first,
            "rel_id": f"rId{idx}",
            "target": f"media/flow{idx}.png",
            "part_name": f"word/media/flow{idx}.png",
            "table_index": 8,
            "row_index": row_index,
            "cell_index": cell_index,
            "previous_row_text": previous_row,
            "previous_row_texts": [previous_row],
            "cell_text": "",
            "below_cell_text": caption,
            "row_text": row_text,
            "caption_candidates": [caption],
        }
        for idx, row_index, cell_index, previous_row, row_text, caption in [
            (1, 2, 0, "第一阶段施工流程 | 放线、搭设满堂架 | 绑扎梁钢筋", "封梁侧模板 | 铺板底模板并弹板筋控制线", "封梁侧模板"),
            (2, 2, 1, "第一阶段施工流程 | 放线、搭设满堂架 | 绑扎梁钢筋", "封梁侧模板 | 铺板底模板并弹板筋控制线", "铺板底模板并弹板筋控制线"),
            (3, 4, 0, "第二阶段施工流程 | 封梁侧模板 | 铺板底模板并弹板筋控制线", "绑扎楼板钢筋 | 混凝土浇筑", "绑扎楼板钢筋"),
            (4, 4, 1, "第二阶段施工流程 | 封梁侧模板 | 铺板底模板并弹板筋控制线", "绑扎楼板钢筋 | 混凝土浇筑", "混凝土浇筑"),
        ]
    ]

    result = build_excellent_bid_material_library([("docx.json", index)])
    flow_groups = [group for group in result.image_groups if group.detection_method == "same_table_flow_merge"]

    assert len(flow_groups) == 1
    group = flow_groups[0]
    assert group.member_count == 4
    assert group.must_keep_together is True
    assert [asset.image_group_id for asset in result.image_assets] == [group.image_group_id] * 4
    assert [asset.group_member_index for asset in result.image_assets] == [1, 2, 3, 4]


def test_library_search_by_query_and_section_path():
    result = build_excellent_bid_material_library(
        [
            ("docx.json", _docx_index()),
            ("fusion.json", _fusion_index()),
        ]
    )

    hits = search_excellent_bid_materials(
        result,
        query="钢筋 工程",
        section_path=["1. 施工方案与技术措施", "1.4 钢筋工程"],
        top_k=3,
    )

    assert hits
    assert hits[0].slice is not None
    assert hits[0].slice.title == "1.4 钢筋工程"
    assert "section_path_exact" in hits[0].reasons


def test_library_search_recalls_related_chinese_phrases():
    result = build_excellent_bid_material_library([("fusion.json", _fusion_index())])
    result.slices[0].title = "施工总平面图"
    result.slices[0].section_path = ["附表五 施工总平面图"]
    result.slices[0].search_text = "附表五 施工总平面图 临时道路 材料堆场"
    result.slices[0].section_key = "附表五施工总平面图"

    hits = search_excellent_bid_materials(result, query="施工总平面布置图", top_k=3)

    assert hits
    assert "phrase_overlap" in hits[0].reasons


def test_library_search_keeps_schedule_intent_from_generic_construction_matches():
    result = build_excellent_bid_material_library([("fusion.json", _fusion_index())])
    result.slices[0].title = "附表四 计划开、竣工日期和施工进度网络图"
    result.slices[0].section_path = ["附表四 计划开、竣工日期和施工进度网络图"]
    result.slices[0].search_text = "附表四 计划开、竣工日期和施工进度网络图 总工期 关键线路"
    result.slices[0].section_key = "附表四计划开竣工日期和施工进度网络图"
    result.slices[1].title = "施工机械选型"
    result.slices[1].section_path = ["施工机械选型"]
    result.slices[1].search_text = "施工机械选型 机械设备投入计划 混凝土施工方案及技术措施"
    result.slices[1].section_key = "施工机械选型"

    hits = search_excellent_bid_materials(result, query="施工进度表", top_k=5)

    assert hits
    assert hits[0].slice is not None
    assert hits[0].slice.title == "附表四 计划开、竣工日期和施工进度网络图"
    assert "intent_match" in hits[0].reasons
    assert all(hit.slice and hit.slice.title != "施工机械选型" for hit in hits)


def test_library_assigns_reuse_control_for_typical_section_types():
    result = build_excellent_bid_material_library([("docx.json", _reuse_policy_docx_index())])

    levels = {slice_.title: (slice_.reuse_level, slice_.project_specific_risk) for slice_ in result.slices}

    assert levels["成品保护措施"] == ("direct_reuse", "low")
    assert levels["环境保护措施"] == ("direct_reuse", "low")
    assert levels["钢筋工程施工方案"] == ("parameterized_reuse", "medium")
    assert levels["模板工程施工方案"] == ("parameterized_reuse", "medium")
    assert levels["施工总平面布置图"] == ("manual_review", "high")
    assert levels["施工进度网络图"] == ("manual_review", "high")
    assert levels["项目概况"] == ("manual_review", "high")
    assert levels["标准化防护现场照片"] == ("direct_reuse", "low")
    assert levels["现场踏勘现状照片"] == ("manual_review", "high")


def test_library_normalizes_legacy_reuse_level_when_loading_json_dict():
    library = {
        "schema_version": "excellent_bid_material_library_v1",
        "slices": [
            {
                "material_slice_id": "SRC0001-M00001",
                "source_id": "SRC0001",
                "source_type": "docx_only",
                "source_slice_id": "S1",
                "title": "质量保证体系",
                "section_path": ["质量保证体系"],
                "material_quality": "high",
                "reuse_level": "light_rewrite",
                "project_specific_risk": "low",
            }
        ],
    }

    hits = search_excellent_bid_materials(library, query="质量保证体系", top_k=1)

    assert hits
    assert hits[0].slice is not None
    assert hits[0].slice.reuse_level == "rewrite_reuse"


def test_library_report_contains_source_and_quality_summary():
    result = build_excellent_bid_material_library([("docx.json", _docx_index())])

    report = render_excellent_bid_material_library_report(result)

    assert "# 优秀标书统一素材库报告" in report
    assert "来源类型分布" in report
    assert "可取用表格数" in report
    assert "PDF 页级参考疑似表格数" in report
    assert "复用等级分布" in report
    assert "图片资产数" in report
    assert "图片资产预览" in report
    assert "DOCX优秀标书" in report


def test_image_asset_prefers_embedded_caption_over_weak_row_item():
    index = _docx_index()
    binding = index["slices"][0]["image_bindings"][0]
    embedded_caption = "大体积混凝土浇筑后温度分布梯度示意图(红、橙、黄、绿、蓝代表温度依次递减)"
    cell_text = (
        "结构在有约束的情况下变形时，受到一定的约束而在其内部产生应力。"
        f"{embedded_caption}"
    )
    binding.update(
        {
            "cell_text": cell_text,
            "row_text": f"2 | 约束条件 | {cell_text}",
            "left_cell_text": "约束条件",
            "previous_non_empty_cell_text": "",
            "right_cell_text": "",
            "next_non_empty_cell_text": "",
            "nearby_text": cell_text,
            "caption_candidates": ["约束条件"],
        }
    )

    result = build_excellent_bid_material_library([("docx.json", index)])
    asset = result.image_assets[0]

    assert "大体积混凝土浇筑后温度分布梯度示意图" in asset.caption_actual
    assert "大体积混凝土浇筑后温度分布梯度示意图" in asset.semantic_text
    assert asset.caption_actual != "约束条件"
    assert asset.semantic_sources[0]["source_type"] == "embedded_same_cell_caption"


def _docx_index():
    return {
        "source_path": "DOCX优秀标书.docx",
        "heading_count": 1,
        "slice_count": 1,
        "table_count": 1,
        "table_image_ref_count": 1,
        "paragraph_image_ref_count": 0,
        "slices": [
            {
                "slice_id": "S1",
                "level": 2,
                "section_path": ["1 施工方案与技术措施", "1.1 成品保护措施"],
                "paragraph_count": 1,
                "paragraph_char_count": 20,
                "table_count": 1,
                "image_count": 1,
                "subtree_table_count": 1,
                "subtree_image_count": 1,
                "paragraphs": [
                    {
                        "paragraph_index": 1,
                        "block_index": 1,
                        "char_count": 20,
                        "text_preview": "成品保护标准化做法正文",
                    }
                ],
                "tables": [
                    {
                        "table_index": 1,
                        "block_index": 2,
                        "section_path": ["1 施工方案与技术措施", "1.1 成品保护措施"],
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 1,
                        "header_preview": ["分类", "内容"],
                        "row_previews": [
                            {
                                "row_index": 0,
                                "cells": [
                                    {"cell_index": 0, "text_preview": "分类", "image_count": 0},
                                    {"cell_index": 1, "text_preview": "内容", "image_count": 0},
                                ],
                            },
                            {
                                "row_index": 1,
                                "cells": [
                                    {"cell_index": 0, "text_preview": "1", "image_count": 0},
                                    {"cell_index": 1, "text_preview": "标准化防护示意图", "image_count": 1},
                                ],
                            },
                        ],
                    }
                ],
                "image_bindings": [
                    {
                        "rel_id": "rId1",
                        "target": "media/image1.png",
                        "part_name": "word/media/image1.png",
                        "context": "table_cell",
                        "block_index": 2,
                        "section_path": ["1 施工方案与技术措施", "1.1 成品保护措施"],
                        "table_index": 1,
                        "row_index": 1,
                        "cell_index": 1,
                        "cell_text": "标准化防护示意图",
                        "row_text": "1 | 标准化防护示意图",
                        "header_text": "分类 | 内容",
                        "previous_row_text": "上一行说明",
                        "next_row_text": "下一行说明",
                        "nearby_text": "标准化防护示意图；上一行说明；下一行说明",
                        "caption_candidates": ["标准化防护示意图", "上一行说明"],
                    }
                ],
            }
        ],
    }


def _fusion_index():
    return {
        "schema_version": "excellent_bid_fusion_index_v1",
        "source_pdf_path": "优秀标书.pdf",
        "source_docx_path": "优秀标书-转格式.docx",
        "fusion_slice_count": 2,
        "matched_count": 1,
        "unmatched_count": 1,
        "table_count": 2,
        "image_count": 1,
        "slices": [
            {
                "fusion_slice_id": "FUS-PDFS0001",
                "pdf_slice_id": "PDFS0001",
                "docx_slice_id": "S10",
                "match": {
                    "status": "matched",
                    "method": "number_and_title",
                    "score": 1.0,
                    "pdf_slice_id": "PDFS0001",
                    "docx_slice_id": "S10",
                },
                "level": 2,
                "title": "1.4 钢筋工程",
                "clean_title": "钢筋工程",
                "number": "1.4",
                "section_path": ["1. 施工方案与技术措施", "1.4 钢筋工程"],
                "start_page": 10,
                "end_page": 12,
                "page_count": 3,
                "paragraph_count": 10,
                "paragraph_char_count": 300,
                "docx_table_count": 2,
                "docx_image_count": 1,
                "pdf_table_like_count": 3,
                "pdf_image_count": 2,
                "confidence": 0.96,
                "paragraphs": [
                    {
                        "paragraph_index": 2,
                        "block_index": 3,
                        "char_count": 30,
                        "text_preview": "钢筋工程施工正文",
                    }
                ],
                "tables": [
                    {
                        "table_index": 2,
                        "block_index": 4,
                        "section_path": ["1. 施工方案与技术措施", "1.4 钢筋工程"],
                        "row_count": 3,
                        "max_column_count": 2,
                        "image_count": 0,
                        "header_preview": ["工序", "措施"],
                    },
                    {
                        "table_index": 3,
                        "block_index": 5,
                        "section_path": ["1. 施工方案与技术措施", "1.4 钢筋工程"],
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 1,
                        "header_preview": ["图片", "说明"],
                    },
                ],
                "image_bindings": [
                    {
                        "rel_id": "rId2",
                        "target": "media/image2.png",
                        "part_name": "word/media/image2.png",
                        "context": "table_cell",
                        "block_index": 5,
                        "section_path": ["1. 施工方案与技术措施", "1.4 钢筋工程"],
                        "table_index": 3,
                        "row_index": 1,
                        "cell_index": 1,
                    }
                ],
            },
            {
                "fusion_slice_id": "FUS-PDFS0002",
                "pdf_slice_id": "PDFS0002",
                "docx_slice_id": None,
                "match": {
                    "status": "unmatched",
                    "method": None,
                    "score": 0,
                    "pdf_slice_id": "PDFS0002",
                },
                "level": 1,
                "title": "附表一 机械设备表",
                "clean_title": "附表一 机械设备表",
                "section_path": ["附表一 机械设备表"],
                "start_page": 100,
                "end_page": 101,
                "page_count": 2,
                "paragraph_count": 3,
                "paragraph_char_count": 80,
                "docx_table_count": 0,
                "docx_image_count": 0,
                "pdf_table_like_count": 1,
                "pdf_image_count": 0,
                "confidence": 0.72,
                "paragraphs": [
                    {
                        "paragraph_index": 9,
                        "block_index": 9,
                        "char_count": 20,
                        "text_preview": "机械设备表",
                    }
                ],
                "pdf_tables": [
                    {
                        "table_id": "PDF-T1",
                        "table_index": 1,
                        "page_no": 100,
                        "row_count": 3,
                        "max_column_count": 4,
                    }
                ],
            },
        ],
    }


def _text_image_block(
    block_id: str,
    title: str,
    caption: str,
    primary_topic: str,
    topics: list[str],
) -> dict:
    return {
        "block_id": block_id,
        "block_type": "image_group_block",
        "source_id": "SRC0001",
        "material_slice_id": f"SRC0001-{block_id}",
        "title": title,
        "section_path": ["主要施工方案与技术措施", title],
        "topics": topics,
        "primary_topic": primary_topic,
        "secondary_topics": [],
        "topic_confidence": 0.9,
        "summary": caption,
        "captions": [caption],
        "image_count": 4,
        "image_group_count": 1,
        "table_count": 1,
        "reuse_level": "parameterized_reuse",
        "project_specific_risk": "medium",
        "use_policy": "whole_block_preferred",
        "render_policy": {"preserve_image_order": True, "preserve_image_groups": True},
    }


def _reuse_policy_docx_index():
    def slice_(slice_id, title, text="", header=None):
        return {
            "slice_id": slice_id,
            "level": 2,
            "section_path": ["施工组织设计", title],
            "paragraph_count": 1,
            "paragraph_char_count": len(text),
            "table_count": 1 if header else 0,
            "image_count": 0,
            "paragraphs": [
                {
                    "paragraph_index": 1,
                    "block_index": 1,
                    "char_count": len(text),
                    "text_preview": text,
                }
            ],
            "tables": [
                {
                    "table_index": 1,
                    "block_index": 2,
                    "section_path": ["施工组织设计", title],
                    "row_count": 2,
                    "max_column_count": 2,
                    "image_count": 0,
                    "header_preview": header or [],
                }
            ]
            if header
            else [],
            "image_bindings": [],
        }

    slices = [
        slice_("S1", "成品保护措施", "建立成品保护责任制，明确交接验收和巡查管理要求。"),
        slice_("S2", "环境保护措施", "落实环境保护、扬尘治理、噪声控制和绿色施工措施。"),
        slice_("S3", "钢筋工程施工方案", "钢筋加工、绑扎、连接、验收应结合工程结构部位和图纸参数实施。"),
        slice_("S4", "模板工程施工方案", "模板支撑体系、周转材料和拆模时间应结合层高与跨度确定。"),
        slice_("S5", "施工总平面布置图", "施工总平面图含办公区、生活区、材料堆场和临时道路布置。"),
        slice_("S6", "施工进度网络图", "计划开、竣工日期和施工进度网络图应按本工程工期编制。"),
        slice_("S7", "项目概况", "项目名称、建设地点、建设单位、建筑面积和结构形式如下。", ["项目", "内容"]),
        slice_("S8", "标准化防护现场照片", "现场照片展示标准化防护、临边洞口防护和安全文明优秀做法。"),
        slice_("S9", "现场踏勘现状照片", "现场踏勘现状照片用于反映本项目周边环境和场地现状。"),
    ]
    return {
        "source_path": "复用策略样本.docx",
        "heading_count": len(slices),
        "slice_count": len(slices),
        "table_count": 1,
        "table_image_ref_count": 0,
        "paragraph_image_ref_count": 0,
        "slices": slices,
    }
