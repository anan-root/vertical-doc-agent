"""章节图片素材召回质量检查。

该模块对比章节生成结果、章节输入包和优秀标书素材库，诊断“哪些小节应配图但没召回、
候选池有图但没使用、全库有图但当前输入包没带进来”等问题。它不调用 LLM，也不修改正文。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = "chapter_image_retrieval_quality_v0.1"

TOPIC_RULES = {
    "测量": {
        "terms": ["测量", "控制网", "控制点", "轴线", "标高", "监测", "引测", "铅直仪", "内控点"],
        "strong_terms": ["测量", "控制网", "控制点", "轴线", "标高", "监测", "引测", "铅直仪", "内控点"],
        "anchor_terms": ["测量", "控制网", "控制点", "标高", "监测", "引测", "铅直仪", "内控点"],
        "excluded_terms": [],
    },
    "土方基坑": {
        "terms": ["土方", "基坑", "开挖", "支护", "降水", "边坡"],
        "strong_terms": ["土方", "基坑", "开挖", "支护", "降水", "边坡"],
        "anchor_terms": ["土方", "基坑", "开挖", "支护", "降水", "边坡"],
        "excluded_terms": ["脚手架", "外架", "立杆", "扫地杆", "剪刀撑"],
    },
    "钢筋": {
        "terms": ["钢筋", "箍筋", "绑扎", "直螺纹", "套筒", "马凳筋", "梯子筋"],
        "strong_terms": ["钢筋", "箍筋", "绑扎", "直螺纹", "套筒", "马凳筋", "梯子筋"],
        "anchor_terms": ["钢筋", "箍筋", "绑扎", "直螺纹", "套筒", "马凳筋", "梯子筋"],
        "excluded_terms": [],
    },
    "模板": {
        "terms": ["模板", "支模", "支撑", "吊模", "对拉螺栓", "方圆扣", "覆膜板"],
        "strong_terms": ["模板", "支模", "支撑", "吊模", "对拉螺栓", "方圆扣", "覆膜板"],
        "anchor_terms": ["模板", "支模", "支撑", "吊模", "对拉螺栓", "方圆扣", "覆膜板"],
        "excluded_terms": [],
    },
    "混凝土": {
        "terms": ["混凝土", "浇筑", "振捣", "测温", "温控", "养护", "大体积"],
        "strong_terms": ["浇筑", "振捣", "测温", "温控", "养护", "大体积", "混凝土施工"],
        "anchor_terms": ["浇筑", "振捣", "测温", "温控", "养护", "大体积", "混凝土施工"],
        "excluded_terms": ["预制块", "预留洞口", "门窗洞口", "电箱", "套管", "构造柱", "马牙槎", "砌筑", "砌体"],
    },
    "防水": {
        "terms": ["防水", "地下室", "屋面", "卷材", "涂膜", "止水", "阴角"],
        "strong_terms": ["防水", "地下室", "屋面", "卷材", "涂膜", "止水", "阴角"],
        "anchor_terms": ["防水", "地下室", "屋面", "卷材", "涂膜", "止水", "阴角"],
        "excluded_terms": [],
    },
    "脚手架": {
        "terms": ["脚手架", "外架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "悬挑", "扫地杆"],
        "strong_terms": ["脚手架", "外架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "悬挑", "扫地杆"],
        "anchor_terms": ["脚手架", "外架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "悬挑", "扫地杆"],
        "excluded_terms": ["基坑支护"],
    },
    "砌体": {
        "terms": ["砌体", "砌筑", "砖", "加气块", "构造柱", "拉结筋", "马牙槎", "灰缝"],
        "strong_terms": ["砌体", "砌筑", "砖", "加气块", "构造柱", "拉结筋", "马牙槎", "灰缝"],
        "anchor_terms": ["砌体", "砌筑", "砖", "加气块", "构造柱", "拉结筋", "马牙槎", "灰缝"],
        "excluded_terms": [],
    },
    "后浇带": {
        "terms": ["后浇带", "变形缝", "施工缝", "止水"],
        "strong_terms": ["后浇带", "变形缝", "施工缝", "止水"],
        "anchor_terms": ["后浇带", "变形缝", "施工缝", "止水"],
        "excluded_terms": [],
    },
}

PROJECT_SPECIFIC_TERMS = ["总平面", "平面布置图", "进度计划", "网络图", "横道图", "踏勘", "现状", "周边环境", "交通组织"]
REUSABLE_MATERIAL_QUALITIES = {"high", "usable", "review_required", "pdf_fallback"}
MIN_MATCH_SCORE = 2.6


def build_chapter_image_retrieval_quality_report_from_files(
    chapter_generation_result_json: str | Path,
    chapter_inputs_json: str | Path,
    material_library_json: str | Path | None = None,
) -> dict[str, Any]:
    """从文件构建章节图片素材召回质量报告。"""

    generation_result = _read_json(chapter_generation_result_json)
    chapter_inputs = _read_json(chapter_inputs_json)
    material_library = _read_json(material_library_json) if material_library_json else None
    return build_chapter_image_retrieval_quality_report(generation_result, chapter_inputs, material_library)


def build_chapter_image_retrieval_quality_report(
    generation_result: dict[str, Any],
    chapter_inputs: dict[str, Any],
    material_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建章节图片素材召回质量报告。"""

    packages_by_unit = {
        str((package.get("generation_unit") or {}).get("unit_id") or ""): package
        for package in chapter_inputs.get("packages") or []
        if isinstance(package, dict)
    }
    chapters = [chapter for chapter in generation_result.get("chapters") or [] if isinstance(chapter, dict)]
    chapter_reviews = [
        _review_chapter(chapter, packages_by_unit.get(str(chapter.get("unit_id") or "")) or {}, material_library)
        for chapter in chapters
    ]
    summary = _summary(chapter_reviews)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_result_schema_version": generation_result.get("schema_version"),
        "material_library_schema_version": (material_library or {}).get("schema_version"),
        "chapter_count": len(chapter_reviews),
        "summary": summary,
        "chapter_reviews": chapter_reviews,
        "recommendations": _recommendations(summary, chapter_reviews),
    }


def write_chapter_image_retrieval_quality_report(
    report: dict[str, Any],
    json_path: str | Path | None,
    report_path: str | Path,
) -> None:
    """写入图片素材召回质量 JSON 和 Markdown 报告。"""

    if json_path:
        json_target = Path(json_path)
        json_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target = Path(report_path)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.write_text(render_chapter_image_retrieval_quality_report(report), encoding="utf-8")


def render_chapter_image_retrieval_quality_report(report: dict[str, Any]) -> str:
    """渲染图片素材召回质量 Markdown 报告。"""

    summary = report.get("summary") or {}
    lines = [
        "# 章节图片素材召回质量报告",
        "",
        "## 总体结论",
        "",
        f"- 章节数：{report.get('chapter_count', 0)}",
        f"- 小节数：{summary.get('section_count', 0)}",
        f"- 宜配图小节数：{summary.get('image_preferred_section_count', 0)}",
        f"- 已使用图片数：{summary.get('used_image_count', 0)}",
        f"- 候选图片命中数：{summary.get('matched_candidate_image_count', 0)}",
        f"- 候选套图命中数：{summary.get('matched_candidate_group_count', 0)}",
        f"- 全库可用素材命中数：{summary.get('matched_library_material_count', 0)}",
        f"- 候选池漏召回小节数：{summary.get('candidate_pool_miss_section_count', 0)}",
        f"- 候选未使用小节数：{summary.get('candidate_unused_section_count', 0)}",
        f"- 图片使用偏保守小节数：{summary.get('low_usage_section_count', 0)}",
        f"- 高风险问题数：{summary.get('high_risk_count', 0)}",
        f"- 中风险问题数：{summary.get('medium_risk_count', 0)}",
        "",
        "## 小节清单",
        "",
        "| 序号 | 章节 | 小节 | 已用图 | 候选图 | 候选套图 | 素材摘要 | 全库可用素材 | 问题 | 结论 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    row_index = 1
    for chapter in report.get("chapter_reviews") or []:
        chapter_path = " > ".join(chapter.get("chapter_path") or [])
        for section in chapter.get("section_reviews") or []:
            lines.append(
                f"| {row_index} | {_cell(chapter_path)} | {_cell(section.get('heading'))} | "
                f"{section.get('used_image_count', 0)} | "
                f"{section.get('candidate_image_count', 0)} | "
                f"{section.get('candidate_group_count', 0)} | "
                f"{section.get('package_material_hit_count', 0)} | "
                f"{section.get('library_reusable_hit_count', 0)} | "
                f"{len(section.get('issues') or [])} | {_cell(section.get('conclusion'))} |"
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
                f"- 已用图：{section.get('used_image_count', 0)} 张；"
                f"候选图：{section.get('candidate_image_count', 0)} 张；"
                f"候选套图：{section.get('candidate_group_count', 0)} 组；"
                f"全库可用素材：{section.get('library_reusable_hit_count', 0)} 条"
            )
            if section.get("issues"):
                lines.append("- 问题：" + "；".join(_issue_text(issue) for issue in section.get("issues") or []))
            else:
                lines.append("- 问题：未发现明显召回问题")

            _append_preview_table(lines, "候选图片预览", section.get("candidate_images") or [], ["caption", "match_reason"])
            _append_preview_table(lines, "候选套图预览", section.get("candidate_groups") or [], ["group_title", "member_count"])
            _append_preview_table(lines, "全库可用素材预览", section.get("library_reusable_hits") or [], ["title", "image_count"])
            lines.append("")

    recommendations = report.get("recommendations") or []
    if recommendations:
        lines.extend(["## 后续建议", ""])
        for item in recommendations:
            lines.append(f"- [{item.get('priority')}] {item.get('message')}")
        lines.append("")
    return "\n".join(lines)


def _review_chapter(
    chapter: dict[str, Any],
    package: dict[str, Any],
    material_library: dict[str, Any] | None,
) -> dict[str, Any]:
    section_reviews = [
        _review_section(section, package, material_library)
        for section in chapter.get("sections") or []
        if isinstance(section, dict)
    ]
    return {
        "unit_id": chapter.get("unit_id"),
        "chapter_path": chapter.get("chapter_path") or [],
        "section_reviews": section_reviews,
        "metrics": _chapter_metrics(section_reviews),
    }


def _review_section(
    section: dict[str, Any],
    package: dict[str, Any],
    material_library: dict[str, Any] | None,
) -> dict[str, Any]:
    heading = str(section.get("heading") or "")
    topics = _heading_topics(heading)
    used_images = _section_image_blocks(section)
    candidate_images = _matching_candidates(heading, _candidate_images(package))
    candidate_groups = _matching_candidates(heading, _candidate_groups(package))
    package_material_hits = _matching_candidates(heading, _package_material_summaries(package))
    library_hits = _matching_library_hits(heading, material_library)
    library_reusable_hits = [hit for hit in library_hits if hit.get("auto_reuse_feasible")]
    issues = _section_issues(
        heading=heading,
        topics=topics,
        used_images=used_images,
        candidate_images=candidate_images,
        candidate_groups=candidate_groups,
        package_material_hits=package_material_hits,
        library_reusable_hits=library_reusable_hits,
    )
    return {
        "heading": heading,
        "level": section.get("level"),
        "topics": topics,
        "image_preferred": bool(topics),
        "used_image_count": len(used_images),
        "candidate_image_count": len(candidate_images),
        "candidate_group_count": len(candidate_groups),
        "package_material_hit_count": len(package_material_hits),
        "library_hit_count": len(library_hits),
        "library_reusable_hit_count": len(library_reusable_hits),
        "candidate_images": candidate_images[:8],
        "candidate_groups": candidate_groups[:8],
        "package_material_hits": package_material_hits[:8],
        "library_reusable_hits": library_reusable_hits[:8],
        "issues": issues,
        "conclusion": _section_conclusion(used_images, candidate_images, candidate_groups, library_reusable_hits, issues),
    }


def _section_issues(
    *,
    heading: str,
    topics: list[str],
    used_images: list[dict[str, Any]],
    candidate_images: list[dict[str, Any]],
    candidate_groups: list[dict[str, Any]],
    package_material_hits: list[dict[str, Any]],
    library_reusable_hits: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if not topics:
        return []
    issues: list[dict[str, str]] = []
    used_count = len(used_images)
    candidate_count = len(candidate_images) + len(candidate_groups)
    if used_count == 0 and candidate_count > 0:
        issues.append(_issue("medium", "candidate_not_used", "候选池已有匹配图片或套图，但当前小节未使用图片，需检查自动插图匹配或图文锚定规则。"))
    if used_count == 0 and candidate_count == 0 and package_material_hits:
        issues.append(_issue("medium", "matched_material_not_in_candidate_pool", "当前输入包命中了含图素材摘要，但候选图片池没有形成可用图片。"))
    if used_count == 0 and candidate_count == 0 and not package_material_hits and library_reusable_hits:
        issues.append(_issue("high", "candidate_pool_miss", "优秀标书素材库存在相关可用图片素材，但当前章节输入包未召回到候选池。"))
    if used_count > 0 and used_count <= 1 and _rich_image_topic(heading) and (len(library_reusable_hits) >= 3 or candidate_count >= 6):
        issues.append(_issue("medium", "image_usage_too_conservative", "该小节属于适合多图或节点图的工艺章节，当前图片使用偏保守，建议复核是否扩充套图或节点图。"))
    return issues


def _section_conclusion(
    used_images: list[dict[str, Any]],
    candidate_images: list[dict[str, Any]],
    candidate_groups: list[dict[str, Any]],
    library_reusable_hits: list[dict[str, Any]],
    issues: list[dict[str, str]],
) -> str:
    if any(issue.get("severity") == "high" for issue in issues):
        return "需修复召回"
    if any(issue.get("severity") == "medium" for issue in issues):
        return "建议调整"
    if used_images:
        return "召回与使用基本合理"
    if candidate_images or candidate_groups or library_reusable_hits:
        return "有素材未使用"
    return "未发现可用图片素材"


def _summary(chapter_reviews: list[dict[str, Any]]) -> dict[str, int]:
    sections = [section for chapter in chapter_reviews for section in chapter.get("section_reviews") or []]
    issues = [issue for section in sections for issue in section.get("issues") or []]
    return {
        "section_count": len(sections),
        "image_preferred_section_count": sum(1 for section in sections if section.get("image_preferred")),
        "used_image_count": sum(int(section.get("used_image_count") or 0) for section in sections),
        "matched_candidate_image_count": sum(int(section.get("candidate_image_count") or 0) for section in sections),
        "matched_candidate_group_count": sum(int(section.get("candidate_group_count") or 0) for section in sections),
        "matched_library_material_count": sum(int(section.get("library_reusable_hit_count") or 0) for section in sections),
        "candidate_pool_miss_section_count": sum(_has_issue(section, "candidate_pool_miss") for section in sections),
        "candidate_unused_section_count": sum(_has_issue(section, "candidate_not_used") for section in sections),
        "low_usage_section_count": sum(_has_issue(section, "image_usage_too_conservative") for section in sections),
        "high_risk_count": sum(1 for issue in issues if issue.get("severity") == "high"),
        "medium_risk_count": sum(1 for issue in issues if issue.get("severity") == "medium"),
    }


def _chapter_metrics(section_reviews: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "section_count": len(section_reviews),
        "used_image_count": sum(int(section.get("used_image_count") or 0) for section in section_reviews),
        "issue_count": sum(len(section.get("issues") or []) for section in section_reviews),
    }


def _recommendations(summary: dict[str, int], chapter_reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    if summary.get("candidate_pool_miss_section_count", 0):
        sections = _sections_with_issue(chapter_reviews, "candidate_pool_miss")
        recommendations.append(
            {
                "priority": "high",
                "message": "存在素材库有图但输入包未召回的小节，优先增强子标题检索或全库兜底召回：" + "、".join(sections[:8]),
            }
        )
    if summary.get("candidate_unused_section_count", 0):
        sections = _sections_with_issue(chapter_reviews, "candidate_not_used")
        recommendations.append(
            {
                "priority": "medium",
                "message": "存在候选池有图但正文未使用的小节，建议检查自动插图匹配阈值、每小节容量和图文锚定：" + "、".join(sections[:8]),
            }
        )
    if summary.get("low_usage_section_count", 0):
        sections = _sections_with_issue(chapter_reviews, "image_usage_too_conservative")
        recommendations.append(
            {
                "priority": "medium",
                "message": "存在图片使用偏保守的小节，可考虑补充完整套图或关键节点图：" + "、".join(sections[:8]),
            }
        )
    if not recommendations:
        recommendations.append({"priority": "low", "message": "未发现明显图片素材召回问题，可继续检查 Word 版式和图文位置。"})
    return recommendations


def _candidate_images(package: dict[str, Any]) -> list[dict[str, Any]]:
    return _dedupe_items([*(package.get("image_candidate_pool") or []), *(package.get("image_candidates") or [])])


def _candidate_groups(package: dict[str, Any]) -> list[dict[str, Any]]:
    return _dedupe_items([*(package.get("image_group_candidate_pool") or []), *(package.get("image_group_candidates") or [])])


def _package_material_summaries(package: dict[str, Any]) -> list[dict[str, Any]]:
    summary = package.get("material_retrieval_summary") or {}
    materials = list(summary.get("image_group_summary") or [])
    for material in package.get("excellent_bid_references") or []:
        if isinstance(material, dict):
            materials.append(material)
    return _dedupe_items(materials, key_fields=("material_slice_id", "ref_id", "title"))


def _matching_candidates(heading: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in items:
        match = _match_item(heading, item)
        if match["score"] < MIN_MATCH_SCORE:
            continue
        matches.append({**_candidate_preview(item), **match})
    matches.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("material_slice_id") or ""), str(item.get("image_id") or "")))
    return matches


def _matching_library_hits(heading: str, material_library: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not material_library:
        return []
    hits: list[dict[str, Any]] = []
    for slice_ in material_library.get("slices") or []:
        if not isinstance(slice_, dict) or int(slice_.get("image_count") or 0) <= 0:
            continue
        match = _match_item(heading, slice_, library_slice=True)
        if match["score"] < MIN_MATCH_SCORE:
            continue
        hits.append(
            {
                "material_slice_id": slice_.get("material_slice_id"),
                "title": slice_.get("title") or slice_.get("clean_title"),
                "section_path": slice_.get("section_path") or [],
                "image_count": slice_.get("image_count") or 0,
                "image_group_count": slice_.get("image_group_count") or 0,
                "material_quality": slice_.get("material_quality"),
                "reuse_level": slice_.get("reuse_level"),
                "project_specific_risk": slice_.get("project_specific_risk"),
                "primary_material_source": slice_.get("primary_material_source"),
                "auto_reuse_feasible": _auto_reuse_feasible(slice_),
                **match,
            }
        )
    hits.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("material_slice_id") or "")))
    return hits[:20]


def _match_item(heading: str, item: dict[str, Any], *, library_slice: bool = False) -> dict[str, Any]:
    topics = _heading_topics(heading)
    if not topics:
        return {"score": 0.0, "matched_topics": [], "matched_terms": [], "match_reason": ""}
    strong_text = _strong_match_text(item, library_slice=library_slice)
    nearby_text = str(item.get("nearby_text") or "")
    matched_topics: list[str] = []
    matched_terms: list[str] = []
    score = 0.0
    for topic in topics:
        rule = TOPIC_RULES[topic]
        if any(term in strong_text for term in rule["excluded_terms"]):
            continue
        strong_hits = [term for term in rule["strong_terms"] if term in strong_text]
        anchor_hits = [term for term in rule["anchor_terms"] if term in strong_text]
        weak_hits = [term for term in rule["terms"] if term in nearby_text]
        if strong_hits and _strong_hits_are_specific(topic, strong_hits, anchor_hits):
            matched_topics.append(topic)
            matched_terms.extend(strong_hits)
            score += len(set(strong_hits)) * 2.0
        elif len(set(weak_hits)) >= 2:
            matched_topics.append(topic)
            matched_terms.extend(weak_hits)
            score += len(set(weak_hits)) * 0.5
    if not matched_topics:
        return {"score": 0.0, "matched_topics": [], "matched_terms": [], "match_reason": ""}
    if _project_specific_image(item):
        score -= 1.5
    if item.get("image_group_id") or item.get("image_group_count") or item.get("member_count"):
        score += 0.4
    confidence = float(item.get("semantic_confidence") or 0)
    if confidence >= 0.8:
        score += 0.4
    elif confidence and confidence < 0.55:
        score -= 0.3
    score = round(max(score, 0.0), 4)
    return {
        "score": score,
        "matched_topics": sorted(set(matched_topics)),
        "matched_terms": sorted(set(matched_terms)),
        "match_reason": "、".join(sorted(set(matched_terms))[:6]),
    }


def _strong_hits_are_specific(topic: str, strong_hits: list[str], anchor_hits: list[str]) -> bool:
    if len(set(strong_hits)) >= 2:
        return True
    if topic == "混凝土":
        return bool(anchor_hits) and "混凝土" not in set(strong_hits)
    if topic == "测量":
        specific_hits = {"控制网", "控制点", "内控点", "引测", "铅直仪", "监测"}
        return bool(set(strong_hits) & specific_hits)
    if topic == "模板":
        return bool(anchor_hits) and not (set(strong_hits) <= {"支撑"})
    return bool(anchor_hits)


def _heading_topics(heading: str) -> list[str]:
    return [
        topic
        for topic, rule in TOPIC_RULES.items()
        if any(term in heading for term in rule["terms"])
    ]


def _rich_image_topic(heading: str) -> bool:
    topics = set(_heading_topics(heading))
    return bool(topics & {"测量", "土方基坑", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体", "后浇带"})


def _strong_match_text(item: dict[str, Any], *, library_slice: bool = False) -> str:
    if library_slice:
        parts = [
            item.get("title"),
            item.get("clean_title"),
            " ".join(str(part) for part in item.get("section_path") or []),
            item.get("search_text"),
        ]
    else:
        parts = [
            item.get("caption"),
            item.get("group_title"),
            item.get("semantic_text"),
            item.get("group_semantic_text"),
            item.get("material_title"),
            " ".join(str(part) for part in item.get("source_section_path") or item.get("section_path") or []),
            " ".join(str(tag) for tag in item.get("tags") or []),
        ]
    return " ".join(str(part) for part in parts if part)


def _candidate_preview(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_id": item.get("image_id"),
        "image_asset_id": item.get("image_asset_id"),
        "image_group_id": item.get("image_group_id"),
        "material_slice_id": item.get("material_slice_id"),
        "caption": item.get("caption") or item.get("semantic_text") or item.get("group_title") or item.get("title"),
        "group_title": item.get("group_title") or item.get("semantic_text") or item.get("caption") or item.get("title"),
        "member_count": item.get("member_count") or item.get("group_member_count") or item.get("image_count"),
        "source_section_path": item.get("source_section_path") or item.get("section_path") or [],
        "material_quality": item.get("material_quality"),
        "reuse_level": item.get("reuse_level") or item.get("use_policy"),
        "risk_level": item.get("risk_level") or item.get("project_specific_risk"),
    }


def _section_image_blocks(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in section.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "image_ref"
    ]


def _auto_reuse_feasible(slice_: dict[str, Any]) -> bool:
    quality = str(slice_.get("material_quality") or "")
    if quality not in REUSABLE_MATERIAL_QUALITIES:
        return False
    if str(slice_.get("reuse_level") or "") == "manual_review":
        return False
    if str(slice_.get("project_specific_risk") or "") == "high":
        return False
    return True


def _project_specific_image(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(part)
        for part in [
            item.get("caption"),
            item.get("group_title"),
            item.get("semantic_text"),
            item.get("nearby_text"),
            " ".join(str(part) for part in item.get("source_section_path") or item.get("section_path") or []),
        ]
        if part
    )
    return any(term in text for term in PROJECT_SPECIFIC_TERMS)


def _dedupe_items(items: list[dict[str, Any]], key_fields: tuple[str, ...] = ("image_group_id", "image_asset_id", "image_id", "material_slice_id", "title")) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = "|".join(str(item.get(field) or "") for field in key_fields if item.get(field))
        if not key:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _has_issue(section: dict[str, Any], issue_type: str) -> int:
    return int(any(issue.get("type") == issue_type for issue in section.get("issues") or []))


def _sections_with_issue(chapter_reviews: list[dict[str, Any]], issue_type: str) -> list[str]:
    return [
        str(section.get("heading"))
        for chapter in chapter_reviews
        for section in chapter.get("section_reviews") or []
        if _has_issue(section, issue_type)
    ]


def _append_preview_table(lines: list[str], title: str, rows: list[dict[str, Any]], fields: list[str]) -> None:
    if not rows:
        return
    lines.append("")
    lines.append(f"**{title}**")
    lines.append("")
    lines.append("| 序号 | 名称 | 匹配 | 来源 |")
    lines.append("|---:|---|---|---|")
    for index, row in enumerate(rows[:5], start=1):
        name = row.get(fields[0]) or row.get("caption") or row.get("group_title") or row.get("title")
        extra = row.get(fields[1]) if len(fields) > 1 else ""
        if fields[1] == "image_count":
            extra = f"{extra} 张"
        source = " > ".join(str(part) for part in row.get("source_section_path") or row.get("section_path") or [])
        lines.append(f"| {index} | {_cell(name)} | {_cell(row.get('match_reason') or extra)} | {_cell(source)} |")


def _issue(severity: str, type_: str, message: str) -> dict[str, str]:
    return {"severity": severity, "type": type_, "message": message}


def _issue_text(issue: dict[str, Any]) -> str:
    return f"[{issue.get('severity')}] {issue.get('message')}"


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
