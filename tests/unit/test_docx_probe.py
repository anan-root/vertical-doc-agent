from xml.etree import ElementTree as ET

from construction_bidding_agent.document_parser.docx_probe import _extract_body_paragraphs


def test_extract_body_paragraphs_reads_content_controls():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:sdt>
          <w:sdtContent>
            <w:p>
              <w:r><w:t>目录标题</w:t></w:r>
            </w:p>
          </w:sdtContent>
        </w:sdt>
      </w:body>
    </w:document>
    """
    root = ET.fromstring(xml)

    paragraphs = _extract_body_paragraphs(root)

    assert len(paragraphs) == 1
    assert paragraphs[0].text == "目录标题"
