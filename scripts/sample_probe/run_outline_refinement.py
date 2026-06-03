"""执行 LLM 二三级目录补强，并输出补强后的目录树。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.outline_generator import (  # noqa: E402
    run_outline_refinement_from_files,
    write_outline_refinement_outputs,
)


DEFAULT_OUTLINE = ROOT / "outputs" / "json" / "technical_bid_outline.json"
DEFAULT_INPUTS = ROOT / "outputs" / "json" / "outline_refinement_inputs.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="执行 LLM 二三级目录补强。")
    parser.add_argument("--outline-json", default=str(DEFAULT_OUTLINE), help="规则版技术标目录树 JSON 路径。")
    parser.add_argument("--refinement-inputs", default=str(DEFAULT_INPUTS), help="补强输入包 JSON 路径。")
    parser.add_argument("--prompt-path", help="可选：目录补强提示词路径。不传则使用内置生产提示词。")
    parser.add_argument("--model", default=None, help="模型覆盖值。默认读取 .env 中的 MODEL。")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="LLM 补强并发数。不传则读取 configs/llm-task-profiles.json 中 outline_refinement.max_workers。",
    )
    parser.add_argument(
        "--target-node-id",
        action="append",
        default=None,
        help="只补强指定一级目录节点；可重复传入，用于失败节点局部重试。",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(ROOT / "outputs" / "cache" / "outline_refinement_tasks"),
        help="目录补强任务级缓存目录。传空字符串可关闭缓存。",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="忽略已有目录补强缓存并重新调用模型。",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "outline_refinement_result.json"),
        help="补强运行结果 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "outline_refinement_report.md"),
        help="补强运行报告 Markdown 输出路径。",
    )
    parser.add_argument(
        "--outline-output",
        default=str(ROOT / "outputs" / "json" / "technical_bid_outline_refined.json"),
        help="补强后的目录树 JSON 输出路径。",
    )
    parser.add_argument(
        "--outline-report-output",
        default=str(ROOT / "outputs" / "reports" / "technical_bid_outline_refined_report.md"),
        help="补强后的目录树报告 Markdown 输出路径。",
    )
    args = parser.parse_args()

    result = run_outline_refinement_from_files(
        args.outline_json,
        args.refinement_inputs,
        prompt_path=args.prompt_path,
        model=args.model,
        max_workers=args.max_workers,
        cache_dir=args.cache_dir or None,
        force_refresh=args.force_refresh,
        target_node_ids=args.target_node_id,
    )
    write_outline_refinement_outputs(
        result,
        args.json_output,
        args.report_output,
        outline_json_path=args.outline_output,
        outline_report_path=args.outline_report_output,
    )

    print(f"Result JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(f"Refined outline JSON: {Path(args.outline_output).resolve()}")
    print(f"Refined outline report: {Path(args.outline_report_output).resolve()}")
    print(
        "Counts: "
        f"tasks={result.task_count}, "
        f"applied={result.applied_count}, "
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
