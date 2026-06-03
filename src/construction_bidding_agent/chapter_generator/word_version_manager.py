"""Word 成稿版本与摘要管理。"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "word_quality_summary_v0.1"
DEFAULT_TIMEZONE = "Asia/Shanghai"
SYSTEM_GENERATED_NAME = "system_generated.docx"
REVIEW_EDITING_NAME = "review_editing.docx"
FINAL_EXPORT_NAME = "final_export.docx"
SUMMARY_NAME = "word_quality_summary.json"
VERSIONS_DIR_NAME = "versions"


def word_version_paths(documents_dir: str | Path) -> dict[str, Path]:
    """返回项目 documents 目录内的 Word 版本路径。"""

    root = Path(documents_dir)
    return {
        "documents_dir": root,
        "system_generated": root / SYSTEM_GENERATED_NAME,
        "review_editing": root / REVIEW_EDITING_NAME,
        "final_export": root / FINAL_EXPORT_NAME,
        "summary": root / SUMMARY_NAME,
        "versions_dir": root / VERSIONS_DIR_NAME,
    }


def ensure_word_versions_dir(documents_dir: str | Path) -> Path:
    """确保 Word 历史版本目录存在。"""

    versions_dir = word_version_paths(documents_dir)["versions_dir"]
    versions_dir.mkdir(parents=True, exist_ok=True)
    return versions_dir


def publish_system_generated_docx(source_docx: str | Path, documents_dir: str | Path) -> Path:
    """把当前系统导出的 Word 同步为 system_generated.docx，并归档一个版本副本。"""

    source = Path(source_docx)
    paths = word_version_paths(documents_dir)
    paths["documents_dir"].mkdir(parents=True, exist_ok=True)
    target = paths["system_generated"]
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    archive_word_version(target, documents_dir, "system_generated")
    return target


def archive_word_version(source_docx: str | Path, documents_dir: str | Path, version_kind: str) -> Path | None:
    """把指定 Word 文件复制到 versions 目录，文件不存在则返回 None。"""

    source = Path(source_docx)
    if not source.exists():
        return None
    versions_dir = ensure_word_versions_dir(documents_dir)
    next_index = _next_version_index(versions_dir)
    target = versions_dir / f"v{next_index:03d}_{_safe_version_kind(version_kind)}.docx"
    shutil.copyfile(source, target)
    return target


def write_word_quality_summary(
    documents_dir: str | Path,
    *,
    draft_json: dict[str, Any] | str | Path | None = None,
    render_stats: dict[str, Any] | None = None,
    extra_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成并写入成稿摘要 JSON。"""

    root = Path(documents_dir)
    root.mkdir(parents=True, exist_ok=True)
    draft_data = _load_json_if_path(draft_json)
    paths = word_version_paths(root)
    _promote_legacy_word_draft(paths)
    version_states = _version_states(paths)
    latest_version = _latest_available_version(version_states)
    system_docx_stats = _docx_stats(paths["system_generated"])
    render_stats = dict(render_stats or {})
    stats = {
        "paragraph_count": int(render_stats.get("paragraph_count") or system_docx_stats.get("paragraph_count") or 0),
        "table_count": int(render_stats.get("table_count") or system_docx_stats.get("table_count") or 0),
        "image_count": int(render_stats.get("rendered_image_count") or system_docx_stats.get("image_count") or 0),
        "heading_count": int(render_stats.get("heading_count") or system_docx_stats.get("heading_count") or 0),
        "heading1_count": int(render_stats.get("heading1_count") or system_docx_stats.get("heading1_count") or 0),
        "heading2_count": int(render_stats.get("heading2_count") or system_docx_stats.get("heading2_count") or 0),
        "heading3_count": int(render_stats.get("heading3_count") or system_docx_stats.get("heading3_count") or 0),
        "missing_image_count": int(render_stats.get("missing_image_count") or 0),
        "placeholder_count": int(render_stats.get("placeholder_count") or 0),
    }
    outline_consistency = _outline_consistency_summary(draft_data, render_stats, system_docx_stats)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "word_status": "ready" if latest_version else "missing",
        "latest_version": latest_version,
        "versions": version_states,
        "stats": stats,
        "toc_status": {
            "enabled": True,
            "title": "目录",
            "levels": 3,
            "inserted": bool(system_docx_stats.get("toc_inserted")),
            "page_numbers_status": "pending_update" if system_docx_stats.get("toc_inserted") else "not_inserted",
            "body_page_restart_at": system_docx_stats.get("body_page_restart_at"),
        },
        "outline_consistency": outline_consistency,
        "review_tips": _review_tips(stats, outline_consistency),
    }
    if extra_summary:
        summary["source_summary"] = extra_summary
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def read_word_quality_summary(documents_dir: str | Path) -> dict[str, Any]:
    """读取成稿摘要；不存在时即时生成一个轻量摘要。"""

    root = Path(documents_dir)
    paths = word_version_paths(root)
    _promote_legacy_word_draft(paths)
    summary_path = paths["summary"]
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if _summary_matches_current_files(summary, paths):
            return summary
    return write_word_quality_summary(documents_dir)


def _promote_legacy_word_draft(paths: dict[str, Path]) -> None:
    """兼容旧版导出的 technical_bid_draft.docx，纳入新版 Word 版本管理。"""

    system_generated = paths["system_generated"]
    legacy = paths["documents_dir"] / "technical_bid_draft.docx"
    if system_generated.exists() or not legacy.exists():
        return
    shutil.copyfile(legacy, system_generated)


def _summary_matches_current_files(summary: dict[str, Any], paths: dict[str, Path]) -> bool:
    versions = summary.get("versions") if isinstance(summary.get("versions"), dict) else {}
    latest_version = summary.get("latest_version")
    current_states = _version_states(paths)
    current_latest = _latest_available_version(current_states)
    if latest_version != current_latest:
        return False
    for key, state in current_states.items():
        stored = versions.get(key) if isinstance(versions.get(key), dict) else {}
        if bool(stored.get("exists")) != bool(state.get("exists")):
            return False
        if state.get("exists") and int(stored.get("size") or 0) != int(state.get("size") or 0):
            return False
    return True


def _version_states(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {
        key: _file_state(path)
        for key, path in paths.items()
        if key in {"system_generated", "review_editing", "final_export"}
    }


def _file_state(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "file_name": path.name,
        "exists": exists,
        "size": path.stat().st_size if exists else 0,
        "modified_at": _mtime_iso(path) if exists else None,
    }


def _latest_available_version(version_states: dict[str, dict[str, Any]]) -> str | None:
    for key in ["final_export", "review_editing", "system_generated"]:
        if version_states.get(key, {}).get("exists"):
            return key
    return None


def _docx_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
            rel_names = [name for name in archive.namelist() if name.startswith("word/media/")]
    except Exception:
        return {}
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError:
        return {}
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = root.findall(".//w:p", ns)
    tables = root.findall(".//w:tbl", ns)
    styles = [
        style.attrib.get(f"{{{ns['w']}}}val", "")
        for style in root.findall(".//w:pStyle", ns)
    ]
    text = document_xml.decode("utf-8", errors="ignore")
    return {
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "image_count": len(rel_names),
        "heading_count": sum(1 for style in styles if style.startswith("Heading")),
        "heading1_count": styles.count("Heading1"),
        "heading2_count": styles.count("Heading2"),
        "heading3_count": styles.count("Heading3"),
        "toc_inserted": "TOC" in text,
        "body_page_restart_at": _body_page_restart_at(root, ns),
    }


def _body_page_restart_at(root: ET.Element, ns: dict[str, str]) -> int | None:
    for pg_num_type in root.findall(".//w:pgNumType", ns):
        value = pg_num_type.attrib.get(f"{{{ns['w']}}}start")
        if value:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _outline_consistency_summary(
    draft_data: dict[str, Any],
    render_stats: dict[str, Any],
    docx_stats: dict[str, Any],
) -> dict[str, Any]:
    chapters = [chapter for chapter in draft_data.get("chapters") or [] if isinstance(chapter, dict)]
    level1_count = len(chapters)
    level2_count = sum(
        1
        for chapter in chapters
        for section in chapter.get("sections") or []
        if isinstance(section, dict) and int(section.get("level") or 2) <= 2
    )
    level3_count = sum(
        1
        for chapter in chapters
        for section in chapter.get("sections") or []
        if isinstance(section, dict) and int(section.get("level") or 2) > 2
    )
    expected_heading_count = level1_count + level2_count + level3_count
    actual_heading_count = int(render_stats.get("heading_count") or docx_stats.get("heading_count") or 0)
    empty_summary = (
        draft_data.get("full_bid_export_summary", {}).get("empty_heading_summary", {})
        if isinstance(draft_data.get("full_bid_export_summary"), dict)
        else {}
    )
    warnings = []
    if actual_heading_count and expected_heading_count and actual_heading_count != expected_heading_count:
        warnings.append(f"正式标题数量不一致：预计 {expected_heading_count} 个，Word 中 {actual_heading_count} 个。")
    if int(empty_summary.get("empty_heading_count") or 0) > 0:
        warnings.append(f"存在空标题 {empty_summary.get('empty_heading_count')} 个。")
    return {
        "level1_count": level1_count,
        "level2_count": level2_count,
        "level3_count": level3_count,
        "expected_heading_count": expected_heading_count,
        "actual_heading_count": actual_heading_count,
        "heading_count_matched": not expected_heading_count or not actual_heading_count or expected_heading_count == actual_heading_count,
        "empty_heading_count": int(empty_summary.get("empty_heading_count") or 0),
        "warnings": warnings,
    }


def _review_tips(stats: dict[str, Any], outline_consistency: dict[str, Any]) -> list[str]:
    tips = []
    if outline_consistency.get("warnings"):
        tips.append("目录与正文标题存在差异，建议复核正式标题。")
    if int(stats.get("missing_image_count") or 0) > 0:
        tips.append("存在图片源未定位，建议检查素材库引用。")
    if int(stats.get("placeholder_count") or 0) > 0:
        tips.append("存在图片或内容占位，建议人工补齐项目专属资料。")
    return tips[:8]


def _load_json_if_path(data: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, (str, Path)):
        path = Path(data)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _next_version_index(versions_dir: Path) -> int:
    indexes = []
    for path in versions_dir.glob("v*_*.docx"):
        prefix = path.name.split("_", 1)[0].removeprefix("v")
        if prefix.isdigit():
            indexes.append(int(prefix))
    return max(indexes, default=0) + 1


def _safe_version_kind(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value) or "word"


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).replace(microsecond=0).isoformat()


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(DEFAULT_TIMEZONE)).replace(microsecond=0).isoformat()
