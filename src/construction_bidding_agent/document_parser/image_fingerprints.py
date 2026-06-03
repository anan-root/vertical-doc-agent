"""优秀标书图片指纹治理工具。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


@dataclass(slots=True)
class ImageFingerprintIndex:
    asset_lookup: dict[str, dict[str, str]]
    byte_lookup: dict[tuple[str, str], bytes]
    fingerprint_cache: dict[tuple[str, str], dict[str, str]]


def enrich_material_library_image_fingerprints(
    material_library: dict[str, Any],
    *,
    raw_root: str | Path,
) -> dict[str, Any]:
    """从原始 DOCX 媒体文件为素材库图片资产补充稳定指纹字段。"""

    index = build_image_fingerprint_index(material_library, raw_root)
    assets = [item for item in material_library.get("image_assets") or [] if isinstance(item, dict)]
    groups = [item for item in material_library.get("image_groups") or [] if isinstance(item, dict)]
    newly_enriched_count = 0
    fingerprinted_asset_count = 0
    missing_assets: list[str] = []
    mismatch_count = 0

    for asset in assets:
        before = image_fingerprint_keys(asset)
        metadata = fingerprint_metadata_for_record(asset, index)
        if not metadata:
            missing_assets.append(str(asset.get("image_asset_id") or asset.get("image_id") or ""))
            continue
        mismatch_count += _count_fingerprint_mismatches(asset, metadata)
        for key in ["canonical_image_id", "sha256", "perceptual_hash"]:
            value = metadata.get(key)
            if value and not asset.get(key):
                asset[key] = value
        if metadata.get("sha256") and not asset.get("fingerprint_source"):
            asset["fingerprint_source"] = "docx_media"
        if image_fingerprint_keys(asset) != before:
            newly_enriched_count += 1
        if asset.get("sha256"):
            fingerprinted_asset_count += 1

    group_enriched_count = _propagate_group_fingerprints(groups, assets)
    exact_duplicate_groups = _duplicate_value_groups(assets, "sha256")
    perceptual_duplicate_groups = _duplicate_value_groups(assets, "perceptual_hash")
    cross_source_duplicate_groups = _cross_source_duplicate_groups(assets)
    stats = {
        "enabled": True,
        "source": "material_library_docx_media",
        "asset_count": len(assets),
        "group_count": len(groups),
        "newly_enriched_count": newly_enriched_count,
        "fingerprinted_asset_count": fingerprinted_asset_count,
        "missing_count": len(missing_assets),
        "mismatch_count": mismatch_count,
        "group_enriched_count": group_enriched_count,
        "loaded_media_count": len(index.byte_lookup),
        "exact_duplicate_group_count": len(exact_duplicate_groups),
        "perceptual_duplicate_group_count": len(perceptual_duplicate_groups),
        "cross_source_duplicate_group_count": len(cross_source_duplicate_groups),
        "missing_asset_ids_sample": [item for item in missing_assets if item][:30],
        "exact_duplicate_groups_sample": exact_duplicate_groups[:20],
        "perceptual_duplicate_groups_sample": perceptual_duplicate_groups[:20],
        "cross_source_duplicate_groups_sample": cross_source_duplicate_groups[:20],
    }
    material_library["image_fingerprint_summary"] = stats
    return stats


def build_image_fingerprint_index(
    material_library: dict[str, Any] | str | Path,
    raw_root: str | Path,
) -> ImageFingerprintIndex:
    data = _load_material_library(material_library)
    source_paths = material_library_source_paths(data, raw_root)
    asset_lookup: dict[str, dict[str, str]] = {}
    for asset in data.get("image_assets") or []:
        if not isinstance(asset, dict):
            continue
        metadata = {
            "source_id": str(asset.get("source_id") or ""),
            "part_name": str(asset.get("part_name") or ""),
            "image_asset_id": str(asset.get("image_asset_id") or ""),
            "image_id": str(asset.get("image_id") or ""),
        }
        for key in image_asset_lookup_keys(asset):
            asset_lookup[key] = metadata
    return ImageFingerprintIndex(
        asset_lookup=asset_lookup,
        byte_lookup=load_docx_image_bytes(source_paths),
        fingerprint_cache={},
    )


def material_library_source_paths(data: dict[str, Any], raw_root: str | Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if not source_id:
            continue
        paths = [
            resolved
            for item in source.get("source_paths") or []
            for resolved in [_resolve_source_path(Path(str(item)), Path(raw_root))]
            if resolved is not None and resolved.suffix.lower() == ".docx"
        ]
        result[source_id] = paths
    return result


def load_docx_image_bytes(source_paths: dict[str, list[Path]]) -> dict[tuple[str, str], bytes]:
    lookup: dict[tuple[str, str], bytes] = {}
    for source_id, paths in source_paths.items():
        for path in paths:
            try:
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        if not name.startswith("word/media/"):
                            continue
                        image_bytes = archive.read(name)
                        lookup[(source_id, name)] = image_bytes
                        lookup.setdefault(("", name), image_bytes)
                        short_name = name.removeprefix("word/")
                        lookup[(source_id, short_name)] = image_bytes
                        lookup.setdefault(("", short_name), image_bytes)
            except zipfile.BadZipFile:
                continue
    return lookup


def fingerprint_metadata_for_record(
    record: dict[str, Any],
    index: ImageFingerprintIndex,
) -> dict[str, str]:
    metadata = asset_metadata_for_record(record, index)
    source_id = metadata.get("source_id") or str(record.get("source_bid_id") or record.get("source_id") or "")
    part_name = metadata.get("part_name") or str(record.get("source_part_name") or record.get("part_name") or "")
    image_bytes = image_bytes_for_ref(source_id, part_name, index)
    if not image_bytes:
        return {}
    cache_key = (source_id, part_name)
    if cache_key not in index.fingerprint_cache:
        index.fingerprint_cache[cache_key] = image_fingerprint_metadata(image_bytes)
    return index.fingerprint_cache[cache_key]


def asset_metadata_for_record(record: dict[str, Any], index: ImageFingerprintIndex) -> dict[str, str]:
    source_id = str(record.get("source_bid_id") or record.get("source_id") or "")
    part_name = str(record.get("source_part_name") or record.get("part_name") or "")
    keys = [
        lookup_key("image_asset_id", record.get("image_asset_id")),
        lookup_key("image_id", record.get("image_id")),
        lookup_key("source_part_name", f"{source_id}|{part_name}") if source_id and part_name else None,
        lookup_key("part_name", f"{source_id}|{part_name}") if source_id and part_name else None,
        lookup_key("source_part_name", part_name),
        lookup_key("part_name", part_name),
    ]
    for key in keys:
        if key and key in index.asset_lookup:
            return index.asset_lookup[key]
    return {"source_id": source_id, "part_name": part_name}


def image_bytes_for_ref(source_id: str, part_name: str, index: ImageFingerprintIndex) -> bytes | None:
    if not part_name:
        return None
    names = [part_name]
    if part_name.startswith("media/"):
        names.append(f"word/{part_name}")
    elif part_name.startswith("word/media/"):
        names.append(part_name.removeprefix("word/"))
    for name in names:
        image_bytes = index.byte_lookup.get((source_id, name)) or index.byte_lookup.get(("", name))
        if image_bytes:
            return image_bytes
    return None


def image_fingerprint_metadata(image_bytes: bytes) -> dict[str, str]:
    sha256 = hashlib.sha256(image_bytes).hexdigest()
    perceptual_hash = average_image_hash(image_bytes)
    metadata = {
        "sha256": sha256,
        "canonical_image_id": f"sha256:{sha256}",
    }
    if perceptual_hash:
        metadata["perceptual_hash"] = perceptual_hash
    return metadata


def average_image_hash(image_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            grayscale = image.convert("L").resize((8, 8))
            pixels = list(grayscale.getdata())
    except (UnidentifiedImageError, OSError, ValueError):
        return ""
    if not pixels:
        return ""
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= avg else "0" for pixel in pixels)
    return f"{int(bits, 2):016x}"


def image_fingerprint_keys(record: dict[str, Any]) -> set[str]:
    return {
        key
        for key in [
            lookup_key("canonical_image_id", record.get("canonical_image_id")),
            lookup_key("sha256", record.get("sha256")),
            lookup_key("perceptual_hash", record.get("perceptual_hash")),
        ]
        if key
    }


def image_asset_lookup_keys(asset: dict[str, Any]) -> set[str]:
    source_id = str(asset.get("source_id") or "")
    part_name = str(asset.get("part_name") or "")
    return {
        key
        for key in [
            lookup_key("image_asset_id", asset.get("image_asset_id")),
            lookup_key("image_id", asset.get("image_id")),
            lookup_key("source_part_name", part_name),
            lookup_key("part_name", part_name),
            lookup_key("source_part_name", f"{source_id}|{part_name}") if source_id and part_name else None,
            lookup_key("part_name", f"{source_id}|{part_name}") if source_id and part_name else None,
        ]
        if key
    }


def lookup_key(kind: str, value: Any) -> str | None:
    text = str(value or "").strip()
    return f"{kind}:{text}" if text else None


def render_material_library_image_fingerprint_report(
    material_library: dict[str, Any],
    stats: dict[str, Any],
) -> str:
    lines = [
        "# 优秀标书素材库图片指纹治理报告",
        "",
        f"- 素材库 ID：`{material_library.get('library_id') or '-'}`",
        f"- 图片资产数：{stats.get('asset_count')}",
        f"- 套图组数：{stats.get('group_count')}",
        f"- 已具备指纹图片数：{stats.get('fingerprinted_asset_count')}",
        f"- 本次新增/补齐指纹数：{stats.get('newly_enriched_count')}",
        f"- 缺失指纹数：{stats.get('missing_count')}",
        f"- 指纹冲突数：{stats.get('mismatch_count')}",
        f"- 已补齐套图组指纹数：{stats.get('group_enriched_count')}",
        f"- 精确重复图片组数：{stats.get('exact_duplicate_group_count')}",
        f"- 感知哈希重复图片组数：{stats.get('perceptual_duplicate_group_count')}",
        f"- 跨来源重复图片组数：{stats.get('cross_source_duplicate_group_count')}",
        "",
        "## 来源文件",
        "",
    ]
    for source in material_library.get("sources") or []:
        if not isinstance(source, dict):
            continue
        paths = "；".join(str(item) for item in source.get("source_paths") or [])
        lines.append(f"- {source.get('source_id')}: {source.get('source_name')}，{paths}")

    lines.extend(["", "## 精确重复图片样例", ""])
    for item in stats.get("exact_duplicate_groups_sample") or []:
        lines.append(
            f"- {item.get('value')}: count={item.get('count')}，"
            f"sources={','.join(item.get('source_ids') or [])}，"
            f"assets={','.join((item.get('image_asset_ids') or [])[:8])}"
        )
    if not stats.get("exact_duplicate_groups_sample"):
        lines.append("- 暂无。")

    lines.extend(["", "## 跨来源重复图片样例", ""])
    for item in stats.get("cross_source_duplicate_groups_sample") or []:
        lines.append(
            f"- {item.get('value')}: sources={','.join(item.get('source_ids') or [])}，"
            f"assets={','.join((item.get('image_asset_ids') or [])[:8])}"
        )
    if not stats.get("cross_source_duplicate_groups_sample"):
        lines.append("- 暂无。")

    if stats.get("missing_asset_ids_sample"):
        lines.extend(["", "## 缺失指纹图片样例", ""])
        for asset_id in stats.get("missing_asset_ids_sample") or []:
            lines.append(f"- {asset_id}")
    lines.append("")
    return "\n".join(lines)


def _propagate_group_fingerprints(groups: list[dict[str, Any]], assets: list[dict[str, Any]]) -> int:
    asset_by_id = {str(asset.get("image_asset_id") or ""): asset for asset in assets}
    enriched_count = 0
    for group in groups:
        member_assets = [
            asset_by_id[asset_id]
            for asset_id in [str(item) for item in group.get("image_asset_ids") or [] if str(item).strip()]
            if asset_id in asset_by_id
        ]
        canonical_ids = _unique_values(asset.get("canonical_image_id") for asset in member_assets)
        sha256_values = _unique_values(asset.get("sha256") for asset in member_assets)
        perceptual_hash_values = _unique_values(asset.get("perceptual_hash") for asset in member_assets)
        if not canonical_ids and not sha256_values and not perceptual_hash_values:
            continue
        if canonical_ids and not group.get("canonical_image_ids"):
            group["canonical_image_ids"] = canonical_ids
        if sha256_values and not group.get("sha256_values"):
            group["sha256_values"] = sha256_values
        if perceptual_hash_values and not group.get("perceptual_hash_values"):
            group["perceptual_hash_values"] = perceptual_hash_values
        if canonical_ids and not group.get("group_canonical_image_key"):
            group["group_canonical_image_key"] = _group_canonical_image_key(canonical_ids)
        if not group.get("fingerprint_source"):
            group["fingerprint_source"] = "docx_media"
        enriched_count += 1
    return enriched_count


def _group_canonical_image_key(canonical_ids: list[str]) -> str:
    joined = "|".join(sorted(canonical_ids))
    return f"sha256-set:{hashlib.sha256(joined.encode('utf-8')).hexdigest()}"


def _count_fingerprint_mismatches(asset: dict[str, Any], metadata: dict[str, str]) -> int:
    mismatches = 0
    for key in ["canonical_image_id", "sha256", "perceptual_hash"]:
        current = str(asset.get(key) or "").strip()
        expected = str(metadata.get(key) or "").strip()
        if current and expected and current != expected:
            mismatches += 1
    return mismatches


def _duplicate_value_groups(assets: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        value = str(asset.get(key) or "").strip()
        if value:
            buckets[value].append(asset)
    result = [_duplicate_group_summary(value, items) for value, items in buckets.items() if len(items) > 1]
    result.sort(key=lambda item: (-int(item["count"]), str(item["value"])))
    return result


def _cross_source_duplicate_groups(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exact_groups = _duplicate_value_groups(assets, "sha256")
    return [group for group in exact_groups if len(group.get("source_ids") or []) > 1]


def _duplicate_group_summary(value: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = sorted({str(item.get("source_id") or "") for item in items if str(item.get("source_id") or "")})
    return {
        "value": value,
        "count": len(items),
        "source_ids": source_ids,
        "image_asset_ids": [str(item.get("image_asset_id") or "") for item in items if item.get("image_asset_id")],
        "captions": _most_common_values(item.get("caption_actual") or item.get("semantic_text") for item in items),
    }


def _most_common_values(values: Any) -> list[str]:
    counter = Counter(str(value).strip() for value in values if str(value or "").strip())
    return [value for value, _ in counter.most_common(8)]


def _unique_values(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _resolve_source_path(path: Path, raw_root: Path) -> Path | None:
    if path.is_absolute() and path.exists():
        return path
    candidates = [path, Path.cwd() / path]
    search_root = raw_root if raw_root.is_absolute() else Path.cwd() / raw_root
    candidates.append(search_root / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if search_root.exists():
        matches = list(search_root.rglob(path.name))
        if matches:
            return matches[0]
    return None


def _load_material_library(material_library: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(material_library, dict):
        return material_library
    path = Path(material_library)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
