from construction_bidding_agent.document_parser.models import (
    TenderDocumentBlock,
    TenderRegionSlice,
    TenderRegionSliceIndexResult,
    TenderSourceRef,
)
from construction_bidding_agent.document_parser.tender_extraction_input_builder import (
    build_tender_extraction_inputs,
)


def test_build_tender_extraction_inputs_groups_regions_by_task_and_uses_full_text_content():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_001",
        file_name="sample.docx",
        file_type="docx",
        slice_count=4,
        slices=[
            _region_slice(
                "chapter_1_notice",
                "招标公告",
                0,
                [
                    TenderDocumentBlock(
                        0,
                        "paragraph",
                        "项目名称：幼儿园",
                        8,
                        text_content="项目名称：幼儿园",
                        paragraph_index=0,
                    )
                ],
            ),
            _region_slice(
                "bidder_instructions_preface_table",
                "投标人须知前附表",
                1,
                [
                    TenderDocumentBlock(
                        1,
                        "table",
                        "计划工期 | 质量要求",
                        20,
                        text_content="计划工期 | 180日历天\n质量要求 | 合格\n安全文明要求 | 达标",
                        table_index=0,
                        row_count=3,
                        max_column_count=2,
                    )
                ],
            ),
            _region_slice(
                "evaluation_method_preface_table",
                "评标办法前附表",
                2,
                [
                    TenderDocumentBlock(
                        2,
                        "table",
                        "施工组织设计 | 20分",
                        12,
                        text_content="施工组织设计 | 20分 | 内容完整、针对性强",
                        table_index=1,
                        row_count=1,
                        max_column_count=3,
                    )
                ],
            ),
            _region_slice(
                "technical_standards_and_requirements",
                "技术标准和要求",
                3,
                [
                    TenderDocumentBlock(
                        3,
                        "paragraph",
                        "执行国家现行规范",
                        8,
                        text_content="执行国家现行规范",
                        paragraph_index=1,
                    )
                ],
            ),
        ],
    )

    result = build_tender_extraction_inputs(slice_result)
    packages = {package.task_key: package for package in result.packages}

    assert result.input_profile == "full"
    assert result.package_count == 3
    assert packages["project_info_extraction_input"].region_keys == [
        "chapter_1_notice",
        "bidder_instructions_preface_table",
    ]
    assert "安全文明要求 | 达标" in packages["project_info_extraction_input"].input_text
    assert packages["score_points_extraction_input"].region_keys == [
        "evaluation_method_preface_table"
    ]
    assert "施工组织设计 | 20分 | 内容完整、针对性强" in packages[
        "score_points_extraction_input"
    ].input_text
    assert packages["technical_requirements_extraction_input"].region_keys == [
        "bidder_instructions_preface_table",
        "technical_standards_and_requirements",
    ]
    assert packages["technical_requirements_extraction_input"].block_count == 2
    assert len(packages["technical_requirements_extraction_input"].cell_refs) == 6
    assert packages["technical_requirements_extraction_input"].block_refs[0].row_count == 3
    assert packages["technical_requirements_extraction_input"].estimated_tokens > 0


def test_build_tender_extraction_inputs_warns_when_required_region_is_missing():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_001",
        file_name="sample.docx",
        file_type="docx",
        slice_count=1,
        slices=[
            _region_slice(
                "chapter_1_notice",
                "招标公告",
                0,
                [
                    TenderDocumentBlock(
                        0,
                        "paragraph",
                        "项目名称：幼儿园",
                        8,
                        text_content="项目名称：幼儿园",
                    )
                ],
            )
        ],
    )

    result = build_tender_extraction_inputs(slice_result, token_warning_threshold=1)
    packages = {package.task_key: package for package in result.packages}

    assert "Missing required region: bidder_instructions_preface_table" in packages[
        "project_info_extraction_input"
    ].warnings
    assert "Missing required region: evaluation_method_preface_table" in packages[
        "score_points_extraction_input"
    ].warnings
    assert any("exceeds warning threshold" in warning for warning in result.warnings)


def test_build_tender_extraction_inputs_dedupes_pdf_paragraphs_covered_by_tables_and_merges_units():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.pdf",
        file_id="file_pdf",
        file_name="sample.pdf",
        file_type="pdf",
        slice_count=2,
        slices=[
            _region_slice(
                "chapter_1_notice",
                "招标公告",
                0,
                [
                    TenderDocumentBlock(
                        0,
                        "paragraph",
                        "项目名称 洛阳项目",
                        9,
                        text_content="项目名称 洛阳项目",
                        page_no=1,
                        paragraph_index=0,
                    ),
                    TenderDocumentBlock(
                        1,
                        "paragraph",
                        "建设地点 新安县",
                        8,
                        text_content="建设地点 新安县",
                        page_no=1,
                        paragraph_index=1,
                    ),
                    TenderDocumentBlock(
                        2,
                        "table",
                        "项目名称 | 洛阳项目",
                        12,
                        text_content="项目名称 | 洛阳项目",
                        page_no=1,
                        table_index=0,
                        row_count=1,
                        max_column_count=2,
                    ),
                ],
            ),
            _region_slice(
                "bidder_instructions_preface_table",
                "投标人须知前附表",
                3,
                [
                    TenderDocumentBlock(
                        3,
                        "paragraph",
                        "工期要求 180日历天",
                        12,
                        text_content="工期要求 180日历天",
                        page_no=2,
                        paragraph_index=2,
                    ),
                    TenderDocumentBlock(
                        4,
                        "paragraph",
                        "质量要求 合格",
                        7,
                        text_content="质量要求 合格",
                        page_no=2,
                        paragraph_index=3,
                    ),
                ],
            ),
        ],
    )

    result = build_tender_extraction_inputs(slice_result)
    package = {
        package.task_key: package
        for package in result.packages
    }["project_info_extraction_input"]

    assert package.block_count == 5
    assert package.included_block_count == 4
    assert package.dropped_duplicate_block_count == 1
    assert package.input_unit_count == 3
    assert "项目名称 洛阳项目\n" not in package.input_text
    assert "项目名称 | 洛阳项目" in package.input_text
    assert "工期要求 180日历天\n质量要求 合格" in package.input_text


def test_build_tender_extraction_inputs_adds_structured_cell_refs_for_tables():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.pdf",
        file_id="file_pdf",
        file_name="sample.pdf",
        file_type="pdf",
        slice_count=1,
        slices=[
            _region_slice(
                "evaluation_method_preface_table",
                "评标办法前附表",
                10,
                [
                    TenderDocumentBlock(
                        10,
                        "table",
                        "最低分 | 最高分 | 评分点名称\n0.0 | 1.5 | 质量管理体系与措施",
                        41,
                        text_content="最低分 | 最高分 | 评分点名称\n0.0 | 1.5 | 质量管理体系与措施",
                        page_no=3,
                        table_index=5,
                        row_count=2,
                        max_column_count=3,
                    )
                ],
            )
        ],
    )

    result = build_tender_extraction_inputs(slice_result)
    package = {
        package.task_key: package
        for package in result.packages
    }["score_points_extraction_input"]

    assert "STRUCTURED_TABLE_CELLS" in package.input_text
    assert "[B10_R1_C2] 质量管理体系与措施" in package.input_text
    assert package.cell_refs[0].cell_id == "B10_R0_C0"
    assert package.cell_refs[-1].text_raw == "质量管理体系与措施"


def test_build_tender_extraction_inputs_adds_cell_refs_for_project_info_tables():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_docx",
        file_name="sample.docx",
        file_type="docx",
        slice_count=1,
        slices=[
            _region_slice(
                "bidder_instructions_preface_table",
                "投标人须知前附表",
                20,
                [
                    TenderDocumentBlock(
                        20,
                        "table",
                        "建设地点 | 固始县",
                        8,
                        text_content="建设地点 | 固始县\n计划工期 | 365日历天",
                        table_index=2,
                        row_count=2,
                        max_column_count=2,
                    )
                ],
            )
        ],
    )

    result = build_tender_extraction_inputs(slice_result)
    package = {
        package.task_key: package
        for package in result.packages
    }["project_info_extraction_input"]

    assert "STRUCTURED_TABLE_CELLS" in package.input_text
    assert "[B20_R0_C1] 固始县" in package.input_text
    assert package.cell_refs[-1].cell_id == "B20_R1_C1"


def test_build_tender_extraction_inputs_balanced_profile_slimes_non_score_packages_only():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_docx",
        file_name="sample.docx",
        file_type="docx",
        slice_count=3,
        slices=[
            _region_slice(
                "chapter_1_notice",
                "招标公告",
                1,
                [
                    TenderDocumentBlock(
                        1,
                        "paragraph",
                        "投标保证金要求",
                        6,
                        text_content="投标保证金要求：人民币伍万元",
                    ),
                    TenderDocumentBlock(
                        2,
                        "paragraph",
                        "项目名称：幼儿园项目",
                        10,
                        text_content="项目名称：幼儿园项目",
                    ),
                ],
            ),
            _region_slice(
                "bidder_instructions_preface_table",
                "投标人须知前附表",
                10,
                [
                    TenderDocumentBlock(
                        10,
                        "table",
                        "投标保证金 | 50000",
                        80,
                        text_content=(
                            "投标保证金 | 50000\n"
                            "建设地点 | 固始县\n"
                            "报价方式 | 固定总价\n"
                            "质量要求 | 合格"
                        ),
                        table_index=1,
                        row_count=4,
                        max_column_count=2,
                    )
                ],
            ),
            _region_slice(
                "evaluation_method_preface_table",
                "评标办法前附表",
                20,
                [
                    TenderDocumentBlock(
                        20,
                        "table",
                        "施工组织设计 | 20分 | 内容完整",
                        18,
                        text_content="施工组织设计 | 20分 | 内容完整",
                        table_index=2,
                        row_count=1,
                        max_column_count=3,
                    )
                ],
            ),
        ],
    )

    full = build_tender_extraction_inputs(slice_result, input_profile="full")
    balanced = build_tender_extraction_inputs(slice_result, input_profile="balanced")
    full_packages = {package.task_key: package for package in full.packages}
    balanced_packages = {package.task_key: package for package in balanced.packages}

    assert balanced.input_profile == "balanced"
    assert balanced_packages["project_info_extraction_input"].text_char_count < full_packages[
        "project_info_extraction_input"
    ].text_char_count
    assert "投标保证金 | 50000" not in balanced_packages["project_info_extraction_input"].input_text
    assert "建设地点 | 固始县" in balanced_packages["project_info_extraction_input"].input_text
    assert [cell.cell_id for cell in balanced_packages["project_info_extraction_input"].cell_refs] == [
        "B10_R1_C0",
        "B10_R1_C1",
        "B10_R3_C0",
        "B10_R3_C1",
    ]
    assert balanced_packages["score_points_extraction_input"].input_text == full_packages[
        "score_points_extraction_input"
    ].input_text
    assert balanced_packages["score_points_extraction_input"].cell_refs == full_packages[
        "score_points_extraction_input"
    ].cell_refs


def test_build_tender_extraction_inputs_rejects_unknown_input_profile():
    slice_result = TenderRegionSliceIndexResult(
        schema_version="test",
        source_path="sample.docx",
        file_id="file_001",
        file_name="sample.docx",
        file_type="docx",
        slice_count=0,
        slices=[],
    )

    try:
        build_tender_extraction_inputs(slice_result, input_profile="tiny")
    except ValueError as exc:
        assert "Unsupported input_profile" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported input_profile")


def _region_slice(
    region_key: str,
    title: str,
    start_block: int,
    blocks: list[TenderDocumentBlock],
) -> TenderRegionSlice:
    return TenderRegionSlice(
        region_key=region_key,
        region_title=title,
        region_role="core_region",
        primary_candidate_id=f"{region_key}_001",
        source_type="canonical_chapter",
        source_refs=[
            TenderSourceRef(
                file_id="file_001",
                file_name="sample.docx",
                file_type="docx",
                block_index=start_block,
                text_excerpt=title,
            )
        ],
        slice_start_block=start_block,
        slice_end_block=start_block + len(blocks) - 1,
        block_count=len(blocks),
        paragraph_count=sum(1 for block in blocks if block.block_type == "paragraph"),
        table_count=sum(1 for block in blocks if block.block_type == "table"),
        text_char_count=sum(block.char_count for block in blocks),
        blocks=blocks,
    )
