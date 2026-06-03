"""执行技术标章节正文生成，输出结构化章节初稿。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import (  # noqa: E402
    run_chapter_generation_from_files,
    write_chapter_generation_outputs,
)


DEFAULT_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_with_materials.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="执行技术标章节正文生成。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_INPUTS), help="章节正文生成输入包 JSON 路径。")
    parser.add_argument("--prompt-path", help="可选：章节正文生成提示词路径。不传则使用内置提示词。")
    parser.add_argument("--model", default=None, help="模型覆盖值。默认读取 .env 中的 MODEL。")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="LLM 并发数。不传则读取 configs/llm-task-profiles.json 中 technical_bid_chapter_generation.max_workers。",
    )
    parser.add_argument("--max-packages", type=int, default=1, help="最多生成多少个章节包。默认 1，避免误跑全量。")
    parser.add_argument("--chapter-title-contains", help="仅生成章节路径中包含该关键词的输入包。")
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "chapter_generation_result.json"),
        help="章节正文生成结果 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "chapter_generation_report.md"),
        help="章节正文生成报告 Markdown 输出路径。",
    )
    args = parser.parse_args()

    result = run_chapter_generation_from_files(
        args.chapter_inputs,
        prompt_path=args.prompt_path,
        model=args.model,
        max_workers=args.max_workers,
        max_packages=args.max_packages,
        chapter_title_contains=args.chapter_title_contains,
    )
    write_chapter_generation_outputs(result, args.json_output, args.report_output)

    print(f"Result JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Counts: "
        f"tasks={result.task_count}, "
        f"completed={result.completed_count}, "
        f"skipped={result.skipped_count}, "
        f"failed={result.failed_count}, "
        f"duration={result.duration_seconds:.2f}s"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0 if result.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
