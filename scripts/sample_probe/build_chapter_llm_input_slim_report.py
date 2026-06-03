"""生成章节正文 LLM 输入包瘦身对比报告。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.chapter_writer import LLM_INPUT_PROFILE, _llm_call_payload, _llm_input


DEFAULT_CASES = [
    {
        "name": "5个典型章节回归输入包",
        "path": ROOT
        / "outputs"
        / "json"
        / "batch_tender_01_chapter_generation_inputs_image_regression_5chapters.json",
        "old_actual_llm_chars": 79_723,
    },
    {
        "name": "50章完整正文输入包",
        "path": ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_zhenggui_full_adapted.json",
        "old_actual_llm_chars": 694_909,
    },
]
DEFAULT_REPORT = ROOT / "outputs" / "reports" / "chapter_generation_llm_input_slim_v3_report.md"
DEFAULT_JSON = ROOT / "outputs" / "json" / "chapter_generation_llm_input_slim_v3_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成章节正文 LLM 输入包瘦身对比报告。")
    parser.add_argument(
        "--chapter-inputs",
        action="append",
        default=[],
        help="章节正文生成输入包 JSON 路径；可重复传入。未传时使用脚本内置样例。",
    )
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT), help="Markdown 报告输出路径。")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON), help="JSON 摘要输出路径。")
    parser.add_argument(
        "--write-preview-packages",
        action="store_true",
        help="额外输出 slim 后的实际 LLM 输入包预览 JSON。",
    )
    args = parser.parse_args()

    cases = (
        [
            {"name": Path(path).parent.parent.name or Path(path).stem, "path": ROOT / path if not Path(path).is_absolute() else Path(path)}
            for path in args.chapter_inputs
        ]
        if args.chapter_inputs
        else DEFAULT_CASES
    )
    summaries = [build_case_summary(case, write_preview=args.write_preview_packages) for case in cases]

    report_path = Path(args.report_output)
    json_path = Path(args.json_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(summaries), encoding="utf-8")
    json_path.write_text(json.dumps({"cases": summaries}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Report: {report_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    for summary in summaries:
        print(
            summary["name"],
            "packages=",
            summary["package_count"],
            "old_actual=",
            summary["old_actual_llm_total_chars"],
            f"{LLM_INPUT_PROFILE}=",
            summary["slim_actual_llm_total_chars"],
            "reduction=",
            f"{summary['reduction_vs_old_actual_percent'] * 100:.1f}%",
        )
    return 0


def build_case_summary(case: dict[str, Any], *, write_preview: bool) -> dict[str, Any]:
    source_path = Path(case["path"])
    data = json.loads(source_path.read_text(encoding="utf-8"))
    packages = data.get("packages") or []
    slim_debug_packages = [_llm_input(package) for package in packages]
    slim_packages = [_llm_call_payload(package) for package in slim_debug_packages]

    full_sizes = [_json_size(package) for package in packages]
    slim_sizes = [_json_size(package) for package in slim_packages]
    field_totals: dict[str, int] = defaultdict(int)
    field_max: dict[str, dict[str, Any]] = defaultdict(lambda: {"chars": 0, "chapter_path": ""})
    rows = []

    for package, slim_package, full_size, slim_size in zip(packages, slim_packages, full_sizes, slim_sizes):
        chapter_path = " > ".join((package.get("generation_unit") or {}).get("chapter_path") or [])
        rows.append(
            {
                "unit_id": (package.get("generation_unit") or {}).get("unit_id"),
                "chapter_path": chapter_path,
                "full_package_chars": full_size,
                "slim_llm_chars": slim_size,
                "slim_ratio_to_full": _ratio(slim_size, full_size),
                "llm_input_profile": slim_package.get("llm_input_profile"),
                "image_group_count": len(slim_package.get("image_groups_slim") or []),
                "image_candidate_count": len(slim_package.get("image_candidates_slim") or []),
                "table_reference_count": len(slim_package.get("table_references_slim") or []),
            }
        )
        for key, value in slim_package.items():
            size = _json_size(value)
            field_totals[key] += size
            if size > field_max[key]["chars"]:
                field_max[key] = {"chars": size, "chapter_path": chapter_path}

    total_full = sum(full_sizes)
    total_slim = sum(slim_sizes)
    old_actual = int(case.get("old_actual_llm_chars") or total_full)

    if write_preview:
        preview_path = source_path.with_name(f"{source_path.stem}_llm_{LLM_INPUT_PROFILE}.json")
        preview_path.write_text(
            json.dumps(
                {
                    "schema_version": f"chapter_generation_llm_input_{LLM_INPUT_PROFILE}_preview",
                    "source_path": str(source_path),
                    "package_count": len(packages),
                    "packages": slim_packages,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return {
        "name": case["name"],
        "source_path": str(source_path),
        "package_count": len(packages),
        "full_package_total_chars": total_full,
        "old_actual_llm_total_chars": old_actual,
        "slim_actual_llm_total_chars": total_slim,
        "old_actual_to_full_ratio": _ratio(old_actual, total_full),
        "slim_to_full_ratio": _ratio(total_slim, total_full),
        "slim_to_old_actual_ratio": _ratio(total_slim, old_actual),
        "reduction_vs_old_actual_chars": old_actual - total_slim,
        "reduction_vs_old_actual_percent": _ratio(old_actual - total_slim, old_actual),
        "field_totals": dict(sorted(field_totals.items(), key=lambda item: -item[1])),
        "field_max": dict(sorted(field_max.items(), key=lambda item: -item[1]["chars"])),
        "largest_chapters": sorted(rows, key=lambda row: row["slim_llm_chars"], reverse=True)[:12],
        "heaviest_full_chapters": sorted(rows, key=lambda row: row["full_package_chars"], reverse=True)[:12],
    }


def render_report(summaries: list[dict[str, Any]]) -> str:
    lines = [
        "# 正文生成 LLM 输入包瘦身对比报告",
        "",
        "本报告只统计真正送入 LLM 的 `_llm_call_payload(_llm_input())` 结果，不改变完整输入包。完整输入包仍用于溯源、图片补全、去重和 Word 渲染。",
        "",
    ]
    for summary in summaries:
        lines.extend(
            [
                f"## {summary['name']}",
                "",
                f"- 来源文件：`{summary['source_path']}`",
                f"- 章节数：{summary['package_count']}",
                f"- 完整输入包字符数：{_fmt_int(summary['full_package_total_chars'])}",
                f"- 瘦身前实际 LLM 输入字符数：{_fmt_int(summary['old_actual_llm_total_chars'])}",
                f"- {LLM_INPUT_PROFILE} 实际 LLM 输入字符数：{_fmt_int(summary['slim_actual_llm_total_chars'])}",
                f"- {LLM_INPUT_PROFILE} / 完整输入包：{_fmt_pct(summary['slim_to_full_ratio'])}",
                f"- {LLM_INPUT_PROFILE} / 瘦身前实际输入：{_fmt_pct(summary['slim_to_old_actual_ratio'])}",
                (
                    f"- 相比瘦身前减少：{_fmt_int(summary['reduction_vs_old_actual_chars'])} 字符，"
                    f"约 {_fmt_pct(summary['reduction_vs_old_actual_percent'])}"
                ),
                "",
                f"### {LLM_INPUT_PROFILE} 字段体积排行",
                "",
                "| 字段 | 总字符数 | 最大单章字符数 | 最大章节 |",
                "|---|---:|---:|---|",
            ]
        )
        for key, total in list(summary["field_totals"].items())[:12]:
            max_item = summary["field_max"][key]
            lines.append(
                f"| `{key}` | {_fmt_int(total)} | {_fmt_int(max_item['chars'])} | {max_item['chapter_path']} |"
            )
        lines.extend(
            [
                "",
                f"### {LLM_INPUT_PROFILE} 最大章节",
                "",
                "| 章节 | 完整包字符数 | LLM 输入字符数 | 占完整包比例 | 表格 | 单图 | 套图 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in summary["largest_chapters"][:10]:
            lines.append(
                (
                    f"| {row['chapter_path']} | {_fmt_int(row['full_package_chars'])} | "
                    f"{_fmt_int(row['slim_llm_chars'])} | {_fmt_pct(row['slim_ratio_to_full'])} | "
                    f"{row['table_reference_count']} | {row['image_candidate_count']} | {row['image_group_count']} |"
                )
            )
        lines.extend(
            [
                "",
                "### 完整素材包最大章节",
                "",
                "| 章节 | 完整包字符数 | LLM 输入字符数 | 占完整包比例 |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in summary["heaviest_full_chapters"][:10]:
            lines.append(
                (
                    f"| {row['chapter_path']} | {_fmt_int(row['full_package_chars'])} | "
                    f"{_fmt_int(row['slim_llm_chars'])} | {_fmt_pct(row['slim_ratio_to_full'])} |"
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 结论",
            "",
            "- 当前优化重点应放在实际 LLM 输入，而不是删除完整输入包中的素材池。",
            f"- {LLM_INPUT_PROFILE} 主要压缩优秀标书参考、表格参考、图片候选和复用警告，保留图片分组、语义、章节适配和复用等级等关键字段。",
            "- 下一步建议用 5 个典型章节真实调用模型回归，观察生成质量、图片选择和耗时是否同步改善。",
            "",
        ]
    )
    return "\n".join(lines)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
