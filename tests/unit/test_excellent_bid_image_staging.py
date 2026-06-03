from construction_bidding_agent.document_parser.excellent_bid_image_staging import (
    build_excellent_bid_image_staging,
    render_excellent_bid_image_staging_report,
)


def test_staging_detects_exact_duplicates_and_candidate_reuse():
    existing = _library(
        library_id="existing",
        source_path="existing.docx",
        assets=[
            _asset("OLD-IMG1", "word/media/image1.png", "钢筋加工示意图", confidence=0.92),
        ],
    )
    staging = _library(
        library_id="staging",
        source_path="new.docx",
        assets=[
            _asset("NEW-IMG1", "word/media/imageA.png", "钢筋加工示意图", confidence=0.92),
            _asset("NEW-IMG2", "word/media/imageB.png", "模板支撑体系示意图", confidence=0.88),
        ],
    )
    bytes_by_part = {
        "existing.docx|word/media/image1.png": b"same-image",
        "new.docx|word/media/imageA.png": b"same-image",
        "new.docx|word/media/imageB.png": b"different-image",
    }

    result = build_excellent_bid_image_staging(
        staging,
        existing_library=existing,
        root_dir=".",
        _read_docx_part_override=lambda docx, part: bytes_by_part.get(f"{docx}|{part}"),
        _image_meta_override=lambda _: {"perceptual_hash": "", "image_width": 100, "image_height": 80, "image_format": "PNG"},
    )

    decisions = {image["image_asset_id"]: image["decision"] for image in result["images"]}
    assert decisions["NEW-IMG1"] == "duplicate_existing"
    assert decisions["NEW-IMG2"] == "candidate_reuse"
    assert result["summary"]["exact_duplicate_existing_count"] == 1
    assert result["summary"]["decision_counts"]["candidate_reuse"] == 1


def test_staging_marks_project_specific_and_missing_group_candidates():
    staging = _library(
        library_id="staging",
        source_path="new.docx",
        assets=[
            _asset(
                "IMG-SITE",
                "word/media/site.png",
                "施工总平面布置图",
                confidence=0.9,
                risk="high",
            ),
            _asset("IMG-FLOW1", "word/media/flow1.png", "钢筋加工示意图", row=1),
            _asset("IMG-FLOW2", "word/media/flow2.png", "钢筋弯曲示意图", row=2),
        ],
    )

    result = build_excellent_bid_image_staging(
        staging,
        root_dir=".",
        _read_docx_part_override=lambda docx, part: f"{docx}|{part}".encode(),
        _image_meta_override=lambda _: {"perceptual_hash": "", "image_width": 100, "image_height": 80, "image_format": "PNG"},
    )

    decisions = {image["image_asset_id"]: image["decision"] for image in result["images"]}
    assert decisions["IMG-SITE"] == "project_specific_manual_review"
    assert result["summary"]["missing_group_candidate_count"] == 1
    assert result["missing_group_candidates"][0]["member_count"] == 2

    report = render_excellent_bid_image_staging_report(result)
    assert "# 优秀标书图片 staging 诊断报告" in report
    assert "疑似漏识别套图" in report
    assert "施工总平面布置图" in report


def _library(*, library_id, source_path, assets):
    return {
        "library_id": library_id,
        "source_count": 1,
        "slice_count": 1,
        "sources": [
            {
                "source_id": "SRC0001",
                "source_name": source_path,
                "source_type": "docx_only",
                "source_paths": [source_path],
            }
        ],
        "image_assets": assets,
        "image_groups": [],
    }


def _asset(asset_id, part_name, caption, *, confidence=0.9, risk="low", row=1):
    return {
        "image_asset_id": asset_id,
        "image_id": asset_id,
        "source_id": "SRC0001",
        "source_type": "docx_only",
        "source_slice_id": "S1",
        "material_slice_id": "SRC0001-M00001",
        "title": "钢筋工程施工方案",
        "section_path": ["施工方案", "钢筋工程施工方案"],
        "part_name": part_name,
        "context": "table_cell",
        "table_index": 1,
        "row_index": row,
        "cell_index": 1,
        "caption_actual": caption,
        "semantic_text": caption,
        "semantic_confidence": confidence,
        "nearby_text": caption,
        "reuse_level": "candidate_reuse",
        "project_specific_risk": risk,
        "review_required": False,
        "review_reason": "",
    }
