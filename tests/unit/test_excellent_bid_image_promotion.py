from construction_bidding_agent.document_parser.excellent_bid_image_promotion import (
    build_excellent_bid_image_promotion_package,
    render_excellent_bid_image_promotion_report,
)


def test_promotion_keeps_candidate_groups_together_and_skips_group_members():
    staging = {
        "schema_version": "excellent_bid_image_staging_v1",
        "staging_library_id": "staging",
        "summary": {"missing_group_candidate_count": 0},
        "images": [
            _image("IMG1", decision="candidate_reuse", group_id="G1", member_index=1),
            _image("IMG2", decision="candidate_reuse", group_id="G1", member_index=2),
            _image("IMG3", decision="candidate_reuse"),
            _image("IMG4", decision="duplicate_existing"),
        ],
        "image_groups": [
            {
                "image_group_id": "G1",
                "group_title": "钢筋加工示意图",
                "semantic_text": "钢筋加工示意图",
                "semantic_confidence": 0.9,
                "section_path": ["钢筋工程"],
                "member_count": 2,
                "image_asset_ids": ["IMG1", "IMG2"],
                "captions": ["钢筋调直", "钢筋切断"],
                "tags": ["钢筋"],
                "project_specific_risk": "low",
                "decision": "candidate_reuse_group",
                "must_keep_together": True,
            }
        ],
    }

    result = build_excellent_bid_image_promotion_package(staging)

    assert result["summary"]["promote_group_count"] == 1
    assert result["summary"]["promote_single_image_count"] == 1
    assert result["summary"]["promote_image_count"] == 3
    assert result["promote_groups"][0]["must_keep_together"] is True
    assert [member["image_asset_id"] for member in result["promote_groups"][0]["members"]] == ["IMG1", "IMG2"]
    assert result["promote_images"][0]["image_asset_id"] == "IMG3"
    assert any(item["reason_type"] == "duplicate_existing" for item in result["skipped_items"])


def test_promotion_separates_review_items_from_candidates():
    staging = {
        "schema_version": "excellent_bid_image_staging_v1",
        "staging_library_id": "staging",
        "summary": {"missing_group_candidate_count": 2},
        "images": [
            _image("IMG1", decision="project_specific_manual_review"),
            _image("IMG2", decision="suspected_duplicate_existing"),
            _image("IMG3", decision="manual_review"),
        ],
        "image_groups": [
            {
                "image_group_id": "G1",
                "group_title": "疑似重复套图",
                "section_path": ["模板工程"],
                "member_count": 2,
                "image_asset_ids": ["IMG2", "IMG3"],
                "decision": "suspected_duplicate_group",
            }
        ],
    }

    result = build_excellent_bid_image_promotion_package(staging)
    report = render_excellent_bid_image_promotion_report(result)

    assert result["summary"]["promote_image_count"] == 0
    assert result["summary"]["review_item_count"] == 4
    assert any(item["reason_type"] == "project_specific_manual_review" for item in result["review_items"])
    assert any(item["reason_type"] == "suspected_duplicate_group" for item in result["review_items"])
    assert "优秀标书图片候选入库包" in report
    assert "staging 中仍有 2 处疑似漏识别套图" in "；".join(result["warnings"])


def test_promotion_sends_low_quality_section_path_to_review():
    image = _image("IMG1", decision="candidate_reuse")
    image["section_path"] = [
        "2 救援路线：离项目最近的医院是郑州市第九人民医院，为大型公立二甲医院，基本满足发生突发事件时紧急医疗救助。"
    ]
    staging = {
        "schema_version": "excellent_bid_image_staging_v1",
        "staging_library_id": "staging",
        "summary": {"missing_group_candidate_count": 0},
        "images": [image],
        "image_groups": [],
    }

    result = build_excellent_bid_image_promotion_package(staging)

    assert result["summary"]["promote_image_count"] == 0
    assert result["review_items"][0]["reason_type"] == "section_path_quality_risk"


def _image(asset_id, *, decision, group_id=None, member_index=None):
    return {
        "image_asset_id": asset_id,
        "image_id": asset_id,
        "source_id": "SRC0001",
        "material_slice_id": "SRC0001-M00001",
        "title": "钢筋工程",
        "section_path": ["钢筋工程"],
        "part_name": f"word/media/{asset_id}.png",
        "caption_actual": asset_id,
        "semantic_text": asset_id,
        "semantic_confidence": 0.9,
        "nearby_text": asset_id,
        "tags": ["钢筋"],
        "sha256": asset_id,
        "perceptual_hash": asset_id,
        "source_docx_path": "sample.docx",
        "image_group_id": group_id,
        "group_member_index": member_index,
        "project_specific_risk": "low",
        "decision": decision,
        "decision_reasons": [decision],
    }
