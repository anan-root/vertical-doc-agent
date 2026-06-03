from xml.etree import ElementTree as ET

from construction_bidding_agent.document_parser.docx_section_material_index import (
    index_section_materials_from_root,
)


def test_index_section_materials_skips_toc_and_collects_direct_materials():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="TOC1"/></w:pPr>
          <w:r><w:t>1.施工方案1</w:t></w:r>
        </w:p>
        <w:p><w:pPr><w:pStyle w:val="11"/></w:pPr><w:r><w:t>施工方案</w:t></w:r></w:p>
        <w:p><w:r><w:t>本章介绍施工总体安排。</w:t></w:r></w:p>
        <w:p>
          <w:r><w:t>带图段落</w:t></w:r>
          <w:r><w:drawing><a:blip r:embed="rIdP"/></w:drawing></w:r>
        </w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>序号</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>措施</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
            <w:tc>
              <w:p><w:r><w:t>保护措施</w:t></w:r></w:p>
              <w:p><w:r><w:drawing><a:blip r:embed="rIdT"/></w:drawing></w:r></w:p>
            </w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    result = index_section_materials_from_root(
        root,
        {"rIdP": "media/paragraph.png", "rIdT": "media/table.png"},
        source_path="sample.docx",
    )

    assert result.heading_count == 1
    assert result.slice_count == 1
    assert result.material_paragraph_count == 2
    assert result.table_count == 1
    assert result.document_image_ref_count == 2
    assert result.paragraph_image_ref_count == 1
    assert result.table_image_ref_count == 1
    assert result.slices[0].section_path == ["施工方案"]
    assert result.slices[0].paragraph_count == 2
    assert result.slices[0].table_count == 1
    assert result.slices[0].image_count == 2
    assert result.slices[0].paragraphs[0].text_preview == "本章介绍施工总体安排。"
    assert result.slices[0].tables[0].header_preview == ["序号", "措施"]
    assert {binding.context for binding in result.slices[0].image_bindings} == {
        "paragraph",
        "table_cell",
    }


def test_index_section_materials_populates_subtree_counts():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="11"/></w:pPr><w:r><w:t>施工方案</w:t></w:r></w:p>
        <w:p><w:r><w:t>一级正文。</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="22"/></w:pPr><w:r><w:t>项目概况</w:t></w:r></w:p>
        <w:p><w:r><w:t>二级正文。</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>分类</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    result = index_section_materials_from_root(root, {}, source_path="sample.docx")

    assert result.slice_count == 2
    assert result.slices[0].paragraph_count == 1
    assert result.slices[0].table_count == 0
    assert result.slices[0].subtree_paragraph_count == 2
    assert result.slices[0].subtree_table_count == 1
    assert result.slices[0].descendant_slice_count == 1
    assert result.slices[1].section_path == ["施工方案", "项目概况"]
    assert result.slices[1].paragraph_count == 1
    assert result.slices[1].table_count == 1
