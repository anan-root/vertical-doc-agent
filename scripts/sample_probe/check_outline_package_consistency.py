"""检查技术标目录树与正文生成输入包的一致性。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.outline_package_consistency import (  # noqa: E402
    build_outline_package_consistency_from_files,
    write_outline_package_consistency_outputs,
)


DEFAULT_OUTLINE = ROOT / "outputs" / "json" / "batch_tender_01_outline_refined_parallel.json"
DEFAULT_CHAPTER_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_image_assets.json"
DEFAULT_JSON_OUT = ROOT / "outputs" / "json" / "batch_tender_01_outline_package_consistency.json"
DEFAULT_REPORT_OUT = ROOT / "outputs" / "reports" / "batch_tender_01_outline_package_consistency.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查技术标目录树与正文生成输入包的一致性。")
    parser.add_argument("--outline-json", default=str(DEFAULT_OUTLINE), help="技术标目录树 JSON 路径。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_CHAPTER_INPUTS), help="正文生成输入包 JSON 路径。")
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_OUT), help="一致性检查 JSON 输出路径。")
    parser.add_argument("--report-out", default=str(DEFAULT_REPORT_OUT), help="一致性检查 Markdown 报告输出路径。")
    args = parser.parse_args()

    result = build_outline_package_consistency_from_files(args.outline_json, args.chapter_inputs)
    write_outline_package_consistency_outputs(result, args.json_out, args.report_out)
    print(f"JSON: {Path(args.json_out).resolve()}")
    print(f"Report: {Path(args.report_out).resolve()}")
    print(json.dumps({key: result[key] for key in ["status", "outline_level1_count", "package_count", "issue_counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
