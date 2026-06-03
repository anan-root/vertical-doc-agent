"""探测 PDF 优秀标书书签和正文可解析性。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.pdf_bookmark_probe import (  # noqa: E402
    build_pdf_bookmark_probe,
    write_pdf_bookmark_probe_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "投标文件" / "回民中学技术标9月21.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 PDF 优秀标书书签结构。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="PDF 投标文件路径。")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "pdf_bookmark_probe.json"),
        help="JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "pdf_bookmark_probe_report.md"),
        help="Markdown 报告输出路径。",
    )
    parser.add_argument("--sample-pages", type=int, default=30, help="页眉页脚和正文抽样页数。")
    args = parser.parse_args()

    result = build_pdf_bookmark_probe(args.input, sample_pages=args.sample_pages)
    write_pdf_bookmark_probe_outputs(result, args.json_output, args.report_output)
    print(f"JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Counts: "
        f"pages={result.page_count}, "
        f"bookmarks={result.bookmark_count}, "
        f"max_level={result.max_bookmark_level}, "
        f"mapped={result.mapped_bookmark_count}, "
        f"unmapped={result.unmapped_bookmark_count}, "
        f"text_pages={result.text_page_count}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
