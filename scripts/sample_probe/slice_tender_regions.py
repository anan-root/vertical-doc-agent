"""为招标文件构建核心抽取区域切片。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_region_slicer import (
    build_tender_region_slices_from_path,
    write_tender_region_slice_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "招标文件(周口市直幼儿园).docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建招标文件核心区域切片。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="招标文件 .docx 或 .pdf 路径")
    parser.add_argument("--file-id", default=None, help="可选的稳定文件 ID")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "tender_region_slices.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "tender_region_slices.md"),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--max-blocks-per-slice",
        type=int,
        default=None,
        help="每个切片的可选块数安全上限",
    )
    args = parser.parse_args()

    result = build_tender_region_slices_from_path(
        args.input,
        file_id=args.file_id,
        max_blocks_per_slice=args.max_blocks_per_slice,
    )
    write_tender_region_slice_outputs(result, args.json_output, args.report_output)

    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"type={result.file_type}, "
        f"slices={result.slice_count}, "
        f"blocks={sum(region_slice.block_count for region_slice in result.slices)}, "
        f"paragraphs={sum(region_slice.paragraph_count for region_slice in result.slices)}, "
        f"tables={sum(region_slice.table_count for region_slice in result.slices)}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
