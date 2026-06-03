"""从图片 staging 诊断结果生成候选入库包。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "excellent_bid_image_promotion_v1"


def build_excellent_bid_image_promotion_package(staging_result: dict[str, Any]) -> dict[str, Any]:
    """把 staging 结果整理为可人工确认的图片入库候选包。"""

    images = [image for image in staging_result.get("images") or [] if isinstance(image, dict)]
    groups = [group for group in staging_result.get("image_groups") or [] if isinstance(group, dict)]
    image_by_id = {str(image.get("image_asset_id") or ""): image for image in images}

    promotion_groups = _promotion_groups(groups, image_by_id)
    grouped_image_ids = {
        str(image_id)
        for group in promotion_groups
        for image_id in group.get("image_asset_ids") or []
        if image_id
    }
    promotion_images = _promotion_single_images(images, grouped_image_ids)
    review_items = _review_items(images, groups)
    skipped_items = _skipped_items(images, grouped_image_ids)
    section_summary = _promotion_section_summary(promotion_images, promotion_groups)

    summary = {
        "source_image_count": len(images),
        "source_group_count": len(groups),
        "promote_image_count": len(promotion_images) + sum(
            int(group.get("member_count") or 0) for group in promotion_groups
        ),
        "promote_single_image_count": len(promotion_images),
        "promote_group_count": len(promotion_groups),
        "review_item_count": len(review_items),
        "skipped_item_count": len(skipped_items),
        "decision_counts": dict(Counter(str(image.get("decision") or "") for image in images)),
        "review_reason_counts": dict(Counter(str(item.get("reason_type") or "") for item in review_items)),
        "skipped_reason_counts": dict(Counter(str(item.get("reason_type") or "") for item in skipped_items)),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "staging_library_id": staging_result.get("staging_library_id") or "",
        "generated_from": staging_result.get("schema_version") or "",
        "summary": summary,
        "promote_groups": promotion_groups,
        "promote_images": promotion_images,
        "review_items": review_items,
        "skipped_items": skipped_items,
        "section_summary": section_summary,
        "warnings": _promotion_warnings(staging_result, promotion_groups, review_items),
    }


def build_excellent_bid_image_promotion_package_from_file(path: str | Path) -> dict[str, Any]:
    return build_excellent_bid_image_promotion_package(_read_json(path))


def write_excellent_bid_image_promotion_outputs(
    result: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_excellent_bid_image_promotion_report(result), encoding="utf-8")


def render_excellent_bid_image_promotion_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# 优秀标书图片候选入库包",
        "",
        f"- staging 素材库 ID：`{result.get('staging_library_id') or ''}`",
        f"- 来源图片数：{summary.get('source_image_count', 0)}",
        f"- 来源套图组数：{summary.get('source_group_count', 0)}",
        f"- 候选入库图片数：{summary.get('promote_image_count', 0)}",
        f"- 候选入库套图组数：{summary.get('promote_group_count', 0)}",
        f"- 候选入库单图数：{summary.get('promote_single_image_count', 0)}",
        f"- 人工复核项数：{summary.get('review_item_count', 0)}",
        f"- 自动跳过项数：{summary.get('skipped_item_count', 0)}",
        f"- staging 决策分布：{_format_counter(summary.get('decision_counts'))}",
        "",
        "## 候选入库套图",
        "",
    ]

    groups = result.get("promote_groups") or []
    if groups:
        for group in groups[:120]:
            lines.append(
                f"- {group.get('promotion_id')}: {group.get('member_count')} 张，"
                f"title={group.get('group_title') or '-'}，path={' > '.join(group.get('section_path') or [])}"
            )
            captions = group.get("captions") or []
            if captions:
                lines.append(f"  - captions：{'；'.join(captions[:10])}")
    else:
        lines.append("- 暂无候选入库套图。")

    lines.extend(["", "## 候选入库单图", ""])
    images = result.get("promote_images") or []
    if images:
        for image in images[:120]:
            lines.append(
                f"- {image.get('promotion_id')}: {image.get('caption_actual') or image.get('semantic_text') or '-'}，"
                f"path={' > '.join(image.get('section_path') or [])}"
            )
    else:
        lines.append("- 暂无候选入库单图。")

    lines.extend(["", "## 人工复核项", ""])
    review_items = result.get("review_items") or []
    if review_items:
        for item in review_items[:160]:
            lines.append(
                f"- {item.get('item_id')}: type={item.get('item_type')}, reason={item.get('reason_type')}，"
                f"title={item.get('title') or '-'}"
            )
            if item.get("note"):
                lines.append(f"  - note：{item.get('note')}")
            if item.get("section_path"):
                lines.append(f"  - path：{' > '.join(item.get('section_path') or [])}")
    else:
        lines.append("- 暂无人工复核项。")

    lines.extend(["", "## 章节分布", ""])
    section_summary = result.get("section_summary") or []
    if section_summary:
        for section in section_summary[:80]:
            lines.append(
                f"- {' > '.join(section.get('section_path') or []) or '(未归属)'}："
                f"images={section.get('image_count')}, groups={section.get('group_count')}"
            )
    else:
        lines.append("- 暂无章节分布。")

    if result.get("warnings"):
        lines.extend(["", "## 警告", ""])
        for warning in result["warnings"]:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _promotion_groups(groups: list[dict[str, Any]], image_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        if group.get("decision") != "candidate_reuse_group":
            continue
        member_ids = [str(image_id) for image_id in group.get("image_asset_ids") or [] if str(image_id)]
        members = [image_by_id.get(image_id) for image_id in member_ids]
        members = [member for member in members if member]
        if len(members) < 2:
            continue
        if any(member.get("decision") != "candidate_reuse" for member in members):
            continue
        if _has_section_path_quality_risk(group.get("section_path") or []):
            continue
        result.append(
            {
                "promotion_id": f"PG{len(result) + 1:04d}",
                "source_group_id": group.get("image_group_id"),
                "group_title": group.get("group_title") or group.get("semantic_text") or "",
                "semantic_text": group.get("semantic_text") or "",
                "semantic_confidence": group.get("semantic_confidence") or 0,
                "section_path": group.get("section_path") or [],
                "table_index": group.get("table_index"),
                "start_row_index": group.get("start_row_index"),
                "end_row_index": group.get("end_row_index"),
                "member_count": len(members),
                "image_asset_ids": member_ids,
                "captions": group.get("captions") or [],
                "tags": group.get("tags") or [],
                "reuse_level": "candidate_reuse",
                "project_specific_risk": group.get("project_specific_risk") or "low",
                "must_keep_together": True,
                "members": [_promotion_image_payload(member) for member in members],
            }
        )
    return result


def _promotion_single_images(images: list[dict[str, Any]], grouped_image_ids: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for image in images:
        image_id = str(image.get("image_asset_id") or "")
        if image_id in grouped_image_ids:
            continue
        if image.get("image_group_id"):
            continue
        if image.get("decision") != "candidate_reuse":
            continue
        if _has_section_path_quality_risk(image.get("section_path") or []):
            continue
        payload = _promotion_image_payload(image)
        payload["promotion_id"] = f"PI{len(result) + 1:04d}"
        result.append(payload)
    return result


def _promotion_image_payload(image: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "image_asset_id",
        "image_id",
        "source_id",
        "material_slice_id",
        "title",
        "section_path",
        "part_name",
        "caption_actual",
        "semantic_text",
        "semantic_confidence",
        "semantic_sources",
        "nearby_text",
        "tags",
        "sha256",
        "perceptual_hash",
        "image_width",
        "image_height",
        "image_format",
        "source_docx_path",
        "table_index",
        "row_index",
        "cell_index",
    ]
    payload = {key: image.get(key) for key in keys}
    payload["reuse_level"] = "candidate_reuse"
    payload["project_specific_risk"] = image.get("project_specific_risk") or "low"
    return payload


def _review_items(images: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        reason = _group_review_reason(group)
        if not reason:
            continue
        result.append(
            {
                "item_id": f"RG{len(result) + 1:04d}",
                "item_type": "group",
                "reason_type": reason,
                "source_id": group.get("image_group_id"),
                "title": group.get("group_title") or group.get("semantic_text") or "",
                "section_path": group.get("section_path") or [],
                "member_count": group.get("member_count") or 0,
                "image_asset_ids": group.get("image_asset_ids") or [],
                "note": _review_note_for_group(group),
            }
        )

    grouped_ids = {
        str(image_id)
        for group in groups
        for image_id in group.get("image_asset_ids") or []
        if image_id
    }
    for image in images:
        image_id = str(image.get("image_asset_id") or "")
        if image_id in grouped_ids and image.get("decision") == "candidate_reuse":
            continue
        reason = _image_review_reason(image)
        if not reason:
            continue
        result.append(
            {
                "item_id": f"RI{len(result) + 1:04d}",
                "item_type": "image",
                "reason_type": reason,
                "source_id": image_id,
                "title": image.get("caption_actual") or image.get("semantic_text") or image.get("title") or "",
                "section_path": image.get("section_path") or [],
                "member_count": 1,
                "image_asset_ids": [image_id],
                "note": _review_note_for_image(image),
            }
        )
    return result


def _skipped_items(images: list[dict[str, Any]], grouped_image_ids: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for image in images:
        image_id = str(image.get("image_asset_id") or "")
        if image_id in grouped_image_ids:
            continue
        reason = _skip_reason(image)
        if not reason:
            continue
        result.append(
            {
                "item_id": f"SK{len(result) + 1:04d}",
                "item_type": "image",
                "reason_type": reason,
                "source_id": image_id,
                "title": image.get("caption_actual") or image.get("semantic_text") or image.get("title") or "",
                "section_path": image.get("section_path") or [],
                "note": _skip_note(image),
            }
        )
    return result


def _group_review_reason(group: dict[str, Any]) -> str:
    decision = str(group.get("decision") or "")
    if decision == "suspected_duplicate_group":
        return "suspected_duplicate_group"
    if decision == "manual_review_group":
        return "manual_review_group"
    return ""


def _image_review_reason(image: dict[str, Any]) -> str:
    decision = str(image.get("decision") or "")
    if decision in {"manual_review", "project_specific_manual_review", "suspected_duplicate_existing"}:
        return decision
    if decision == "candidate_reuse" and _has_section_path_quality_risk(image.get("section_path") or []):
        return "section_path_quality_risk"
    return ""


def _skip_reason(image: dict[str, Any]) -> str:
    decision = str(image.get("decision") or "")
    if decision in {"duplicate_existing", "internal_duplicate"}:
        return decision
    if image.get("image_group_id") and decision == "candidate_reuse":
        return "covered_by_group"
    return ""


def _has_section_path_quality_risk(section_path: list[Any]) -> bool:
    for part in section_path:
        text = str(part or "").strip()
        if len(text) > 70:
            return True
        if len(text) > 42 and any(mark in text for mark in "，,。；;：:"):
            return True
    return False


def _review_note_for_group(group: dict[str, Any]) -> str:
    if group.get("decision") == "suspected_duplicate_group":
        return "套图疑似与正式库已有图片相似，建议人工确认是否合并。"
    if group.get("review_reason"):
        return str(group.get("review_reason"))
    return "套图中存在需要人工确认的图片。"


def _review_note_for_image(image: dict[str, Any]) -> str:
    reasons = image.get("decision_reasons") or []
    if reasons:
        return "；".join(str(reason) for reason in reasons)
    return str(image.get("review_reason") or "")


def _skip_note(image: dict[str, Any]) -> str:
    if image.get("decision") == "duplicate_existing":
        return "正式素材库已有相同图片，不重复入库。"
    if image.get("decision") == "internal_duplicate":
        return f"新标书内部重复，首图为 {image.get('internal_duplicate_of') or '-'}。"
    if image.get("image_group_id"):
        return f"已作为套图 {image.get('image_group_id')} 的成员整体入库。"
    return ""


def _promotion_section_summary(
    promotion_images: list[dict[str, Any]],
    promotion_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for image in promotion_images:
        key = " > ".join(image.get("section_path") or [])
        bucket = buckets.setdefault(key, {"section_path": image.get("section_path") or [], "image_count": 0, "group_count": 0})
        bucket["image_count"] += 1
    for group in promotion_groups:
        key = " > ".join(group.get("section_path") or [])
        bucket = buckets.setdefault(key, {"section_path": group.get("section_path") or [], "image_count": 0, "group_count": 0})
        bucket["group_count"] += 1
        bucket["image_count"] += int(group.get("member_count") or 0)
    return sorted(buckets.values(), key=lambda item: (-int(item["image_count"]), " > ".join(item["section_path"])))


def _promotion_warnings(
    staging_result: dict[str, Any],
    promotion_groups: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    missing_group_count = int((staging_result.get("summary") or {}).get("missing_group_candidate_count") or 0)
    if missing_group_count:
        warnings.append(f"staging 中仍有 {missing_group_count} 处疑似漏识别套图，建议复核后再正式入库。")
    if review_items:
        warnings.append(f"仍有 {len(review_items)} 个复核项未进入候选入库包。")
    incomplete_groups = [group for group in promotion_groups if not group.get("must_keep_together")]
    if incomplete_groups:
        warnings.append(f"{len(incomplete_groups)} 个候选套图未标记整体使用。")
    return warnings


def _format_counter(counter: Any) -> str:
    if not counter:
        return "-"
    return "，".join(f"{key}={value}" for key, value in sorted(dict(counter).items()))


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
