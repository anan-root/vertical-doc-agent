"""为 DOCX 文件构建轻量章节素材切片索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.docx_section_material_index import (
    build_docx_section_material_index,
    write_section_material_index_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "总体施工方案.docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 DOCX 章节素材索引。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=".docx 文件路径")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "docx_section_material_index.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "docx_section_material_index.md"),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--preview-paragraphs-per-slice",
        type=int,
        default=5,
        help="每个章节切片保留的段落预览数",
    )
    parser.add_argument(
        "--preview-paragraph-chars",
        type=int,
        default=220,
        help="每个段落预览保留的字符数",
    )
    parser.add_argument(
        "--preview-rows-per-table",
        type=int,
        default=3,
        help="每个表格保留的文本预览行数",
    )
    parser.add_argument(
        "--preview-text-chars",
        type=int,
        default=80,
        help="每个预览表格单元格保留的字符数",
    )
    args = parser.parse_args()

    result = build_docx_section_material_index(
        args.input,
        preview_paragraphs_per_slice=args.preview_paragraphs_per_slice,
        preview_paragraph_chars=args.preview_paragraph_chars,
        preview_rows_per_table=args.preview_rows_per_table,
        preview_text_chars=args.preview_text_chars,
    )
    write_section_material_index_outputs(result, args.json_output, args.report_output)

    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"headings={result.heading_count}, "
        f"slices={result.slice_count}, "
        f"paragraphs={result.material_paragraph_count}, "
        f"tables={result.table_count}, "
        f"document_images={result.document_image_ref_count}, "
        f"table_images={result.table_image_ref_count}, "
        f"paragraph_images={result.paragraph_image_ref_count}, "
        f"header_footer_texts={result.header_footer_text_count}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
