"""汇总招标文件 LLM 抽取输出，便于快速人工质检。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总招标文件 LLM 抽取 JSON。")
    parser.add_argument("--input", required=True, help="LLM 抽取结果 JSON 路径")
    parser.add_argument("--input-packages", default=None, help="可选的抽取输入包 JSON，用于引用检查")
    parser.add_argument("--output", required=True, help="Markdown 摘要输出路径")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    input_packages = json.loads(Path(args.input_packages).read_text(encoding="utf-8")) if args.input_packages else None
    valid_refs_by_task = _valid_refs_by_task(input_packages) if input_packages else {}
    lines = [
        "# LLM 抽取质量摘要",
        "",
        f"- 模型：{data.get('model')}",
        f"- 服务商：{data.get('provider')}",
        f"- 完成任务：{data.get('completed_task_count')}/{data.get('task_count')}",
        "",
    ]

    for task in data.get("tasks", []):
        parsed = task.get("parsed_json") or {}
        invalid_refs = _invalid_refs(parsed, valid_refs_by_task.get(task.get("task_key"), set()))
        lines.extend(
            [
                f"## {task.get('task_title') or task.get('task_key')}",
                "",
                f"- 状态：{task.get('status')}",
                f"- 耗时：{task.get('duration_seconds'):.2f}s",
                f"- 校验：{task.get('validation', {}).get('summary', '')}",
            ]
        )
        issues = task.get("validation", {}).get("issues") or []
        if issues:
            lines.append(f"- 校验问题：{'; '.join(issues)}")
        if invalid_refs:
            lines.append(f"- 引用回填问题：{', '.join(invalid_refs[:30])}")
        if task.get("error"):
            lines.append(f"- 错误：{task.get('error')}")
        lines.append("")

        if task.get("task_key") == "project_info_extraction_input":
            _append_project_info(lines, parsed)
        elif task.get("task_key") == "score_points_extraction_input":
            _append_score_points(lines, parsed)
        elif task.get("task_key") == "technical_requirements_extraction_input":
            _append_technical_requirements(lines, parsed)
        lines.append("")

    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"Summary: {target}")
    return 0


def _append_project_info(lines: list[str], data: dict[str, Any]) -> None:
    lines.extend(
        [
            f"- 项目类型：{data.get('project_type')}",
            f"- 是否含设计任务：{data.get('contains_design_task')}",
            "",
            "| 字段 | 值 | 置信度 | 需复核 | 来源 |",
            "|---|---|---:|---|---|",
        ]
    )
    for field_name, field in (data.get("fields") or {}).items():
        ref = field.get("field_ref") or {}
        lines.append(
            f"| {field_name} | {_cell(field.get('value'))} | {field.get('confidence')} | "
            f"{field.get('needs_confirmation')} | {ref.get('id')} |"
        )
    _append_warnings(lines, data)


def _append_score_points(lines: list[str], data: dict[str, Any]) -> None:
    points = data.get("score_points") or []
    lines.extend(
        [
            f"- 是否评分区域：{data.get('is_score_region')}",
            f"- 技术评分点数量：{len(points)}",
            "",
            "| 序号 | 评分点观察文本 | 置信度 | 需复核 | 来源 |",
            "|---:|---|---:|---|---|",
        ]
    )
    for index, point in enumerate(points, start=1):
        ref = point.get("score_point_ref") or {}
        lines.append(
            f"| {index} | {_cell(point.get('model_observed_text'))} | {point.get('confidence')} | "
            f"{point.get('needs_confirmation')} | {ref.get('id')} |"
        )
    _append_warnings(lines, data)


def _append_technical_requirements(lines: list[str], data: dict[str, Any]) -> None:
    for key, title, ref_key in [
        ("requirements", "编制要求", "requirement_ref"),
        ("technical_standards", "技术标准", "standard_ref"),
        ("technical_risks", "技术风险", "risk_ref"),
    ]:
        items = data.get(key) or []
        lines.extend(
            [
                f"### {title}：{len(items)}",
                "",
                "| 序号 | 原文观察 | 类型 | 适用 | 优先级/严重性 | 置信度 | 需复核 | 来源 |",
                "|---:|---|---|---|---|---:|---|---|",
            ]
        )
        for index, item in enumerate(items, start=1):
            ref = item.get(ref_key) or {}
            type_value = item.get("requirement_type") or item.get("standard_type") or item.get("risk_type")
            priority = item.get("priority") or item.get("severity")
            lines.append(
                f"| {index} | {_cell(item.get('model_observed_text'))} | {type_value} | "
                f"{item.get('applies_to')} | {priority} | {item.get('confidence')} | "
                f"{item.get('needs_confirmation')} | {ref.get('id')} |"
            )
        lines.append("")
    _append_warnings(lines, data)


def _append_warnings(lines: list[str], data: dict[str, Any]) -> None:
    warnings = data.get("warnings") or []
    if not warnings:
        lines.append("")
        lines.append("- warnings：无")
        return
    lines.append("")
    lines.append("warnings：")
    for warning in warnings:
        lines.append(f"- {warning}")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _valid_refs_by_task(data: dict[str, Any] | None) -> dict[str, set[str]]:
    if not data:
        return {}
    result: dict[str, set[str]] = {}
    for package in data.get("packages", []):
        refs = {
            f"B{block.get('block_index')}"
            for block in package.get("block_refs", [])
            if block.get("block_index") is not None
        }
        refs.update(
            cell.get("cell_id")
            for cell in package.get("cell_refs", [])
            if cell.get("cell_id")
        )
        result[package.get("task_key", "")] = refs
    return result


def _invalid_refs(data: Any, valid_refs: set[str]) -> list[str]:
    if not valid_refs:
        return []
    refs: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            ref_id = value.get("id")
            if isinstance(ref_id, str):
                if ref_id in valid_refs:
                    pass
                else:
                    block_ids = re.findall(r"B\d+(?!_R)", ref_id)
                    if not block_ids or any(block_id not in valid_refs for block_id in block_ids):
                        refs.add(ref_id)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return sorted(refs)


if __name__ == "__main__":
    raise SystemExit(main())
