"""生成章节图片复用质量报告。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.image_reuse_quality import (  # noqa: E402
    build_chapter_image_reuse_quality_report_from_files,
    write_chapter_image_reuse_quality_report,
)


DEFAULT_GENERATION_RESULT = ROOT / "outputs" / "docx" / "chapter_draft_civil_sample_image_context_fix.json"
DEFAULT_CHAPTER_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_image_assets.json"
DEFAULT_JSON = ROOT / "outputs" / "json" / "chapter_image_reuse_quality_report.json"
DEFAULT_REPORT = ROOT / "outputs" / "reports" / "chapter_image_reuse_quality_report.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成章节图片复用质量报告。")
    parser.add_argument("--generation-result", default=str(DEFAULT_GENERATION_RESULT), help="章节生成结果 JSON 路径。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_CHAPTER_INPUTS), help="章节生成输入包 JSON 路径。")
    parser.add_argument("--json-out", default=str(DEFAULT_JSON), help="质量报告 JSON 输出路径。")
    parser.add_argument("--report-out", default=str(DEFAULT_REPORT), help="质量报告 Markdown 输出路径。")
    args = parser.parse_args()

    report = build_chapter_image_reuse_quality_report_from_files(args.generation_result, args.chapter_inputs)
    write_chapter_image_reuse_quality_report(report, args.json_out, args.report_out)
    summary = report.get("summary") or {}
    print(f"JSON: {Path(args.json_out).resolve()}")
    print(f"Report: {Path(args.report_out).resolve()}")
    print(
        "Counts: "
        f"chapters={report.get('chapter_count', 0)}, "
        f"sections={summary.get('section_count', 0)}, "
        f"images={summary.get('image_count', 0)}, "
        f"groups={summary.get('image_group_count', 0)}, "
        f"duplicates={summary.get('duplicate_image_count', 0)}, "
        f"split_groups={summary.get('split_group_count', 0)}, "
        f"high={summary.get('high_risk_count', 0)}, "
        f"medium={summary.get('medium_risk_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

