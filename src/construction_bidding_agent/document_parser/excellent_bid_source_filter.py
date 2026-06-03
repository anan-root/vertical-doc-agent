"""优秀标书素材库来源过滤工具。"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_ENABLED_SOURCE_NAMES = {
    "总体施工方案",
    "郑轨·云庭01标段技术标投标文件",
}


def filter_excellent_bid_material_library(
    material_library: dict[str, Any],
    *,
    enabled_source_names: set[str] | None = None,
    enabled_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """按白名单过滤优秀标书素材库，保留完整切片、图片、套图关系。"""

    data = copy.deepcopy(material_library)
    names = enabled_source_names or DEFAULT_ENABLED_SOURCE_NAMES
    ids = enabled_source_ids or set()
    enabled_sources = [
        source
        for source in data.get("sources") or []
        if _source_enabled(source, enabled_source_names=names, enabled_source_ids=ids)
    ]
    enabled_ids = {str(source.get("source_id") or "") for source in enabled_sources if str(source.get("source_id") or "")}
    disabled_sources = [
        source
        for source in data.get("sources") or []
        if str(source.get("source_id") or "") not in enabled_ids
    ]

    data["sources"] = enabled_sources
    data["slices"] = [
        item for item in data.get("slices") or [] if str(item.get("source_id") or "") in enabled_ids
    ]
    data["image_assets"] = [
        item for item in data.get("image_assets") or [] if str(item.get("source_id") or "") in enabled_ids
    ]
    data["image_groups"] = [
        item for item in data.get("image_groups") or [] if str(item.get("source_id") or "") in enabled_ids
    ]
    data["source_count"] = len(enabled_sources)
    data["slice_count"] = len(data["slices"])
    data["table_count"] = sum(_int(item.get("table_count")) for item in data["slices"])
    data["image_count"] = sum(_int(item.get("image_count")) for item in data["slices"])
    data["docx_table_count"] = sum(_int(item.get("docx_table_count") or item.get("table_count")) for item in data["slices"])
    data["docx_image_count"] = sum(_int(item.get("docx_image_count") or item.get("image_count")) for item in data["slices"])
    data["pdf_fallback_table_count"] = 0
    data["pdf_fallback_image_count"] = 0
    data["pdf_reference_table_like_count"] = 0
    data["pdf_reference_image_count"] = 0
    data["image_asset_count"] = len(data["image_assets"])
    data["image_group_count"] = len(data["image_groups"])
    data["source_type_counts"] = dict(Counter(str(item.get("source_type") or "unknown") for item in enabled_sources))
    data["material_quality_counts"] = dict(Counter(str(item.get("material_quality") or "unknown") for item in data["slices"]))
    data["source_filter"] = {
        "enabled": True,
        "mode": "whitelist",
        "enabled_source_names": sorted(names),
        "enabled_source_ids": sorted(enabled_ids),
        "disabled_sources": [
            {
                "source_id": source.get("source_id"),
                "source_name": source.get("source_name"),
                "source_type": source.get("source_type"),
                "reason": "一期弃用 PDF/转 Word 融合来源，避免污染图文块索引。",
            }
            for source in disabled_sources
        ],
    }
    warnings = list(data.get("warnings") or [])
    for source in disabled_sources:
        warnings.append(
            f"已弃用优秀标书来源：{source.get('source_id')} {source.get('source_name')} ({source.get('source_type')})"
        )
    data["warnings"] = warnings
    return data


def filter_excellent_bid_material_library_file(
    source_json: str | Path,
    target_json: str | Path,
    report_path: str | Path | None = None,
    *,
    enabled_source_names: set[str] | None = None,
    enabled_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    source = Path(source_json)
    data = json.loads(source.read_text(encoding="utf-8"))
    filtered = filter_excellent_bid_material_library(
        data,
        enabled_source_names=enabled_source_names,
        enabled_source_ids=enabled_source_ids,
    )
    target = Path(target_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path:
        report = Path(report_path)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(render_source_filter_report(filtered), encoding="utf-8")
    return filtered


def render_source_filter_report(material_library: dict[str, Any]) -> str:
    source_filter = material_library.get("source_filter") or {}
    lines = [
        "# 优秀标书素材源治理报告",
        "",
        "## 启用来源",
        "",
        "| source_id | 来源名称 | 类型 | 切片 | 表格 | 图片 | 套图 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    group_counts = Counter(str(group.get("source_id") or "") for group in material_library.get("image_groups") or [])
    for source in material_library.get("sources") or []:
        source_id = str(source.get("source_id") or "")
        lines.append(
            f"| {source_id} | {source.get('source_name') or ''} | {source.get('source_type') or ''} | "
            f"{source.get('slice_count') or 0} | {source.get('table_count') or 0} | "
            f"{source.get('image_count') or 0} | {group_counts.get(source_id, 0)} |"
        )
    lines.extend(
        [
            "",
            "## 弃用来源",
            "",
            "| source_id | 来源名称 | 类型 | 原因 |",
            "|---|---|---|---|",
        ]
    )
    for source in source_filter.get("disabled_sources") or []:
        lines.append(
            f"| {source.get('source_id') or ''} | {source.get('source_name') or ''} | "
            f"{source.get('source_type') or ''} | {source.get('reason') or ''} |"
        )
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 启用来源数：{material_library.get('source_count', 0)}",
            f"- 切片数：{material_library.get('slice_count', 0)}",
            f"- 表格数：{material_library.get('table_count', 0)}",
            f"- 图片数：{material_library.get('image_count', 0)}",
            f"- 图片资产数：{material_library.get('image_asset_count', 0)}",
            f"- 套图数：{material_library.get('image_group_count', 0)}",
            "",
        ]
    )
    return "\n".join(lines)


def _source_enabled(
    source: dict[str, Any],
    *,
    enabled_source_names: set[str],
    enabled_source_ids: set[str],
) -> bool:
    source_id = str(source.get("source_id") or "")
    source_name = str(source.get("source_name") or "")
    if source_id and source_id in enabled_source_ids:
        return True
    return any(name and name in source_name for name in enabled_source_names)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
