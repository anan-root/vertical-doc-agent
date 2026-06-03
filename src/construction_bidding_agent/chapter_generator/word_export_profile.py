"""Word 导出格式配置。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "word_export_profile_v1"
DEFAULT_PROFILE_NAME = "默认技术标格式"


DEFAULT_WORD_EXPORT_PROFILE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "profile_name": DEFAULT_PROFILE_NAME,
    "page": {
        "paper_size": "A4",
        "orientation": "portrait",
        "top_margin_cm": 2.54,
        "bottom_margin_cm": 2.54,
        "left_margin_cm": 3.18,
        "right_margin_cm": 3.18,
        "header_distance_cm": 1.5,
        "footer_distance_cm": 1.75,
    },
    "toc": {
        "enabled": True,
        "title": "目录",
        "levels": 3,
        "separate_page": True,
        "body_starts_new_page": True,
        "show_page_numbers": True,
        "right_align_page_numbers": True,
        "use_tab_leader": True,
        "body_page_number_restart": True,
        "body_page_number_start": 1,
        "toc_page_number_style": "roman_lower",
        "body_page_number_style": "decimal",
    },
    "heading_1": {
        "font_family": "宋体",
        "font_size_pt": 16,
        "font_size_label": "三号",
        "bold": True,
        "color": "C00000",
        "alignment": "center",
        "first_line_indent_chars": 0,
        "line_spacing": 1.35,
        "space_before_pt": 12,
        "space_after_pt": 12,
        "page_break_before": True,
        "numbering": {
            "enabled": True,
            "format": "decimal",
            "suffix": ".",
            "include_parent": False,
        },
    },
    "heading_2": {
        "font_family": "宋体",
        "font_size_pt": 14,
        "font_size_label": "四号",
        "bold": True,
        "color": "002060",
        "alignment": "left",
        "first_line_indent_chars": 0,
        "line_spacing": 1.35,
        "space_before_pt": 12,
        "space_after_pt": 6,
        "page_break_before": False,
        "numbering": {
            "enabled": True,
            "format": "decimal",
            "suffix": ".",
            "include_parent": True,
        },
    },
    "heading_3": {
        "font_family": "宋体",
        "font_size_pt": 14,
        "font_size_label": "四号",
        "bold": True,
        "color": "000000",
        "alignment": "left",
        "first_line_indent_chars": 0,
        "line_spacing": 1.35,
        "space_before_pt": 6,
        "space_after_pt": 6,
        "page_break_before": False,
        "numbering": {
            "enabled": True,
            "format": "decimal",
            "suffix": ".",
            "include_parent": True,
        },
    },
    "body": {
        "font_family": "宋体",
        "font_size_pt": 12,
        "font_size_label": "小四",
        "bold": False,
        "color": "000000",
        "alignment": "justify",
        "first_line_indent_chars": 2,
        "line_spacing": 1.35,
        "space_before_pt": 0,
        "space_after_pt": 0,
    },
    "table": {
        "font_family": "宋体",
        "font_size_pt": 12,
        "font_size_label": "小四",
        "color": "000000",
        "line_spacing": 1.35,
        "width_percent": 100,
        "border_style": "grid",
        "header_bold": True,
        "header_background": "FCE4D6",
        "repeat_header": True,
        "allow_row_break_across_pages": True,
        "cell_padding_cm": 0.08,
        "min_row_height_cm": 0.0,
        "default_alignment": "justify",
        "default_vertical_alignment": "center",
        "first_line_indent_chars": 0,
        "column_alignment_rules": [
            {
                "match_titles": ["序号", "编号", "项目", "类别", "阶段"],
                "alignment": "center",
            },
            {
                "match_titles": ["内容", "措施", "要求", "控制要点", "管理措施", "备注"],
                "alignment": "justify",
            },
        ],
        "column_width_strategy": "auto_by_content_type",
        "image_column_width_cm": 5.2,
    },
    "image": {
        "max_width_cm": 15.0,
        "max_height_cm": 10.5,
        "single_image_alignment": "center",
        "caption_font_family": "宋体",
        "caption_font_size_pt": 10.5,
        "caption_font_size_label": "五号",
        "caption_alignment": "center",
        "caption_space_before_pt": 3,
        "caption_space_after_pt": 6,
        "keep_image_with_caption": True,
        "multi_image_layout": {
            "default_columns": 2,
            "max_columns": 3,
            "use_three_columns_when_count_at_least": 7,
            "avoid_too_small_images": True,
        },
    },
    "header_footer": {
        "header_enabled": False,
        "header_text": "",
        "footer_enabled": True,
        "page_number_enabled": True,
        "page_number_alignment": "center",
        "different_first_page": False,
    },
}


def default_word_export_profile() -> dict[str, Any]:
    """返回默认 Word 导出配置副本。"""

    return copy.deepcopy(DEFAULT_WORD_EXPORT_PROFILE)


def merge_word_export_profile(project_profile: dict[str, Any] | None) -> dict[str, Any]:
    """将项目级配置合并到默认配置上，并做基础容错。"""

    merged = default_word_export_profile()
    if project_profile:
        _deep_merge(merged, project_profile)
    merged["schema_version"] = SCHEMA_VERSION
    return validate_word_export_profile(merged)


def load_word_export_profile(path: str | Path) -> dict[str, Any]:
    """读取项目级配置；文件不存在时返回默认配置。"""

    target = Path(path)
    if not target.exists():
        return default_word_export_profile()
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Word export profile must be a JSON object: {target}")
    return merge_word_export_profile(data)


def save_word_export_profile(path: str | Path, profile: dict[str, Any]) -> dict[str, Any]:
    """保存项目级配置，返回已合并和校验的配置。"""

    validated = merge_word_export_profile(profile)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return validated


def reset_word_export_profile(path: str | Path) -> dict[str, Any]:
    """恢复默认配置并写入项目 profile 文件。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    profile = default_word_export_profile()
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def validate_word_export_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """基础字段校验与容错，避免前端提交的局部配置破坏导出。"""

    if not isinstance(profile, dict):
        raise ValueError("Word export profile must be a JSON object.")
    _ensure_range(profile, ("page", "top_margin_cm"), 0.5, 8.0)
    _ensure_range(profile, ("page", "bottom_margin_cm"), 0.5, 8.0)
    _ensure_range(profile, ("page", "left_margin_cm"), 0.5, 8.0)
    _ensure_range(profile, ("page", "right_margin_cm"), 0.5, 8.0)
    _ensure_choice(profile, ("page", "paper_size"), {"A4"}, "A4")
    _ensure_choice(profile, ("page", "orientation"), {"portrait", "landscape"}, "portrait")
    _ensure_int_range(profile, ("toc", "levels"), 1, 3)
    _ensure_int_range(profile, ("toc", "body_page_number_start"), 1, 999)
    for key in ["heading_1", "heading_2", "heading_3", "body", "table"]:
        _ensure_range(profile, (key, "font_size_pt"), 6, 36)
        _ensure_range(profile, (key, "line_spacing"), 1.0, 3.0)
        _ensure_choice(profile, (key, "alignment"), {"left", "center", "right", "justify"}, "left")
    for key in ["heading_1", "heading_2", "heading_3", "body"]:
        _ensure_range(profile, (key, "space_before_pt"), 0, 72)
        _ensure_range(profile, (key, "space_after_pt"), 0, 72)
    _ensure_range(profile, ("body", "first_line_indent_chars"), 0, 4)
    _ensure_range(profile, ("table", "min_row_height_cm"), 0, 5)
    _ensure_range(profile, ("table", "image_column_width_cm"), 2, 10)
    _ensure_choice(profile, ("table", "column_width_strategy"), {"auto_by_content_type", "balanced", "fixed"}, "auto_by_content_type")
    _ensure_range(profile, ("image", "max_width_cm"), 1.0, 21.0)
    _ensure_range(profile, ("image", "max_height_cm"), 1.0, 29.7)
    _ensure_range(profile, ("image", "caption_font_size_pt"), 6, 18)
    _ensure_choice(profile, ("image", "caption_alignment"), {"left", "center", "right"}, "center")
    _ensure_int_range(profile, ("image", "multi_image_layout", "default_columns"), 1, 4)
    _ensure_int_range(profile, ("image", "multi_image_layout", "max_columns"), 1, 4)
    return profile


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key not in base:
            base[key] = copy.deepcopy(value)
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _ensure_range(profile: dict[str, Any], path: tuple[str, ...], min_value: float, max_value: float) -> None:
    value = _get_nested(profile, path)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(_get_nested(DEFAULT_WORD_EXPORT_PROFILE, path))
    numeric = min(max(numeric, min_value), max_value)
    _set_nested(profile, path, numeric)


def _ensure_int_range(profile: dict[str, Any], path: tuple[str, ...], min_value: int, max_value: int) -> None:
    value = _get_nested(profile, path)
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = int(_get_nested(DEFAULT_WORD_EXPORT_PROFILE, path))
    numeric = min(max(numeric, min_value), max_value)
    _set_nested(profile, path, numeric)


def _ensure_choice(profile: dict[str, Any], path: tuple[str, ...], choices: set[str], default: str) -> None:
    value = _get_nested(profile, path)
    if value not in choices:
        _set_nested(profile, path, default)
