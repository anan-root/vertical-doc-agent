from construction_bidding_agent.document_parser.models import PdfPageMaterialSummary
from construction_bidding_agent.document_parser.pdf_bookmark_material_index import (
    _build_slices_from_page_summaries,
    render_pdf_bookmark_material_index_report,
)
from construction_bidding_agent.document_parser.pdf_bookmark_probe import _bookmark_items


class _Ref:
    def __init__(self, objid):
        self.objid = objid


def test_build_slices_from_page_summaries_aggregates_by_bookmark_page_range():
    bookmarks = _bookmark_items(
        [
            (1, "1. 施工方案与技术措施", [_Ref(10)], None, None),
            (2, "1.1 项目概况", [_Ref(10)], None, None),
            (2, "1.2 施工部署", [_Ref(30)], None, None),
        ],
        {10: 1, 30: 3},
        page_count=4,
    )
    pages = {
        1: PdfPageMaterialSummary(1, paragraph_count=2, text_char_count=100, table_like_count=1, image_count=0, text_preview="项目概况\n建设规模"),
        2: PdfPageMaterialSummary(2, paragraph_count=1, text_char_count=50, table_like_count=0, image_count=1, text_preview="编制依据"),
        3: PdfPageMaterialSummary(3, paragraph_count=1, text_char_count=80, table_like_count=2, image_count=0, text_preview="施工部署"),
        4: PdfPageMaterialSummary(4, paragraph_count=1, text_char_count=70, table_like_count=0, image_count=0, text_preview="资源安排"),
    }

    slices = _build_slices_from_page_summaries(
        bookmarks,
        pages,
        preview_paragraphs_per_slice=3,
        preview_paragraph_chars=20,
        preview_tables_per_slice=2,
        preview_images_per_slice=2,
    )

    assert len(slices) == 3
    assert slices[0].section_path == ["1. 施工方案与技术措施"]
    assert slices[0].start_page == 1
    assert slices[0].end_page == 4
    assert slices[0].paragraph_char_count == 300
    assert slices[0].table_like_count == 3
    assert slices[0].image_count == 1
    assert slices[0].descendant_slice_count == 2
    assert slices[1].end_page == 2
    assert slices[2].start_page == 3


def test_render_pdf_bookmark_material_index_report_lists_usage_guidance():
    from construction_bidding_agent.document_parser.models import PdfBookmarkMaterialIndexResult

    result = PdfBookmarkMaterialIndexResult(
        source_path="demo.pdf",
        page_count=4,
        bookmark_count=0,
        slice_count=0,
        text_page_count=4,
        material_paragraph_count=5,
        material_paragraph_char_count=300,
        table_like_count=3,
        image_count=1,
    )

    report = render_pdf_bookmark_material_index_report(result)

    assert "# PDF 优秀标书素材切片索引报告" in report
    assert "优先使用 L3/L4 低层级切片" in report
