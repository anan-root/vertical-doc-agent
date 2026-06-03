from xml.etree import ElementTree as ET

from construction_bidding_agent.document_parser.docx_table_index import index_tables_from_root


def test_index_tables_from_content_control_with_table_image():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:sdt>
          <w:sdtContent>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:t>序号</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>措施</w:t></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
                <w:tc>
                  <w:p><w:r><w:t>保护措施</w:t></w:r></w:p>
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

    result = index_tables_from_root(
        root,
        {"rId1": "media/image1.png"},
        source_path="sample.docx",
    )

    assert result.table_count == 1
    assert result.tables[0].row_count == 2
    assert result.tables[0].max_column_count == 2
    assert result.tables[0].image_count == 1
    assert result.table_image_ref_count == 1
    assert result.image_bindings[0].table_index == 0
    assert result.image_bindings[0].row_index == 1
    assert result.image_bindings[0].cell_index == 1
