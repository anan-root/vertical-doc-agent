"""批量为选定的原始招标文件构建抽取输入包。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.tender_extraction_input_builder import (
    build_tender_extraction_inputs_from_path,
    write_tender_extraction_input_outputs,
)


TARGET_KEYWORDS = [
    "固始县轴承厂家属院棚户区改造项目",
    "豫东南高新技术产业开发区生物医药产业园项目",
    "实训基地建设项目施工招标文件",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="批量测试招标文件抽取输入包构建。")
    parser.add_argument("--raw-dir", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--json-dir", default=str(ROOT / "outputs" / "json"))
    parser.add_argument("--report-dir", default=str(ROOT / "outputs" / "reports"))
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    json_dir = Path(args.json_dir)
    report_dir = Path(args.report_dir)
    selected = _select_files(raw_dir)
    summary: list[dict] = []
    for index, path in enumerate(selected, start=1):
        file_id = f"batch_tender_{index:02d}_{_slug(path.stem)}"
        output_stem = f"batch_tender_{index:02d}_{_slug(path.stem)}"
        result = build_tender_extraction_inputs_from_path(path, file_id=file_id)
        json_path = json_dir / f"{output_stem}_extraction_inputs.json"
        report_path = report_dir / f"{output_stem}_extraction_inputs.md"
        write_tender_extraction_input_outputs(result, json_path, report_path)
        score_package = next(
            (package for package in result.packages if package.task_key == "score_points_extraction_input"),
            None,
        )
        summary.append(
            {
                "file_name": path.name,
                "file_id": file_id,
                "file_type": result.file_type,
                "json_path": str(json_path),
                "report_path": str(report_path),
                "package_count": result.package_count,
                "warnings": result.warnings,
                "packages": [
                    {
                        "task_key": package.task_key,
                        "tokens": package.estimated_tokens,
                        "chars": package.text_char_count,
                        "block_count": package.block_count,
                        "included_block_count": package.included_block_count,
                        "cell_ref_count": len(package.cell_refs),
                        "warnings": package.warnings,
                    }
                    for package in result.packages
                ],
                "score_cell_ref_count": len(score_package.cell_refs) if score_package else 0,
            }
        )
        print(f"Built: {path.name}")
        for package in result.packages:
            print(
                f"- {package.task_key}: tokens={package.estimated_tokens}, "
                f"cells={len(package.cell_refs)}, warnings={len(package.warnings)}"
            )

    summary_path = json_dir / "batch_tender_extraction_inputs_summary.json"
    summary_report_path = report_dir / "batch_tender_extraction_inputs_summary.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_report_path.write_text(_render_summary(summary), encoding="utf-8")
    print(f"Summary JSON: {summary_path}")
    print(f"Summary report: {summary_report_path}")
    return 0


def _select_files(raw_dir: Path) -> list[Path]:
    files = list(raw_dir.iterdir())
    selected: list[Path] = []
    for keyword in TARGET_KEYWORDS:
        matches = [path for path in files if keyword in path.name]
        if not matches:
            raise FileNotFoundError(f"No file found for keyword: {keyword}")
        selected.append(matches[0])
    return selected


def _slug(text: str) -> str:
    value = re.sub(r"\W+", "_", text, flags=re.UNICODE).strip("_")
    return value[:36] or "unknown"


def _render_summary(summary: list[dict]) -> str:
    lines = [
        "# 批量招标文件抽取输入包测试汇总",
        "",
        "| 文件 | 类型 | 输入包 | 估算 tokens | cell refs | warnings |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in summary:
        for package in item["packages"]:
            lines.append(
                f"| {item['file_name']} | {item['file_type']} | {package['task_key']} | "
                f"{package['tokens']} | {package['cell_ref_count']} | {len(package['warnings'])} |"
            )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
