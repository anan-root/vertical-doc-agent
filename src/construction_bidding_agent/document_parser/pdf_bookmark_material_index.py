"""按 PDF 书签页码范围构建优秀标书素材切片索引。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .models import (
    PdfBookmarkMaterialIndexResult,
    PdfBookmarkMaterialSlice,
    PdfImageBinding,
    PdfPageMaterialSummary,
    PdfTableLikeRecord,
    SectionParagraphRecord,
    TableIndexCellPreview,
    TableIndexRowPreview,
)
from .pdf_bookmark_probe import (
    _bookmark_items,
    _page_objid_to_page_no,
)


def build_pdf_bookmark_material_index(
    path: str | Path,
    *,
    preview_paragraphs_per_slice: int = 5,
    preview_paragraph_chars: int = 260,
    preview_tables_per_slice: int = 3,
    preview_images_per_slice: int = 5,
    preview_rows_per_table: int = 3,
    preview_text_chars: int = 80,
    body_top_ratio: float = 0.08,
    body_bottom_ratio: float = 0.08,
    include_page_summaries: bool = False,
) -> PdfBookmarkMaterialIndexResult:
    """构建 PDF 优秀标书素材切片索引。

    PDF 入口以书签为章节边界；正文按页预处理后再聚合到书签范围，避免
    对长文档进行重复扫描。
    """

    source = Path(path)
    if not source.exists():
        return PdfBookmarkMaterialIndexResult(
            source_path=str(source),
            page_count=0,
            bookmark_count=0,
            slice_count=0,
            text_page_count=0,
            material_paragraph_count=0,
            material_paragraph_char_count=0,
            table_like_count=0,
            image_count=0,
            warnings=[f"File not found: {source}"],
        )

    try:
        import pdfplumber
    except ModuleNotFoundError:
        return PdfBookmarkMaterialIndexResult(
            source_path=str(source),
            page_count=0,
            bookmark_count=0,
            slice_count=0,
            text_page_count=0,
            material_paragraph_count=0,
            material_paragraph_char_count=0,
            table_like_count=0,
            image_count=0,
            warnings=["pdfplumber is not installed."],
        )

    warnings: list[str] = []
    with pdfplumber.open(str(source)) as pdf:
        page_count = len(pdf.pages)
        raw_outlines = list(pdf.doc.get_outlines()) if hasattr(pdf.doc, "get_outlines") else []
        bookmarks = _bookmark_items(raw_outlines, _page_objid_to_page_no(pdf), page_count)
        page_summaries = _build_page_summaries(
            pdf,
            preview_rows_per_table=preview_rows_per_table,
            preview_text_chars=preview_text_chars,
            body_top_ratio=body_top_ratio,
            body_bottom_ratio=body_bottom_ratio,
        )

    if not bookmarks:
        warnings.append("PDF 未读取到书签，无法按章节切片。")
    unmapped = [item for item in bookmarks if item.start_page is None or item.end_page is None]
    if unmapped:
        warnings.append(f"{len(unmapped)} 个书签缺少页码范围，未生成对应切片。")

    page_by_no = {summary.page_no: summary for summary in page_summaries}
    slices = _build_slices_from_page_summaries(
        bookmarks,
        page_by_no,
        preview_paragraphs_per_slice=preview_paragraphs_per_slice,
        preview_paragraph_chars=preview_paragraph_chars,
        preview_tables_per_slice=preview_tables_per_slice,
        preview_images_per_slice=preview_images_per_slice,
    )

    level_counts = Counter(slice_.level for slice_ in slices)
    text_page_count = sum(1 for page in page_summaries if page.text_char_count > 0)
    material_paragraph_count = sum(page.paragraph_count for page in page_summaries)
    material_char_count = sum(page.text_char_count for page in page_summaries)
    table_count = sum(page.table_like_count for page in page_summaries)
    image_count = sum(page.image_count for page in page_summaries)
    if page_count and text_page_count == 0:
        warnings.append("未抽取到正文文本，PDF 可能是扫描件。")

    return PdfBookmarkMaterialIndexResult(
        source_path=str(source),
        page_count=page_count,
        bookmark_count=len(bookmarks),
        slice_count=len(slices),
        text_page_count=text_page_count,
        material_paragraph_count=material_paragraph_count,
        material_paragraph_char_count=material_char_count,
        table_like_count=table_count,
        image_count=image_count,
        header_footer_ignored=True,
        boundary_precision="page_level",
        bookmark_level_counts=dict(sorted(level_counts.items())),
        slices=slices,
        page_summaries=page_summaries if include_page_summaries else [],
        warnings=warnings,
    )


def write_pdf_bookmark_material_index_outputs(
    result: PdfBookmarkMaterialIndexResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_pdf_bookmark_material_index_report(result), encoding="utf-8")


def render_pdf_bookmark_material_index_report(result: PdfBookmarkMaterialIndexResult) -> str:
    lines = [
        "# PDF 优秀标书素材切片索引报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 页数：{result.page_count}",
        f"- 书签数：{result.bookmark_count}",
        f"- 切片数：{result.slice_count}",
        f"- 可抽取文本页数：{result.text_page_count}",
        f"- 正文素材段落数：{result.material_paragraph_count}",
        f"- 正文素材字符数：{result.material_paragraph_char_count}",
        f"- 疑似表格数：{result.table_like_count}",
        f"- 页级图片数：{result.image_count}",
        f"- 是否裁剪页眉页脚区域：{'是' if result.header_footer_ignored else '否'}",
        f"- 切片边界精度：{result.boundary_precision}",
        "- 书签层级分布：" + _format_counts(result.bookmark_level_counts),
        "",
        "## 使用建议",
        "",
        "- 目录范式：可参与二三级目录补强。",
        "- 正文素材：可按章节路径检索后送入章节正文生成输入包。",
        "- 表格素材：当前为 PDF 疑似表格预览，后续再决定是否升级为 rich_table 模板。",
        "- 图片素材：当前绑定到页和章节，通用施工做法图可候选复用，项目专属图需人工复核。",
        "- 边界说明：当前按书签起止页聚合素材；同页多个书签时可能包含同页相邻小节内容。",
        "- 检索优先级：优先使用 L3/L4 低层级切片；L1/L2 大切片更适合作为目录范式和章节范围参考。",
        "",
        "## 素材统计",
        "",
    ]
    rich_slices = sorted(
        result.slices,
        key=lambda slice_: (slice_.table_like_count, slice_.image_count, slice_.paragraph_char_count),
        reverse=True,
    )
    if rich_slices:
        lines.append("- 表格/图片较多的切片：")
        for slice_ in rich_slices[:20]:
            lines.append(
                f"  - {slice_.slice_id} L{slice_.level} P{_page_range(slice_)} "
                f"T{slice_.table_like_count}/I{slice_.image_count}: {' > '.join(slice_.section_path)}"
            )
    else:
        lines.append("- 未形成表格/图片统计。")

    lines.extend(["", "## 切片预览", ""])
    if not result.slices:
        lines.append("- 未生成素材切片。")
    for slice_ in result.slices[:180]:
        path = " > ".join(slice_.section_path)
        lines.append(
            f"- {slice_.slice_id}: L{slice_.level} P{_page_range(slice_)} {path} "
            f"chars={slice_.paragraph_char_count}, paragraphs={slice_.paragraph_count}, "
            f"tables={slice_.table_like_count}, images={slice_.image_count}"
        )
        for paragraph in slice_.paragraphs[:2]:
            lines.append(f"  - Page {paragraph.paragraph_index}: {paragraph.text_preview[:180]}")
        for table in slice_.tables[:2]:
            header = " | ".join(table.header_preview)
            lines.append(
                f"  - {table.table_id}: page={table.page_no}, rows={table.row_count}, "
                f"cols={table.max_column_count}, header={header}"
            )
    if len(result.slices) > 180:
        lines.append("")
        lines.append(f"... 仅展示前 180 个切片，完整索引见 JSON。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _build_page_summaries(
    pdf,
    *,
    preview_rows_per_table: int,
    preview_text_chars: int,
    body_top_ratio: float,
    body_bottom_ratio: float,
) -> list[PdfPageMaterialSummary]:
    summaries: list[PdfPageMaterialSummary] = []
    global_table_index = 0
    for page_no, page in enumerate(pdf.pages, start=1):
        body = _body_crop(page, top_ratio=body_top_ratio, bottom_ratio=body_bottom_ratio)
        text = _normalize_page_text(body.extract_text() or "")
        paragraphs = _split_paragraphs(text)
        tables = []
        try:
            raw_tables = body.extract_tables() or []
        except Exception:
            raw_tables = []
        for raw_table in raw_tables:
            table = _table_like_record(
                raw_table,
                table_index=global_table_index,
                page_no=page_no,
                preview_rows_per_table=preview_rows_per_table,
                preview_text_chars=preview_text_chars,
            )
            if table.text_char_count > 0:
                tables.append(table)
                global_table_index += 1
        images = _image_bindings_for_page(page, page_no, body_top_ratio=body_top_ratio, body_bottom_ratio=body_bottom_ratio)
        summaries.append(
            PdfPageMaterialSummary(
                page_no=page_no,
                paragraph_count=len(paragraphs),
                text_char_count=len(text),
                table_like_count=len(tables),
                image_count=len(images),
                text_preview="\n".join(paragraphs[:4])[:500],
                tables=tables,
                image_bindings=images,
            )
        )
    return summaries


def _build_slices_from_page_summaries(
    bookmarks,
    page_by_no: dict[int, PdfPageMaterialSummary],
    *,
    preview_paragraphs_per_slice: int,
    preview_paragraph_chars: int,
    preview_tables_per_slice: int,
    preview_images_per_slice: int,
) -> list[PdfBookmarkMaterialSlice]:
    slices: list[PdfBookmarkMaterialSlice] = []
    for bookmark in bookmarks:
        if bookmark.start_page is None or bookmark.end_page is None:
            continue
        pages = [
            page_by_no[page_no]
            for page_no in range(bookmark.start_page, bookmark.end_page + 1)
            if page_no in page_by_no
        ]
        paragraph_count = sum(page.paragraph_count for page in pages)
        char_count = sum(page.text_char_count for page in pages)
        table_count = sum(page.table_like_count for page in pages)
        image_count = sum(page.image_count for page in pages)
        paragraphs = _slice_paragraph_previews(
            pages,
            limit=preview_paragraphs_per_slice,
            preview_chars=preview_paragraph_chars,
        )
        tables = _slice_tables(pages, limit=preview_tables_per_slice)
        images = _slice_images(pages, limit=preview_images_per_slice)
        slices.append(
            PdfBookmarkMaterialSlice(
                slice_id=f"PDFS{bookmark.bookmark_index:04d}",
                bookmark_index=bookmark.bookmark_index,
                level=bookmark.level,
                title=bookmark.title,
                clean_title=bookmark.clean_title,
                number=bookmark.number,
                section_path=bookmark.path,
                start_page=bookmark.start_page,
                end_page=bookmark.end_page,
                page_count=len(pages),
                paragraph_count=paragraph_count,
                paragraph_char_count=char_count,
                table_like_count=table_count,
                image_count=image_count,
                child_count=bookmark.child_count,
                descendant_slice_count=_descendant_count(bookmark, bookmarks),
                reuse_level="light_rewrite",
                project_specific_risk=_project_specific_risk(bookmark.title, bookmark.path),
                confidence=0.94 if bookmark.level <= 4 else 0.86,
                paragraphs=paragraphs,
                tables=tables,
                image_bindings=images,
            )
        )
    return slices


def _slice_paragraph_previews(
    pages: list[PdfPageMaterialSummary],
    *,
    limit: int,
    preview_chars: int,
) -> list[SectionParagraphRecord]:
    paragraphs: list[SectionParagraphRecord] = []
    for page in pages:
        for text in _split_paragraphs(page.text_preview):
            if len(paragraphs) >= limit:
                return paragraphs
            paragraphs.append(
                SectionParagraphRecord(
                    paragraph_index=page.page_no,
                    block_index=page.page_no,
                    style=None,
                    char_count=len(text),
                    text_preview=text[:preview_chars],
                    image_count=page.image_count,
                )
            )
    return paragraphs


def _slice_tables(pages: list[PdfPageMaterialSummary], *, limit: int) -> list[PdfTableLikeRecord]:
    tables: list[PdfTableLikeRecord] = []
    for page in pages:
        for table in page.tables:
            if len(tables) >= limit:
                return tables
            tables.append(table)
    return tables


def _slice_images(pages: list[PdfPageMaterialSummary], *, limit: int) -> list[PdfImageBinding]:
    images: list[PdfImageBinding] = []
    for page in pages:
        for image in page.image_bindings:
            if len(images) >= limit:
                return images
            images.append(image)
    return images


def _body_crop(page, *, top_ratio: float, bottom_ratio: float):
    height = float(page.height)
    top = height * top_ratio
    bottom = height * (1 - bottom_ratio)
    if top >= bottom:
        return page
    return page.crop((0, top, page.width, bottom))


def _table_like_record(
    raw_table,
    *,
    table_index: int,
    page_no: int,
    preview_rows_per_table: int,
    preview_text_chars: int,
) -> PdfTableLikeRecord:
    row_count = 0
    max_column_count = 0
    text_char_count = 0
    row_previews: list[TableIndexRowPreview] = []
    header_preview: list[str] = []
    for row_index, row in enumerate(raw_table or []):
        row_count += 1
        cells = [_normalize_cell(cell) for cell in row]
        max_column_count = max(max_column_count, len(cells))
        text_char_count += sum(len(cell) for cell in cells)
        if row_index < preview_rows_per_table:
            preview = TableIndexRowPreview(row_index=row_index)
            for cell_index, cell in enumerate(cells):
                preview.cells.append(
                    TableIndexCellPreview(
                        cell_index=cell_index,
                        text_preview=cell[:preview_text_chars],
                        image_count=0,
                    )
                )
            row_previews.append(preview)
            if row_index == 0:
                header_preview = [cell[:preview_text_chars] for cell in cells]
    return PdfTableLikeRecord(
        table_id=f"PDF-T{table_index:05d}",
        table_index=table_index,
        page_no=page_no,
        row_count=row_count,
        max_column_count=max_column_count,
        text_char_count=text_char_count,
        header_preview=header_preview,
        row_previews=row_previews,
    )


def _image_bindings_for_page(
    page,
    page_no: int,
    *,
    body_top_ratio: float,
    body_bottom_ratio: float,
) -> list[PdfImageBinding]:
    height = float(page.height)
    top_limit = height * body_top_ratio
    bottom_limit = height * (1 - body_bottom_ratio)
    bindings: list[PdfImageBinding] = []
    for index, image in enumerate(page.images or []):
        top = _as_float(image.get("top"))
        bottom = _as_float(image.get("bottom"))
        if top is not None and top < top_limit:
            continue
        if bottom is not None and bottom > bottom_limit:
            continue
        srcsize = image.get("srcsize") if isinstance(image.get("srcsize"), tuple) else (None, None)
        bindings.append(
            PdfImageBinding(
                image_id=f"PDFIMG-P{page_no:04d}-{index:03d}",
                page_no=page_no,
                image_index=index,
                x0=_as_float(image.get("x0")),
                top=top,
                width=_as_float(image.get("width")),
                height=_as_float(image.get("height")),
                src_width=srcsize[0],
                src_height=srcsize[1],
                reuse_level="review_required",
                risk_level="medium",
                notes="PDF 图片先按页级绑定，需结合章节语义复核后使用。",
            )
        )
    return bindings


def _descendant_count(bookmark, bookmarks) -> int:
    count = 0
    for candidate in bookmarks[bookmark.bookmark_index + 1 :]:
        if candidate.level <= bookmark.level:
            break
        count += 1
    return count


def _project_specific_risk(title: str, path: list[str]) -> str:
    text = " ".join([title, *path])
    if any(keyword in text for keyword in ["总平面", "平面布置", "进度计划", "网络计划", "临时用地", "地理位置"]):
        return "high"
    if any(keyword in text for keyword in ["项目概况", "工程概况", "现场", "地质", "水文"]):
        return "medium"
    return "low"


def _normalize_page_text(text: str) -> str:
    lines = [_normalize_cell(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line and not _looks_like_page_number(line))


def _split_paragraphs(text: str) -> list[str]:
    return [line for line in (_normalize_cell(line) for line in text.splitlines()) if line]


def _normalize_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _looks_like_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"(?:第\s*)?\d{1,4}(?:\s*页)?", text.strip()))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_counts(counts: dict[int, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"L{level}={count}" for level, count in sorted(counts.items()))


def _page_range(slice_: PdfBookmarkMaterialSlice) -> str:
    if slice_.start_page is None:
        return "?"
    if slice_.end_page is None or slice_.end_page == slice_.start_page:
        return str(slice_.start_page)
    return f"{slice_.start_page}-{slice_.end_page}"
