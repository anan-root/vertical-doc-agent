"""优秀标书图片入库前 staging 诊断。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .docx_section_material_index import (
    build_docx_section_material_index,
    write_section_material_index_outputs,
)
from .excellent_bid_material_library import build_excellent_bid_material_library


SCHEMA_VERSION = "excellent_bid_image_staging_v1"
DEFAULT_PHASH_DISTANCE_THRESHOLD = 5


def build_excellent_bid_image_staging_from_docx(
    docx_path: str | Path,
    *,
    existing_library_path: str | Path | None = None,
    library_id: str | None = None,
    root_dir: str | Path = ".",
    index_json_path: str | Path | None = None,
    index_report_path: str | Path | None = None,
    staging_json_path: str | Path | None = None,
    staging_report_path: str | Path | None = None,
    perceptual_hash_distance_threshold: int = DEFAULT_PHASH_DISTANCE_THRESHOLD,
) -> dict[str, Any]:
    """从单份 DOCX 优秀标书构建 staging 图片诊断结果。"""

    index = build_docx_section_material_index(docx_path)
    if index_json_path and index_report_path:
        write_section_material_index_outputs(index, index_json_path, index_report_path)

    staging_library = build_excellent_bid_material_library(
        [(str(index_json_path or docx_path), index.to_dict())],
        library_id=library_id or f"staging_{Path(docx_path).stem}",
    )
    existing_library = _read_json(existing_library_path) if existing_library_path else None
    result = build_excellent_bid_image_staging(
        staging_library,
        existing_library=existing_library,
        root_dir=root_dir,
        perceptual_hash_distance_threshold=perceptual_hash_distance_threshold,
    )

    if staging_json_path:
        target = Path(staging_json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if staging_report_path:
        target = Path(staging_report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_excellent_bid_image_staging_report(result), encoding="utf-8")
    return result


def build_excellent_bid_image_staging(
    staging_library: Any,
    *,
    existing_library: Any | None = None,
    root_dir: str | Path = ".",
    perceptual_hash_distance_threshold: int = DEFAULT_PHASH_DISTANCE_THRESHOLD,
    _read_docx_part_override: Any | None = None,
    _image_meta_override: Any | None = None,
) -> dict[str, Any]:
    """构建新优秀标书图片 staging 诊断。"""

    staging = _normalize_library(staging_library)
    existing = _normalize_library(existing_library) if existing_library else None
    root = Path(root_dir)

    staging_sources = _source_lookup(staging)
    existing_sources = _source_lookup(existing) if existing else {}
    existing_assets = existing.get("image_assets", []) if existing else []
    staging_assets = staging.get("image_assets", [])
    staging_groups = staging.get("image_groups", [])

    existing_enriched = [
        _enrich_asset(
            asset,
            existing_sources,
            root_dir=root,
            read_docx_part_override=_read_docx_part_override,
            image_meta_override=_image_meta_override,
        )
        for asset in existing_assets
        if isinstance(asset, dict)
    ]
    existing_by_sha = _assets_by_hash(existing_enriched, "sha256")
    existing_phash_candidates = [asset for asset in existing_enriched if asset.get("perceptual_hash")]

    first_seen_sha: dict[str, str] = {}
    staged_images: list[dict[str, Any]] = []
    for asset in staging_assets:
        if not isinstance(asset, dict):
            continue
        enriched = _enrich_asset(
            asset,
            staging_sources,
            root_dir=root,
            read_docx_part_override=_read_docx_part_override,
            image_meta_override=_image_meta_override,
        )
        sha = str(enriched.get("sha256") or "")
        exact_matches = existing_by_sha.get(sha, []) if sha else []
        internal_duplicate_of = first_seen_sha.get(sha) if sha else None
        if sha and sha not in first_seen_sha:
            first_seen_sha[sha] = str(enriched.get("image_asset_id") or "")
        phash_matches = _perceptual_matches(
            enriched,
            existing_phash_candidates,
            distance_threshold=perceptual_hash_distance_threshold,
            limit=5,
        )
        decision, reasons = _staging_decision(
            enriched,
            exact_matches=exact_matches,
            internal_duplicate_of=internal_duplicate_of,
            perceptual_matches=phash_matches,
        )
        staged_images.append(
            {
                **_image_preview(enriched),
                "sha256": sha,
                "perceptual_hash": enriched.get("perceptual_hash") or "",
                "image_width": enriched.get("image_width"),
                "image_height": enriched.get("image_height"),
                "image_format": enriched.get("image_format") or "",
                "binary_status": enriched.get("binary_status") or "missing",
                "decision": decision,
                "decision_reasons": reasons,
                "exact_duplicate_matches": [_match_preview(match) for match in exact_matches[:8]],
                "perceptual_duplicate_matches": [
                    {
                        **_match_preview(match["asset"]),
                        "distance": match["distance"],
                    }
                    for match in phash_matches
                ],
                "internal_duplicate_of": internal_duplicate_of,
            }
        )

    group_records = _stage_groups(staging_groups, staged_images)
    missing_group_candidates = _missing_group_candidates(staged_images)
    section_summary = _section_summary(staged_images, group_records)
    warnings = _staging_warnings(staged_images, group_records, missing_group_candidates)
    summary = {
        "source_count": int(staging.get("source_count") or len(staging.get("sources", []))),
        "slice_count": int(staging.get("slice_count") or len(staging.get("slices", []))),
        "image_count": len(staged_images),
        "group_count": len(group_records),
        "existing_image_count": len(existing_assets),
        "decision_counts": dict(Counter(image["decision"] for image in staged_images)),
        "reuse_level_counts": dict(Counter(str(image.get("reuse_level") or "") for image in staged_images)),
        "risk_counts": dict(Counter(str(image.get("project_specific_risk") or "") for image in staged_images)),
        "semantic_quality_counts": dict(Counter(_semantic_quality(image) for image in staged_images)),
        "exact_duplicate_existing_count": sum(1 for image in staged_images if image["exact_duplicate_matches"]),
        "perceptual_duplicate_existing_count": sum(1 for image in staged_images if image["perceptual_duplicate_matches"]),
        "internal_duplicate_count": sum(1 for image in staged_images if image.get("internal_duplicate_of")),
        "review_required_count": sum(1 for image in staged_images if image.get("review_required")),
        "project_specific_review_count": sum(
            1 for image in staged_images if image.get("project_specific_risk") == "high"
        ),
        "missing_group_candidate_count": len(missing_group_candidates),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "staging_library_id": staging.get("library_id") or "",
        "generated_from": "excellent_bid_image_staging",
        "perceptual_hash_distance_threshold": perceptual_hash_distance_threshold,
        "summary": summary,
        "sources": staging.get("sources", []),
        "images": staged_images,
        "image_groups": group_records,
        "missing_group_candidates": missing_group_candidates,
        "section_summary": section_summary,
        "warnings": warnings,
    }


def write_excellent_bid_image_staging_outputs(
    result: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_excellent_bid_image_staging_report(result), encoding="utf-8")


def render_excellent_bid_image_staging_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# 优秀标书图片 staging 诊断报告",
        "",
        f"- staging 素材库 ID：`{result.get('staging_library_id') or ''}`",
        f"- 图片数：{summary.get('image_count', 0)}",
        f"- 套图组数：{summary.get('group_count', 0)}",
        f"- 已有正式库图片数：{summary.get('existing_image_count', 0)}",
        f"- 与正式库完全重复图片数：{summary.get('exact_duplicate_existing_count', 0)}",
        f"- 与正式库疑似相似图片数：{summary.get('perceptual_duplicate_existing_count', 0)}",
        f"- 新标书内部重复图片数：{summary.get('internal_duplicate_count', 0)}",
        f"- 需要人工复核图片数：{summary.get('review_required_count', 0)}",
        f"- 项目专属性高风险图片数：{summary.get('project_specific_review_count', 0)}",
        f"- 疑似漏识别套图数：{summary.get('missing_group_candidate_count', 0)}",
        f"- 决策分布：{_format_counter(summary.get('decision_counts'))}",
        f"- 语义质量分布：{_format_counter(summary.get('semantic_quality_counts'))}",
        "",
        "## 使用建议",
        "",
        "- `candidate_reuse`：可进入候选复用池，后续按章节语义检索使用。",
        "- `duplicate_existing`：正式库已有同图，原则上不重复入库，可只补充更好的语义说明。",
        "- `suspected_duplicate_existing`：疑似相似图，建议抽样看图后再决定是否合并。",
        "- `project_specific_manual_review`：多为总平面、进度图、踏勘或项目事实图，不能自动复用。",
        "- `manual_review`：语义来源不够稳或说明过泛，需要人工确认后再入库。",
        "",
        "## 套图组预览",
        "",
    ]

    groups = result.get("image_groups") or []
    if groups:
        for group in groups[:80]:
            lines.append(
                f"- {group.get('image_group_id')}: {group.get('member_count')} 张，"
                f"decision={group.get('decision')}, title={group.get('group_title') or '-'}，"
                f"path={' > '.join(group.get('section_path') or [])}"
            )
            captions = group.get("captions") or []
            if captions:
                lines.append(f"  - captions：{'；'.join(captions[:8])}")
            if group.get("semantic_text"):
                lines.append(f"  - semantic：{str(group.get('semantic_text'))[:180]}")
    else:
        lines.append("- 未识别到套图组。")

    lines.extend(["", "## 疑似漏识别套图", ""])
    missing_groups = result.get("missing_group_candidates") or []
    if missing_groups:
        for item in missing_groups[:80]:
            lines.append(
                f"- {item.get('candidate_id')}: {item.get('member_count')} 张，"
                f"table={item.get('table_index')} rows={item.get('start_row_index')}-{item.get('end_row_index')}，"
                f"path={' > '.join(item.get('section_path') or [])}"
            )
            if item.get("semantic_text"):
                lines.append(f"  - semantic：{str(item.get('semantic_text'))[:180]}")
    else:
        lines.append("- 未发现明显漏识别套图。")

    lines.extend(["", "## 人工复核优先清单", ""])
    review_images = [
        image
        for image in result.get("images") or []
        if image.get("decision") in {"project_specific_manual_review", "manual_review", "suspected_duplicate_existing"}
    ]
    if review_images:
        for image in review_images[:120]:
            lines.append(
                f"- {image.get('image_asset_id')}: decision={image.get('decision')}, "
                f"risk={image.get('project_specific_risk')}, conf={image.get('semantic_confidence')}, "
                f"caption={image.get('caption_actual') or '-'}"
            )
            lines.append(f"  - path：{' > '.join(image.get('section_path') or [])}")
            if image.get("decision_reasons"):
                lines.append(f"  - reason：{'；'.join(image.get('decision_reasons') or [])}")
            if image.get("nearby_text"):
                lines.append(f"  - nearby：{str(image.get('nearby_text'))[:180]}")
    else:
        lines.append("- 暂无需要优先人工复核的图片。")

    lines.extend(["", "## 可候选复用图片抽样", ""])
    candidates = [image for image in result.get("images") or [] if image.get("decision") == "candidate_reuse"]
    if candidates:
        for image in candidates[:80]:
            lines.append(
                f"- {image.get('image_asset_id')}: {image.get('caption_actual') or image.get('semantic_text') or '-'}，"
                f"path={' > '.join(image.get('section_path') or [])}"
            )
    else:
        lines.append("- 暂无自动候选复用图片。")

    if result.get("warnings"):
        lines.extend(["", "## 警告", ""])
        for warning in result["warnings"]:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _normalize_library(library: Any) -> dict[str, Any]:
    if library is None:
        return {}
    if isinstance(library, dict):
        return library
    if hasattr(library, "to_dict"):
        return library.to_dict()
    if is_dataclass(library):
        return asdict(library)
    raise TypeError(f"Unsupported library type: {type(library)!r}")


def _source_lookup(library: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not library:
        return {}
    return {
        str(source.get("source_id") or ""): source
        for source in library.get("sources") or []
        if isinstance(source, dict)
    }


def _enrich_asset(
    asset: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    *,
    root_dir: Path,
    read_docx_part_override: Any | None = None,
    image_meta_override: Any | None = None,
) -> dict[str, Any]:
    result = dict(asset)
    source = sources.get(str(asset.get("source_id") or ""), {})
    docx_path = _docx_path_for_asset(asset, source, root_dir)
    part_name = str(asset.get("part_name") or "")
    result["source_docx_path"] = str(docx_path) if docx_path else ""
    if read_docx_part_override is not None and docx_path and part_name:
        image_bytes = read_docx_part_override(docx_path, part_name)
    else:
        image_bytes = _read_docx_part(docx_path, part_name) if docx_path and part_name else None
    if image_bytes is None:
        result.update(
            {
                "sha256": "",
                "perceptual_hash": "",
                "image_width": None,
                "image_height": None,
                "image_format": "",
                "binary_status": "missing",
            }
        )
        return result

    result["sha256"] = hashlib.sha256(image_bytes).hexdigest()
    result["binary_size"] = len(image_bytes)
    result["binary_status"] = "ok"
    image_meta = image_meta_override(image_bytes) if image_meta_override is not None else _image_meta(image_bytes)
    result.update(image_meta)
    return result


def _docx_path_for_asset(asset: dict[str, Any], source: dict[str, Any], root_dir: Path) -> Path | None:
    source_paths = [str(path) for path in source.get("source_paths") or [] if str(path)]
    if not source_paths and source.get("source_path"):
        source_paths = [str(source.get("source_path"))]
    docx_candidates = [path for path in source_paths if path.lower().endswith(".docx")]
    if not docx_candidates:
        return None
    raw = Path(docx_candidates[-1])
    if raw.is_absolute():
        return raw
    return root_dir / raw


def _read_docx_part(docx_path: Path | None, part_name: str) -> bytes | None:
    if not docx_path or not docx_path.exists() or not part_name:
        return None
    normalized = part_name.replace("\\", "/").lstrip("/")
    try:
        with zipfile.ZipFile(docx_path) as package:
            with package.open(normalized) as fp:
                return fp.read()
    except (KeyError, zipfile.BadZipFile, OSError):
        return None


def _image_meta(image_bytes: bytes) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError:
        return {
            "perceptual_hash": "",
            "image_width": None,
            "image_height": None,
            "image_format": "",
        }

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = image.format or ""
            phash = _average_hash(image)
    except Exception:
        return {
            "perceptual_hash": "",
            "image_width": None,
            "image_height": None,
            "image_format": "",
        }
    return {
        "perceptual_hash": phash,
        "image_width": width,
        "image_height": height,
        "image_format": image_format,
    }


def _average_hash(image: Any, hash_size: int = 8) -> str:
    gray = image.convert("L").resize((hash_size, hash_size))
    pixels = list(gray.getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def _assets_by_hash(assets: list[dict[str, Any]], field_name: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        value = str(asset.get(field_name) or "")
        if value:
            result[value].append(asset)
    return result


def _perceptual_matches(
    asset: dict[str, Any],
    existing_assets: list[dict[str, Any]],
    *,
    distance_threshold: int,
    limit: int,
) -> list[dict[str, Any]]:
    phash = str(asset.get("perceptual_hash") or "")
    if not phash:
        return []
    matches: list[dict[str, Any]] = []
    for existing in existing_assets:
        existing_hash = str(existing.get("perceptual_hash") or "")
        if not existing_hash or existing_hash == phash and existing.get("sha256") == asset.get("sha256"):
            continue
        distance = _hash_distance(phash, existing_hash)
        if distance <= distance_threshold:
            matches.append({"asset": existing, "distance": distance})
    matches.sort(key=lambda item: (item["distance"], str(item["asset"].get("image_asset_id") or "")))
    return matches[:limit]


def _hash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 999


def _staging_decision(
    asset: dict[str, Any],
    *,
    exact_matches: list[dict[str, Any]],
    internal_duplicate_of: str | None,
    perceptual_matches: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if exact_matches:
        reasons.append("正式库已存在相同图片二进制。")
        return "duplicate_existing", reasons
    if internal_duplicate_of:
        reasons.append(f"新标书内部与 {internal_duplicate_of} 完全相同。")
        return "internal_duplicate", reasons
    if str(asset.get("project_specific_risk") or "") == "high":
        reasons.append("项目专属性风险高，需人工确认是否可复用。")
        return "project_specific_manual_review", reasons
    if perceptual_matches:
        reasons.append("与正式库图片感知哈希相近，疑似相似图。")
        return "suspected_duplicate_existing", reasons
    if asset.get("review_required"):
        reasons.append(str(asset.get("review_reason") or "图片语义或复用风险需人工确认。"))
        return "manual_review", reasons
    if float(asset.get("semantic_confidence") or 0) < 0.58:
        reasons.append("图片语义置信度偏低。")
        return "manual_review", reasons
    if str(asset.get("binary_status") or "") != "ok":
        reasons.append("未读取到图片二进制，无法做重复检测。")
        return "manual_review", reasons
    reasons.append("语义与复用风险满足候选入库条件。")
    return "candidate_reuse", reasons


def _image_preview(asset: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "image_asset_id",
        "image_id",
        "source_id",
        "material_slice_id",
        "title",
        "section_path",
        "part_name",
        "context",
        "table_index",
        "row_index",
        "cell_index",
        "image_group_id",
        "group_title",
        "group_member_index",
        "group_member_count",
        "must_keep_with_group",
        "caption_actual",
        "caption_candidates",
        "semantic_text",
        "semantic_confidence",
        "semantic_sources",
        "nearby_text",
        "tags",
        "reuse_level",
        "project_specific_risk",
        "review_required",
        "review_reason",
        "source_docx_path",
    ]
    return {key: asset.get(key) for key in keys}


def _match_preview(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_asset_id": asset.get("image_asset_id"),
        "source_id": asset.get("source_id"),
        "title": asset.get("title"),
        "section_path": asset.get("section_path") or [],
        "part_name": asset.get("part_name"),
        "caption_actual": asset.get("caption_actual"),
        "semantic_text": asset.get("semantic_text"),
    }


def _stage_groups(groups: list[dict[str, Any]], staged_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images_by_id = {str(image.get("image_asset_id") or ""): image for image in staged_images}
    result: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        members = [images_by_id.get(str(asset_id)) for asset_id in group.get("image_asset_ids") or []]
        members = [member for member in members if member]
        member_decisions = [str(member.get("decision") or "") for member in members]
        if any(decision == "duplicate_existing" for decision in member_decisions):
            decision = "duplicate_existing_group"
        elif any(decision in {"manual_review", "project_specific_manual_review"} for decision in member_decisions):
            decision = "manual_review_group"
        elif any(decision == "suspected_duplicate_existing" for decision in member_decisions):
            decision = "suspected_duplicate_group"
        else:
            decision = "candidate_reuse_group"
        result.append(
            {
                "image_group_id": group.get("image_group_id"),
                "group_title": group.get("group_title") or "",
                "semantic_text": group.get("semantic_text") or "",
                "semantic_confidence": group.get("semantic_confidence") or 0,
                "section_path": group.get("section_path") or [],
                "table_index": group.get("table_index"),
                "start_row_index": group.get("start_row_index"),
                "end_row_index": group.get("end_row_index"),
                "member_count": len(members) or int(group.get("member_count") or 0),
                "image_asset_ids": group.get("image_asset_ids") or [],
                "captions": group.get("captions") or [],
                "tags": group.get("tags") or [],
                "reuse_level": group.get("reuse_level"),
                "project_specific_risk": group.get("project_specific_risk"),
                "review_required": group.get("review_required"),
                "review_reason": group.get("review_reason") or "",
                "detection_method": group.get("detection_method") or "",
                "must_keep_together": bool(group.get("must_keep_together")),
                "decision": decision,
                "member_decision_counts": dict(Counter(member_decisions)),
            }
        )
    return result


def _missing_group_candidates(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for image in images:
        if image.get("image_group_id"):
            continue
        if image.get("table_index") is None:
            continue
        if image.get("decision") in {
            "duplicate_existing",
            "internal_duplicate",
            "project_specific_manual_review",
            "manual_review",
        }:
            continue
        if image.get("project_specific_risk") == "high":
            continue
        buckets[(str(image.get("material_slice_id") or ""), int(image.get("table_index")))].append(image)

    result: list[dict[str, Any]] = []
    for index, ((material_slice_id, table_index), members) in enumerate(sorted(buckets.items()), start=1):
        if len(members) < 2:
            continue
        rows = [int(member.get("row_index")) for member in members if member.get("row_index") is not None]
        if not rows or max(rows) - min(rows) > 8:
            continue
        text = " ".join(
            str(value or "")
            for member in members
            for value in [
                member.get("caption_actual"),
                member.get("semantic_text"),
                member.get("nearby_text"),
                member.get("title"),
            ]
        )
        if not _looks_like_suite_text(text):
            continue
        ordered = sorted(
            members,
            key=lambda item: (
                item.get("row_index") if item.get("row_index") is not None else 10**9,
                item.get("cell_index") if item.get("cell_index") is not None else 10**9,
                str(item.get("image_asset_id") or ""),
            ),
        )
        result.append(
            {
                "candidate_id": f"MISS-G{index:04d}",
                "material_slice_id": material_slice_id,
                "table_index": table_index,
                "section_path": ordered[0].get("section_path") or [],
                "member_count": len(ordered),
                "image_asset_ids": [member.get("image_asset_id") for member in ordered],
                "start_row_index": min(rows),
                "end_row_index": max(rows),
                "semantic_text": _compact_semantic_text(
                    [member.get("caption_actual") or member.get("semantic_text") for member in ordered]
                ),
                "reason": "同一表格相邻行存在多张未成组图片，且语义包含流程/示意/做法等套图特征。",
            }
        )
    return result


def _looks_like_suite_text(text: str) -> bool:
    return any(term in str(text or "") for term in ["流程", "示意", "做法", "工艺", "控制", "加工", "绑扎", "安装"])


def _compact_semantic_text(values: list[Any]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" |；;，,")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return "；".join(result[:10])


def _section_summary(images: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_by_path: Counter[str] = Counter(" > ".join(group.get("section_path") or []) for group in groups)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for image in images:
        buckets[" > ".join(image.get("section_path") or [])].append(image)

    result: list[dict[str, Any]] = []
    for section_path, items in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:120]:
        decisions = Counter(str(image.get("decision") or "") for image in items)
        result.append(
            {
                "section_path": section_path.split(" > ") if section_path else [],
                "image_count": len(items),
                "group_count": group_by_path.get(section_path, 0),
                "decision_counts": dict(decisions),
            }
        )
    return result


def _staging_warnings(
    images: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    missing_group_candidates: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    missing_binary = [image for image in images if image.get("binary_status") != "ok"]
    if missing_binary:
        warnings.append(f"{len(missing_binary)} 张图片未读取到二进制，重复检测结果不完整。")
    low_semantic = [image for image in images if _semantic_quality(image) == "low"]
    if low_semantic:
        warnings.append(f"{len(low_semantic)} 张图片语义置信度偏低，需要优先治理。")
    if missing_group_candidates:
        warnings.append(f"{len(missing_group_candidates)} 处疑似套图未成组，需要人工抽样确认。")
    split_group_risk = [group for group in groups if not group.get("must_keep_together")]
    if split_group_risk:
        warnings.append(f"{len(split_group_risk)} 个图片组未标记必须整体使用。")
    return warnings


def _semantic_quality(image: dict[str, Any]) -> str:
    confidence = float(image.get("semantic_confidence") or 0)
    if confidence >= 0.78:
        return "high"
    if confidence >= 0.58:
        return "medium"
    return "low"


def _format_counter(counter: Any) -> str:
    if not counter:
        return "-"
    return "，".join(f"{key}={value}" for key, value in sorted(dict(counter).items()))


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
