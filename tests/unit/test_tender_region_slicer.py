from construction_bidding_agent.document_parser.models import (
    TenderDetectedSection,
    TenderDocumentBlock,
    TenderDocumentIndexResult,
    TenderDocumentProfile,
    TenderSectionCandidate,
    TenderSourceRef,
)
from construction_bidding_agent.document_parser.tender_region_slicer import build_tender_region_slices


def test_build_tender_region_slices_uses_boundaries_and_preface_end_markers():
    blocks = [
        TenderDocumentBlock(0, "paragraph", "第一章 招标公告", 7),
        TenderDocumentBlock(1, "paragraph", "项目概况内容", 6),
        TenderDocumentBlock(2, "paragraph", "第二章 投标人须知", 8),
        TenderDocumentBlock(3, "paragraph", "投标人须知前附表", 8),
        TenderDocumentBlock(4, "table", "工期 | 质量", 7, table_index=0),
        TenderDocumentBlock(5, "paragraph", "投标人须知正文部分", 9),
        TenderDocumentBlock(6, "paragraph", "第三章 评标办法", 7),
        TenderDocumentBlock(7, "paragraph", "评标办法前附表", 7),
        TenderDocumentBlock(8, "table", "技术评分标准 | 10分", 12, table_index=1),
        TenderDocumentBlock(9, "paragraph", "附件", 2),
        TenderDocumentBlock(10, "paragraph", "第八章 技术标准和要求", 10),
        TenderDocumentBlock(11, "paragraph", "110", 3),
        TenderDocumentBlock(12, "table", "第八章 | 技术标准和要求", 10, table_index=2),
        TenderDocumentBlock(13, "paragraph", "第八章 技术标准和要求", 10),
        TenderDocumentBlock(14, "paragraph", "第一节 一般要求", 7),
        TenderDocumentBlock(15, "paragraph", "质量标准内容", 6),
    ]
    document_index = TenderDocumentIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_001",
        file_name="sample.docx",
        file_type="docx",
        document_profile=TenderDocumentProfile(
            file_count=1,
            has_word=True,
            has_pdf=False,
            has_scanned_pdf=False,
            paragraph_count=9,
            table_count=2,
            image_count=0,
        ),
        detected_sections=[
            _section("chapter_1_notice", "招标公告", "core_region", 0),
            _section("chapter_2_bidder_instructions", "第二章 投标人须知", "boundary_section", 2),
            _section("bidder_instructions_preface_table", "投标人须知前附表", "core_region", 3),
            _section("chapter_3_evaluation", "第三章 评标办法", "boundary_section", 6),
            _section("evaluation_method_preface_table", "评标办法前附表", "core_region", 7),
            _section("technical_standards_and_requirements", "技术标准和要求", "core_region", 10),
        ],
        blocks=blocks,
    )

    result = build_tender_region_slices(document_index)
    by_key = {region_slice.region_key: region_slice for region_slice in result.slices}

    assert by_key["chapter_1_notice"].slice_start_block == 0
    assert by_key["chapter_1_notice"].slice_end_block == 1
    assert by_key["bidder_instructions_preface_table"].slice_start_block == 3
    assert by_key["bidder_instructions_preface_table"].slice_end_block == 4
    assert by_key["evaluation_method_preface_table"].slice_start_block == 7
    assert by_key["evaluation_method_preface_table"].slice_end_block == 8
    assert by_key["technical_standards_and_requirements"].slice_start_block == 10
    assert by_key["technical_standards_and_requirements"].slice_end_block == 15
    assert by_key["evaluation_method_preface_table"].recommended_llm_tasks == [
        "technical_score_points_extraction"
    ]


def _section(section_key: str, title: str, region_role: str, block_index: int) -> TenderDetectedSection:
    source_ref = TenderSourceRef(
        file_id="file_001",
        file_name="sample.docx",
        file_type="docx",
        block_index=block_index,
        text_excerpt=title,
    )
    candidate = TenderSectionCandidate(
        candidate_id=f"{section_key}_001",
        detected_title=title,
        source_type="canonical_chapter",
        source_refs=[source_ref],
        confidence=0.9,
    )
    return TenderDetectedSection(
        section_key=section_key,
        title=title,
        found=True,
        region_role=region_role,
        primary_candidate_id=candidate.candidate_id,
        detection_mode=candidate.source_type,
        confidence=candidate.confidence,
        candidates=[candidate],
        source_refs=[source_ref],
        matched_text=title,
    )
