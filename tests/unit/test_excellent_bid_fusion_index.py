from construction_bidding_agent.document_parser.excellent_bid_fusion_index import (
    build_excellent_bid_fusion_index,
    render_excellent_bid_fusion_index_report,
)


def test_fusion_matches_docx_slice_by_number_and_title():
    result = build_excellent_bid_fusion_index(_pdf_index(), _docx_index())

    first = result.slices[0]
    assert result.fusion_slice_count == 2
    assert result.matched_count == 2
    assert result.unmatched_count == 0
    assert first.match.status == "matched"
    assert first.match.method == "number_and_title"
    assert first.docx_slice_id == "S1"
    assert first.docx_table_count == 1
    assert first.docx_image_count == 1
    assert first.tables[0].header_preview == ["分类", "概况内容"]
    assert first.image_bindings[0].part_name == "word/media/image1.png"


def test_fusion_falls_back_to_pdf_material_when_unmatched():
    pdf = _pdf_index()
    pdf["slices"][0]["title"] = "9.9 不存在章节"
    pdf["slices"][0]["clean_title"] = "不存在章节"
    pdf["slices"][0]["number"] = "9.9"
    pdf["slices"][0]["section_path"] = ["9. 不存在章节组", "9.9 不存在章节"]
    result = build_excellent_bid_fusion_index(pdf, _docx_index())

    first = result.slices[0]
    assert first.match.status == "unmatched"
    assert first.docx_slice_id is None
    assert first.pdf_table_like_count == 2
    assert first.pdf_image_count == 1
    assert first.pdf_tables[0].table_id == "PDF-T00001"


def test_fusion_uses_parent_subtree_material_when_exact_docx_heading_is_missing():
    pdf = _pdf_index()
    pdf["slices"][0]["title"] = "1.1.9 DOCX缺失子章节"
    pdf["slices"][0]["clean_title"] = "DOCX缺失子章节"
    pdf["slices"][0]["number"] = "1.1.9"
    pdf["slices"][0]["section_path"] = [
        "1. 施工方案与技术措施",
        "1.1 项目概况",
        "1.1.9 DOCX缺失子章节",
    ]

    result = build_excellent_bid_fusion_index(pdf, _docx_index())

    first = result.slices[0]
    assert result.fallback_count == 1
    assert first.match.status == "fallback"
    assert first.match.method == "parent_subtree"
    assert first.docx_slice_id == "FALLBACK:S1..S1"
    assert first.docx_table_count == 1
    assert first.tables[0].header_preview == ["分类", "概况内容"]


def test_fusion_report_summarizes_sources_and_counts():
    result = build_excellent_bid_fusion_index(_pdf_index(), _docx_index())

    report = render_excellent_bid_fusion_index_report(result)

    assert "# 优秀标书 PDF+DOCX 融合素材索引报告" in report
    assert "结构来源：pdf_bookmark" in report
    assert "表格、表内图片和行级样例优先使用已匹配的 DOCX 素材" in report


def _pdf_index():
    return {
        "source_path": "demo.pdf",
        "boundary_precision": "page_level",
        "slices": [
            {
                "slice_id": "PDFS0001",
                "bookmark_index": 1,
                "level": 3,
                "title": "1.1.1 项目位置、规模及承包范围",
                "clean_title": "项目位置、规模及承包范围",
                "number": "1.1.1",
                "section_path": ["1. 施工方案与技术措施", "1.1 项目概况", "1.1.1 项目位置、规模及承包范围"],
                "start_page": 1,
                "end_page": 1,
                "page_count": 1,
                "paragraph_count": 2,
                "paragraph_char_count": 120,
                "table_like_count": 2,
                "image_count": 1,
                "confidence": 0.94,
                "paragraphs": [{"paragraph_index": 1, "block_index": 1, "style": None, "char_count": 20, "text_preview": "PDF 段落"}],
                "tables": [{"table_id": "PDF-T00001", "table_index": 1, "page_no": 1, "row_count": 2, "max_column_count": 2}],
                "image_bindings": [{"image_id": "PDFIMG-001", "page_no": 1, "image_index": 1}],
            },
            {
                "slice_id": "PDFS0002",
                "bookmark_index": 2,
                "level": 3,
                "title": "1.1.2 工程地理位置",
                "clean_title": "工程地理位置",
                "number": "1.1.2",
                "section_path": ["1. 施工方案与技术措施", "1.1 项目概况", "1.1.2 工程地理位置"],
                "start_page": 2,
                "end_page": 2,
                "page_count": 1,
                "paragraph_count": 1,
                "paragraph_char_count": 80,
                "table_like_count": 1,
                "image_count": 0,
            },
        ],
    }


def _docx_index():
    return {
        "source_path": "demo.docx",
        "slices": [
            {
                "slice_id": "S1",
                "level": 3,
                "section_path": ["1 施工方案与技术措施", "1.1 项目概况", "1.1.1 项目位置、规模及承包范围"],
                "paragraph_count": 1,
                "paragraph_char_count": 20,
                "table_count": 1,
                "image_count": 1,
                "subtree_table_count": 1,
                "subtree_image_count": 1,
                "paragraphs": [{"paragraph_index": 3, "block_index": 3, "style": None, "char_count": 20, "text_preview": "DOCX 段落"}],
                "tables": [
                    {
                        "table_index": 1,
                        "block_index": 5,
                        "section_path": ["1 施工方案与技术措施", "1.1 项目概况", "1.1.1 项目位置、规模及承包范围"],
                        "section_level": 3,
                        "nearest_heading_index": 2,
                        "nearest_heading_text": "1.1.1 项目位置、规模及承包范围",
                        "row_count": 2,
                        "max_column_count": 2,
                        "image_count": 1,
                        "header_preview": ["分类", "概况内容"],
                        "row_previews": [],
                    }
                ],
                "image_bindings": [
                    {
                        "rel_id": "rId1",
                        "target": "media/image1.png",
                        "part_name": "word/media/image1.png",
                        "context": "table_cell",
                        "block_index": 5,
                        "section_path": ["1 施工方案与技术措施", "1.1 项目概况", "1.1.1 项目位置、规模及承包范围"],
                        "table_index": 1,
                        "row_index": 1,
                        "cell_index": 1,
                    }
                ],
            },
            {
                "slice_id": "S2",
                "level": 3,
                "section_path": ["1 施工方案与技术措施", "1.1 项目概况", "1.1.2 工程地理位置"],
                "table_count": 1,
                "image_count": 0,
                "subtree_table_count": 1,
                "subtree_image_count": 0,
            },
        ],
    }
