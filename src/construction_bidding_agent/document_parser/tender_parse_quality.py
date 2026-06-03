"""招标文件解析质量基准与回归对比。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


QUALITY_BASELINE_SCHEMA_VERSION = "tender_parse_quality_baseline_v0.1"
QUALITY_COMPARISON_SCHEMA_VERSION = "tender_parse_quality_comparison_v0.1"

REQUIRED_PROJECT_INFO_FIELDS = [
    "project_name",
    "construction_location",
    "construction_scale",
    "tender_scope",
    "duration_requirement",
    "quality_requirement",
    "safety_civilization_requirement",
]


@dataclass(frozen=True, slots=True)
class TenderParseQualityMetrics:
    file_name: str
    parse_status: str
    project_type: str | None
    can_generate_outline: bool
    technical_score_point_count: int
    technical_bid_requirement_count: int
    technical_standard_count: int
    review_item_count: int
    warning_count: int
    blocking_warning_count: int
    project_info_missing_fields: list[str] = field(default_factory=list)
    score_point_titles: list[str] = field(default_factory=list)
    score_point_original_texts: list[str] = field(default_factory=list)
    score_point_source_ref_count: int = 0
    score_point_titles_from_original_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_quality_baseline(parse_results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [extract_quality_metrics(result).to_dict() for result in parse_results]
    return {
        "schema_version": QUALITY_BASELINE_SCHEMA_VERSION,
        "description": "招标文件解析质量回归基准。首次生成后应由人工复核确认，再作为后续解析质量对比依据。",
        "sample_count": len(metrics),
        "samples": metrics,
    }


def compare_quality_to_baseline(
    baseline: dict[str, Any],
    parse_results: list[dict[str, Any]],
) -> dict[str, Any]:
    actual_by_file = {
        metrics.file_name: metrics
        for metrics in [extract_quality_metrics(result) for result in parse_results]
    }
    comparisons: list[dict[str, Any]] = []
    for expected in baseline.get("samples") or []:
        file_name = expected.get("file_name")
        actual = actual_by_file.get(file_name)
        if actual is None:
            comparisons.append(
                {
                    "file_name": file_name,
                    "status": "failed",
                    "issues": [f"缺少样本解析结果：{file_name}"],
                    "expected": expected,
                    "actual": None,
                }
            )
            continue
        issues = _compare_sample(expected, actual.to_dict())
        comparisons.append(
            {
                "file_name": file_name,
                "status": "passed" if not issues else "failed",
                "issues": issues,
                "expected": expected,
                "actual": actual.to_dict(),
            }
        )

    extra_files = sorted(set(actual_by_file) - {sample.get("file_name") for sample in baseline.get("samples") or []})
    for file_name in extra_files:
        comparisons.append(
            {
                "file_name": file_name,
                "status": "extra",
                "issues": ["当前结果中存在基准未登记的额外样本。"],
                "expected": None,
                "actual": actual_by_file[file_name].to_dict(),
            }
        )

    failed_count = sum(1 for item in comparisons if item["status"] == "failed")
    return {
        "schema_version": QUALITY_COMPARISON_SCHEMA_VERSION,
        "status": "passed" if failed_count == 0 else "failed",
        "sample_count": len(comparisons),
        "failed_count": failed_count,
        "comparisons": comparisons,
    }


def extract_quality_metrics(result: dict[str, Any]) -> TenderParseQualityMetrics:
    input_file = (result.get("input_files") or [{}])[0]
    project_info = result.get("project_info") or {}
    score_points = result.get("technical_score_points") or []
    warnings = result.get("warnings") or []
    execution = result.get("execution") or {}
    titles = [_clean_text(point.get("catalog_level_1_title")) for point in score_points]
    original_texts = [_clean_text(point.get("original_text")) for point in score_points]
    return TenderParseQualityMetrics(
        file_name=input_file.get("file_name") or "",
        parse_status=(result.get("parse_job") or {}).get("status") or "",
        project_type=(result.get("project_type") or {}).get("value"),
        can_generate_outline=_can_generate_outline(execution, score_points, warnings),
        technical_score_point_count=len(score_points),
        technical_bid_requirement_count=len(result.get("technical_bid_requirements") or []),
        technical_standard_count=len(result.get("technical_standards") or []),
        review_item_count=len(result.get("review_items") or []),
        warning_count=len(warnings),
        blocking_warning_count=sum(1 for warning in warnings if warning.get("level") == "blocking"),
        project_info_missing_fields=_missing_project_info_fields(project_info),
        score_point_titles=titles,
        score_point_original_texts=original_texts,
        score_point_source_ref_count=sum(1 for point in score_points if point.get("source_refs")),
        score_point_titles_from_original_count=sum(
            1
            for title, original in zip(titles, original_texts, strict=False)
            if title and original and _normalize_text(title) == _normalize_text(original)
        ),
    )


def write_quality_json(data: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def render_quality_comparison_report(comparison: dict[str, Any]) -> str:
    lines = [
        "# 招标文件解析质量回归报告",
        "",
        f"- 状态：{comparison.get('status')}",
        f"- 样本数：{comparison.get('sample_count')}",
        f"- 失败数：{comparison.get('failed_count')}",
        "",
        "| 文件 | 状态 | 评分点 | 一级目录原文一致 | 项目信息缺失 | 问题数 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in comparison.get("comparisons") or []:
        actual = item.get("actual") or {}
        lines.append(
            f"| {item.get('file_name')} | {item.get('status')} | "
            f"{actual.get('technical_score_point_count', '')} | "
            f"{actual.get('score_point_titles_from_original_count', '')} | "
            f"{len(actual.get('project_info_missing_fields') or []) if actual else ''} | "
            f"{len(item.get('issues') or [])} |"
        )
    lines.append("")
    for item in comparison.get("comparisons") or []:
        issues = item.get("issues") or []
        if not issues:
            continue
        lines.extend([f"## {item.get('file_name')}", ""])
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("")
    return "\n".join(lines)


def _compare_sample(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field_name in ["project_type", "can_generate_outline"]:
        if actual.get(field_name) != expected.get(field_name):
            issues.append(f"{field_name} 变化：期望 {expected.get(field_name)!r}，实际 {actual.get(field_name)!r}。")

    _compare_count_not_lower(
        issues,
        "technical_score_point_count",
        expected,
        actual,
        "技术评分点数量",
    )
    _compare_count_not_lower(
        issues,
        "score_point_source_ref_count",
        expected,
        actual,
        "评分点来源引用数量",
    )
    _compare_count_not_lower(
        issues,
        "score_point_titles_from_original_count",
        expected,
        actual,
        "一级目录与评分点原文一致数量",
    )
    expected_titles = expected.get("score_point_titles") or []
    actual_titles = actual.get("score_point_titles") or []
    missing_titles = [title for title in expected_titles if title not in actual_titles]
    if missing_titles:
        issues.append(f"技术评分点标题缺失：{'; '.join(missing_titles)}。")

    expected_missing_fields = set(expected.get("project_info_missing_fields") or [])
    actual_missing_fields = set(actual.get("project_info_missing_fields") or [])
    new_missing_fields = sorted(actual_missing_fields - expected_missing_fields)
    if new_missing_fields:
        issues.append(f"项目基础信息新增缺失字段：{'; '.join(new_missing_fields)}。")

    if int(actual.get("blocking_warning_count") or 0) > int(expected.get("blocking_warning_count") or 0):
        issues.append(
            "blocking 警告数量增加："
            f"期望 {expected.get('blocking_warning_count')}，实际 {actual.get('blocking_warning_count')}。"
        )
    return issues


def _compare_count_not_lower(
    issues: list[str],
    field_name: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    label: str,
) -> None:
    expected_value = int(expected.get(field_name) or 0)
    actual_value = int(actual.get(field_name) or 0)
    if actual_value < expected_value:
        issues.append(f"{label}下降：期望至少 {expected_value}，实际 {actual_value}。")


def _missing_project_info_fields(project_info: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_name in REQUIRED_PROJECT_INFO_FIELDS:
        field = project_info.get(field_name) or {}
        value = field.get("value")
        if value in {None, "", "未明确"}:
            missing.append(field_name)
    return missing


def _can_generate_outline(
    execution: dict[str, Any],
    score_points: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> bool:
    value = execution.get("can_generate_outline")
    if isinstance(value, bool):
        return value
    has_blocking_warning = any(warning.get("level") == "blocking" for warning in warnings)
    return bool(score_points) and not has_blocking_warning


def _clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_text(value: str) -> str:
    return "".join(value.split())
