"""为招标文件构建分任务抽取输入包。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_extraction_input_builder import (
    DEFAULT_TOKEN_WARNING_THRESHOLD,
    build_tender_extraction_inputs_from_path,
    write_tender_extraction_input_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "tender.docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建招标文件抽取输入包。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="招标文件 .docx 或 .pdf 路径")
    parser.add_argument("--file-id", default=None, help="可选的稳定文件 ID")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "tender_extraction_inputs.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "tender_extraction_inputs.md"),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--max-blocks-per-slice",
        type=int,
        default=None,
        help="每个来源区域切片的可选块数上限",
    )
    parser.add_argument(
        "--token-warning-threshold",
        type=int,
        default=DEFAULT_TOKEN_WARNING_THRESHOLD,
        help="输入包估算 token 数超过该值时给出警告",
    )
    parser.add_argument(
        "--input-profile",
        choices=["full", "balanced"],
        default="full",
        help="输入瘦身方案。full 保留完整任务文本；balanced 瘦身非评分点输入包。",
    )
    args = parser.parse_args()

    result = build_tender_extraction_inputs_from_path(
        args.input,
        file_id=args.file_id,
        max_blocks_per_slice=args.max_blocks_per_slice,
        token_warning_threshold=args.token_warning_threshold,
        input_profile=args.input_profile,
    )
    write_tender_extraction_input_outputs(result, args.json_output, args.report_output)

    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"type={result.file_type}, "
        f"profile={result.input_profile}, "
        f"packages={result.package_count}, "
        f"blocks={sum(package.block_count for package in result.packages)}, "
        f"chars={sum(package.text_char_count for package in result.packages)}, "
        f"estimated_tokens={sum(package.estimated_tokens for package in result.packages)}"
    )
    for package in result.packages:
        print(
            f"- {package.task_key}: "
            f"regions={','.join(package.region_keys)}, "
            f"blocks={package.block_count}, "
            f"chars={package.text_char_count}, "
            f"estimated_tokens={package.estimated_tokens}, "
            f"warnings={len(package.warnings)}"
        )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
