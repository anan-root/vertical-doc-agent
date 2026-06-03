"""投标模板推荐。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def recommend_bid_templates(
    templates: list[dict[str, Any]],
    workflow_summary: Mapping[str, Any],
    *,
    limit: int = 3,
) -> dict[str, Any]:
    project = _dict(workflow_summary.get("project"))
    score_points = _list(workflow_summary.get("score_points"))
    project_type = str(project.get("project_type") or "")
    keywords = _summary_keywords(score_points)
    project_text = " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("description") or ""),
            " ".join(str(item.get("title") or "") for item in score_points if isinstance(item, Mapping)),
        ]
    )
    recommendations = []
    for template in templates:
        score = 0
        reasons = []
        matched_keywords: list[str] = []
        if project_type and template.get("project_type") in {project_type, "general", ""}:
            score += 5
            reasons.append("项目类型匹配")
        elif project_type and template.get("project_type"):
            reasons.append("项目类型需人工判断")
        text = " ".join(
            [
                str(template.get("name") or ""),
                str(template.get("description") or ""),
                " ".join(str(tag) for tag in template.get("tags") or []),
                " ".join(str(item) for item in template.get("applicable_scenarios") or []),
                " ".join(str(chapter.get("title") or "") for chapter in template.get("chapters") or [] if isinstance(chapter, Mapping)),
                " ".join(" ".join(str(focus) for focus in chapter.get("writing_focus") or []) for chapter in template.get("chapters") or [] if isinstance(chapter, Mapping)),
            ]
        ).lower()
        matched = [keyword for keyword in keywords if keyword and keyword.lower() in text]
        if matched:
            score += min(len(matched), 5)
            matched_keywords.extend(matched)
            reasons.append("覆盖评分点关键词：" + "、".join(matched[:3]))
        text_matched = [keyword for keyword in _text_keywords(project_text) if keyword.lower() in text]
        if text_matched:
            score += min(len(text_matched), 4)
            matched_keywords.extend(text_matched)
            if not matched:
                reasons.append("覆盖项目关键词：" + "、".join(text_matched[:3]))
        if template.get("chapters"):
            score += 1
        if template.get("tables") or template.get("table_templates"):
            score += 1
        if not reasons:
            reasons.append("可作为通用结构参考")
        fit_score = min(100, max(35, score * 10))
        fit_level = _fit_level(fit_score)
        unique_matched = _unique(matched_keywords)
        recommendations.append(
            {
                "template_id": template.get("template_id"),
                "name": template.get("name"),
                "project_type": template.get("project_type"),
                "version": template.get("version"),
                "description": template.get("description"),
                "chapter_count": template.get("chapter_count") or len(template.get("chapters") or []),
                "table_count": template.get("table_count") or len(template.get("tables") or template.get("table_templates") or []),
                "score": score,
                "fit_score": fit_score,
                "fit_level": fit_level,
                "fit_level_label": _fit_level_label(fit_level),
                "reason": "；".join(reasons),
                "matched_keywords": unique_matched[:8],
                "coverage_tips": _coverage_tips(template, unique_matched, score_points),
                "usage_boundary": template.get("usage_boundary") or "模板只做推荐和预览，不自动覆盖已确认目录或正文。",
                "tags": template.get("tags") or [],
            }
        )
    recommendations.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return {
        "project_id": project.get("project_id"),
        "project_type": project_type or None,
        "recommendations": recommendations[: max(limit, 0)],
        "total_templates": len(templates),
    }


def _summary_keywords(score_points: list[Any]) -> list[str]:
    keywords = []
    seeds = ["质量", "安全", "进度", "文明", "施工", "环保", "组织", "BIM", "应急", "资源"]
    text = " ".join(str(item.get("title") or "") for item in score_points if isinstance(item, Mapping))
    for seed in seeds:
        if seed in text:
            keywords.append(seed)
    return keywords


def _text_keywords(text: str) -> list[str]:
    seeds = ["房建", "施工", "质量", "安全", "进度", "文明", "环保", "BIM", "设计", "采购", "机电", "装饰", "医院", "学校", "住宅", "厂房"]
    return [seed for seed in seeds if seed in text]


def _fit_level(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "reference"


def _fit_level_label(level: str) -> str:
    return {"high": "高度适配", "medium": "可参考", "reference": "仅作参考"}.get(level, "仅作参考")


def _coverage_tips(template: Mapping[str, Any], matched_keywords: list[str], score_points: list[Any]) -> list[str]:
    tips = []
    if matched_keywords:
        tips.append("已命中：" + "、".join(matched_keywords[:5]))
    else:
        tips.append("未明显命中评分点关键词，建议只参考目录骨架。")
    if score_points and not matched_keywords:
        tips.append("使用前请逐条核对评分点，避免漏项。")
    if template.get("tables") or template.get("table_templates"):
        tips.append("可参考其表格清单，但表格内容需按当前项目参数改写。")
    tips.append("不会自动覆盖已编辑目录或正文。")
    return tips[:4]


def _unique(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        key = item.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
