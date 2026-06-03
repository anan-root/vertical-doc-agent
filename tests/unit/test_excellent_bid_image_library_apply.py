from construction_bidding_agent.document_parser.excellent_bid_image_library_apply import (
    apply_excellent_bid_image_promotion,
    render_excellent_bid_image_library_apply_report,
)


def test_apply_promotion_adds_group_members_as_searchable_slice():
    library = _library()
    promotion = {
        "schema_version": "excellent_bid_image_promotion_v1",
        "staging_library_id": "staging_01",
        "summary": {"review_item_count": 2},
        "promote_groups": [
            {
                "promotion_id": "PG0001",
                "source_group_id": "OLD-G1",
                "group_title": "钢筋加工示意图",
                "semantic_text": "钢筋加工示意图",
                "semantic_confidence": 0.91,
                "section_path": ["钢筋工程", "钢筋加工"],
                "member_count": 2,
                "image_asset_ids": ["OLD-IMG1", "OLD-IMG2"],
                "captions": ["钢筋调直", "钢筋切断"],
                "tags": ["钢筋"],
                "members": [
                    _promoted_image("OLD-IMG1", sha="sha-1", caption="钢筋调直"),
                    _promoted_image("OLD-IMG2", sha="sha-2", caption="钢筋切断"),
                ],
            }
        ],
        "promote_images": [],
        "review_items": [{"item_id": "RI1"}],
        "skipped_items": [{"item_id": "SK1"}],
    }

    result = apply_excellent_bid_image_promotion(library, promotion, output_library_id="preview")
    preview = result["library"]
    report = render_excellent_bid_image_library_apply_report(result)

    assert result["summary"]["promoted_image_asset_count"] == 2
    assert result["summary"]["promoted_image_group_count"] == 1
    assert result["summary"]["promoted_slice_count"] == 1
    assert preview["source_count"] == 2
    assert preview["image_asset_count"] == 3
    assert preview["image_group_count"] == 1
    group = preview["image_groups"][0]
    assert group["must_keep_together"] is True
    assert group["member_count"] == 2
    assert len(group["image_asset_ids"]) == 2
    material_slice = preview["slices"][-1]
    assert material_slice["source_type"] == "docx_image_promotion"
    assert material_slice["image_count"] == 2
    assert "钢筋加工" in material_slice["search_text"]
    assert "复核项" in "；".join(result["warnings"])
    assert "优秀标书图片正式素材库预览报告" in report


def test_apply_promotion_skips_duplicate_sha_and_keeps_review_out():
    library = _library()
    promotion = {
        "schema_version": "excellent_bid_image_promotion_v1",
        "staging_library_id": "staging_01",
        "summary": {"review_item_count": 1},
        "promote_groups": [],
        "promote_images": [
            _promoted_image("OLD-DUP", sha="existing-sha", caption="重复图片"),
            _promoted_image("OLD-NEW", sha="new-sha", caption="模板支设"),
        ],
        "review_items": [_promoted_image("OLD-REVIEW", sha="review-sha", caption="复核图片")],
        "skipped_items": [],
    }

    result = apply_excellent_bid_image_promotion(library, promotion, output_library_id="preview")
    preview = result["library"]

    assert result["summary"]["promoted_image_asset_count"] == 1
    assert result["summary"]["skipped_item_count"] == 1
    assert result["skipped_items"][0]["reason_type"] == "duplicate_sha256"
    assert preview["image_asset_count"] == 2
    original_ids = {asset.get("original_image_asset_id") for asset in preview["image_assets"]}
    assert "OLD-NEW" in original_ids
    assert "OLD-REVIEW" not in original_ids


def test_apply_promotion_splits_sources_by_docx_path():
    library = _library()
    promotion = {
        "schema_version": "excellent_bid_image_promotion_v1",
        "staging_library_id": "staging_01",
        "summary": {},
        "promote_groups": [],
        "promote_images": [
            _promoted_image("IMG-A", sha="sha-a", caption="测量控制", source_docx_path=r"data\raw\a.docx"),
            _promoted_image("IMG-B", sha="sha-b", caption="模板支设", source_docx_path=r"data\raw\b.docx"),
        ],
    }

    result = apply_excellent_bid_image_promotion(library, promotion, output_library_id="preview")

    assert result["summary"]["promoted_source_count"] == 2
    assert result["summary"]["promoted_slice_count"] == 2
    source_paths = [source["source_paths"][0] for source in result["applied_sources"]]
    assert r"data\raw\a.docx" in source_paths
    assert r"data\raw\b.docx" in source_paths


def _library():
    return {
        "schema_version": "excellent_bid_material_library_v1",
        "library_id": "base",
        "source_count": 1,
        "slice_count": 1,
        "table_count": 0,
        "image_count": 1,
        "docx_table_count": 0,
        "docx_image_count": 1,
        "image_asset_count": 1,
        "image_group_count": 0,
        "sources": [
            {
                "source_id": "SRC0001",
                "source_name": "base",
                "source_type": "docx_only",
                "source_index_path": "base.json",
                "source_paths": [r"data\raw\base.docx"],
                "slice_count": 1,
                "table_count": 0,
                "image_count": 1,
            }
        ],
        "slices": [
            {
                "material_slice_id": "SRC0001-M00001",
                "source_id": "SRC0001",
                "source_type": "docx_only",
                "source_slice_id": "S1",
                "title": "基础",
                "section_path": ["基础"],
                "section_key": ":基础",
                "search_text": "基础",
                "material_quality": "usable",
                "primary_material_source": "docx",
                "table_count": 0,
                "image_count": 1,
                "docx_table_count": 0,
                "docx_image_count": 1,
                "pdf_table_like_count": 0,
                "pdf_image_count": 0,
                "reuse_level": "direct_reuse",
                "project_specific_risk": "low",
                "paragraphs": [],
                "tables": [],
                "image_bindings": [],
            }
        ],
        "image_assets": [
            {
                "image_asset_id": "SRC0001-IMG000001",
                "image_id": "SRC0001-IMG000001",
                "source_id": "SRC0001",
                "source_type": "docx_only",
                "material_slice_id": "SRC0001-M00001",
                "section_path": ["基础"],
                "part_name": "word/media/existing.png",
                "caption_actual": "已有图片",
                "semantic_text": "已有图片",
                "reuse_level": "candidate_reuse",
                "project_specific_risk": "low",
                "sha256": "existing-sha",
            }
        ],
        "image_groups": [],
        "warnings": [],
    }


def _promoted_image(asset_id, *, sha, caption, source_docx_path=r"data\raw\new.docx"):
    return {
        "image_asset_id": asset_id,
        "image_id": asset_id,
        "source_id": "STAGING",
        "material_slice_id": "STAGING-M1",
        "title": caption,
        "section_path": ["钢筋工程", "钢筋加工"] if "钢筋" in caption else ["模板工程", "模板支设"],
        "part_name": f"word/media/{asset_id}.png",
        "caption_actual": caption,
        "semantic_text": caption,
        "semantic_confidence": 0.9,
        "semantic_sources": [{"source_type": "same_cell_caption", "text": caption, "confidence": 0.9}],
        "nearby_text": caption,
        "tags": ["钢筋"] if "钢筋" in caption else ["模板"],
        "sha256": sha,
        "perceptual_hash": sha,
        "image_width": 640,
        "image_height": 480,
        "image_format": "PNG",
        "source_docx_path": source_docx_path,
        "table_index": 1,
        "row_index": 1,
        "cell_index": 1,
        "reuse_level": "candidate_reuse",
        "project_specific_risk": "low",
    }
