"""面向大型 DOCX 文件的轻量全量表格索引。"""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .docx_probe import (
    NS,
    _image_rel_ids,
    _iter_body_blocks,
    _node_text,
    _part_name_from_target,
    _read_header_footer_texts,
    _read_relationships,
    _read_xml,
)
from .models import (
    DocxTableIndexResult,
    TableImageBinding,
    TableIndexCellPreview,
    TableIndexRowPreview,
    TableIndexTable,
)


def build_docx_table_index(
    path: str | Path,
    *,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
    include_image_bindings: bool = True,
) -> DocxTableIndexResult:
    source = Path(path)
    if not source.exists():
        return DocxTableIndexResult(
            source_path=str(source),
            table_count=0,
            document_image_ref_count=0,
            table_image_ref_count=0,
            header_footer_text_count=0,
            warnings=[f"File not found: {source}"],
        )

    with zipfile.ZipFile(source) as package:
        rels = _read_relationships(package)
        document_root = _read_xml(package, "word/document.xml")
        if document_root is None:
            return DocxTableIndexResult(
                source_path=str(source),
                table_count=0,
                document_image_ref_count=0,
                table_image_ref_count=0,
                header_footer_text_count=0,
                warnings=["Missing word/document.xml"],
            )

        header_footer_texts = _read_header_footer_texts(package)
        return index_tables_from_root(
            document_root,
            rels,
            source_path=str(source),
            header_footer_text_count=len(header_footer_texts),
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
            include_image_bindings=include_image_bindings,
        )


def index_tables_from_root(
    document_root: ET.Element,
    rels: dict[str, str],
    *,
    source_path: str,
    header_footer_text_count: int = 0,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
    include_image_bindings: bool = True,
) -> DocxTableIndexResult:
    tables: list[TableIndexTable] = []
    image_bindings: list[TableImageBinding] = []
    all_image_refs = _image_rel_ids(document_root)
    table_image_ref_count = 0

    body = document_root.find("w:body", NS)
    if body is None:
        return DocxTableIndexResult(
            source_path=source_path,
            table_count=0,
            document_image_ref_count=len(all_image_refs),
            table_image_ref_count=0,
            header_footer_text_count=header_footer_text_count,
            warnings=["Missing w:body"],
        )

    for block in _iter_body_blocks(body):
        if not _is_table(block):
            continue

        table_index = len(tables)
        row_count = 0
        max_column_count = 0
        table_image_count = 0
        row_previews: list[TableIndexRowPreview] = []
        header_preview: list[str] = []

        for row_index, row_node in enumerate(block.findall("w:tr", NS)):
            row_count += 1
            cell_nodes = row_node.findall("w:tc", NS)
            max_column_count = max(max_column_count, len(cell_nodes))

            if row_index < preview_rows_per_table:
                row_preview = TableIndexRowPreview(row_index=row_index)
            else:
                row_preview = None

            for cell_index, cell_node in enumerate(cell_nodes):
                rel_ids = _image_rel_ids(cell_node)
                table_image_count += len(rel_ids)
                if include_image_bindings:
                    for rel_id in rel_ids:
                        target = rels.get(rel_id, "")
                        image_bindings.append(
                            TableImageBinding(
                                rel_id=rel_id,
                                target=target,
                                part_name=_part_name_from_target(target),
                                table_index=table_index,
                                row_index=row_index,
                                cell_index=cell_index,
                            )
                        )

                if row_preview is not None:
                    text_preview = _node_text(cell_node)[:preview_text_chars]
                    row_preview.cells.append(
                        TableIndexCellPreview(
                            cell_index=cell_index,
                            text_preview=text_preview,
                            image_count=len(rel_ids),
                        )
                    )

            if row_preview is not None:
                row_previews.append(row_preview)
                if row_index == 0:
                    header_preview = [cell.text_preview for cell in row_preview.cells]

        table_image_ref_count += table_image_count
        tables.append(
            TableIndexTable(
                table_index=table_index,
                row_count=row_count,
                max_column_count=max_column_count,
                image_count=table_image_count,
                header_preview=header_preview,
                row_previews=row_previews,
            )
        )

    warnings: list[str] = []
    if all_image_refs and table_image_ref_count < len(all_image_refs):
        warnings.append(
            "Some image references are outside table cells or in structures not yet classified."
        )

    return DocxTableIndexResult(
        source_path=source_path,
        table_count=len(tables),
        document_image_ref_count=len(all_image_refs),
        table_image_ref_count=table_image_ref_count,
        header_footer_text_count=header_footer_text_count,
        tables=tables,
        image_bindings=image_bindings,
        warnings=warnings,
    )


def write_table_index_outputs(
    result: DocxTableIndexResult,
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
    report_target.write_text(render_table_index_report(result), encoding="utf-8")


def render_table_index_report(result: DocxTableIndexResult) -> str:
    lines = [
        "# DOCX 全量表格轻量索引报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 表格数：{result.table_count}",
        f"- 文档图片引用数：{result.document_image_ref_count}",
        f"- 表内图片引用数：{result.table_image_ref_count}",
        f"- 含表内图片的表格数：{sum(1 for table in result.tables if table.image_count > 0)}",
        f"- 页眉页脚文本数：{result.header_footer_text_count}",
        "",
        "## 结构统计",
        "",
    ]

    column_counts = Counter(table.max_column_count for table in result.tables)
    image_tables = sorted(
        result.tables,
        key=lambda table: table.image_count,
        reverse=True,
    )
    lines.append("- 列数分布：" + ", ".join(f"{cols}列={count}" for cols, count in sorted(column_counts.items())))
    if image_tables and image_tables[0].image_count:
        lines.append("- 图片最多的表格：")
        for table in image_tables[:10]:
            if table.image_count <= 0:
                break
            header = " | ".join(table.header_preview)
            lines.append(
                f"  - Table {table.table_index}: rows={table.row_count}, "
                f"columns={table.max_column_count}, images={table.image_count}, header={header}"
            )

    lines.extend(
        [
            "",
        "## 表格概览",
        "",
        ]
    )

    for table in result.tables[:200]:
        header = " | ".join(table.header_preview)
        lines.append(
            f"- Table {table.table_index}: rows={table.row_count}, "
            f"max_columns={table.max_column_count}, images={table.image_count}"
        )
        if header:
            lines.append(f"  - header: {header}")
        for row in table.row_previews[:2]:
            preview = " | ".join(cell.text_preview for cell in row.cells)
            lines.append(f"  - R{row.row_index}: {preview}")

    if len(result.tables) > 200:
        lines.append("")
        lines.append(f"... 仅展示前 200 张表，完整索引见 JSON。")

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


def _is_table(node: ET.Element) -> bool:
    return node.tag.endswith("}tbl")
