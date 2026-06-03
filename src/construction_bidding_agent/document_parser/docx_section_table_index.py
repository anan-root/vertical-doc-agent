"""将 DOCX 表格归属到最近的章节标题路径。"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .docx_probe import (
    NS,
    _image_rel_ids,
    _iter_body_blocks,
    _node_text,
    _paragraph_style,
    _part_name_from_target,
    _read_header_footer_texts,
    _read_relationships,
    _read_xml,
)
from .models import (
    DocxSectionTableIndexResult,
    SectionHeading,
    SectionTableRecord,
    SectionTableSummary,
    TableImageBinding,
    TableIndexCellPreview,
    TableIndexRowPreview,
)


_NUMBERED_HEADING_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:[.．、]\s*|\s+)(?P<title>\S.+)$")
_HEADING_STYLE_RE = re.compile(r"^(?:Heading|标题|Titre)", re.IGNORECASE)


def build_docx_section_table_index(
    path: str | Path,
    *,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
    include_image_bindings: bool = True,
) -> DocxSectionTableIndexResult:
    source = Path(path)
    if not source.exists():
        return DocxSectionTableIndexResult(
            source_path=str(source),
            heading_count=0,
            table_count=0,
            unassigned_table_count=0,
            document_image_ref_count=0,
            table_image_ref_count=0,
            header_footer_text_count=0,
            warnings=[f"File not found: {source}"],
        )

    with zipfile.ZipFile(source) as package:
        rels = _read_relationships(package)
        document_root = _read_xml(package, "word/document.xml")
        if document_root is None:
            return DocxSectionTableIndexResult(
                source_path=str(source),
                heading_count=0,
                table_count=0,
                unassigned_table_count=0,
                document_image_ref_count=0,
                table_image_ref_count=0,
                header_footer_text_count=0,
                warnings=["Missing word/document.xml"],
            )

        header_footer_texts = _read_header_footer_texts(package)
        return index_sections_tables_from_root(
            document_root,
            rels,
            source_path=str(source),
            header_footer_text_count=len(header_footer_texts),
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
            include_image_bindings=include_image_bindings,
        )


def index_sections_tables_from_root(
    document_root: ET.Element,
    rels: dict[str, str],
    *,
    source_path: str,
    header_footer_text_count: int = 0,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
    include_image_bindings: bool = True,
) -> DocxSectionTableIndexResult:
    body = document_root.find("w:body", NS)
    all_image_refs = _image_rel_ids(document_root)
    if body is None:
        return DocxSectionTableIndexResult(
            source_path=source_path,
            heading_count=0,
            table_count=0,
            unassigned_table_count=0,
            document_image_ref_count=len(all_image_refs),
            table_image_ref_count=0,
            header_footer_text_count=header_footer_text_count,
            warnings=["Missing w:body"],
        )

    headings: list[SectionHeading] = []
    tables: list[SectionTableRecord] = []
    image_bindings: list[TableImageBinding] = []
    current_path: list[SectionHeading] = []
    table_image_ref_count = 0
    paragraph_index = 0

    for block_index, block in enumerate(_iter_body_blocks(body)):
        if _is_paragraph(block):
            text = _node_text(block)
            if text:
                style = _paragraph_style(block)
                heading_info = detect_heading(text, style)
                if heading_info is not None:
                    level, number, heading_text = heading_info
                    level = _resolve_heading_level(level, number, style, current_path)
                    heading = SectionHeading(
                        heading_index=len(headings),
                        paragraph_index=paragraph_index,
                        block_index=block_index,
                        level=level,
                        text=heading_text,
                        style=style,
                        number=number,
                    )
                    headings.append(heading)
                    current_path = _updated_heading_path(current_path, heading)
                paragraph_index += 1
            continue

        if not _is_table(block):
            continue

        table_index = len(tables)
        table_record, bindings = summarize_table_for_section(
            block,
            rels,
            table_index=table_index,
            block_index=block_index,
            section_path=[heading.text for heading in current_path],
            nearest_heading=current_path[-1] if current_path else None,
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
            include_image_bindings=include_image_bindings,
        )
        tables.append(table_record)
        image_bindings.extend(bindings)
        table_image_ref_count += table_record.image_count

    sections = _summarize_sections(tables)
    unassigned_table_count = sum(1 for table in tables if not table.section_path)
    warnings: list[str] = []
    if unassigned_table_count:
        warnings.append(f"{unassigned_table_count} table(s) appeared before any detected body heading.")
    if all_image_refs and table_image_ref_count < len(all_image_refs):
        warnings.append(
            "Some image references are outside table cells or in structures not yet classified."
        )

    return DocxSectionTableIndexResult(
        source_path=source_path,
        heading_count=len(headings),
        table_count=len(tables),
        unassigned_table_count=unassigned_table_count,
        document_image_ref_count=len(all_image_refs),
        table_image_ref_count=table_image_ref_count,
        header_footer_text_count=header_footer_text_count,
        headings=headings,
        sections=sections,
        tables=tables,
        image_bindings=image_bindings,
        warnings=warnings,
    )


def detect_heading(text: str, style: str | None) -> tuple[int, str | None, str] | None:
    """段落像正文标题时，返回层级、编号和标题文本。"""
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _is_toc_style(style):
        return None

    numbered = _NUMBERED_HEADING_RE.match(normalized)
    if numbered is not None and _looks_like_toc_entry(numbered.group("title")):
        return None
    if numbered is not None:
        number = numbered.group("number")
        title = _strip_leading_separator(numbered.group("title"))
        return number.count(".") + 1, number, f"{number} {title}"

    if _is_heading_style(style) and not _looks_like_body_paragraph(normalized):
        return _heading_level_from_style(style), None, normalized

    return None


def write_section_table_index_outputs(
    result: DocxSectionTableIndexResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_target.write_text(render_section_table_index_report(result), encoding="utf-8")


def render_section_table_index_report(result: DocxSectionTableIndexResult) -> str:
    lines = [
        "# DOCX 章节标题与表格归属索引报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 识别标题数：{result.heading_count}",
        f"- 表格数：{result.table_count}",
        f"- 未归属表格数：{result.unassigned_table_count}",
        f"- 文档图片引用数：{result.document_image_ref_count}",
        f"- 表内图片引用数：{result.table_image_ref_count}",
        f"- 页眉页脚文本数：{result.header_footer_text_count}",
        "",
        "## 章节统计",
        "",
    ]

    if result.sections:
        top_sections = sorted(
            result.sections,
            key=lambda section: (section.table_count, section.image_count),
            reverse=True,
        )
        lines.append("- 表格最多的章节：")
        for section in top_sections[:20]:
            path = " > ".join(section.section_path)
            lines.append(
                f"  - {path}: tables={section.table_count}, images={section.image_count}, "
                f"range=T{section.first_table_index}-T{section.last_table_index}"
            )
    else:
        lines.append("- 未形成章节统计。")

    level_counts = Counter(heading.level for heading in result.headings)
    if level_counts:
        lines.append("- 标题层级分布：" + ", ".join(f"L{level}={count}" for level, count in sorted(level_counts.items())))

    lines.extend(["", "## 标题预览", ""])
    if result.headings:
        for heading in result.headings[:120]:
            style = heading.style or ""
            lines.append(
                f"- H{heading.heading_index} L{heading.level} P{heading.paragraph_index} "
                f"B{heading.block_index} `{style}` {heading.text}"
            )
        if len(result.headings) > 120:
            lines.append("")
            lines.append(f"... 仅展示前 120 个标题，完整索引见 JSON。")
    else:
        lines.append("- 未识别到正文标题。")

    lines.extend(["", "## 表格归属预览", ""])
    for table in result.tables[:220]:
        path = " > ".join(table.section_path) if table.section_path else "(未归属)"
        header = " | ".join(table.header_preview)
        lines.append(
            f"- Table {table.table_index}: section={path}, rows={table.row_count}, "
            f"max_columns={table.max_column_count}, images={table.image_count}"
        )
        if header:
            lines.append(f"  - header: {header}")
        for row in table.row_previews[:2]:
            preview = " | ".join(cell.text_preview for cell in row.cells)
            lines.append(f"  - R{row.row_index}: {preview}")
    if len(result.tables) > 220:
        lines.append("")
        lines.append(f"... 仅展示前 220 张表，完整索引见 JSON。")

    lines.extend(["", "## 表内图片绑定预览", ""])
    if result.image_bindings:
        for binding in result.image_bindings[:200]:
            lines.append(
                f"- {binding.rel_id}: {binding.target} "
                f"(table={binding.table_index}, row={binding.row_index}, cell={binding.cell_index})"
            )
        if len(result.image_bindings) > 200:
            lines.append("")
            lines.append(f"... 仅展示前 200 个绑定，完整索引见 JSON。")
    else:
        lines.append("- 未发现表内图片绑定。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def summarize_table_for_section(
    table_node: ET.Element,
    rels: dict[str, str],
    *,
    table_index: int,
    block_index: int,
    section_path: list[str],
    nearest_heading: SectionHeading | None,
    preview_rows_per_table: int,
    preview_text_chars: int,
    include_image_bindings: bool,
) -> tuple[SectionTableRecord, list[TableImageBinding]]:
    row_count = 0
    max_column_count = 0
    image_count = 0
    row_previews: list[TableIndexRowPreview] = []
    header_preview: list[str] = []
    bindings: list[TableImageBinding] = []

    for row_index, row_node in enumerate(table_node.findall("w:tr", NS)):
        row_count += 1
        cell_nodes = row_node.findall("w:tc", NS)
        max_column_count = max(max_column_count, len(cell_nodes))
        row_preview = TableIndexRowPreview(row_index=row_index) if row_index < preview_rows_per_table else None
        row_cell_texts = [_node_text(cell_node) for cell_node in cell_nodes]
        previous_row_text = _row_text(table_node, row_index - 1)
        previous_row_texts = _previous_row_texts(table_node, row_index, max_count=3)
        next_row_text = _row_text(table_node, row_index + 1)
        previous_row_cell_texts = _row_cell_texts(table_node, row_index - 1)
        next_row_cell_texts = _row_cell_texts(table_node, row_index + 1)
        header_text = " | ".join(header_preview)

        for cell_index, cell_node in enumerate(cell_nodes):
            rel_ids = _image_rel_ids(cell_node)
            image_count += len(rel_ids)
            if include_image_bindings:
                for rel_id in rel_ids:
                    target = rels.get(rel_id, "")
                    cell_text = row_cell_texts[cell_index] if cell_index < len(row_cell_texts) else ""
                    left_cell_text = _cell_text_at(row_cell_texts, cell_index - 1)
                    right_cell_text = _cell_text_at(row_cell_texts, cell_index + 1)
                    previous_non_empty = _nearest_non_empty(row_cell_texts, cell_index, direction=-1)
                    next_non_empty = _nearest_non_empty(row_cell_texts, cell_index, direction=1)
                    above_cell_text = _cell_text_at(previous_row_cell_texts, cell_index)
                    below_cell_text = _cell_text_at(next_row_cell_texts, cell_index)
                    row_text = " | ".join(text for text in row_cell_texts if text)
                    bindings.append(
                        TableImageBinding(
                            rel_id=rel_id,
                            target=target,
                            part_name=_part_name_from_target(target),
                            table_index=table_index,
                            row_index=row_index,
                            cell_index=cell_index,
                            cell_text=cell_text,
                            row_text=row_text,
                            header_text=header_text,
                            previous_row_text=previous_row_text,
                            previous_row_texts=previous_row_texts,
                            next_row_text=next_row_text,
                            previous_non_empty_cell_text=previous_non_empty,
                            next_non_empty_cell_text=next_non_empty,
                            left_cell_text=left_cell_text,
                            right_cell_text=right_cell_text,
                            above_cell_text=above_cell_text,
                            below_cell_text=below_cell_text,
                            nearby_text=_nearby_text(
                                [
                                    cell_text,
                                    below_cell_text,
                                    above_cell_text,
                                    previous_non_empty,
                                    next_non_empty,
                                    left_cell_text,
                                    right_cell_text,
                                    previous_row_text,
                                    *previous_row_texts,
                                    next_row_text,
                                    header_text,
                                ]
                            ),
                            caption_candidates=_caption_candidates(
                                [
                                    cell_text,
                                    below_cell_text,
                                    above_cell_text,
                                    previous_non_empty,
                                    next_non_empty,
                                    left_cell_text,
                                    right_cell_text,
                                    previous_row_text,
                                    *previous_row_texts,
                                    next_row_text,
                                ]
                            ),
                        )
                    )

            if row_preview is not None:
                row_preview.cells.append(
                    TableIndexCellPreview(
                        cell_index=cell_index,
                        text_preview=_node_text(cell_node)[:preview_text_chars],
                        image_count=len(rel_ids),
                    )
                )

        if row_preview is not None:
            row_previews.append(row_preview)
            if row_index == 0:
                header_preview = [cell.text_preview for cell in row_preview.cells]

    return (
        SectionTableRecord(
            table_index=table_index,
            block_index=block_index,
            section_path=section_path,
            section_level=nearest_heading.level if nearest_heading else None,
            nearest_heading_index=nearest_heading.heading_index if nearest_heading else None,
            nearest_heading_text=nearest_heading.text if nearest_heading else None,
            row_count=row_count,
            max_column_count=max_column_count,
            image_count=image_count,
            header_preview=header_preview,
            row_previews=row_previews,
        ),
        bindings,
    )


def _summarize_sections(tables: list[SectionTableRecord]) -> list[SectionTableSummary]:
    summaries: dict[tuple[str, ...], SectionTableSummary] = {}
    for table in tables:
        if not table.section_path:
            continue
        key = tuple(table.section_path)
        summary = summaries.get(key)
        if summary is None:
            summary = SectionTableSummary(
                section_path=table.section_path,
                level=len(table.section_path),
                first_table_index=table.table_index,
                last_table_index=table.table_index,
            )
            summaries[key] = summary
        summary.table_count += 1
        summary.image_count += table.image_count
        summary.last_table_index = table.table_index
    return list(summaries.values())


def _row_text(table_node: ET.Element, row_index: int) -> str:
    texts = _row_cell_texts(table_node, row_index)
    return " | ".join(text for text in texts if text)


def _previous_row_texts(table_node: ET.Element, row_index: int, *, max_count: int) -> list[str]:
    texts: list[str] = []
    for previous_index in range(row_index - 1, max(row_index - max_count - 1, -1), -1):
        text = _row_text(table_node, previous_index)
        if text:
            texts.append(text)
    return texts


def _row_cell_texts(table_node: ET.Element, row_index: int) -> list[str]:
    if row_index < 0:
        return []
    rows = table_node.findall("w:tr", NS)
    if row_index >= len(rows):
        return []
    return [_node_text(cell) for cell in rows[row_index].findall("w:tc", NS)]


def _cell_text_at(texts: list[str], index: int) -> str:
    if index < 0 or index >= len(texts):
        return ""
    return texts[index]


def _nearest_non_empty(texts: list[str], start_index: int, *, direction: int) -> str:
    index = start_index + direction
    while 0 <= index < len(texts):
        text = texts[index].strip()
        if text:
            return text
        index += direction
    return ""


def _nearby_text(parts: list[str]) -> str:
    return "；".join(_dedupe_texts(parts))


def _caption_candidates(parts: list[str]) -> list[str]:
    candidates = []
    for text in _dedupe_texts(parts):
        cleaned = _normalize_text(text)
        if not cleaned:
            continue
        if len(cleaned) <= 40 or any(term in cleaned for term in ["图", "示意", "照片", "流程", "做法", "控制", "标识"]):
            candidates.append(cleaned[:80])
    return candidates[:5]


def _dedupe_texts(parts: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = _normalize_text(part)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _updated_heading_path(current_path: list[SectionHeading], heading: SectionHeading) -> list[SectionHeading]:
    level = max(heading.level, 1)
    path = current_path[: level - 1]
    path.append(heading)
    return path


def _resolve_heading_level(
    level: int,
    number: str | None,
    style: str | None,
    current_path: list[SectionHeading],
) -> int:
    if not number or "." in number or _is_heading_style(style) or not current_path:
        return level

    last_heading = current_path[-1]
    if (
        last_heading.number
        and "." not in last_heading.number
        and not _is_heading_style(last_heading.style)
    ):
        return last_heading.level
    return last_heading.level + 1


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_leading_separator(text: str) -> str:
    return text.lstrip(".．、 \t")


def _is_toc_style(style: str | None) -> bool:
    return bool(style and style.upper().startswith("TOC"))


def _is_heading_style(style: str | None) -> bool:
    if not style or _is_toc_style(style):
        return False
    if style.isdigit():
        return len(style) <= 2 and len(set(style)) == 1
    return bool(_HEADING_STYLE_RE.match(style))


def _looks_like_toc_entry(title: str) -> bool:
    return bool(re.search(r".+\D\d{1,4}$", title.strip()))


def _looks_like_body_paragraph(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) > 90:
        return True
    if len(normalized) > 48 and re.search(r"[，,。；;：:]", normalized):
        return True
    return False


def _heading_level_from_style(style: str) -> int:
    if style.isdigit() and len(set(style)) == 1:
        return int(style[0])
    number = re.search(r"(\d+)$", style)
    if number is None:
        return 1
    return max(int(number.group(1)), 1)


def _is_paragraph(node: ET.Element) -> bool:
    return node.tag.endswith("}p")


def _is_table(node: ET.Element) -> bool:
    return node.tag.endswith("}tbl")
