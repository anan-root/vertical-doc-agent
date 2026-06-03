"""章节图片复用质量检查。

该模块对章节生成结果中的 image_ref 进行确定性检查，输出给编标人员可读的图片复用质量报告。
它不调用 LLM，也不修改章节正文，只解释图片为什么被使用以及哪里需要人工复核。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = "chapter_image_reuse_quality_v0.1"

PROJECT_SPECIFIC_IMAGE_TERMS = [
    "施工总平面",
    "总平面",
    "平面布置图",
    "进度计划",
    "网络图",
    "横道图",
    "踏勘",
    "现状",
    "周边环境",
    "交通组织",
]
MANAGEMENT_HEADING_TERMS = ["管理", "组织", "质量", "安全", "工期", "进度", "风险", "协调", "保障", "责任"]
MANAGEMENT_IMAGE_TERMS = ["流程", "组织", "架构", "体系", "闭环", "责任", "分工", "检查", "验收", "制度", "管理"]
PROCESS_HEADING_TERMS = ["测量", "钢筋", "模板", "脚手架", "砌体", "防水", "混凝土", "后浇带", "土方", "基坑"]
SECTION_REQUIRED_TERMS = {
    "混凝土": ["浇筑", "振捣", "测温", "温控", "养护", "大体积"],
    "防水": ["防水", "卷材", "涂膜", "止水", "阴角", "屋面", "地下室"],
    "后浇带": ["后浇带", "变形缝", "止水", "施工缝"],
}
SECTION_EXCLUDED_TERMS = {
    "混凝土": ["预制块", "预留洞口", "门窗洞口", "电箱", "套管", "构造柱", "马牙槎", "砌筑", "砌体"],
}


def build_chapter_image_reuse_quality_report_from_files(
    chapter_generation_result_json: str | Path,
    chapter_inputs_json: str | Path | None = None,
) -> dict[str, Any]:
    """从文件构建章节图片复用质量报告。"""

    result = _read_json(chapter_generation_result_json)
    inputs = _read_json(chapter_inputs_json) if chapter_inputs_json else {}
    return build_chapter_image_reuse_quality_report(result, inputs)


def build_chapter_image_reuse_quality_report(
    generation_result: dict[str, Any],
    chapter_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建章节图片复用质量报告。"""

    packages_by_unit = {
        str((package.get("generation_unit") or {}).get("unit_id") or ""): package
        for package in (chapter_inputs or {}).get("packages") or []
        if isinstance(package, dict)
    }
    chapters = [chapter for chapter in generation_result.get("chapters") or [] if isinstance(chapter, dict)]
    chapter_reviews = [
        _review_chapter(chapter, packages_by_unit.get(str(chapter.get("unit_id") or "")) or {})
        for chapter in chapters
    ]
    summary = _summary(chapter_reviews)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_result_schema_version": generation_result.get("schema_version"),
        "chapter_count": len(chapter_reviews),
        "summary": summary,
        "chapter_reviews": chapter_reviews,
        "recommendations": _recommendations(summary, chapter_reviews),
    }


def write_chapter_image_reuse_quality_report(
    report: dict[str, Any],
    json_path: str | Path | None,
    report_path: str | Path,
) -> None:
    """写入图片复用质量 JSON 和 Markdown 报告。"""

    if json_path:
        json_target = Path(json_path)
        json_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target = Path(report_path)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.write_text(render_chapter_image_reuse_quality_report(report), encoding="utf-8")


def render_chapter_image_reuse_quality_report(report: dict[str, Any]) -> str:
    """渲染图片复用质量 Markdown 报告。"""

    summary = report.get("summary") or {}
    lines = [
        "# 章节图片复用质量报告",
        "",
        "## 总体结论",
        "",
        f"- 章节数：{report.get('chapter_count', 0)}",
        f"- 小节数：{summary.get('section_count', 0)}",
        f"- 图片数：{summary.get('image_count', 0)}",
        f"- 套图数：{summary.get('image_group_count', 0)}",
        f"- 散图数：{summary.get('single_image_count', 0)}",
        f"- 拆分套图数：{summary.get('split_group_count', 0)}",
        f"- 重复图片数：{summary.get('duplicate_image_count', 0)}",
        f"- 高风险问题数：{summary.get('high_risk_count', 0)}",
        f"- 中风险问题数：{summary.get('medium_risk_count', 0)}",
        "",
        "## 小节清单",
        "",
        "| 序号 | 章节 | 小节 | 图片 | 套图 | 散图 | 风险 | 结论 |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]
    row_index = 1
    for chapter in report.get("chapter_reviews") or []:
        chapter_path = " > ".join(chapter.get("chapter_path") or [])
        for section in chapter.get("section_reviews") or []:
            lines.append(
                f"| {row_index} | {_cell(chapter_path)} | {_cell(section.get('heading'))} | "
                f"{section.get('image_count', 0)} | {section.get('group_count', 0)} | "
                f"{section.get('single_image_count', 0)} | {len(section.get('issues') or [])} | "
                f"{_cell(section.get('conclusion'))} |"
            )
            row_index += 1

    lines.extend(["", "## 逐节明细", ""])
    for chapter in report.get("chapter_reviews") or []:
        lines.append(f"### {' > '.join(chapter.get('chapter_path') or [])}")
        lines.append("")
        for section in chapter.get("section_reviews") or []:
            lines.append(f"#### {section.get('heading')}")
            lines.append("")
            lines.append(
                f"- 图片：{section.get('image_count', 0)} 张；套图：{section.get('group_count', 0)} 组；散图：{section.get('single_image_count', 0)} 张"
            )
            if section.get("issues"):
                lines.append("- 风险：" + "；".join(_issue_text(issue) for issue in section.get("issues") or []))
            else:
                lines.append("- 风险：未发现明显问题")
            if section.get("images"):
                lines.append("")
                lines.append("| 序号 | 题注 | 来源小节 | 套图 | 成员 | 风险 |")
                lines.append("|---:|---|---|---|---|---|")
                for index, image in enumerate(section.get("images") or [], start=1):
                    member = ""
                    if image.get("image_group_id"):
                        member = f"{image.get('group_member_index') or ''}/{image.get('group_member_count') or ''}"
                    lines.append(
                        f"| {index} | {_cell(image.get('caption'))} | "
                        f"{_cell(' > '.join(image.get('source_section_path') or []))} | "
                        f"{_cell(image.get('image_group_id'))} | {_cell(member)} | "
                        f"{_cell(_image_issue_summary(image.get('issues') or []))} |"
                    )
            lines.append("")

    recommendations = report.get("recommendations") or []
    if recommendations:
        lines.extend(["## 后续建议", ""])
        for item in recommendations:
            lines.append(f"- [{item.get('priority')}] {item.get('message')}")
        lines.append("")
    return "\n".join(lines)


def _review_chapter(chapter: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    section_reviews = [_review_section(section, package) for section in chapter.get("sections") or [] if isinstance(section, dict)]
    group_issues = _chapter_group_issues(section_reviews)
    duplicate_issues = _chapter_duplicate_issues(section_reviews)
    return {
        "unit_id": chapter.get("unit_id"),
        "chapter_path": chapter.get("chapter_path") or [],
        "section_reviews": section_reviews,
        "chapter_issues": [*group_issues, *duplicate_issues],
        "metrics": _chapter_metrics(section_reviews),
    }


def _review_section(section: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    heading = str(section.get("heading") or "")
    images = [_image_review(block, heading) for block in _section_image_blocks(section)]
    group_ids = {str(image.get("image_group_id") or "") for image in images if image.get("image_group_id")}
    issues = _section_issues(heading, images, package)
    return {
        "heading": heading,
        "level": section.get("level"),
        "image_count": len(images),
        "group_count": len(group_ids),
        "single_image_count": sum(1 for image in images if not image.get("image_group_id")),
        "images": images,
        "issues": issues,
        "conclusion": _section_conclusion(images, issues),
    }


def _image_review(block: dict[str, Any], heading: str) -> dict[str, Any]:
    issues = _image_issues(block, heading)
    return {
        "image_id": block.get("image_id"),
        "image_asset_id": block.get("image_asset_id"),
        "caption": block.get("caption"),
        "source_part_name": block.get("source_part_name") or block.get("part_name"),
        "material_slice_id": block.get("material_slice_id"),
        "source_bid_id": block.get("source_bid_id"),
        "source_slice_id": block.get("source_slice_id"),
        "source_section_path": block.get("source_section_path") or [],
        "image_group_id": block.get("image_group_id"),
        "group_title": block.get("group_title"),
        "group_member_index": block.get("group_member_index"),
        "group_member_count": block.get("group_member_count"),
        "must_keep_with_group": bool(block.get("must_keep_with_group")),
        "semantic_text": block.get("semantic_text"),
        "semantic_confidence": block.get("semantic_confidence"),
        "reuse_level": block.get("reuse_level"),
        "risk_level": block.get("risk_level"),
        "issues": issues,
    }


def _image_issues(block: dict[str, Any], heading: str) -> list[dict[str, str]]:
    text = _image_text(block)
    issues: list[dict[str, str]] = []
    if str(block.get("reuse_level") or "") == "manual_review" or str(block.get("risk_level") or "") == "high":
        issues.append(_issue("high", "manual_review_image", "图片被标记为人工复核或高风险，不应自动复用。"))
    if any(term in text for term in PROJECT_SPECIFIC_IMAGE_TERMS):
        issues.append(_issue("high", "project_specific_image", "图片疑似项目专属图纸或现场事实图片，需人工确认。"))
    if _topic_conflict(heading, text):
        issues.append(_issue("high", "topic_conflict", "图片语义与当前小节主题存在明显冲突。"))
    if _management_section(heading) and not _management_image(text) and not _process_section(heading):
        issues.append(_issue("medium", "management_image_type", "管理措施类小节宜优先使用流程、组织、闭环、责任或检查类图片。"))
    if _weak_semantic(block):
        issues.append(_issue("medium", "weak_image_semantic", "图片语义较弱，主要依赖章节路径或小节标题，建议人工抽查。"))
    return issues


def _section_issues(heading: str, images: list[dict[str, Any]], package: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not images and _section_should_have_image(heading, package):
        issues.append(_issue("medium", "missing_expected_image", "该小节适合配图，但当前未使用图片。"))
    high_count = sum(1 for image in images for issue in image.get("issues") or [] if issue.get("severity") == "high")
    medium_count = sum(1 for image in images for issue in image.get("issues") or [] if issue.get("severity") == "medium")
    if high_count:
        issues.append(_issue("high", "section_has_high_risk_images", f"小节中存在 {high_count} 个高风险图片问题。"))
    elif medium_count:
        issues.append(_issue("medium", "section_has_medium_risk_images", f"小节中存在 {medium_count} 个中风险图片提示。"))
    similar_groups = _similar_group_warnings(images)
    issues.extend(similar_groups)
    return issues


def _chapter_group_issues(section_reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    members_by_group: dict[str, set[int]] = defaultdict(set)
    expected_by_group: dict[str, int] = {}
    for section in section_reviews:
        for image in section.get("images") or []:
            group_id = str(image.get("image_group_id") or "")
            if not group_id:
                continue
            member_index = int(image.get("group_member_index") or 0)
            member_count = int(image.get("group_member_count") or 0)
            if member_index:
                members_by_group[group_id].add(member_index)
            if member_count:
                expected_by_group[group_id] = max(expected_by_group.get(group_id, 0), member_count)
    issues: list[dict[str, str]] = []
    for group_id, indexes in members_by_group.items():
        expected = expected_by_group.get(group_id, 0)
        if expected and len(indexes) < expected:
            issues.append(_issue("high", "split_image_group", f"套图 {group_id} 未完整引用，已引用 {len(indexes)}/{expected}。"))
    return issues


def _chapter_duplicate_issues(section_reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    ids = [
        str(image.get("image_id") or image.get("image_asset_id") or image.get("source_part_name") or "")
        for section in section_reviews
        for image in section.get("images") or []
    ]
    duplicates = [image_id for image_id, count in Counter(image_id for image_id in ids if image_id).items() if count > 1]
    if not duplicates:
        return []
    return [_issue("high", "duplicate_image", f"存在重复图片引用：{', '.join(duplicates[:10])}。")]


def _chapter_metrics(section_reviews: list[dict[str, Any]]) -> dict[str, int]:
    images = [image for section in section_reviews for image in section.get("images") or []]
    return {
        "section_count": len(section_reviews),
        "image_count": len(images),
        "image_group_count": len({str(image.get("image_group_id") or "") for image in images if image.get("image_group_id")}),
        "single_image_count": sum(1 for image in images if not image.get("image_group_id")),
        "section_issue_count": sum(len(section.get("issues") or []) for section in section_reviews),
        "image_issue_count": sum(len(image.get("issues") or []) for image in images),
    }


def _summary(chapter_reviews: list[dict[str, Any]]) -> dict[str, int]:
    section_reviews = [section for chapter in chapter_reviews for section in chapter.get("section_reviews") or []]
    images = [image for section in section_reviews for image in section.get("images") or []]
    chapter_issues = [issue for chapter in chapter_reviews for issue in chapter.get("chapter_issues") or []]
    section_issues = [issue for section in section_reviews for issue in section.get("issues") or []]
    image_issues = [issue for image in images for issue in image.get("issues") or []]
    all_issues = [*chapter_issues, *section_issues, *image_issues]
    duplicates = [issue for issue in chapter_issues if issue.get("type") == "duplicate_image"]
    split_groups = [issue for issue in chapter_issues if issue.get("type") == "split_image_group"]
    return {
        "section_count": len(section_reviews),
        "image_count": len(images),
        "image_group_count": len({str(image.get("image_group_id") or "") for image in images if image.get("image_group_id")}),
        "single_image_count": sum(1 for image in images if not image.get("image_group_id")),
        "duplicate_image_count": len(duplicates),
        "split_group_count": len(split_groups),
        "high_risk_count": sum(1 for issue in all_issues if issue.get("severity") == "high"),
        "medium_risk_count": sum(1 for issue in all_issues if issue.get("severity") == "medium"),
    }


def _recommendations(summary: dict[str, int], chapter_reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    if summary.get("split_group_count", 0):
        recommendations.append({"priority": "high", "message": "存在拆分套图，应修复图片组识别或自动插图后处理。"})
    if summary.get("duplicate_image_count", 0):
        recommendations.append({"priority": "high", "message": "存在重复图片引用，应检查图片去重逻辑。"})
    if summary.get("high_risk_count", 0):
        recommendations.append({"priority": "high", "message": "存在高风险图片问题，生成 Word 前应人工复核。"})
    missing_sections = [
        section.get("heading")
        for chapter in chapter_reviews
        for section in chapter.get("section_reviews") or []
        for issue in section.get("issues") or []
        if issue.get("type") == "missing_expected_image"
    ]
    if missing_sections:
        recommendations.append(
            {
                "priority": "medium",
                "message": "存在适合配图但未配图的小节：" + "、".join(str(item) for item in missing_sections[:8]),
            }
        )
    if not recommendations:
        recommendations.append({"priority": "low", "message": "未发现明显图片复用质量问题，可进入 Word 版式检查。"})
    return recommendations


def _section_image_blocks(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in section.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "image_ref"
    ]


def _section_should_have_image(heading: str, package: dict[str, Any]) -> bool:
    if not any(term in heading for term in PROCESS_HEADING_TERMS):
        return False
    candidate_text = json.dumps(
        {
            "image_candidates": package.get("image_candidates") or [],
            "image_candidate_pool": package.get("image_candidate_pool") or [],
            "image_group_candidate_pool": package.get("image_group_candidate_pool") or [],
        },
        ensure_ascii=False,
    )
    heading_topics = [term for term in PROCESS_HEADING_TERMS if term in heading]
    return any(term in candidate_text for term in heading_topics)


def _similar_group_warnings(images: list[dict[str, Any]]) -> list[dict[str, str]]:
    titles_by_group: dict[str, set[str]] = defaultdict(set)
    for image in images:
        group_id = str(image.get("image_group_id") or "")
        if not group_id:
            continue
        title = _normalize_text(str(image.get("group_title") or image.get("semantic_text") or image.get("caption") or ""))
        if title:
            titles_by_group[group_id].add(title)

    representative_titles = [
        sorted(titles)[0]
        for titles in titles_by_group.values()
        if titles
    ]
    counts = Counter(representative_titles)
    if any(count > 1 for count in counts.values()):
        return [_issue("medium", "similar_group_density", "小节中存在多个语义相近的不同套图，建议人工判断是否存在低价值重复。")]
    return []


def _section_conclusion(images: list[dict[str, Any]], issues: list[dict[str, str]]) -> str:
    if any(issue.get("severity") == "high" for issue in issues):
        return "需人工复核"
    if any(issue.get("severity") == "medium" for issue in issues):
        return "建议抽查"
    if images:
        return "图片复用基本合理"
    return "未使用图片"


def _topic_conflict(heading: str, image_text: str) -> bool:
    for topic, excluded_terms in SECTION_EXCLUDED_TERMS.items():
        if topic in heading and any(term in image_text for term in excluded_terms):
            return True
    for topic, required_terms in SECTION_REQUIRED_TERMS.items():
        if topic not in heading:
            continue
        heading_requires_detail = any(term in heading for term in required_terms)
        if heading_requires_detail and not any(term in image_text for term in required_terms):
            return True
    return False


def _management_section(heading: str) -> bool:
    return any(term in heading for term in MANAGEMENT_HEADING_TERMS)


def _management_image(text: str) -> bool:
    return any(term in text for term in MANAGEMENT_IMAGE_TERMS)


def _process_section(heading: str) -> bool:
    return any(term in heading for term in PROCESS_HEADING_TERMS)


def _weak_semantic(block: dict[str, Any]) -> bool:
    confidence = float(block.get("semantic_confidence") or 0)
    semantic_text = str(block.get("semantic_text") or "").strip()
    caption = str(block.get("caption") or "").strip()
    if confidence >= 0.58 and semantic_text:
        return False
    return not caption or caption in {"图片", "图示", "示意图", "施工图示", "效果图"}


def _image_text(block: dict[str, Any]) -> str:
    parts = [
        block.get("caption"),
        block.get("group_title"),
        block.get("group_semantic_text"),
        block.get("semantic_text"),
        block.get("nearby_text"),
        " ".join(str(item) for item in block.get("caption_candidates") or []),
        " ".join(str(part) for part in block.get("source_section_path") or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _normalize_text(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")[:24]


def _issue(severity: str, type_: str, message: str) -> dict[str, str]:
    return {"severity": severity, "type": type_, "message": message}


def _issue_text(issue: dict[str, Any]) -> str:
    return f"[{issue.get('severity')}] {issue.get('message')}"


def _image_issue_summary(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "无"
    return "；".join(f"{issue.get('severity')}:{issue.get('type')}" for issue in issues)


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
