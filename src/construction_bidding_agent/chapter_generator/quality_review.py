"""章节正文生成质量评审。

该模块基于已经生成的章节结构化初稿和原始章节输入包，输出轻量质量评审报告。
评审结果用于决定是否进入全量章节生成，以及提示词、素材检索、表格/图片策略是否需要调整。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REVIEW_SCHEMA_VERSION = "chapter_generation_quality_review_v0.1"


def build_chapter_generation_quality_review_from_files(
    chapter_generation_result_json: str | Path,
    chapter_inputs_json: str | Path | None = None,
) -> dict[str, Any]:
    """从文件构建章节生成质量评审结果。"""

    result = _read_json(chapter_generation_result_json)
    inputs = _read_json(chapter_inputs_json) if chapter_inputs_json else {}
    return build_chapter_generation_quality_review(result, inputs)


def build_chapter_generation_quality_review(
    generation_result: dict[str, Any],
    chapter_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建章节生成质量评审结果。"""

    packages_by_unit = {
        str((package.get("generation_unit") or {}).get("unit_id") or ""): package
        for package in (chapter_inputs or {}).get("packages") or []
        if isinstance(package, dict)
    }
    task_by_unit = {
        str(task.get("unit_id") or ""): task
        for task in generation_result.get("tasks") or []
        if isinstance(task, dict)
    }

    chapter_reviews = []
    for chapter in generation_result.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        unit_id = str(chapter.get("unit_id") or "")
        chapter_reviews.append(
            _review_chapter(
                chapter,
                packages_by_unit.get(unit_id) or {},
                task_by_unit.get(unit_id) or {},
            )
        )

    summary = _summary(chapter_reviews, generation_result)
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "source_result_schema_version": generation_result.get("schema_version"),
        "chapter_count": len(chapter_reviews),
        "summary": summary,
        "chapter_reviews": chapter_reviews,
        "recommendations": _recommendations(summary, chapter_reviews),
    }


def write_chapter_generation_quality_review(
    review: dict[str, Any],
    json_path: str | Path | None,
    report_path: str | Path,
) -> None:
    """写入评审 JSON 和 Markdown 报告。"""

    if json_path:
        json_target = Path(json_path)
        json_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target = Path(report_path)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.write_text(render_chapter_generation_quality_review(review), encoding="utf-8")


def render_chapter_generation_quality_review(review: dict[str, Any]) -> str:
    """渲染章节生成质量评审 Markdown。"""

    summary = review.get("summary") or {}
    lines = [
        "# 章节生成质量评审报告",
        "",
        "## 总体结论",
        "",
        f"- 评审章节数：{review.get('chapter_count', 0)}",
        f"- 平均质量分：{summary.get('average_score', 0)} / 100",
        f"- 可进入全量生成：{'是' if summary.get('ready_for_full_generation') else '否'}",
        f"- 高优先级问题数：{summary.get('high_priority_issue_count', 0)}",
        f"- 中优先级问题数：{summary.get('medium_priority_issue_count', 0)}",
        f"- 平均二级/三级小节覆盖率：{summary.get('average_heading_coverage_ratio', 0)}",
        f"- 总表格数：{summary.get('rich_table_count', 0)}",
        f"- 总图片占位数：{summary.get('image_placeholder_count', 0)}",
        f"- 总人工复核项：{summary.get('review_item_count', 0)}",
        "",
        "## 章节明细",
        "",
        "| 序号 | 章节 | 质量分 | 结论 | 小节覆盖 | 正文段落 | 表格 | 图片占位 | 复核项 | 主要问题 |",
        "|---:|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(review.get("chapter_reviews") or [], start=1):
        metrics = item.get("metrics") or {}
        lines.append(
            f"| {index} | {_cell(' > '.join(item.get('chapter_path') or []))} | "
            f"{item.get('quality_score', 0)} | {_cell(item.get('quality_level'))} | "
            f"{metrics.get('heading_coverage_ratio', 0)} | "
            f"{metrics.get('paragraph_count', 0)} | "
            f"{metrics.get('rich_table_count', 0)} | "
            f"{metrics.get('image_placeholder_count', 0)} | "
            f"{metrics.get('review_item_count', 0)} | "
            f"{_cell(_issue_summary(item.get('issues') or []))} |"
        )

    lines.extend(["", "## 逐章评审", ""])
    for item in review.get("chapter_reviews") or []:
        lines.append(f"### {' > '.join(item.get('chapter_path') or [])}")
        lines.append("")
        lines.append(f"- 质量分：{item.get('quality_score', 0)} / 100")
        lines.append(f"- 结论：{item.get('quality_level')}")
        lines.append(f"- 评分点响应：{_cell(item.get('score_response_summary'))}")
        strengths = item.get("strengths") or []
        if strengths:
            lines.append("- 优点：" + "；".join(_cell(value) for value in strengths))
        issues = item.get("issues") or []
        if issues:
            lines.append("- 问题：" + "；".join(f"[{issue.get('severity')}] {issue.get('message')}" for issue in issues))
        suggestions = item.get("suggestions") or []
        if suggestions:
            lines.append("- 建议：" + "；".join(_cell(value) for value in suggestions))
        lines.append("")

    recommendations = review.get("recommendations") or []
    if recommendations:
        lines.extend(["## 后续建议", ""])
        for item in recommendations:
            lines.append(f"- [{item.get('priority')}] {item.get('message')}")
        lines.append("")
    return "\n".join(lines)


def _review_chapter(chapter: dict[str, Any], package: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    metrics = _metrics(chapter, package, task)
    issues = _issues(chapter, package, task, metrics)
    strengths = _strengths(chapter, package, metrics)
    score = _quality_score(metrics, issues)
    return {
        "unit_id": chapter.get("unit_id"),
        "chapter_path": chapter.get("chapter_path") or [],
        "quality_score": score,
        "quality_level": _quality_level(score, issues),
        "metrics": metrics,
        "score_response_summary": (chapter.get("score_response_check") or {}).get("response_summary"),
        "strengths": strengths,
        "issues": issues,
        "suggestions": _suggestions(chapter, package, issues, metrics),
    }


def _metrics(chapter: dict[str, Any], package: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    sections = [section for section in chapter.get("sections") or [] if isinstance(section, dict)]
    blocks = [block for section in sections for block in section.get("blocks") or [] if isinstance(block, dict)]
    child_headings = (package.get("generation_unit") or {}).get("child_headings") or []
    generated_headings = [str(section.get("heading") or "") for section in sections]
    covered_headings = sum(1 for heading in child_headings if heading in generated_headings)
    heading_coverage_ratio = round(covered_headings / len(child_headings), 2) if child_headings else 1.0
    paragraph_texts = [str(block.get("text") or "") for block in blocks if block.get("type") == "paragraph"]
    return {
        "section_count": len(sections),
        "expected_heading_count": len(child_headings),
        "covered_heading_count": covered_headings,
        "heading_coverage_ratio": heading_coverage_ratio,
        "paragraph_count": len(paragraph_texts),
        "paragraph_char_count": sum(len(text) for text in paragraph_texts),
        "rich_table_count": sum(1 for block in blocks if block.get("type") == "rich_table"),
        "image_placeholder_count": sum(1 for block in blocks if block.get("type") == "image_placeholder"),
        "review_item_count": len(chapter.get("review_items") or []),
        "source_usage_count": len(chapter.get("source_usage") or []),
        "input_reference_count": len(package.get("excellent_bid_references") or []),
        "input_table_reference_count": len(package.get("table_references") or []),
        "input_image_candidate_count": len(package.get("image_candidates") or []),
        "input_reuse_warning_count": len(package.get("reuse_warnings") or []),
        "validation_issue_count": (task.get("validation") or {}).get("issue_count", 0),
    }


def _issues(
    chapter: dict[str, Any],
    package: dict[str, Any],
    task: dict[str, Any],
    metrics: dict[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    validation = task.get("validation") or {}
    for issue in validation.get("issues") or []:
        issues.append(
            {
                "severity": "high" if issue.get("severity") == "blocking" else "medium",
                "type": str(issue.get("type") or "validation"),
                "message": str(issue.get("message") or "章节校验存在问题。"),
            }
        )
    if metrics["heading_coverage_ratio"] < 0.8:
        issues.append({"severity": "high", "type": "heading_coverage", "message": "未充分覆盖输入包中的二三级目录。"})
    if metrics["paragraph_count"] < max(2, metrics["section_count"]):
        issues.append({"severity": "medium", "type": "thin_content", "message": "正文段落数量偏少，可能不够像完整技术标正文。"})
    if metrics["rich_table_count"] == 0 and metrics["input_table_reference_count"] >= 3:
        issues.append({"severity": "medium", "type": "table_underuse", "message": "输入包有较多表格素材，但生成结果未使用 rich_table。"})
    if metrics["review_item_count"] == 0 and (
        metrics["image_placeholder_count"] > 0 or metrics["input_reuse_warning_count"] > 0
    ):
        issues.append({"severity": "medium", "type": "missing_review_items", "message": "存在图片占位或复用风险，但未给出人工复核项。"})
    if not (chapter.get("score_response_check") or {}).get("covered"):
        issues.append({"severity": "high", "type": "score_response", "message": "评分点响应检查未确认覆盖。"})
    if _contains_process_words(chapter):
        issues.append({"severity": "high", "type": "process_words", "message": "正文中出现模型或参考素材等过程性表述。"})
    if _is_environment_chapter(chapter, package) and "扬尘" not in _chapter_text(chapter):
        issues.append({"severity": "medium", "type": "score_point_scope", "message": "评分点包含扬尘治理，当前章节需确认与扬尘专项章节的边界说明是否足够。"})
    return issues


def _strengths(chapter: dict[str, Any], package: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    strengths = []
    if metrics["heading_coverage_ratio"] >= 1:
        strengths.append("完整覆盖输入目录小节")
    if metrics["rich_table_count"] >= 2:
        strengths.append("表格化表达较充分")
    if (chapter.get("score_response_check") or {}).get("covered"):
        strengths.append("已输出评分点响应检查")
    if metrics["review_item_count"] > 0:
        strengths.append("给出了人工复核清单")
    if package.get("excellent_bid_references"):
        strengths.append("已结合优秀标书素材结构")
    return strengths


def _suggestions(
    chapter: dict[str, Any],
    package: dict[str, Any],
    issues: list[dict[str, str]],
    metrics: dict[str, Any],
) -> list[str]:
    suggestions = []
    issue_types = {issue["type"] for issue in issues}
    if "table_underuse" in issue_types:
        suggestions.append("提示词中加强 rich_table 生成要求，尤其是措施清单、控制要点和责任分工表。")
    if "heading_coverage" in issue_types:
        suggestions.append("生成前应强约束 child_headings 必须逐项转为 section。")
    if _is_environment_chapter(chapter, package):
        suggestions.append("环境保护章节可保留与扬尘专项章节的边界说明，避免遗漏评分点整体要求。")
    if metrics["image_placeholder_count"] > 0:
        suggestions.append("后续 Word 渲染阶段需要把 image_placeholder 转成人工补图占位。")
    if not suggestions:
        suggestions.append("可进入小批量全量生成测试，继续观察不同章节类型的稳定性。")
    return suggestions


def _quality_score(metrics: dict[str, Any], issues: list[dict[str, str]]) -> int:
    score = 100
    score -= max(0, 1 - metrics["heading_coverage_ratio"]) * 30
    score -= max(0, 4 - metrics["paragraph_count"]) * 3
    score -= sum(12 for issue in issues if issue["severity"] == "high")
    score -= sum(5 for issue in issues if issue["severity"] == "medium")
    if metrics["rich_table_count"] >= 2:
        score += 3
    if metrics["review_item_count"] > 0:
        score += 2
    return max(0, min(100, round(score)))


def _quality_level(score: int, issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "high" for issue in issues):
        return "需优化后再全量"
    if score >= 90:
        return "可进入全量试跑"
    if score >= 80:
        return "小幅优化后可试跑"
    return "需重点优化"


def _summary(chapter_reviews: list[dict[str, Any]], generation_result: dict[str, Any]) -> dict[str, Any]:
    if not chapter_reviews:
        return {
            "average_score": 0,
            "ready_for_full_generation": False,
            "high_priority_issue_count": 0,
            "medium_priority_issue_count": 0,
            "average_heading_coverage_ratio": 0,
            "rich_table_count": 0,
            "image_placeholder_count": 0,
            "review_item_count": 0,
        }
    high_count = sum(1 for item in chapter_reviews for issue in item.get("issues") or [] if issue.get("severity") == "high")
    medium_count = sum(1 for item in chapter_reviews for issue in item.get("issues") or [] if issue.get("severity") == "medium")
    average_score = round(sum(item["quality_score"] for item in chapter_reviews) / len(chapter_reviews), 1)
    average_coverage = round(
        sum((item.get("metrics") or {}).get("heading_coverage_ratio", 0) for item in chapter_reviews) / len(chapter_reviews),
        2,
    )
    return {
        "average_score": average_score,
        "ready_for_full_generation": high_count == 0 and average_score >= 85 and generation_result.get("failed_count", 0) == 0,
        "high_priority_issue_count": high_count,
        "medium_priority_issue_count": medium_count,
        "average_heading_coverage_ratio": average_coverage,
        "rich_table_count": sum((item.get("metrics") or {}).get("rich_table_count", 0) for item in chapter_reviews),
        "image_placeholder_count": sum((item.get("metrics") or {}).get("image_placeholder_count", 0) for item in chapter_reviews),
        "review_item_count": sum((item.get("metrics") or {}).get("review_item_count", 0) for item in chapter_reviews),
    }


def _recommendations(summary: dict[str, Any], chapter_reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    recommendations = []
    if summary.get("ready_for_full_generation"):
        recommendations.append({"priority": "high", "message": "建议进入全量章节生成试跑，但保留 max_workers=2 或 3 控制接口压力。"})
    else:
        recommendations.append({"priority": "high", "message": "建议先处理高优先级问题，再进入全量章节生成。"})
    if any((item.get("metrics") or {}).get("image_placeholder_count", 0) for item in chapter_reviews):
        recommendations.append({"priority": "medium", "message": "Word 渲染阶段需要支持 image_placeholder，尤其是进度、总平面、流水段示意图。"})
    if summary.get("rich_table_count", 0) > 0:
        recommendations.append({"priority": "medium", "message": "后续应检查 rich_table 在 Word 中的列宽、表头样式和跨页效果。"})
    if any("环境保护" in " > ".join(item.get("chapter_path") or []) for item in chapter_reviews):
        recommendations.append({"priority": "low", "message": "文明环保类评分点较长，建议继续观察扬尘治理专项章节是否能与环境保护章节形成互补。"})
    return recommendations


def _issue_summary(issues: list[dict[str, str]]) -> str:
    if not issues:
        return "-"
    return "；".join(issue.get("message", "") for issue in issues[:3])


def _chapter_text(chapter: dict[str, Any]) -> str:
    return json.dumps(chapter, ensure_ascii=False)


def _contains_process_words(chapter: dict[str, Any]) -> bool:
    text = _chapter_text(chapter)
    return any(word in text for word in ["优秀标书", "参考素材", "AI生成", "语言模型", "大模型", "模型生成", "模型输出"])


def _is_environment_chapter(chapter: dict[str, Any], package: dict[str, Any]) -> bool:
    path_text = " > ".join(chapter.get("chapter_path") or [])
    score_text = str((package.get("score_point") or {}).get("score_standard_raw") or "")
    return "环境保护" in path_text and "扬尘" in score_text


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
