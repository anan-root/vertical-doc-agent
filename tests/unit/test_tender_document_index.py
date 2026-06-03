from xml.etree import ElementTree as ET

from construction_bidding_agent.document_parser.models import TenderDocumentBlock
from construction_bidding_agent.document_parser.tender_document_index import (
    _summarize_docx_table,
    detect_key_sections,
)


def test_detect_key_sections_finds_preface_tables_and_technical_standards():
    blocks = [
        TenderDocumentBlock(0, "paragraph", "第一章 招标公告", 7),
        TenderDocumentBlock(1, "paragraph", "第二章 投标人须知", 8),
        TenderDocumentBlock(2, "table", "投标人须知前附表 内容", 10, table_index=0),
        TenderDocumentBlock(3, "paragraph", "第三章 评标办法", 7),
        TenderDocumentBlock(4, "table", "评标办法前附表 技术标评分", 12, table_index=1),
        TenderDocumentBlock(5, "paragraph", "第五章 技术标准和要求", 10),
    ]

    sections = detect_key_sections(
        blocks,
        file_id="file_001",
        file_name="招标文件.docx",
        file_type="docx",
    )
    by_key = {section.section_key: section for section in sections}

    assert by_key["chapter_1_notice"].found is True
    assert by_key["chapter_1_notice"].region_role == "core_region"
    assert by_key["chapter_2_bidder_instructions"].region_role == "boundary_section"
    assert by_key["bidder_instructions_preface_table"].source_refs[0].table_index == 0
    assert by_key["bidder_instructions_preface_table"].region_role == "core_region"
    assert by_key["chapter_3_evaluation"].region_role == "boundary_section"
    assert by_key["evaluation_method_preface_table"].source_refs[0].table_index == 1
    assert by_key["evaluation_method_preface_table"].region_role == "core_region"
    assert by_key["technical_standards_and_requirements"].found is True
    assert by_key["technical_standards_and_requirements"].region_role == "core_region"
    assert by_key["technical_standards_and_requirements"].candidates[0].source_type == "canonical_chapter"


def test_detect_key_sections_prefers_body_heading_over_toc_entry():
    blocks = [
        TenderDocumentBlock(0, "paragraph", "第三章 评标办法37", 10, paragraph_index=0),
        TenderDocumentBlock(1, "paragraph", "使用说明中提到第三章评标办法", 14, paragraph_index=1),
        TenderDocumentBlock(2, "paragraph", "第三章 评标办法", 7, paragraph_index=2),
    ]

    sections = detect_key_sections(
        blocks,
        file_id="file_001",
        file_name="招标文件.docx",
        file_type="docx",
    )
    by_key = {section.section_key: section for section in sections}

    assert by_key["chapter_3_evaluation"].source_refs[0].paragraph_index == 2


def test_detect_key_sections_prefers_technical_standards_chapter_heading():
    blocks = [
        TenderDocumentBlock(0, "paragraph", "（7）技术标准和要求；", 10, paragraph_index=0),
        TenderDocumentBlock(1, "paragraph", "第八章技术标准和要求", 10, paragraph_index=1),
    ]

    sections = detect_key_sections(
        blocks,
        file_id="file_001",
        file_name="招标文件.pdf",
        file_type="pdf",
    )
    by_key = {section.section_key: section for section in sections}

    assert by_key["technical_standards_and_requirements"].source_refs[0].paragraph_index == 1
    assert by_key["technical_standards_and_requirements"].primary_candidate_id == "TSR001"
    assert by_key["technical_standards_and_requirements"].candidates[0].source_type == "canonical_chapter"


def test_detect_key_sections_uses_owner_requirement_as_technical_candidate():
    blocks = [
        TenderDocumentBlock(0, "paragraph", "发包人要求", 5, paragraph_index=0),
        TenderDocumentBlock(1, "paragraph", "质量标准：达到国家现行验收规范合格标准。", 20, paragraph_index=1),
    ]

    sections = detect_key_sections(
        blocks,
        file_id="file_001",
        file_name="招标文件.docx",
        file_type="docx",
    )
    by_key = {section.section_key: section for section in sections}
    technical = by_key["technical_standards_and_requirements"]

    assert technical.found is True
    assert technical.detection_mode == "owner_requirement"
    assert technical.source_refs[0].paragraph_index == 0
    assert technical.candidates[0].source_type == "owner_requirement"


def test_summarize_docx_table_returns_preview_and_shape():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:tbl
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:tr>
        <w:tc><w:p><w:r><w:t>条款号</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>条款名称</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>2.2.4</w:t></w:r></w:p></w:tc>
        <w:tc>
          <w:p><w:r><w:t>技术评分标准</w:t></w:r></w:p>
          <w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>
        </w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>2.2.5</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>这一行不应丢失</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    """
    table_node = ET.fromstring(xml)

    block, image_count = _summarize_docx_table(table_node, block_index=3, table_index=1)

    assert block.block_type == "table"
    assert block.table_index == 1
    assert block.row_count == 3
    assert block.max_column_count == 2
    assert "技术评分标准" in block.text_preview
    assert "这一行不应丢失" in block.text_content
    assert block.char_count == len(block.text_content)
    assert image_count == 1
