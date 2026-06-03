"""从 DOCX 文件构建轻量章节素材切片索引。"""

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
    _paragraph_style,
    _part_name_from_target,
    _read_header_footer_texts,
    _read_relationships,
    _read_xml,
)
from .docx_section_table_index import (
    _is_paragraph,
    _is_table,
    _resolve_heading_level,
    _updated_heading_path,
    detect_heading,
    summarize_table_for_section,
)
from .models import (
    DocxSectionMaterialIndexResult,
    SectionHeading,
    SectionImageBinding,
    SectionMaterialSlice,
    SectionParagraphRecord,
)


def build_docx_section_material_index(
    path: str | Path,
    *,
    preview_paragraphs_per_slice: int = 5,
    preview_paragraph_chars: int = 220,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
) -> DocxSectionMaterialIndexResult:
    source = Path(path)
    if not source.exists():
        return DocxSectionMaterialIndexResult(
            source_path=str(source),
            heading_count=0,
            slice_count=0,
            material_paragraph_count=0,
            material_paragraph_char_count=0,
            table_count=0,
            document_image_ref_count=0,
            table_image_ref_count=0,
            paragraph_image_ref_count=0,
            header_footer_text_count=0,
            warnings=[f"File not found: {source}"],
        )

    with zipfile.ZipFile(source) as package:
        rels = _read_relationships(package)
        document_root = _read_xml(package, "word/document.xml")
        if document_root is None:
            return DocxSectionMaterialIndexResult(
                source_path=str(source),
                heading_count=0,
                slice_count=0,
                material_paragraph_count=0,
                material_paragraph_char_count=0,
                table_count=0,
                document_image_ref_count=0,
                table_image_ref_count=0,
                paragraph_image_ref_count=0,
                header_footer_text_count=0,
                warnings=["Missing word/document.xml"],
            )

        header_footer_texts = _read_header_footer_texts(package)
        return index_section_materials_from_root(
            document_root,
            rels,
            source_path=str(source),
            header_footer_text_count=len(header_footer_texts),
            preview_paragraphs_per_slice=preview_paragraphs_per_slice,
            preview_paragraph_chars=preview_paragraph_chars,
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
        )


def index_section_materials_from_root(
    document_root: ET.Element,
    rels: dict[str, str],
    *,
    source_path: str,
    header_footer_text_count: int = 0,
    preview_paragraphs_per_slice: int = 5,
    preview_paragraph_chars: int = 220,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
) -> DocxSectionMaterialIndexResult:
    body = document_root.find("w:body", NS)
    all_image_refs = _image_rel_ids(document_root)
    if body is None:
        return DocxSectionMaterialIndexResult(
            source_path=source_path,
            heading_count=0,
            slice_count=0,
            material_paragraph_count=0,
            material_paragraph_char_count=0,
            table_count=0,
            document_image_ref_count=len(all_image_refs),
            table_image_ref_count=0,
            paragraph_image_ref_count=0,
            header_footer_text_count=header_footer_text_count,
            warnings=["Missing w:body"],
        )

    headings: list[SectionHeading] = []
    slices: list[SectionMaterialSlice] = []
    slices_by_heading: dict[int, SectionMaterialSlice] = {}
    current_path: list[SectionHeading] = []
    paragraph_index = 0
    table_count = 0
    table_image_ref_count = 0
    paragraph_image_ref_count = 0
    material_paragraph_count = 0
    material_paragraph_char_count = 0
    skipped_preface_paragraphs = 0
    preface_slice: SectionMaterialSlice | None = None

    for block_index, block in enumerate(_iter_body_blocks(body)):
        if _is_paragraph(block):
            text = _node_text(block)
            if not text:
                continue
            style = _paragraph_style(block)
            if style and style.upper().startswith("TOC"):
                paragraph_index += 1
                continue
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
                material_slice = SectionMaterialSlice(
                    slice_id=f"S{heading.heading_index}",
                    heading_index=heading.heading_index,
                    level=heading.level,
                    section_path=[item.text for item in current_path],
                    start_block_index=block_index,
                )
                slices.append(material_slice)
                slices_by_heading[heading.heading_index] = material_slice
                paragraph_index += 1
                continue

            paragraph_record, bindings = _summarize_paragraph(
                block,
                rels,
                paragraph_index=paragraph_index,
                block_index=block_index,
                section_path=[item.text for item in current_path],
                preview_paragraph_chars=preview_paragraph_chars,
            )
            target_slice = _current_or_preface_slice(
                current_path,
                slices_by_heading,
                preface_slice,
                slices,
            )
            if target_slice.heading_index is None:
                preface_slice = target_slice
                skipped_preface_paragraphs += 1
            _add_paragraph_to_slice(
                target_slice,
                paragraph_record,
                bindings,
                preview_paragraphs_per_slice=preview_paragraphs_per_slice,
            )
            material_paragraph_count += 1
            material_paragraph_char_count += paragraph_record.char_count
            paragraph_image_ref_count += paragraph_record.image_count
            paragraph_index += 1
            continue

        if not _is_table(block):
            continue

        target_slice = _current_or_preface_slice(
            current_path,
            slices_by_heading,
            preface_slice,
            slices,
        )
        if target_slice.heading_index is None:
            preface_slice = target_slice
        table_record, table_bindings = summarize_table_for_section(
            block,
            rels,
            table_index=table_count,
            block_index=block_index,
            section_path=[item.text for item in current_path],
            nearest_heading=current_path[-1] if current_path else None,
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
            include_image_bindings=True,
        )
        target_slice.tables.append(table_record)
        target_slice.table_count += 1
        target_slice.image_count += table_record.image_count
        target_slice.image_bindings.extend(
            _table_bindings_to_section_bindings(table_bindings, table_record.section_path, block_index)
        )
        _touch_slice_range(target_slice, block_index)
        table_count += 1
        table_image_ref_count += table_record.image_count

    _populate_subtree_counts(slices)

    warnings: list[str] = []
    if skipped_preface_paragraphs:
        warnings.append(
            f"{skipped_preface_paragraphs} paragraph(s) appeared before any detected body heading."
        )
    classified_images = table_image_ref_count + paragraph_image_ref_count
    if all_image_refs and classified_images < len(all_image_refs):
        warnings.append(
            "Some image references are outside paragraphs/tables or in structures not yet classified."
        )

    return DocxSectionMaterialIndexResult(
        source_path=source_path,
        heading_count=len(headings),
        slice_count=len(slices),
        material_paragraph_count=material_paragraph_count,
        material_paragraph_char_count=material_paragraph_char_count,
        table_count=table_count,
        document_image_ref_count=len(all_image_refs),
        table_image_ref_count=table_image_ref_count,
        paragraph_image_ref_count=paragraph_image_ref_count,
        header_footer_text_count=header_footer_text_count,
        headings=headings,
        slices=slices,
        warnings=warnings,
    )


def write_section_material_index_outputs(
    result: DocxSectionMaterialIndexResult,
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
    report_target.write_text(render_section_material_index_report(result), encoding="utf-8")


def render_section_material_index_report(result: DocxSectionMaterialIndexResult) -> str:
    lines = [
        "# DOCX 章节素材切片索引报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 标题数：{result.heading_count}",
        f"- 切片数：{result.slice_count}",
        f"- 正文素材段落数：{result.material_paragraph_count}",
        f"- 正文素材字符数：{result.material_paragraph_char_count}",
        f"- 表格数：{result.table_count}",
        f"- 文档图片引用数：{result.document_image_ref_count}",
        f"- 表内图片引用数：{result.table_image_ref_count}",
        f"- 段落图片引用数：{result.paragraph_image_ref_count}",
        f"- 页眉页脚文本数：{result.header_footer_text_count}",
        "",
        "## 素材统计",
        "",
    ]

    level_counts = Counter(slice_.level for slice_ in result.slices if slice_.level is not None)
    if level_counts:
        lines.append("- 切片层级分布：" + ", ".join(f"L{level}={count}" for level, count in sorted(level_counts.items())))

    rich_slices = sorted(
        result.slices,
        key=lambda slice_: (
            slice_.subtree_table_count,
            slice_.subtree_image_count,
            slice_.subtree_paragraph_count,
        ),
        reverse=True,
    )
    if rich_slices:
        lines.append("- 子树素材最多的章节：")
        for slice_ in rich_slices[:20]:
            path = " > ".join(slice_.section_path) if slice_.section_path else "(未归属)"
            lines.append(
                f"  - {path}: subtree_paragraphs={slice_.subtree_paragraph_count}, "
                f"subtree_tables={slice_.subtree_table_count}, subtree_images={slice_.subtree_image_count}, "
                f"direct_paragraphs={slice_.paragraph_count}, direct_tables={slice_.table_count}"
            )

    lines.extend(["", "## 切片预览", ""])
    if not result.slices:
        lines.append("- 未形成素材切片。")
    for slice_ in result.slices[:160]:
        path = " > ".join(slice_.section_path) if slice_.section_path else "(未归属)"
        lines.append(
            f"- {slice_.slice_id}: L{slice_.level} {path} "
            f"direct=P{slice_.paragraph_count}/T{slice_.table_count}/I{slice_.image_count}, "
            f"subtree=P{slice_.subtree_paragraph_count}/T{slice_.subtree_table_count}/I{slice_.subtree_image_count}"
        )
        for paragraph in slice_.paragraphs[:2]:
            lines.append(f"  - P{paragraph.paragraph_index}: {paragraph.text_preview}")
        for table in slice_.tables[:2]:
            header = " | ".join(table.header_preview)
            lines.append(
                f"  - T{table.table_index}: rows={table.row_count}, "
                f"cols={table.max_column_count}, images={table.image_count}, header={header}"
            )
    if len(result.slices) > 160:
        lines.append("")
        lines.append(f"... 仅展示前 160 个切片，完整索引见 JSON。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _summarize_paragraph(
    paragraph_node: ET.Element,
    rels: dict[str, str],
    *,
    paragraph_index: int,
    block_index: int,
    section_path: list[str],
    preview_paragraph_chars: int,
) -> tuple[SectionParagraphRecord, list[SectionImageBinding]]:
    rel_ids = _image_rel_ids(paragraph_node)
    text = _node_text(paragraph_node)
    record = SectionParagraphRecord(
        paragraph_index=paragraph_index,
        block_index=block_index,
        style=_paragraph_style(paragraph_node),
        char_count=len(text),
        text_preview=text[:preview_paragraph_chars],
        image_count=len(rel_ids),
    )
    bindings = [
        SectionImageBinding(
            rel_id=rel_id,
            target=rels.get(rel_id, ""),
            part_name=_part_name_from_target(rels.get(rel_id, "")),
            context="paragraph",
            block_index=block_index,
            section_path=section_path,
            paragraph_index=paragraph_index,
        )
        for rel_id in rel_ids
    ]
    return record, bindings


def _current_or_preface_slice(
    current_path: list[SectionHeading],
    slices_by_heading: dict[int, SectionMaterialSlice],
    preface_slice: SectionMaterialSlice | None,
    slices: list[SectionMaterialSlice],
) -> SectionMaterialSlice:
    if current_path:
        return slices_by_heading[current_path[-1].heading_index]
    if preface_slice is not None:
        return preface_slice
    preface_slice = SectionMaterialSlice(
        slice_id="S_preface",
        heading_index=None,
        level=None,
        section_path=[],
    )
    slices.append(preface_slice)
    return preface_slice


def _add_paragraph_to_slice(
    material_slice: SectionMaterialSlice,
    paragraph: SectionParagraphRecord,
    image_bindings: list[SectionImageBinding],
    *,
    preview_paragraphs_per_slice: int,
) -> None:
    material_slice.paragraph_count += 1
    material_slice.paragraph_char_count += paragraph.char_count
    material_slice.image_count += paragraph.image_count
    if len(material_slice.paragraphs) < preview_paragraphs_per_slice:
        material_slice.paragraphs.append(paragraph)
    material_slice.image_bindings.extend(image_bindings)
    _touch_slice_range(material_slice, paragraph.block_index)


def _table_bindings_to_section_bindings(
    table_bindings,
    section_path: list[str],
    block_index: int,
) -> list[SectionImageBinding]:
    return [
        SectionImageBinding(
            rel_id=binding.rel_id,
            target=binding.target,
            part_name=binding.part_name,
            context="table_cell",
            block_index=block_index,
            section_path=section_path,
            table_index=binding.table_index,
            row_index=binding.row_index,
            cell_index=binding.cell_index,
            cell_text=binding.cell_text,
            row_text=binding.row_text,
            header_text=binding.header_text,
            previous_row_text=binding.previous_row_text,
            previous_row_texts=list(binding.previous_row_texts),
            next_row_text=binding.next_row_text,
            previous_non_empty_cell_text=binding.previous_non_empty_cell_text,
            next_non_empty_cell_text=binding.next_non_empty_cell_text,
            left_cell_text=binding.left_cell_text,
            right_cell_text=binding.right_cell_text,
            above_cell_text=binding.above_cell_text,
            below_cell_text=binding.below_cell_text,
            nearby_text=binding.nearby_text,
            caption_candidates=list(binding.caption_candidates),
        )
        for binding in table_bindings
    ]


def _touch_slice_range(material_slice: SectionMaterialSlice, block_index: int) -> None:
    if material_slice.start_block_index is None or block_index < material_slice.start_block_index:
        material_slice.start_block_index = block_index
    if material_slice.end_block_index is None or block_index > material_slice.end_block_index:
        material_slice.end_block_index = block_index


def _populate_subtree_counts(slices: list[SectionMaterialSlice]) -> None:
    for parent in slices:
        parent.subtree_paragraph_count = parent.paragraph_count
        parent.subtree_table_count = parent.table_count
        parent.subtree_image_count = parent.image_count
        parent.descendant_slice_count = 0
        if not parent.section_path:
            continue
        parent_path = tuple(parent.section_path)
        for child in slices:
            if child is parent or len(child.section_path) <= len(parent_path):
                continue
            if tuple(child.section_path[: len(parent_path)]) != parent_path:
                continue
            parent.subtree_paragraph_count += child.paragraph_count
            parent.subtree_table_count += child.table_count
            parent.subtree_image_count += child.image_count
            parent.descendant_slice_count += 1
