"""融合 PDF 书签索引和转格式 DOCX 素材索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.excellent_bid_fusion_index import (  # noqa: E402
    build_excellent_bid_fusion_index_from_files,
    write_excellent_bid_fusion_index_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建优秀标书 PDF+DOCX 融合素材索引。")
    parser.add_argument("--pdf-index-json", required=True, help="PDF 书签素材切片索引 JSON。")
    parser.add_argument("--docx-index-json", required=True, help="转格式 DOCX 章节素材索引 JSON。")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "excellent_bid_fusion_index.json"),
        help="融合索引 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "excellent_bid_fusion_index.md"),
        help="融合索引 Markdown 报告输出路径。",
    )
    parser.add_argument("--min-match-score", type=float, default=0.72, help="最低匹配分数。")
    args = parser.parse_args()

    result = build_excellent_bid_fusion_index_from_files(
        args.pdf_index_json,
        args.docx_index_json,
        min_match_score=args.min_match_score,
    )
    write_excellent_bid_fusion_index_outputs(result, args.json_output, args.report_output)
    print(f"JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Counts: "
        f"pdf_slices={result.pdf_slice_count}, "
        f"docx_slices={result.docx_slice_count}, "
        f"fusion_slices={result.fusion_slice_count}, "
        f"matched={result.matched_count}, "
        f"ambiguous={result.ambiguous_count}, "
        f"unmatched={result.unmatched_count}, "
        f"tables={result.table_count}, "
        f"images={result.image_count}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
