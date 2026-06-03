"""章节正文初稿预览渲染。

JSON 结果面向系统，编标人员更适合查看接近技术标正文的预览稿。本模块将结构化章节初稿
渲染为 Markdown，展开标题、正文、表格和图片占位，便于快速浏览正文内容。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_chapter_draft_preview_from_file(result_json: str | Path) -> str:
    """从章节生成结果 JSON 渲染正文预览 Markdown。"""

    data = json.loads(Path(result_json).read_text(encoding="utf-8"))
    return render_chapter_draft_preview(data)


def write_chapter_draft_preview(result_json: str | Path, report_path: str | Path) -> None:
    """写入章节正文预览 Markdown。"""

    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_chapter_draft_preview_from_file(result_json), encoding="utf-8")


def render_chapter_draft_preview(generation_result: dict[str, Any]) -> str:
    """渲染章节正文预览 Markdown。"""

    chapters = [chapter for chapter in generation_result.get("chapters") or [] if isinstance(chapter, dict)]
    lines = [
        "# 技术标章节正文预览稿",
        "",
        f"- 章节数：{len(chapters)}",
        f"- 模型：{generation_result.get('model') or '-'}",
        f"- 生成时间：{generation_result.get('generated_at') or '-'}",
        "",
        "> 本文件供编标人员快速预览正文风格、表格表达和图片占位；正式编辑以 Word 初稿为准。",
        "",
    ]
    for index, chapter in enumerate(chapters, start=1):
        lines.extend(_render_chapter(chapter, index))
    return "\n".join(lines)


def _render_chapter(chapter: dict[str, Any], index: int) -> list[str]:
    chapter_path = [str(part) for part in chapter.get("chapter_path") or [] if str(part).strip()]
    title = " > ".join(chapter_path) or str(chapter.get("title") or f"章节{index}")
    lines = [
        f"## {index}. {title}",
        "",
    ]
    for section in chapter.get("sections") or []:
        if isinstance(section, dict):
            lines.extend(_render_section(section))
    return lines


def _render_section(section: dict[str, Any]) -> list[str]:
    heading = str(section.get("heading") or "未命名小节")
    level = int(section.get("level") or 2)
    prefix = "###" if level <= 2 else "####"
    lines = [f"{prefix} {heading}", ""]
    for block in section.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        lines.extend(_render_block(block))
    return lines


def _render_block(block: dict[str, Any]) -> list[str]:
    block_type = str(block.get("type") or "")
    if block_type == "paragraph":
        text = str(block.get("text") or "").strip()
        return [text, ""] if text else []
    if block_type == "rich_table":
        return _render_table(block)
    if block_type == "image_placeholder":
        caption = str(block.get("caption") or "图片待补充")
        reason = str(block.get("reason") or "需人工结合本项目资料补充。")
        return [f"> 【图片占位】{caption}：{reason}", ""]
    return [f"> 【未识别内容块】{block_type}", ""]


def _render_table(block: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    title = str(block.get("title") or "").strip()
    if title:
        lines.extend([f"**{title}**", ""])
    columns = [column for column in block.get("columns") or [] if isinstance(column, dict)]
    rows = [row for row in block.get("rows") or [] if isinstance(row, dict)]
    if not columns:
        return lines + ["> 【表格】列信息缺失，需复核。", ""]
    headers = [str(column.get("title") or column.get("key") or "") for column in columns]
    keys = [str(column.get("key") or f"col_{index + 1}") for index, column in enumerate(columns)]
    lines.append("| " + " | ".join(_cell(header) for header in headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
        lines.append("| " + " | ".join(_cell(cells.get(key)) for key in keys) + " |")
    if not rows:
        lines.append("| " + " | ".join("" for _ in headers) + " |")
    lines.append("")
    return lines


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
