"""基于 LLM 抽取输出构建招标文件解析结果 JSON 和 Markdown 报告。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_parse_report import (
    build_tender_parse_result_from_files,
    write_tender_parse_report_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建招标文件解析报告。")
    parser.add_argument("--input-json", required=True, help="招标文件抽取输入包 JSON")
    parser.add_argument("--project-technical-json", required=True, help="项目基础信息与技术要求 LLM 抽取 JSON")
    parser.add_argument("--score-json", required=True, help="评分点 LLM 抽取 JSON")
    parser.add_argument("--json-output", required=True, help="招标文件解析结果 JSON 输出路径")
    parser.add_argument("--report-output", required=True, help="招标文件解析 Markdown 报告输出路径")
    args = parser.parse_args()

    result = build_tender_parse_result_from_files(
        args.input_json,
        args.project_technical_json,
        args.score_json,
    )
    write_tender_parse_report_outputs(result, args.json_output, args.report_output)
    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"score_points={len(result.get('technical_score_points') or [])}, "
        f"requirements={len(result.get('technical_bid_requirements') or [])}, "
        f"standards={len(result.get('technical_standards') or [])}, "
        f"review_items={len(result.get('review_items') or [])}, "
        f"warnings={len(result.get('warnings') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
