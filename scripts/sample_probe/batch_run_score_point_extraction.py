"""批量对已生成的招标文件输入包执行评分点 LLM 抽取。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_llm_extractor import (
    run_tender_llm_extraction_from_file,
    write_tender_llm_extraction_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量执行评分点 LLM 抽取。")
    parser.add_argument(
        "--summary-json",
        default=str(ROOT / "outputs" / "json" / "batch_tender_extraction_inputs_summary.json"),
    )
    parser.add_argument("--prompt-dir", default=str(ROOT / "docs" / "prompts"))
    parser.add_argument("--json-dir", default=str(ROOT / "outputs" / "json"))
    parser.add_argument("--report-dir", default=str(ROOT / "outputs" / "reports"))
    args = parser.parse_args()

    summary = json.loads(Path(args.summary_json).read_text(encoding="utf-8"))
    json_dir = Path(args.json_dir)
    report_dir = Path(args.report_dir)
    batch_result: list[dict] = []
    for item in summary:
        input_path = Path(item["json_path"])
        stem = input_path.stem.replace("_extraction_inputs", "")
        result = run_tender_llm_extraction_from_file(
            input_path,
            prompt_dir=args.prompt_dir,
            task_keys=["score_points_extraction_input"],
        )
        json_path = json_dir / f"{stem}_score_points_llm.json"
        report_path = report_dir / f"{stem}_score_points_llm.md"
        write_tender_llm_extraction_outputs(result, json_path, report_path)
        task = result.tasks[0] if result.tasks else None
        parsed = task.parsed_json if task else None
        batch_result.append(
            {
                "file_name": item["file_name"],
                "input_json": str(input_path),
                "result_json": str(json_path),
                "result_report": str(report_path),
                "status": task.status if task else "missing",
                "duration_seconds": task.duration_seconds if task else 0,
                "issue_count": (task.validation or {}).get("issue_count") if task else None,
                "issues": (task.validation or {}).get("issues") if task else [],
                "score_point_count": len((parsed or {}).get("score_points") or []),
                "warning_count": len((parsed or {}).get("warnings") or []),
                "error": task.error if task else "missing task",
            }
        )
        print(
            f"{item['file_name']}: status={batch_result[-1]['status']}, "
            f"points={batch_result[-1]['score_point_count']}, "
            f"issues={batch_result[-1]['issue_count']}, "
            f"duration={batch_result[-1]['duration_seconds']:.2f}s"
        )

    output_json = json_dir / "batch_score_points_llm_summary.json"
    output_report = report_dir / "batch_score_points_llm_summary.md"
    output_json.write_text(json.dumps(batch_result, ensure_ascii=False, indent=2), encoding="utf-8")
    output_report.write_text(_render_report(batch_result), encoding="utf-8")
    print(f"Summary JSON: {output_json}")
    print(f"Summary report: {output_report}")
    return 0 if all(item["status"] == "completed" for item in batch_result) else 1


def _render_report(items: list[dict]) -> str:
    lines = [
        "# 批量评分点 LLM 抽取汇总",
        "",
        "| 文件 | 状态 | 评分点数 | 校验问题 | warnings | 耗时秒 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in items:
        lines.append(
            f"| {item['file_name']} | {item['status']} | {item['score_point_count']} | "
            f"{item['issue_count']} | {item['warning_count']} | {item['duration_seconds']:.2f} |"
        )
    lines.append("")
    for item in items:
        if item.get("issues") or item.get("error"):
            lines.extend([f"## {item['file_name']}", ""])
            if item.get("error"):
                lines.append(f"- error: {item['error']}")
            for issue in item.get("issues") or []:
                lines.append(f"- issue: {issue}")
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
