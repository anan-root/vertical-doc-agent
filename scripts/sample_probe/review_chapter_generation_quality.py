"""生成章节正文质量评审报告。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import (  # noqa: E402
    build_chapter_generation_quality_review_from_files,
    write_chapter_generation_quality_review,
)


DEFAULT_RESULT = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_result_typical_4.json"
DEFAULT_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_with_materials.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成章节正文质量评审报告。")
    parser.add_argument("--generation-result", default=str(DEFAULT_RESULT), help="章节生成结果 JSON 路径。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_INPUTS), help="章节生成输入包 JSON 路径。")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "chapter_generation_quality_review.json"),
        help="质量评审 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "chapter_generation_quality_review.md"),
        help="质量评审 Markdown 报告输出路径。",
    )
    args = parser.parse_args()

    review = build_chapter_generation_quality_review_from_files(args.generation_result, args.chapter_inputs)
    write_chapter_generation_quality_review(review, args.json_output, args.report_output)

    summary = review.get("summary") or {}
    print(f"Review JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Summary: "
        f"chapters={review.get('chapter_count')}, "
        f"average_score={summary.get('average_score')}, "
        f"ready_for_full_generation={summary.get('ready_for_full_generation')}, "
        f"high_issues={summary.get('high_priority_issue_count')}, "
        f"medium_issues={summary.get('medium_priority_issue_count')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
