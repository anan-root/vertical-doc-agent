from construction_bidding_agent.document_parser.pdf_bookmark_probe import (
    _bookmark_items,
    render_pdf_bookmark_probe_report,
)
from construction_bidding_agent.document_parser.models import PdfBookmarkProbeResult


class _Ref:
    def __init__(self, objid):
        self.objid = objid


def test_bookmark_items_build_paths_and_page_ranges():
    raw = [
        (1, "1. 施工方案与技术措施", [_Ref(10)], None, None),
        (2, "1.1 项目概况", [_Ref(10)], None, None),
        (3, "1.1.1 编制依据", [_Ref(20)], None, None),
        (2, "1.2 施工部署", [_Ref(30)], None, None),
    ]

    items = _bookmark_items(raw, {10: 1, 20: 5, 30: 8}, page_count=10)

    assert len(items) == 4
    assert items[0].level == 1
    assert items[0].number == "1"
    assert items[0].clean_title == "施工方案与技术措施"
    assert items[0].start_page == 1
    assert items[0].end_page == 10
    assert items[1].parent_index == 0
    assert items[1].path == ["1. 施工方案与技术措施", "1.1 项目概况"]
    assert items[1].end_page == 7
    assert items[2].end_page == 7
    assert items[3].end_page == 10


def test_render_pdf_bookmark_probe_report_recommends_import_when_bookmarks_are_mapped():
    items = _bookmark_items(
        [(1, "1. 施工方案与技术措施", [_Ref(10)], None, None)],
        {10: 1},
        page_count=3,
    )
    result = PdfBookmarkProbeResult(
        source_path="demo.pdf",
        page_count=3,
        bookmark_count=1,
        max_bookmark_level=1,
        mapped_bookmark_count=1,
        unmapped_bookmark_count=0,
        text_page_count=3,
        scanned_like=False,
        bookmarks=items,
        level_counts={1: 1},
    )

    report = render_pdf_bookmark_probe_report(result)

    assert "建议入库：是" in report
    assert "结构来源：PDF 书签" in report
    assert "1. 施工方案与技术措施" in report
