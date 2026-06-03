from xml.etree import ElementTree as ET

from construction_bidding_agent.document_parser.docx_section_table_index import (
    detect_heading,
    index_sections_tables_from_root,
)


def test_detect_heading_skips_toc_style_and_reads_numbered_body_heading():
    assert detect_heading("1.1 项目概况1", "TOC2") is None

    heading = detect_heading("1.1 项目概况", None)

    assert heading == (2, "1.1", "1.1 项目概况")


def test_detect_heading_reads_repeated_digit_heading_style():
    heading = detect_heading("项目概况", "22")

    assert heading == (2, None, "项目概况")


def test_detect_heading_ignores_long_numeric_custom_styles():
    assert detect_heading("根据本工程测量工作任务，配备满足现场需求的专业测量工程师。", "717") is None
    assert detect_heading("施工机械选型", "1251") is None


def test_index_sections_tables_assigns_tables_to_nearest_heading_path():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="TOC1"/></w:pPr>
          <w:r><w:t>1.针对本项目施工管理提出总体施工方案1</w:t></w:r>
        </w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>未归属</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
        <w:p><w:pPr><w:pStyle w:val="11"/></w:pPr><w:r><w:t>针对本项目施工管理提出总体施工方案</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="22"/></w:pPr><w:r><w:t>项目概况</w:t></w:r></w:p>
        <w:sdt>
          <w:sdtContent>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:t>分类</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>概况内容</w:t></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>建设地点</w:t></w:r></w:p></w:tc>
                <w:tc>
                  <w:p><w:r><w:t>某地</w:t></w:r></w:p>
                  <w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>
                </w:tc>
              </w:tr>
            </w:tbl>
          </w:sdtContent>
        </w:sdt>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    result = index_sections_tables_from_root(
        root,
        {"rId1": "media/image1.png"},
        source_path="sample.docx",
    )

    assert result.heading_count == 2
    assert result.table_count == 2
    assert result.unassigned_table_count == 1
    assert result.tables[0].section_path == []
    assert result.tables[1].section_path == [
        "针对本项目施工管理提出总体施工方案",
        "项目概况",
    ]
    assert result.tables[1].nearest_heading_text == "项目概况"
    assert result.tables[1].image_count == 1
    assert result.image_bindings[0].table_index == 1
    assert result.sections[0].table_count == 1


def test_numbered_local_heading_nests_under_current_styled_heading():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="33"/></w:pPr><w:r><w:t>施工部署原则及施工区段划分</w:t></w:r></w:p>
        <w:p><w:r><w:t>1 施工部署原则</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>内容</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    result = index_sections_tables_from_root(root, {}, source_path="sample.docx")

    assert result.headings[0].level == 3
    assert result.headings[1].level == 4
    assert result.tables[0].section_path == [
        "施工部署原则及施工区段划分",
        "1 施工部署原则",
    ]


def test_image_binding_prefers_same_column_caption_row():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="11"/></w:pPr><w:r><w:t>钢筋工程</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:drawing><a:blip r:embed="rIdLeft"/></w:drawing></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:drawing><a:blip r:embed="rIdRight"/></w:drawing></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>方柱钢筋定位框</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>底板马凳筋</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    result = index_sections_tables_from_root(
        root,
        {"rIdLeft": "media/left.png", "rIdRight": "media/right.png"},
        source_path="sample.docx",
    )

    left, right = result.image_bindings
    assert left.below_cell_text == "方柱钢筋定位框"
    assert right.below_cell_text == "底板马凳筋"
    assert left.caption_candidates[0] == "方柱钢筋定位框"
    assert right.caption_candidates[0] == "底板马凳筋"
