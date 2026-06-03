"""生成或对比招标文件解析质量回归基准。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_parse_quality import (
    build_quality_baseline,
    compare_quality_to_baseline,
    render_quality_comparison_report,
    write_quality_json,
)


DEFAULT_BASELINE = ROOT / "tests" / "fixtures" / "tender_parse_quality_baseline.json"
DEFAULT_REPORT = ROOT / "outputs" / "reports" / "tender_parse_quality_comparison.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成或对比招标文件解析质量基准。")
    parser.add_argument(
        "--parse-result",
        action="append",
        required=True,
        help="招标文件 parse_result JSON 路径；可重复传入。",
    )
    parser.add_argument(
        "--baseline-output",
        default=str(DEFAULT_BASELINE),
        help="基准 JSON 输出路径。",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="将当前解析结果与 baseline-output 指向的基准做对比。",
    )
    parser.add_argument(
        "--comparison-json-output",
        default=str(ROOT / "outputs" / "json" / "tender_parse_quality_comparison.json"),
        help="质量对比 JSON 输出路径。",
    )
    parser.add_argument(
        "--comparison-report-output",
        default=str(DEFAULT_REPORT),
        help="质量对比 Markdown 报告输出路径。",
    )
    args = parser.parse_args()

    parse_results = [_read_json(path) for path in args.parse_result]
    if args.compare:
        baseline = _read_json(args.baseline_output)
        comparison = compare_quality_to_baseline(baseline, parse_results)
        write_quality_json(comparison, args.comparison_json_output)
        report_target = Path(args.comparison_report_output)
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(render_quality_comparison_report(comparison), encoding="utf-8")
        print(f"Comparison JSON: {args.comparison_json_output}")
        print(f"Comparison report: {args.comparison_report_output}")
        print(f"Status: {comparison['status']}, failed={comparison['failed_count']}")
        return 0 if comparison["status"] == "passed" else 1

    baseline = build_quality_baseline(parse_results)
    write_quality_json(baseline, args.baseline_output)
    print(f"Baseline JSON: {args.baseline_output}")
    print(f"Samples: {baseline['sample_count']}")
    return 0


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
