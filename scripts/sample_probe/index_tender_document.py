"""为招标文件构建结构索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_document_index import (
    build_tender_document_index,
    write_tender_document_index_outputs,
)


DEFAULT_INPUT = ROOT / "data" / "raw" / "招标文件(周口市直幼儿园).docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建招标文件结构索引。")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="招标文件 .docx 或 .pdf 路径")
    parser.add_argument("--file-id", default=None, help="可选的稳定文件 ID")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "tender_document_index.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "tender_document_index.md"),
        help="Markdown 报告输出路径",
    )
    args = parser.parse_args()

    result = build_tender_document_index(args.input, file_id=args.file_id)
    write_tender_document_index_outputs(result, args.json_output, args.report_output)

    profile = result.document_profile
    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"type={result.file_type}, "
        f"paragraphs={profile.paragraph_count}, "
        f"tables={profile.table_count}, "
        f"images={profile.image_count}, "
        f"pages={profile.page_count}, "
        f"core_regions_found={sum(1 for section in result.detected_sections if section.region_role == 'core_region' and section.found)}, "
        f"boundary_sections_found={sum(1 for section in result.detected_sections if section.region_role == 'boundary_section' and section.found)}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
