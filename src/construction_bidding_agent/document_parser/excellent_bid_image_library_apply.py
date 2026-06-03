"""将优秀标书图片候选入库包应用到正式素材库预览。"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "excellent_bid_image_library_apply_v1"
PROMOTION_SOURCE_TYPE = "docx_image_promotion"


def apply_excellent_bid_image_promotion(
    material_library: dict[str, Any],
    promotion_package: dict[str, Any],
    *,
    output_library_id: str | None = None,
    promotion_package_path: str | Path | None = None,
) -> dict[str, Any]:
    """生成合并候选图片后的正式素材库预览，不原地修改输入库。"""

    library = copy.deepcopy(material_library)
    library["library_id"] = output_library_id or f"{library.get('library_id') or 'excellent_bid_library'}_preview"

    existing_assets = [item for item in library.get("image_assets") or [] if isinstance(item, dict)]
    existing_hashes = {str(item.get("sha256") or "") for item in existing_assets if item.get("sha256")}
    existing_original_ids = {
        str(item.get("original_image_asset_id") or "")
        for item in existing_assets
        if item.get("original_image_asset_id")
    }
    existing_source_parts = _existing_source_part_keys(library)

    source_id_by_docx = _new_source_ids_by_docx(library, promotion_package)
    slice_id_by_key = _new_slice_ids_by_source_section(source_id_by_docx)
    image_id_by_original: dict[str, str] = {}
    promoted_assets: list[dict[str, Any]] = []
    promoted_groups: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(existing_hashes)
    seen_original_ids: set[str] = set(existing_original_ids)
    seen_source_parts: set[tuple[str, str]] = set(existing_source_parts)

    for group in promotion_package.get("promote_groups") or []:
        if not isinstance(group, dict):
            continue
        members = [member for member in group.get("members") or [] if isinstance(member, dict)]
        duplicate_reason = _group_duplicate_reason(members, seen_hashes, seen_original_ids, seen_source_parts)
        if duplicate_reason:
            skipped_items.append(
                {
                    "item_type": "group",
                    "source_id": group.get("source_group_id") or group.get("promotion_id"),
                    "reason_type": duplicate_reason,
                    "title": group.get("group_title") or group.get("semantic_text") or "",
                    "member_count": len(members),
                }
            )
            continue
        if len(members) < 2:
            skipped_items.append(
                {
                    "item_type": "group",
                    "source_id": group.get("source_group_id") or group.get("promotion_id"),
                    "reason_type": "invalid_group_member_count",
                    "title": group.get("group_title") or "",
                    "member_count": len(members),
                }
            )
            continue

        first_member = members[0]
        source_id = _source_id_for_image(first_member, source_id_by_docx)
        section_path = _section_path(group.get("section_path") or first_member.get("section_path"))
        material_slice_id = _slice_id_for(source_id, section_path, slice_id_by_key)
        group_id = f"{source_id}-PGRP{len(promoted_groups) + 1:04d}"
        group_asset_ids: list[str] = []
        group_image_ids: list[str] = []
        member_count = len(members)

        for member_index, member in enumerate(members, start=1):
            new_asset = _promoted_image_asset(
                member,
                source_id=source_id,
                material_slice_id=material_slice_id,
                image_asset_id=f"{source_id}-PIMG{len(promoted_assets) + 1:06d}",
                image_group_id=group_id,
                group=group,
                group_member_index=member_index,
                group_member_count=member_count,
            )
            promoted_assets.append(new_asset)
            group_asset_ids.append(new_asset["image_asset_id"])
            group_image_ids.append(new_asset["image_id"])
            _mark_seen(member, new_asset, seen_hashes, seen_original_ids, seen_source_parts)
            original_id = str(member.get("image_asset_id") or "")
            if original_id:
                image_id_by_original[original_id] = new_asset["image_asset_id"]

        promoted_groups.append(
            _promoted_image_group(
                group,
                source_id=source_id,
                material_slice_id=material_slice_id,
                image_group_id=group_id,
                image_asset_ids=group_asset_ids,
                image_ids=group_image_ids,
            )
        )

    grouped_original_ids = {
        str(image_id)
        for group in promotion_package.get("promote_groups") or []
        if isinstance(group, dict)
        for image_id in group.get("image_asset_ids") or []
        if image_id
    }
    for image in promotion_package.get("promote_images") or []:
        if not isinstance(image, dict):
            continue
        original_id = str(image.get("image_asset_id") or "")
        if original_id in grouped_original_ids or original_id in image_id_by_original:
            continue
        duplicate_reason = _image_duplicate_reason(image, seen_hashes, seen_original_ids, seen_source_parts)
        if duplicate_reason:
            skipped_items.append(
                {
                    "item_type": "image",
                    "source_id": original_id or image.get("promotion_id"),
                    "reason_type": duplicate_reason,
                    "title": image.get("caption_actual") or image.get("semantic_text") or image.get("title") or "",
                }
            )
            continue
        source_id = _source_id_for_image(image, source_id_by_docx)
        section_path = _section_path(image.get("section_path"))
        material_slice_id = _slice_id_for(source_id, section_path, slice_id_by_key)
        new_asset = _promoted_image_asset(
            image,
            source_id=source_id,
            material_slice_id=material_slice_id,
            image_asset_id=f"{source_id}-PIMG{len(promoted_assets) + 1:06d}",
        )
        promoted_assets.append(new_asset)
        _mark_seen(image, new_asset, seen_hashes, seen_original_ids, seen_source_parts)
        if original_id:
            image_id_by_original[original_id] = new_asset["image_asset_id"]

    promoted_slices = _promoted_slices(promoted_assets, promoted_groups, slice_id_by_key)
    promoted_sources = _promoted_sources(
        source_id_by_docx,
        promoted_slices,
        promoted_assets,
        promotion_package,
        promotion_package_path=promotion_package_path,
    )

    library.setdefault("sources", []).extend(promoted_sources)
    library.setdefault("slices", []).extend(promoted_slices)
    library.setdefault("image_assets", []).extend(promoted_assets)
    library.setdefault("image_groups", []).extend(promoted_groups)
    _refresh_library_counts(library)

    summary = {
        "input_source_count": int(material_library.get("source_count") or len(material_library.get("sources") or [])),
        "input_slice_count": int(material_library.get("slice_count") or len(material_library.get("slices") or [])),
        "input_image_asset_count": int(
            material_library.get("image_asset_count") or len(material_library.get("image_assets") or [])
        ),
        "input_image_group_count": int(
            material_library.get("image_group_count") or len(material_library.get("image_groups") or [])
        ),
        "promoted_source_count": len(promoted_sources),
        "promoted_slice_count": len(promoted_slices),
        "promoted_image_asset_count": len(promoted_assets),
        "promoted_image_group_count": len(promoted_groups),
        "skipped_item_count": len(skipped_items),
        "skipped_reason_counts": dict(Counter(str(item.get("reason_type") or "") for item in skipped_items)),
        "output_source_count": library["source_count"],
        "output_slice_count": library["slice_count"],
        "output_image_asset_count": library["image_asset_count"],
        "output_image_group_count": library["image_group_count"],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "input_library_id": material_library.get("library_id") or "",
        "output_library_id": library.get("library_id") or "",
        "promotion_schema_version": promotion_package.get("schema_version") or "",
        "promotion_staging_library_id": promotion_package.get("staging_library_id") or "",
        "summary": summary,
        "applied_sources": promoted_sources,
        "applied_slices": promoted_slices,
        "applied_image_groups": promoted_groups,
        "skipped_items": skipped_items,
        "library": library,
        "warnings": _apply_warnings(promotion_package, skipped_items),
    }


def apply_excellent_bid_image_promotion_from_files(
    material_library_json: str | Path,
    promotion_package_json: str | Path,
    *,
    output_library_id: str | None = None,
) -> dict[str, Any]:
    return apply_excellent_bid_image_promotion(
        _read_json(material_library_json),
        _read_json(promotion_package_json),
        output_library_id=output_library_id,
        promotion_package_path=promotion_package_json,
    )


def write_excellent_bid_image_library_apply_outputs(
    result: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result["library"], ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_excellent_bid_image_library_apply_report(result), encoding="utf-8")


def render_excellent_bid_image_library_apply_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# 优秀标书图片正式素材库预览报告",
        "",
        f"- 原素材库：`{result.get('input_library_id') or ''}`",
        f"- 预览素材库：`{result.get('output_library_id') or ''}`",
        f"- 候选来源：`{result.get('promotion_staging_library_id') or ''}`",
        f"- 新增来源数：{summary.get('promoted_source_count', 0)}",
        f"- 新增图片切片数：{summary.get('promoted_slice_count', 0)}",
        f"- 新增图片资产数：{summary.get('promoted_image_asset_count', 0)}",
        f"- 新增套图数：{summary.get('promoted_image_group_count', 0)}",
        f"- 跳过项数：{summary.get('skipped_item_count', 0)}",
        f"- 跳过原因分布：{_format_counter(summary.get('skipped_reason_counts'))}",
        f"- 预览库图片资产总数：{summary.get('output_image_asset_count', 0)}",
        f"- 预览库套图总数：{summary.get('output_image_group_count', 0)}",
        "",
        "## 新增来源",
        "",
    ]
    for source in result.get("applied_sources") or []:
        lines.append(
            f"- {source.get('source_id')}: {source.get('source_name')}，"
            f"切片 {source.get('slice_count')}，图片 {source.get('image_count')}"
        )
        if source.get("source_paths"):
            lines.append(f"  - 文件：{'; '.join(source.get('source_paths') or [])}")

    lines.extend(["", "## 新增图片切片", ""])
    for slice_ in (result.get("applied_slices") or [])[:120]:
        lines.append(
            f"- {slice_.get('material_slice_id')}: 图片 {slice_.get('image_count')}，"
            f"套图 {slice_.get('image_group_count', 0)}，路径：{' > '.join(slice_.get('section_path') or [])}"
        )

    lines.extend(["", "## 新增套图", ""])
    groups = result.get("applied_image_groups") or []
    if groups:
        for group in groups[:120]:
            lines.append(
                f"- {group.get('image_group_id')}: {group.get('member_count')} 张，"
                f"{group.get('group_title') or group.get('semantic_text') or '-'}，"
                f"路径：{' > '.join(group.get('section_path') or [])}"
            )
            captions = group.get("captions") or []
            if captions:
                lines.append(f"  - 题注：{'；'.join(captions[:8])}")
    else:
        lines.append("- 无新增套图。")

    lines.extend(["", "## 自动跳过项", ""])
    skipped_items = result.get("skipped_items") or []
    if skipped_items:
        for item in skipped_items[:120]:
            lines.append(
                f"- {item.get('item_type')}: {item.get('source_id')}，"
                f"reason={item.get('reason_type')}，title={item.get('title') or '-'}"
            )
    else:
        lines.append("- 无自动跳过项。")

    if result.get("warnings"):
        lines.extend(["", "## 警告", ""])
        for warning in result["warnings"]:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _new_source_ids_by_docx(
    library: dict[str, Any],
    promotion_package: dict[str, Any],
) -> dict[str, str]:
    paths = sorted(
        {
            _normalize_path(image.get("source_docx_path"))
            for image in _iter_promoted_images(promotion_package)
            if _normalize_path(image.get("source_docx_path"))
        }
    )
    if not paths:
        paths = [""]
    next_number = _next_source_number(library)
    return {path: f"SRC{next_number + index:04d}" for index, path in enumerate(paths)}


def _new_slice_ids_by_source_section(source_id_by_docx: dict[str, str]) -> dict[tuple[str, tuple[str, ...]], str]:
    return {}


def _promoted_sources(
    source_id_by_docx: dict[str, str],
    promoted_slices: list[dict[str, Any]],
    promoted_assets: list[dict[str, Any]],
    promotion_package: dict[str, Any],
    *,
    promotion_package_path: str | Path | None,
) -> list[dict[str, Any]]:
    slices_by_source = Counter(str(item.get("source_id") or "") for item in promoted_slices)
    images_by_source = Counter(str(item.get("source_id") or "") for item in promoted_assets)
    result: list[dict[str, Any]] = []
    for docx_path, source_id in sorted(source_id_by_docx.items(), key=lambda item: item[1]):
        if not images_by_source.get(source_id):
            continue
        source_name = Path(docx_path).stem if docx_path else (promotion_package.get("staging_library_id") or source_id)
        result.append(
            {
                "source_id": source_id,
                "source_name": source_name,
                "source_type": PROMOTION_SOURCE_TYPE,
                "source_index_path": str(promotion_package_path or ""),
                "source_paths": [docx_path] if docx_path else [],
                "source_schema_version": promotion_package.get("schema_version") or "",
                "slice_count": slices_by_source.get(source_id, 0),
                "table_count": 0,
                "image_count": images_by_source.get(source_id, 0),
                "matched_count": 0,
                "ambiguous_count": 0,
                "fallback_count": 0,
                "unmatched_count": 0,
                "warnings": [],
            }
        )
    return result


def _promoted_slices(
    promoted_assets: list[dict[str, Any]],
    promoted_groups: list[dict[str, Any]],
    slice_id_by_key: dict[tuple[str, tuple[str, ...]], str],
) -> list[dict[str, Any]]:
    assets_by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups_by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in promoted_assets:
        assets_by_slice[str(asset.get("material_slice_id") or "")].append(asset)
    for group in promoted_groups:
        groups_by_slice[str(group.get("material_slice_id") or "")].append(group)

    slice_lookup = {slice_id: (source_id, section_path) for (source_id, section_path), slice_id in slice_id_by_key.items()}
    result: list[dict[str, Any]] = []
    for slice_id in sorted(assets_by_slice):
        source_id, section_path_tuple = slice_lookup.get(slice_id, ("", tuple()))
        section_path = list(section_path_tuple)
        assets = assets_by_slice[slice_id]
        groups = groups_by_slice.get(slice_id, [])
        semantic_values = [
            str(value)
            for asset in assets
            for value in [
                asset.get("caption_actual"),
                asset.get("semantic_text"),
                asset.get("nearby_text"),
                " ".join(asset.get("tags") or []),
            ]
            if value
        ]
        title = section_path[-1] if section_path else "候选复用图片"
        avg_confidence = _average_confidence(assets, groups)
        result.append(
            {
                "material_slice_id": slice_id,
                "source_id": source_id,
                "source_type": PROMOTION_SOURCE_TYPE,
                "source_slice_id": slice_id,
                "title": title,
                "clean_title": _strip_heading_number(title),
                "number": None,
                "level": len(section_path) or None,
                "section_path": section_path,
                "section_key": _section_key(section_path),
                "search_text": " ".join([*section_path, *semantic_values]),
                "keywords": _keywords(" ".join([*section_path, *semantic_values])),
                "primary_material_source": "docx_image_promotion",
                "material_quality": "high" if avg_confidence >= 0.78 else "usable",
                "paragraph_count": 0,
                "paragraph_char_count": 0,
                "table_count": 0,
                "image_count": len(assets),
                "subtree_table_count": 0,
                "subtree_image_count": len(assets),
                "docx_table_count": 0,
                "docx_image_count": len(assets),
                "pdf_table_like_count": 0,
                "pdf_image_count": 0,
                "match_status": "image_promotion",
                "match_method": "promotion_package",
                "match_score": None,
                "confidence": avg_confidence,
                "reuse_level": "direct_reuse",
                "project_specific_risk": _max_risk([asset.get("project_specific_risk") for asset in assets]),
                "start_page": None,
                "end_page": None,
                "page_count": 0,
                "start_block_index": None,
                "end_block_index": None,
                "paragraphs": [],
                "tables": [],
                "image_bindings": [],
                "pdf_tables": [],
                "pdf_image_bindings": [],
                "image_group_count": len(groups),
                "promotion_applied": True,
            }
        )
    return result


def _promoted_image_asset(
    image: dict[str, Any],
    *,
    source_id: str,
    material_slice_id: str,
    image_asset_id: str,
    image_group_id: str | None = None,
    group: dict[str, Any] | None = None,
    group_member_index: int | None = None,
    group_member_count: int = 0,
) -> dict[str, Any]:
    section_path = _section_path((group or {}).get("section_path") or image.get("section_path"))
    caption = str(image.get("caption_actual") or image.get("semantic_text") or image.get("title") or "").strip()
    semantic_text = str(image.get("semantic_text") or caption)
    return {
        "image_asset_id": image_asset_id,
        "image_id": image_asset_id,
        "source_id": source_id,
        "source_type": PROMOTION_SOURCE_TYPE,
        "source_slice_id": material_slice_id,
        "material_slice_id": material_slice_id,
        "title": section_path[-1] if section_path else str(image.get("title") or "候选复用图片"),
        "section_path": section_path,
        "section_key": _section_key(section_path),
        "rel_id": f"promotion:{image.get('image_asset_id') or image.get('promotion_id') or image_asset_id}",
        "target": image.get("part_name") or "",
        "part_name": image.get("part_name") or "",
        "context": "promotion_image_asset",
        "table_index": image.get("table_index"),
        "row_index": image.get("row_index"),
        "cell_index": image.get("cell_index"),
        "image_group_id": image_group_id,
        "group_title": (group or {}).get("group_title") or "",
        "group_semantic_text": (group or {}).get("semantic_text") or "",
        "group_member_index": group_member_index,
        "group_member_count": group_member_count,
        "must_keep_with_group": bool(image_group_id),
        "caption_actual": caption,
        "caption_candidates": _caption_candidates(image, group),
        "semantic_sources": image.get("semantic_sources") or [],
        "semantic_text": semantic_text,
        "semantic_confidence": float(image.get("semantic_confidence") or 0),
        "nearby_text": image.get("nearby_text") or "",
        "cell_text": "",
        "row_text": "",
        "header_text": "",
        "previous_row_text": "",
        "previous_row_texts": [],
        "next_row_text": "",
        "previous_non_empty_cell_text": "",
        "next_non_empty_cell_text": "",
        "left_cell_text": "",
        "right_cell_text": "",
        "above_cell_text": "",
        "below_cell_text": "",
        "tags": image.get("tags") or [],
        "reuse_level": "candidate_reuse",
        "project_specific_risk": image.get("project_specific_risk") or "low",
        "confidence": float(image.get("semantic_confidence") or 0),
        "review_required": False,
        "review_reason": "",
        "promotion_id": image.get("promotion_id") or "",
        "original_image_asset_id": image.get("image_asset_id") or "",
        "original_image_id": image.get("image_id") or "",
        "original_source_id": image.get("source_id") or "",
        "source_docx_path": _normalize_path(image.get("source_docx_path")),
        "sha256": image.get("sha256") or "",
        "perceptual_hash": image.get("perceptual_hash") or "",
        "image_width": image.get("image_width"),
        "image_height": image.get("image_height"),
        "image_format": image.get("image_format") or "",
    }


def _promoted_image_group(
    group: dict[str, Any],
    *,
    source_id: str,
    material_slice_id: str,
    image_group_id: str,
    image_asset_ids: list[str],
    image_ids: list[str],
) -> dict[str, Any]:
    section_path = _section_path(group.get("section_path"))
    return {
        "image_group_id": image_group_id,
        "source_id": source_id,
        "source_type": PROMOTION_SOURCE_TYPE,
        "source_slice_id": material_slice_id,
        "material_slice_id": material_slice_id,
        "title": section_path[-1] if section_path else str(group.get("group_title") or "候选复用套图"),
        "group_title": group.get("group_title") or group.get("semantic_text") or "",
        "section_path": section_path,
        "section_key": _section_key(section_path),
        "table_index": group.get("table_index"),
        "start_row_index": group.get("start_row_index"),
        "end_row_index": group.get("end_row_index"),
        "member_count": len(image_asset_ids),
        "image_asset_ids": image_asset_ids,
        "image_ids": image_ids,
        "captions": group.get("captions") or [],
        "semantic_sources": group.get("semantic_sources") or [],
        "semantic_text": group.get("semantic_text") or group.get("group_title") or "",
        "semantic_confidence": float(group.get("semantic_confidence") or 0),
        "nearby_text": "；".join(str(item) for item in group.get("captions") or [] if item),
        "tags": group.get("tags") or [],
        "reuse_level": "candidate_reuse",
        "project_specific_risk": group.get("project_specific_risk") or "low",
        "confidence": float(group.get("semantic_confidence") or 0),
        "review_required": False,
        "review_reason": "",
        "detection_method": "promotion_package_group",
        "must_keep_together": True,
        "promotion_id": group.get("promotion_id") or "",
        "original_image_group_id": group.get("source_group_id") or "",
    }


def _source_id_for_image(image: dict[str, Any], source_id_by_docx: dict[str, str]) -> str:
    docx_path = _normalize_path(image.get("source_docx_path"))
    if docx_path in source_id_by_docx:
        return source_id_by_docx[docx_path]
    return next(iter(source_id_by_docx.values()))


def _slice_id_for(
    source_id: str,
    section_path: list[str],
    slice_id_by_key: dict[tuple[str, tuple[str, ...]], str],
) -> str:
    key = (source_id, tuple(section_path))
    if key not in slice_id_by_key:
        slice_id_by_key[key] = f"{source_id}-PMS{len([item for item in slice_id_by_key if item[0] == source_id]) + 1:05d}"
    return slice_id_by_key[key]


def _image_duplicate_reason(
    image: dict[str, Any],
    seen_hashes: set[str],
    seen_original_ids: set[str],
    seen_source_parts: set[tuple[str, str]],
) -> str:
    sha = str(image.get("sha256") or "")
    if sha and sha in seen_hashes:
        return "duplicate_sha256"
    original_id = str(image.get("image_asset_id") or "")
    if original_id and original_id in seen_original_ids:
        return "duplicate_original_image_asset_id"
    source_part = (_normalize_path(image.get("source_docx_path")), str(image.get("part_name") or ""))
    if source_part[0] and source_part[1] and source_part in seen_source_parts:
        return "duplicate_source_part"
    return ""


def _group_duplicate_reason(
    members: list[dict[str, Any]],
    seen_hashes: set[str],
    seen_original_ids: set[str],
    seen_source_parts: set[tuple[str, str]],
) -> str:
    member_seen_hashes: set[str] = set()
    member_seen_parts: set[tuple[str, str]] = set()
    for member in members:
        reason = _image_duplicate_reason(member, seen_hashes, seen_original_ids, seen_source_parts)
        if reason:
            return f"group_member_{reason}"
        sha = str(member.get("sha256") or "")
        if sha and sha in member_seen_hashes:
            return "group_internal_duplicate_sha256"
        if sha:
            member_seen_hashes.add(sha)
        source_part = (_normalize_path(member.get("source_docx_path")), str(member.get("part_name") or ""))
        if source_part[0] and source_part[1] and source_part in member_seen_parts:
            return "group_internal_duplicate_source_part"
        if source_part[0] and source_part[1]:
            member_seen_parts.add(source_part)
    return ""


def _mark_seen(
    original: dict[str, Any],
    promoted: dict[str, Any],
    seen_hashes: set[str],
    seen_original_ids: set[str],
    seen_source_parts: set[tuple[str, str]],
) -> None:
    sha = str(promoted.get("sha256") or "")
    if sha:
        seen_hashes.add(sha)
    original_id = str(original.get("image_asset_id") or "")
    if original_id:
        seen_original_ids.add(original_id)
    source_part = (_normalize_path(original.get("source_docx_path")), str(original.get("part_name") or ""))
    if source_part[0] and source_part[1]:
        seen_source_parts.add(source_part)


def _existing_source_part_keys(library: dict[str, Any]) -> set[tuple[str, str]]:
    source_paths_by_id = {
        str(source.get("source_id") or ""): [_normalize_path(path) for path in source.get("source_paths") or []]
        for source in library.get("sources") or []
        if isinstance(source, dict)
    }
    result: set[tuple[str, str]] = set()
    for asset in library.get("image_assets") or []:
        if not isinstance(asset, dict):
            continue
        part_name = str(asset.get("part_name") or "")
        if not part_name:
            continue
        source_docx_path = _normalize_path(asset.get("source_docx_path"))
        if source_docx_path:
            result.add((source_docx_path, part_name))
        for path in source_paths_by_id.get(str(asset.get("source_id") or ""), []):
            if path:
                result.add((path, part_name))
    return result


def _iter_promoted_images(promotion_package: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for group in promotion_package.get("promote_groups") or []:
        if isinstance(group, dict):
            images.extend(member for member in group.get("members") or [] if isinstance(member, dict))
    images.extend(item for item in promotion_package.get("promote_images") or [] if isinstance(item, dict))
    return images


def _refresh_library_counts(library: dict[str, Any]) -> None:
    sources = [item for item in library.get("sources") or [] if isinstance(item, dict)]
    slices = [item for item in library.get("slices") or [] if isinstance(item, dict)]
    image_assets = [item for item in library.get("image_assets") or [] if isinstance(item, dict)]
    image_groups = [item for item in library.get("image_groups") or [] if isinstance(item, dict)]
    library["source_count"] = len(sources)
    library["slice_count"] = len(slices)
    library["image_asset_count"] = len(image_assets)
    library["image_group_count"] = len(image_groups)
    library["docx_table_count"] = sum(int(item.get("docx_table_count") or 0) for item in slices)
    library["docx_image_count"] = sum(int(item.get("docx_image_count") or 0) for item in slices)
    library["pdf_fallback_table_count"] = sum(
        int(item.get("pdf_table_like_count") or 0) for item in slices if item.get("material_quality") == "pdf_fallback"
    )
    library["pdf_fallback_image_count"] = sum(
        int(item.get("pdf_image_count") or 0) for item in slices if item.get("material_quality") == "pdf_fallback"
    )
    library["pdf_reference_table_like_count"] = sum(
        int(item.get("pdf_table_like_count") or 0) for item in slices if item.get("material_quality") != "pdf_fallback"
    )
    library["pdf_reference_image_count"] = sum(
        int(item.get("pdf_image_count") or 0) for item in slices if item.get("material_quality") != "pdf_fallback"
    )
    library["table_count"] = library["docx_table_count"] + library["pdf_fallback_table_count"]
    library["image_count"] = library["docx_image_count"] + library["pdf_fallback_image_count"]
    library["source_type_counts"] = dict(Counter(str(item.get("source_type") or "") for item in sources))
    library["material_quality_counts"] = dict(Counter(str(item.get("material_quality") or "") for item in slices))


def _apply_warnings(promotion_package: dict[str, Any], skipped_items: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    review_count = int((promotion_package.get("summary") or {}).get("review_item_count") or 0)
    if review_count:
        warnings.append(f"候选包中仍有 {review_count} 个复核项，本次未进入正式素材库预览。")
    if skipped_items:
        warnings.append(f"应用候选包时自动跳过 {len(skipped_items)} 项，主要原因：{_format_counter(Counter(item.get('reason_type') for item in skipped_items))}。")
    return warnings


def _caption_candidates(image: dict[str, Any], group: dict[str, Any] | None) -> list[str]:
    values: list[Any] = [
        image.get("caption_actual"),
        image.get("semantic_text"),
        image.get("title"),
        *((group or {}).get("captions") or []),
        (group or {}).get("group_title"),
        (group or {}).get("semantic_text"),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result[:8]


def _section_path(value: Any) -> list[str]:
    return [str(part).strip() for part in value or [] if str(part).strip()]


def _section_key(section_path: list[str]) -> str:
    return " > ".join(_canonical_segment(part) for part in section_path)


def _canonical_segment(segment: str) -> str:
    number, title = _split_numbered_title(segment)
    return f"{number or ''}:{_canonical_text(title)}"


def _canonical_text(text: str) -> str:
    return re.sub(r"[\s、.，,。；;：（）()\[\]_-]+", "", str(text or "")).lower()


def _split_numbered_title(title: str) -> tuple[str | None, str]:
    match = re.match(r"^\s*(?P<number>\d+(?:\.\d+)*)(?:[.\s]+)(?P<title>\S.*)$", str(title or ""))
    if not match:
        return None, str(title or "").strip()
    return match.group("number"), match.group("title").strip()


def _strip_heading_number(title: str) -> str:
    return _split_numbered_title(title)[1].strip()


def _keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", str(text or ""))
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.lower()
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= 80:
            break
    return result


def _average_confidence(assets: list[dict[str, Any]], groups: list[dict[str, Any]]) -> float:
    values = [
        float(item.get("semantic_confidence") or item.get("confidence") or 0)
        for item in [*assets, *groups]
        if item.get("semantic_confidence") is not None or item.get("confidence") is not None
    ]
    if not values:
        return 0.72
    return round(sum(values) / len(values), 3)


def _max_risk(values: list[Any]) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    best = "low"
    for value in values:
        risk = str(value or "low")
        if order.get(risk, 0) > order.get(best, 0):
            best = risk
    return best


def _next_source_number(library: dict[str, Any]) -> int:
    numbers = []
    for source in library.get("sources") or []:
        if not isinstance(source, dict):
            continue
        match = re.match(r"^SRC(\d+)$", str(source.get("source_id") or ""))
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers or [0]) + 1


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text))


def _format_counter(counter: Any) -> str:
    if not counter:
        return "-"
    return "；".join(f"{key}={value}" for key, value in sorted(dict(counter).items()))


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
