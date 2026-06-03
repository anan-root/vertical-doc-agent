"""检查 DOCX 文件结构并写出 JSON/Markdown 探测结果。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.docx_probe import probe_docx, write_probe_outputs


DEFAULT_INPUT = Path(r"C:\Users\13321\Desktop\投标文件\目录1.docx")


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 DOCX 结构。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=".docx 文件路径")
    parser.add_argument("--max-paragraphs", type=int, default=None, help="最多抽取的段落数")
    parser.add_argument("--max-tables", type=int, default=None, help="最多抽取的表格数")
    parser.add_argument(
        "--max-rows-per-table",
        type=int,
        default=None,
        help="每个表格最多抽取的行数",
    )
    parser.add_argument(
        "--include-images",
        choices=["true", "false"],
        default="true",
        help="是否抽取图片引用",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="对大型文档使用安全预览上限",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "docx_structure_probe.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "docx_structure_probe.md"),
        help="Markdown 报告输出路径",
    )
    args = parser.parse_args()

    max_paragraphs = args.max_paragraphs
    max_tables = args.max_tables
    max_rows_per_table = args.max_rows_per_table
    if args.preview:
        max_paragraphs = max_paragraphs if max_paragraphs is not None else 300
        max_tables = max_tables if max_tables is not None else 30
        max_rows_per_table = max_rows_per_table if max_rows_per_table is not None else 20

    result = probe_docx(
        args.input,
        max_paragraphs=max_paragraphs,
        max_tables=max_tables,
        max_rows_per_table=max_rows_per_table,
        include_images=args.include_images == "true",
    )
    write_probe_outputs(result, args.json_output, args.report_output)
    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"paragraphs={result.paragraph_count}, "
        f"tables={result.table_count}, "
        f"images={result.image_count}, "
        f"header_footer_texts={len(result.header_footer_texts)}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
