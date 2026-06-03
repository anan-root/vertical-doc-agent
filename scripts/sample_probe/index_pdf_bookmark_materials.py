"""按 PDF 书签构建优秀标书素材切片索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.pdf_bookmark_material_index import (  # noqa: E402
    build_pdf_bookmark_material_index,
    write_pdf_bookmark_material_index_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "投标文件" / "回民中学技术标9月21.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description="按 PDF 书签构建优秀标书素材切片索引。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="PDF 投标文件路径。")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "pdf_bookmark_material_index.json"),
        help="JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "pdf_bookmark_material_index.md"),
        help="Markdown 报告输出路径。",
    )
    parser.add_argument("--preview-paragraphs-per-slice", type=int, default=5, help="每个切片保留的段落预览数。")
    parser.add_argument("--preview-paragraph-chars", type=int, default=260, help="段落预览字符数。")
    parser.add_argument("--preview-tables-per-slice", type=int, default=3, help="每个切片保留的表格预览数。")
    parser.add_argument("--preview-images-per-slice", type=int, default=5, help="每个切片保留的图片候选数。")
    parser.add_argument("--include-page-summaries", action="store_true", help="是否在 JSON 中保留页级素材摘要。")
    args = parser.parse_args()

    result = build_pdf_bookmark_material_index(
        args.input,
        preview_paragraphs_per_slice=args.preview_paragraphs_per_slice,
        preview_paragraph_chars=args.preview_paragraph_chars,
        preview_tables_per_slice=args.preview_tables_per_slice,
        preview_images_per_slice=args.preview_images_per_slice,
        include_page_summaries=args.include_page_summaries,
    )
    write_pdf_bookmark_material_index_outputs(result, args.json_output, args.report_output)
    print(f"JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Counts: "
        f"pages={result.page_count}, "
        f"bookmarks={result.bookmark_count}, "
        f"slices={result.slice_count}, "
        f"text_pages={result.text_page_count}, "
        f"paragraphs={result.material_paragraph_count}, "
        f"tables={result.table_like_count}, "
        f"images={result.image_count}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
