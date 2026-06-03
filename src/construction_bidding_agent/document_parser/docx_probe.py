"""阶段 0 的 DOCX 结构轻量探测工具。

本模块有意只使用 Python 标准库，直接按 OOXML 读取 DOCX 包。
这样后续可以继续细化表格单元格与图片的绑定关系，不受高层库抽象限制。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import (
    DocxProbeResult,
    ImageProbe,
    ParagraphProbe,
    TableCellProbe,
    TableProbe,
    TableRowProbe,
)


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}

REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def probe_docx(
    path: str | Path,
    *,
    max_paragraphs: int | None = None,
    max_tables: int | None = None,
    max_rows_per_table: int | None = None,
    include_images: bool = True,
) -> DocxProbeResult:
    source = Path(path)
    result = DocxProbeResult(source_path=str(source))

    if not source.exists():
        result.warnings.append(f"File not found: {source}")
        return result

    with zipfile.ZipFile(source) as package:
        rels = _read_relationships(package)
        document_root = _read_xml(package, "word/document.xml")
        if document_root is None:
            result.warnings.append("Missing word/document.xml")
            return result

        result.header_footer_texts = _read_header_footer_texts(package)
        result.paragraphs = _extract_body_paragraphs(document_root, max_paragraphs=max_paragraphs)
        result.tables = _extract_tables(
            document_root,
            max_tables=max_tables,
            max_rows_per_table=max_rows_per_table,
        )
        if include_images:
            result.images = _extract_images(document_root, rels, result.tables)
        else:
            result.warnings.append("Image extraction skipped by option.")

        if max_paragraphs is not None and len(result.paragraphs) >= max_paragraphs:
            result.warnings.append(f"Paragraph extraction limited to {max_paragraphs}.")
        if max_tables is not None and len(result.tables) >= max_tables:
            result.warnings.append(f"Table extraction limited to {max_tables}.")
        if max_rows_per_table is not None:
            result.warnings.append(f"Table rows limited to {max_rows_per_table} per table.")
        if include_images and (max_tables is not None or max_rows_per_table is not None):
            result.warnings.append(
                "Image context outside parsed table limits may be approximate in preview mode."
            )

    return result


def write_probe_outputs(result: DocxProbeResult, json_path: str | Path, report_path: str | Path) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)

    json_target.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_target.write_text(_render_markdown_report(result), encoding="utf-8")


def _read_xml(package: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        with package.open(name) as fp:
            return ET.parse(fp).getroot()
    except KeyError:
        return None
    except ET.ParseError:
        return None


def _read_relationships(package: zipfile.ZipFile) -> dict[str, str]:
    root = _read_xml(package, "word/_rels/document.xml.rels")
    if root is None:
        return {}

    rels: dict[str, str] = {}
    for rel in root.findall(f"{REL_NS}Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = target
    return rels


def _read_header_footer_texts(package: zipfile.ZipFile) -> list[str]:
    texts: list[str] = []
    for name in package.namelist():
        if not name.startswith("word/header") and not name.startswith("word/footer"):
            continue
        if not name.endswith(".xml"):
            continue
        root = _read_xml(package, name)
        if root is None:
            continue
        text = _node_text(root)
        if text:
            texts.append(text)
    return texts


def _extract_body_paragraphs(
    document_root: ET.Element,
    *,
    max_paragraphs: int | None = None,
) -> list[ParagraphProbe]:
    body = document_root.find("w:body", NS)
    if body is None:
        return []

    paragraphs: list[ParagraphProbe] = []
    for child in _iter_body_blocks(body):
        if child.tag != f"{W_NS}p":
            continue
        text = _node_text(child)
        if not text:
            continue
        paragraphs.append(
            ParagraphProbe(
                index=len(paragraphs),
                text=text,
                style=_paragraph_style(child),
            )
        )
        if max_paragraphs is not None and len(paragraphs) >= max_paragraphs:
            break
    return paragraphs


def _extract_tables(
    document_root: ET.Element,
    *,
    max_tables: int | None = None,
    max_rows_per_table: int | None = None,
) -> list[TableProbe]:
    body = document_root.find("w:body", NS)
    if body is None:
        return []

    tables: list[TableProbe] = []
    for child in _iter_body_blocks(body):
        if child.tag != f"{W_NS}tbl":
            continue
        table = TableProbe(index=len(tables))
        for row_index, tr in enumerate(child.findall("w:tr", NS)):
            if max_rows_per_table is not None and row_index >= max_rows_per_table:
                break
            row = TableRowProbe(row_index=row_index)
            for cell_index, tc in enumerate(tr.findall("w:tc", NS)):
                image_refs = _image_rel_ids(tc)
                cell = TableCellProbe(
                    row_index=row_index,
                    cell_index=cell_index,
                    text=_node_text(tc),
                    image_refs=image_refs,
                )
                row.cells.append(cell)
                table.image_refs.extend(image_refs)
            table.rows.append(row)
        tables.append(table)
        if max_tables is not None and len(tables) >= max_tables:
            break
    return tables


def _extract_images(
    document_root: ET.Element,
    rels: dict[str, str],
    tables: list[TableProbe],
) -> list[ImageProbe]:
    images: list[ImageProbe] = []
    seen: set[tuple[str, int | None, int | None, int | None]] = set()

    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                for rel_id in cell.image_refs:
                    key = (rel_id, table.index, row.row_index, cell.cell_index)
                    if key in seen:
                        continue
                    seen.add(key)
                    images.append(
                        ImageProbe(
                            rel_id=rel_id,
                            target=rels.get(rel_id, ""),
                            part_name=_part_name_from_target(rels.get(rel_id, "")),
                            context="table_cell",
                            table_index=table.index,
                            row_index=row.row_index,
                            cell_index=cell.cell_index,
                        )
                    )

    for rel_id in _image_rel_ids(document_root):
        if any(image.rel_id == rel_id for image in images):
            continue
        images.append(
            ImageProbe(
                rel_id=rel_id,
                target=rels.get(rel_id, ""),
                part_name=_part_name_from_target(rels.get(rel_id, "")),
                context="document",
            )
        )
    return images


def _iter_body_blocks(node: ET.Element):
    for child in node:
        if child.tag in {f"{W_NS}p", f"{W_NS}tbl"}:
            yield child
            continue
        yield from _iter_body_blocks(child)


def _image_rel_ids(node: ET.Element) -> list[str]:
    rel_ids: list[str] = []
    for blip in node.findall(".//a:blip", NS):
        rel_id = blip.attrib.get(f"{{{NS['r']}}}embed")
        if rel_id:
            rel_ids.append(rel_id)
    return rel_ids


def _node_text(node: ET.Element) -> str:
    parts: list[str] = []
    for text_node in node.findall(".//w:t", NS):
        if text_node.text:
            parts.append(text_node.text)
    return "".join(parts).strip()


def _paragraph_style(paragraph: ET.Element) -> str | None:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    if style is None:
        return None
    return style.attrib.get(f"{W_NS}val")


def _part_name_from_target(target: str) -> str | None:
    if not target:
        return None
    return str(Path("word") / target).replace("\\", "/")


def _render_markdown_report(result: DocxProbeResult) -> str:
    lines = [
        "# DOCX 样本解析报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 段落数：{result.paragraph_count}",
        f"- 表格数：{result.table_count}",
        f"- 图片引用数：{result.image_count}",
        f"- 页眉页脚文本数：{len(result.header_footer_texts)}",
        "",
        "## 段落预览",
        "",
    ]

    for paragraph in result.paragraphs[:30]:
        style = paragraph.style or ""
        lines.append(f"- P{paragraph.index} `{style}` {paragraph.text[:120]}")

    lines.extend(["", "## 表格概览", ""])
    for table in result.tables:
        column_count = max((len(row.cells) for row in table.rows), default=0)
        lines.append(
            f"- Table {table.index}: rows={len(table.rows)}, max_columns={column_count}, images={len(table.image_refs)}"
        )
        for row in table.rows[:3]:
            preview = " | ".join(cell.text[:40] for cell in row.cells)
            lines.append(f"  - R{row.row_index}: {preview}")

    lines.extend(["", "## 图片绑定", ""])
    if result.images:
        for image in result.images:
            location = (
                f"table={image.table_index}, row={image.row_index}, cell={image.cell_index}"
                if image.context == "table_cell"
                else "document"
            )
            lines.append(f"- {image.rel_id}: {image.target} ({location})")
    else:
        lines.append("- 未发现图片引用。")

    if result.header_footer_texts:
        lines.extend(["", "## 页眉页脚文本预览", ""])
        for text in result.header_footer_texts[:10]:
            lines.append(f"- {text[:120]}")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)
