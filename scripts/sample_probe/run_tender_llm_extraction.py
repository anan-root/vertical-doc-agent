"""对招标文件抽取输入包执行真实 LLM 抽取。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_llm_extractor import (
    DEFAULT_MODEL,
    run_tender_llm_extraction_from_file,
    write_tender_llm_extraction_outputs,
)


DEFAULT_INPUT = ROOT / "outputs" / "json" / "tender_extraction_inputs_pdf_luoyang.json"
DEFAULT_PROMPT_DIR = ROOT / "docs" / "prompts"


def main() -> int:
    parser = argparse.ArgumentParser(description="执行招标文件 LLM 抽取任务。")
    parser.add_argument("--input-json", default=str(DEFAULT_INPUT), help="抽取输入包 JSON 路径")
    parser.add_argument("--prompt-dir", default=str(DEFAULT_PROMPT_DIR), help="提示词 Markdown 目录路径")
    parser.add_argument(
        "--model",
        default=None,
        help=f"模型覆盖值。默认读取 .env 中的 MODEL，未配置时使用 {DEFAULT_MODEL}；API 和 base URL 来自 .env。",
    )
    parser.add_argument(
        "--task-key",
        action="append",
        default=None,
        help="只运行指定任务 key；可重复传入。",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["parallel", "serial"],
        default="parallel",
        help="以并行或串行方式运行选中的任务。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="并行执行的最大工作线程数。不传则读取 configs/llm-task-profiles.json 中对应抽取任务的 max_workers。",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(ROOT / "outputs" / "cache" / "tender_llm_tasks"),
        help="任务级 LLM 缓存目录。传空字符串可关闭缓存。",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="忽略已有任务缓存并重新调用模型。",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "tender_llm_extraction_result.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "tender_llm_extraction_report.md"),
        help="Markdown 报告输出路径",
    )
    args = parser.parse_args()

    result = run_tender_llm_extraction_from_file(
        args.input_json,
        prompt_dir=args.prompt_dir,
        model=args.model,
        task_keys=args.task_key,
        execution_mode=args.execution_mode,
        max_workers=args.max_workers,
        cache_dir=args.cache_dir or None,
        force_refresh=args.force_refresh,
    )
    write_tender_llm_extraction_outputs(result, args.json_output, args.report_output)

    print(f"JSON: {args.json_output}")
    print(f"Report: {args.report_output}")
    print(
        "Counts: "
        f"provider={result.provider}, "
        f"model={result.model}, "
        f"mode={result.execution_mode}, "
        f"duration={result.duration_seconds:.2f}s, "
        f"tasks={result.task_count}, "
        f"completed={result.completed_task_count}, "
        f"failed_or_skipped={result.failed_task_count}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    for task in result.tasks:
        print(
            f"- {task.task_key}: status={task.status}, "
            f"cache={task.cache_status}, "
            f"tokens={task.input_estimated_tokens}, "
            f"duration={task.duration_seconds:.2f}s, "
            f"error={task.error or ''}"
        )
    return 0 if result.failed_task_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
