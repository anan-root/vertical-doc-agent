"""优秀标书素材库 manifest 迁移与读取。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_RELATIVE_PATH = Path("knowledge_base") / "excellent_bids" / "indexes" / "library_manifest.json"
PREFERRED_AGGREGATE_INDEX = "excellent_bid_material_library_two_word_sources.json"
UPLOADED_AGGREGATE_INDEX = "excellent_bid_material_library_uploaded.json"
AGGREGATE_INDEX_CANDIDATES = [
    PREFERRED_AGGREGATE_INDEX,
    "excellent_bid_material_library_with_zhenggui_yunting_full_fingerprinted.json",
    "excellent_bid_material_library_with_zhenggui_yunting_full_caption_governance_preview.json",
    "excellent_bid_material_library_with_zhenggui_yunting_full.json",
    "excellent_bid_material_library_full_with_zhenggui_yunting.json",
    "excellent_bid_material_library_with_image_assets.json",
    "excellent_bid_material_library.json",
]
SOURCE_TYPE_LABELS = {
    "docx_only": "Word 优秀标书",
    "pdf_docx_fusion": "PDF/Word 融合优秀标书",
}
RAG_KNOWLEDGE_TYPE_LABELS = {
    "excellent_bid": "优秀标书",
    "law_regulation": "法律法规",
    "technical_standard": "技术规范",
    "enterprise_policy": "企业制度",
    "review_rule": "评审办法",
    "other": "其他资料",
}
EXCELLENT_BID_PROJECT_TYPE_LABELS = {
    "building_construction": "房建",
    "municipal": "市政",
    "highway": "公路",
    "water_conservancy": "水利",
    "rail_transit": "轨道交通",
    "mechanical_electrical": "机电",
    "decoration": "装饰装修",
    "other": "其他",
}
EXCELLENT_BID_TYPE_LABELS = {
    "construction_technical_bid": "施工技术标",
    "epc_technical_bid": "EPC 技术标",
    "design_scheme": "设计方案",
    "construction_organization_design": "施工组织设计",
    "other": "其他",
}
EXCELLENT_BID_STATUS_LABELS = {
    "ready": "已入库",
    "pending_review": "待复核",
    "processing": "解析中",
    "failed": "入库失败",
}
DEFAULT_EXCELLENT_BID_PROJECT_TYPE = "building_construction"
DEFAULT_EXCELLENT_BID_TYPE = "construction_technical_bid"
DEFAULT_RAG_KNOWLEDGE_TYPE = "excellent_bid"


def load_or_migrate_excellent_bid_manifest(
    *,
    project_root: Path,
    storage_root: Path,
    force: bool = False,
) -> dict[str, Any]:
    """读取正式素材库清单；不存在时从历史 outputs/json 索引迁移。"""

    manifest_path = storage_root / MANIFEST_RELATIVE_PATH
    if manifest_path.exists() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _manifest_should_migrate_to_preferred_index(manifest, project_root):
            return migrate_existing_excellent_bid_indexes(project_root=project_root, storage_root=storage_root)
        return normalize_excellent_bid_manifest(manifest)
    return migrate_existing_excellent_bid_indexes(project_root=project_root, storage_root=storage_root)


def normalize_excellent_bid_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """补齐优秀标书素材库清单中的来源元数据。"""

    normalized = dict(manifest)
    sources = manifest.get("sources", [])
    normalized_sources = [
        normalize_excellent_bid_source_metadata(source)
        for source in sources
        if isinstance(source, dict)
    ]
    normalized["sources"] = normalized_sources
    normalized["source_count"] = len(normalized_sources)
    normalized["slice_count"] = sum(_int(item.get("slice_count")) for item in normalized_sources)
    normalized["table_count"] = sum(_int(item.get("table_count")) for item in normalized_sources)
    normalized["image_count"] = sum(_int(item.get("image_count")) for item in normalized_sources)
    normalized["warning_count"] = sum(_int(item.get("warning_count")) for item in normalized_sources)
    normalized["quality_summary"] = _library_quality_summary(normalized_sources)
    return normalized


def normalize_excellent_bid_source_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """补齐单份优秀标书的项目类型、标书类型、状态等字段。"""

    normalized = dict(source)
    knowledge_type = _known_or_default(
        normalized.get("knowledge_type"),
        RAG_KNOWLEDGE_TYPE_LABELS,
        DEFAULT_RAG_KNOWLEDGE_TYPE,
    )
    project_type = _known_or_default(
        normalized.get("project_type"),
        EXCELLENT_BID_PROJECT_TYPE_LABELS,
        DEFAULT_EXCELLENT_BID_PROJECT_TYPE,
    )
    bid_type = _known_or_default(
        normalized.get("bid_type"),
        EXCELLENT_BID_TYPE_LABELS,
        DEFAULT_EXCELLENT_BID_TYPE,
    )
    status = _normalize_source_status(normalized.get("status"))
    normalized["knowledge_type"] = knowledge_type
    normalized["knowledge_type_label"] = RAG_KNOWLEDGE_TYPE_LABELS[knowledge_type]
    normalized["project_type"] = project_type
    normalized["project_type_label"] = EXCELLENT_BID_PROJECT_TYPE_LABELS[project_type]
    normalized["bid_type"] = bid_type
    normalized["bid_type_label"] = EXCELLENT_BID_TYPE_LABELS[bid_type]
    normalized["status"] = status
    normalized["status_label"] = EXCELLENT_BID_STATUS_LABELS.get(status, str(status))
    normalized["allow_image_reuse"] = bool(normalized.get("allow_image_reuse", True))
    normalized["desensitized_confirmed"] = bool(normalized.get("desensitized_confirmed", True))
    quality_flags = _source_quality_flags(normalized)
    normalized["quality_flags"] = quality_flags
    normalized["quality_level"] = _source_quality_level(normalized, quality_flags)
    normalized["usage_advice"] = _source_usage_advice(normalized, quality_flags)
    return normalized


def upsert_excellent_bid_source(
    *,
    project_root: Path,
    storage_root: Path,
    source: dict[str, Any],
) -> dict[str, Any]:
    """新增或更新单份优秀标书来源记录，并重算 manifest 统计。"""

    manifest = load_or_migrate_excellent_bid_manifest(project_root=project_root, storage_root=storage_root)
    normalized_source = normalize_excellent_bid_source_metadata(source)
    source_id = normalized_source.get("source_bid_id")
    sources: list[dict[str, Any]] = []
    replaced = False
    for item in manifest.get("sources", []):
        if not isinstance(item, dict):
            continue
        if item.get("source_bid_id") == source_id:
            sources.append(normalized_source)
            replaced = True
        else:
            sources.append(normalize_excellent_bid_source_metadata(item))
    if not replaced:
        sources.append(normalized_source)
    manifest = normalize_excellent_bid_manifest({**manifest, "sources": sources})
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "ready" if sources else "empty"
    _write_manifest(storage_root / MANIFEST_RELATIVE_PATH, manifest)
    return manifest


def delete_excellent_bid_source(
    *,
    project_root: Path,
    storage_root: Path,
    source_bid_id: str,
) -> dict[str, Any] | None:
    """Remove one source from the manifest and aggregate index.

    This is a soft delete for safety: original uploaded files and extracted
    per-source files stay on disk, but the source is removed from listing and
    search indexes.
    """

    manifest = load_or_migrate_excellent_bid_manifest(project_root=project_root, storage_root=storage_root)
    source = _find_source(manifest, source_bid_id)
    if source is None:
        return None
    sources = [
        normalize_excellent_bid_source_metadata(item)
        for item in manifest.get("sources", [])
        if isinstance(item, dict) and item.get("source_bid_id") != source_bid_id
    ]
    manifest = normalize_excellent_bid_manifest({**manifest, "sources": sources})
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "ready" if sources else "empty"
    _write_manifest(storage_root / MANIFEST_RELATIVE_PATH, manifest)
    _remove_source_from_uploaded_aggregate(storage_root, source_bid_id)
    return {
        "deleted": True,
        "source_bid_id": source_bid_id,
        "source_title": source.get("title"),
        "manifest": manifest,
        "delete_mode": "soft",
    }


def rebuild_excellent_bid_manifest_from_library(
    *,
    project_root: Path,
    storage_root: Path,
    library_path: Path,
    source_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """根据统一素材库 JSON 重建 manifest，并套用上传元数据覆盖。"""

    index_dir = storage_root / "knowledge_base" / "excellent_bids" / "indexes"
    index_dir.mkdir(parents=True, exist_ok=True)
    aggregate_copy = _copy_index_file(library_path, index_dir)
    aggregate_data = json.loads(library_path.read_text(encoding="utf-8"))
    overrides = source_overrides or {}
    records: list[dict[str, Any]] = []
    for source in aggregate_data.get("sources", []):
        record = _source_record(project_root, storage_root, index_dir, source)
        override = overrides.get(str(record.get("source_bid_id") or ""))
        if override:
            record = normalize_excellent_bid_source_metadata(_merge_source_metadata(record, override))
        records.append(record)

    manifest = normalize_excellent_bid_manifest(
        {
            "schema_version": "excellent_bid_library_manifest_v1",
            "library_id": "excellent_bid_material_library",
            "status": "ready" if records else "empty",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "migration_source": str(_relative_to_project(project_root, library_path)),
            "aggregate_index_uri": _storage_uri(storage_root, aggregate_copy),
            "sources": records,
        }
    )
    _write_manifest(storage_root / MANIFEST_RELATIVE_PATH, manifest)
    return manifest


def _remove_source_from_uploaded_aggregate(storage_root: Path, source_bid_id: str) -> None:
    aggregate_path = storage_root / "knowledge_base" / "excellent_bids" / "indexes" / UPLOADED_AGGREGATE_INDEX
    if not aggregate_path.exists():
        return
    try:
        data = json.loads(aggregate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    sources = data.get("sources")
    slices = data.get("slices")
    if isinstance(sources, list):
        data["sources"] = [
            item
            for item in sources
            if not isinstance(item, dict) or item.get("source_id") != source_bid_id
        ]
    if isinstance(slices, list):
        data["slices"] = [
            item
            for item in slices
            if not isinstance(item, dict) or item.get("source_id") != source_bid_id
        ]
    aggregate_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_excellent_bid_detail(
    *,
    project_root: Path,
    storage_root: Path,
    source_bid_id: str,
    limit: int = 20,
) -> dict[str, Any] | None:
    """读取单份优秀标书详情，并返回前若干章节素材切片摘要。"""

    manifest = load_or_migrate_excellent_bid_manifest(project_root=project_root, storage_root=storage_root)
    source = _find_source(manifest, source_bid_id)
    if source is None:
        return None
    slices = _load_source_slices(storage_root, source)
    return {
        "source": source,
        "slice_preview": [_slice_summary(item) for item in slices[: max(limit, 0)]],
        "slice_preview_count": min(len(slices), max(limit, 0)),
        "total_slice_count": len(slices),
    }


def search_excellent_bid_slices(
    *,
    project_root: Path,
    storage_root: Path,
    query: str,
    source_bid_id: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """按关键词检索优秀标书章节素材切片。"""

    manifest = load_or_migrate_excellent_bid_manifest(project_root=project_root, storage_root=storage_root)
    keywords = [item for item in query.strip().lower().split() if item]
    sources = manifest.get("sources", [])
    if source_bid_id:
        sources = [item for item in sources if item.get("source_bid_id") == source_bid_id]
    results: list[dict[str, Any]] = []
    for source in sources:
        for item in _load_source_slices(storage_root, source):
            score = _match_score(item, keywords)
            if keywords and score <= 0:
                continue
            summary = _slice_summary(item)
            summary["source_bid_id"] = source.get("source_bid_id")
            summary["source_title"] = source.get("title")
            summary["source_type_label"] = source.get("source_type_label")
            summary["knowledge_type"] = source.get("knowledge_type")
            summary["knowledge_type_label"] = source.get("knowledge_type_label")
            summary["score"] = score
            results.append(summary)
    results.sort(key=lambda item: (item.get("score") or 0, item.get("image_count") or 0, item.get("table_count") or 0), reverse=True)
    limited = results[: max(limit, 0)]
    return {
        "query": query,
        "source_bid_id": source_bid_id,
        "total": len(results),
        "limit": limit,
        "results": limited,
    }


def migrate_existing_excellent_bid_indexes(*, project_root: Path, storage_root: Path) -> dict[str, Any]:
    """把历史优秀标书索引归并为正式知识库 manifest。"""

    storage_root = storage_root.resolve()
    index_dir = storage_root / "knowledge_base" / "excellent_bids" / "indexes"
    index_dir.mkdir(parents=True, exist_ok=True)
    aggregate_path = _find_aggregate_index(_output_root(project_root) / "json")

    if aggregate_path is None:
        existing_manifest_path = index_dir / "library_manifest.json"
        if existing_manifest_path.exists():
            existing_manifest = normalize_excellent_bid_manifest(
                json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            )
            if existing_manifest.get("source_count"):
                return existing_manifest
        uploaded_aggregate = index_dir / UPLOADED_AGGREGATE_INDEX
        if uploaded_aggregate.exists():
            return rebuild_excellent_bid_manifest_from_library(
                project_root=project_root,
                storage_root=storage_root,
                library_path=uploaded_aggregate,
                source_overrides=None,
            )
        manifest = _empty_manifest()
        _write_manifest(index_dir / "library_manifest.json", manifest)
        return manifest

    aggregate_data = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate_copy = _copy_index_file(aggregate_path, index_dir)
    records = [_source_record(project_root, storage_root, index_dir, source) for source in aggregate_data.get("sources", [])]

    manifest = normalize_excellent_bid_manifest({
        "schema_version": "excellent_bid_library_manifest_v1",
        "library_id": "excellent_bid_material_library",
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "migration_source": str(_relative_to_project(project_root, aggregate_path)),
        "aggregate_index_uri": _storage_uri(storage_root, aggregate_copy),
        "source_count": len(records),
        "slice_count": sum(item["slice_count"] for item in records),
        "table_count": sum(item["table_count"] for item in records),
        "image_count": sum(item["image_count"] for item in records),
        "warning_count": sum(item["warning_count"] for item in records),
        "sources": records,
    })
    _write_manifest(index_dir / "library_manifest.json", manifest)
    return manifest


def _find_source(manifest: dict[str, Any], source_bid_id: str) -> dict[str, Any] | None:
    return next((item for item in manifest.get("sources", []) if item.get("source_bid_id") == source_bid_id), None)


def _merge_source_metadata(record: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    metadata_keys = {
        "knowledge_type",
        "knowledge_type_label",
        "project_type",
        "project_type_label",
        "bid_type",
        "bid_type_label",
        "allow_image_reuse",
        "desensitized_confirmed",
        "remarks",
        "uploaded_at",
        "status",
        "status_label",
        "original_file_names",
        "original_paths",
        "original_uris",
        "warnings",
    }
    merged = dict(record)
    for key in metadata_keys:
        if key in override:
            merged[key] = override[key]
    if override.get("title"):
        merged["title"] = override["title"]
    return merged


def _manifest_should_migrate_to_preferred_index(manifest: dict[str, Any], project_root: Path) -> bool:
    preferred_path = _output_root(project_root) / "json" / PREFERRED_AGGREGATE_INDEX
    if not preferred_path.exists():
        return False
    migration_source = str(manifest.get("migration_source") or "").replace("\\", "/")
    if migration_source.endswith(PREFERRED_AGGREGATE_INDEX):
        return False
    source_ids = {str(item.get("source_bid_id") or "") for item in manifest.get("sources", []) if isinstance(item, dict)}
    return "SRC0002" in source_ids or "SRC0003" not in source_ids


def _load_source_slices(storage_root: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    index_uri = source.get("index_uri")
    if not index_uri or not str(index_uri).startswith("local://"):
        return []
    index_path = storage_root / str(index_uri).removeprefix("local://")
    if not index_path.exists():
        return []
    data = json.loads(index_path.read_text(encoding="utf-8"))
    slices = data.get("slices", [])
    return slices if isinstance(slices, list) else []


def _slice_summary(item: dict[str, Any]) -> dict[str, Any]:
    section_path = [str(value) for value in item.get("section_path", []) if value]
    title = item.get("title") or item.get("clean_title") or (section_path[-1] if section_path else "未命名章节")
    paragraphs = item.get("paragraphs") if isinstance(item.get("paragraphs"), list) else []
    text_preview = _text_preview(item, paragraphs)
    return {
        "slice_id": item.get("material_slice_id") or item.get("slice_id") or item.get("fusion_slice_id") or item.get("pdf_slice_id"),
        "title": str(title),
        "level": item.get("level"),
        "section_path": section_path,
        "paragraph_count": _int(item.get("paragraph_count")),
        "paragraph_char_count": _int(item.get("paragraph_char_count")),
        "table_count": _int(item.get("table_count") or item.get("docx_table_count") or item.get("subtree_table_count") or item.get("docx_subtree_table_count") or item.get("pdf_table_like_count")),
        "image_count": _int(item.get("image_count") or item.get("docx_image_count") or item.get("subtree_image_count") or item.get("docx_subtree_image_count") or item.get("pdf_image_count")),
        "reuse_level": item.get("reuse_level"),
        "project_specific_risk": item.get("project_specific_risk"),
        "match_status": item.get("match_status") or item.get("match"),
        "start_page": item.get("start_page"),
        "end_page": item.get("end_page"),
        "text_preview": text_preview,
    }


def _match_score(item: dict[str, Any], keywords: list[str]) -> int:
    if not keywords:
        return 1
    haystack = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("clean_title") or ""),
            " ".join(str(value) for value in item.get("section_path", []) if value),
            str(item.get("search_text") or ""),
            " ".join(str(value) for value in item.get("keywords", []) if value),
            _text_preview(item, item.get("paragraphs") if isinstance(item.get("paragraphs"), list) else []),
        ]
    ).lower()
    return sum(haystack.count(keyword) for keyword in keywords)


def _text_preview(item: dict[str, Any], paragraphs: list[Any]) -> str:
    if item.get("search_text"):
        return _truncate(str(item["search_text"]))
    pieces: list[str] = []
    for paragraph in paragraphs[:8]:
        if isinstance(paragraph, str):
            pieces.append(paragraph)
        elif isinstance(paragraph, dict):
            pieces.append(str(paragraph.get("text") or paragraph.get("content") or ""))
    return _truncate(" ".join(piece.strip() for piece in pieces if piece and piece.strip()))


def _truncate(value: str, limit: int = 220) -> str:
    text = " ".join(value.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _known_or_default(value: Any, labels: dict[str, str], default: str) -> str:
    key = str(value or "").strip()
    return key if key in labels else default


def _normalize_source_status(value: Any) -> str:
    text = str(value or "").strip()
    legacy_map = {
        "已入库": "ready",
        "待复核": "pending_review",
        "解析中": "processing",
        "入库失败": "failed",
    }
    if text in legacy_map:
        return legacy_map[text]
    if text in EXCELLENT_BID_STATUS_LABELS:
        return text
    return "ready"


def _library_quality_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    source_count = len(sources)
    ready_sources = [item for item in sources if item.get("status") == "ready"]
    pending_sources = [item for item in sources if item.get("status") in {"pending_review", "processing"}]
    failed_sources = [item for item in sources if item.get("status") == "failed"]
    desensitized_count = sum(1 for item in sources if item.get("desensitized_confirmed") is True)
    risky_sources = [item for item in sources if item.get("quality_level") in {"risk", "blocked"}]
    total_slices = sum(_int(item.get("slice_count")) for item in sources)
    knowledge_type_counts: dict[str, int] = {}
    for item in sources:
        label = str(item.get("knowledge_type_label") or item.get("knowledge_type") or "未分类")
        knowledge_type_counts[label] = knowledge_type_counts.get(label, 0) + 1
    readiness_score = 0
    if source_count:
        readiness_score += 30
    if total_slices:
        readiness_score += 25
    if source_count and len(ready_sources) == source_count:
        readiness_score += 20
    elif ready_sources:
        readiness_score += 10
    if source_count and desensitized_count == source_count:
        readiness_score += 15
    elif desensitized_count:
        readiness_score += 7
    if not risky_sources and source_count:
        readiness_score += 10
    readiness_score = min(100, readiness_score)
    if not source_count:
        level = "empty"
        label = "待入库"
    elif risky_sources or failed_sources:
        level = "risk"
        label = "需治理"
    elif pending_sources:
        level = "review"
        label = "待复核"
    elif readiness_score >= 85:
        level = "ready"
        label = "可用"
    else:
        level = "building"
        label = "建设中"
    advice = _library_quality_advice(level, source_count, total_slices, len(risky_sources), len(pending_sources))
    return {
        "level": level,
        "label": label,
        "readiness_score": readiness_score,
        "source_count": source_count,
        "ready_source_count": len(ready_sources),
        "pending_review_count": len(pending_sources),
        "failed_source_count": len(failed_sources),
        "risk_source_count": len(risky_sources),
        "desensitized_count": desensitized_count,
        "knowledge_type_counts": knowledge_type_counts,
        "advice": advice,
    }


def _library_quality_advice(level: str, source_count: int, total_slices: int, risk_count: int, pending_count: int) -> str:
    if level == "empty":
        return "建议先上传已脱敏的优秀标书、法规规范或企业制度，资料库才可用于正文增强和风险提示。"
    if risk_count:
        return f"当前有 {risk_count} 份资料存在风险标记，建议先处理脱敏、入库失败或切片为空问题。"
    if pending_count:
        return f"当前有 {pending_count} 份资料仍在解析或待复核，建议复核后再用于批量生成。"
    if total_slices == 0:
        return "资料已登记但缺少章节切片，暂不建议用于正文生成。"
    if source_count < 3:
        return "资料库已可用于试跑，后续建议补充法规规范和企业制度，提升风险评估质量。"
    return "资料库状态较好，可用于正文生成参考和小助手风险提示。"


def _source_quality_flags(source: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if source.get("desensitized_confirmed") is False:
        flags.append("待脱敏确认")
    if source.get("status") == "failed":
        flags.append("入库失败")
    if source.get("status") == "processing":
        flags.append("解析中")
    if source.get("status") == "pending_review":
        flags.append("待人工复核")
    if _int(source.get("slice_count")) <= 0 and source.get("status") != "processing":
        flags.append("缺少章节切片")
    if _int(source.get("warning_count")) > 0:
        flags.append(f"{_int(source.get('warning_count'))} 条提示")
    if source.get("knowledge_type") in {"law_regulation", "technical_standard", "enterprise_policy", "review_rule"}:
        flags.append("适合风险提示")
    if source.get("knowledge_type") == "excellent_bid":
        flags.append("适合正文参考")
    return flags[:6]


def _source_quality_level(source: dict[str, Any], flags: list[str]) -> str:
    if source.get("desensitized_confirmed") is False or source.get("status") == "failed":
        return "blocked"
    if source.get("status") in {"processing", "pending_review"}:
        return "review"
    if "缺少章节切片" in flags:
        return "risk"
    if _int(source.get("warning_count")) > 0:
        return "review"
    return "ready"


def _source_usage_advice(source: dict[str, Any], flags: list[str]) -> str:
    if source.get("desensitized_confirmed") is False:
        return "该资料尚未确认脱敏，不建议进入生成或风险评估流程。"
    if source.get("status") == "failed":
        return "该资料入库失败，需重新上传或查看后台日志。"
    if source.get("status") == "processing":
        return "该资料仍在解析中，解析完成前只作为登记记录。"
    if source.get("status") == "pending_review":
        return "该资料待人工复核，建议确认切片和来源后再用于批量生成。"
    if "缺少章节切片" in flags:
        return "该资料缺少可检索切片，暂不适合用于正文生成。"
    if source.get("knowledge_type") in {"law_regulation", "technical_standard", "enterprise_policy", "review_rule"}:
        return "适合提供合规、规范和评审风险提示，不替代人工审查。"
    if source.get("knowledge_type") == "excellent_bid":
        return "适合作为章节写法、措施表达和企业风格参考。"
    return "可作为一般参考资料，使用前建议核对来源和适用范围。"


def _source_record(project_root: Path, storage_root: Path, index_dir: Path, source: dict[str, Any]) -> dict[str, Any]:
    source_index_path = _project_or_output_path(project_root, source.get("source_index_path"))
    copied_index_path = _copy_index_file(source_index_path, index_dir) if source_index_path and source_index_path.exists() else None
    original_paths = [_project_path(project_root, item) for item in source.get("source_paths", [])]
    existing_original_paths = [item for item in original_paths if item and item.exists()]
    warnings = [str(item) for item in source.get("warnings", [])]
    source_type = source.get("source_type") or "unknown"
    return normalize_excellent_bid_source_metadata({
        "source_bid_id": source.get("source_id") or _slug(source.get("source_name") or "excellent_bid"),
        "title": source.get("source_name") or "未命名优秀标书",
        "source_type": source_type,
        "source_type_label": SOURCE_TYPE_LABELS.get(source_type, source_type),
        "status": "ready",
        "original_file_names": [item.name for item in existing_original_paths],
        "original_paths": [str(_relative_to_project(project_root, item)) for item in existing_original_paths],
        "original_uris": [_storage_uri(storage_root, item) for item in existing_original_paths if _is_under(item, storage_root)],
        "index_file_name": copied_index_path.name if copied_index_path else None,
        "index_uri": _storage_uri(storage_root, copied_index_path) if copied_index_path else None,
        "slice_count": int(source.get("slice_count") or 0),
        "table_count": int(source.get("table_count") or 0),
        "image_count": int(source.get("image_count") or 0),
        "matched_count": int(source.get("matched_count") or 0),
        "ambiguous_count": int(source.get("ambiguous_count") or 0),
        "fallback_count": int(source.get("fallback_count") or 0),
        "unmatched_count": int(source.get("unmatched_count") or 0),
        "warning_count": len(warnings),
        "warnings": warnings[:5],
    })


def _find_aggregate_index(source_dir: Path) -> Path | None:
    for file_name in AGGREGATE_INDEX_CANDIDATES:
        path = source_dir / file_name
        if path.exists():
            return path
    return None


def _copy_index_file(source: Path, index_dir: Path) -> Path:
    target = index_dir / source.name
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        shutil.copy2(source, target)
    return target


def _empty_manifest() -> dict[str, Any]:
    return {
        "schema_version": "excellent_bid_library_manifest_v1",
        "library_id": "excellent_bid_material_library",
        "status": "empty",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "migration_source": None,
        "aggregate_index_uri": None,
        "source_count": 0,
        "slice_count": 0,
        "table_count": 0,
        "image_count": 0,
        "warning_count": 0,
        "quality_summary": _library_quality_summary([]),
        "sources": [],
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_path(project_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _project_or_output_path(project_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = project_root / path
    if project_path.exists():
        return project_path
    normalized = path.as_posix()
    if normalized.startswith("outputs/"):
        output_relative = Path(*path.parts[1:])
        output_path = _output_root(project_root) / output_relative
        if output_path.exists():
            return output_path
    return project_path


def _output_root(project_root: Path) -> Path:
    value = os.getenv("OUTPUT_DIR")
    path = Path(value) if value else Path("outputs")
    return path if path.is_absolute() else project_root / path


def _relative_to_project(project_root: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return path


def _storage_uri(storage_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        relative = path.resolve().relative_to(storage_root.resolve())
    except ValueError:
        return None
    return "local://" + relative.as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "excellent_bid"
