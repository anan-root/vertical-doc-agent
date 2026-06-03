"""融合 PDF 书签结构和转格式 DOCX 素材的优秀标书索引。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import (
    ExcellentBidFusionIndexResult,
    ExcellentBidFusionSlice,
    FusionMatchInfo,
    PdfImageBinding,
    PdfTableLikeRecord,
    SectionImageBinding,
    SectionParagraphRecord,
    SectionTableRecord,
    TableIndexCellPreview,
    TableIndexRowPreview,
)


SCHEMA_VERSION = "excellent_bid_fusion_index_v1"
_NUMBERED_TITLE_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)*)(?:[.．、]\s*|\s+)(?P<title>\S.*)$")


def build_excellent_bid_fusion_index_from_files(
    pdf_material_index_json: str | Path,
    docx_material_index_json: str | Path,
    *,
    min_match_score: float = 0.72,
    ambiguity_delta: float = 0.01,
) -> ExcellentBidFusionIndexResult:
    pdf_index = _read_json(pdf_material_index_json)
    docx_index = _read_json(docx_material_index_json)
    return build_excellent_bid_fusion_index(
        pdf_index,
        docx_index,
        min_match_score=min_match_score,
        ambiguity_delta=ambiguity_delta,
    )


def build_excellent_bid_fusion_index(
    pdf_index: dict[str, Any],
    docx_index: dict[str, Any],
    *,
    min_match_score: float = 0.72,
    ambiguity_delta: float = 0.01,
) -> ExcellentBidFusionIndexResult:
    """以 PDF 书签为骨架，将 DOCX 表格/图片素材挂回对应章节。"""

    pdf_slices = [slice_ for slice_ in pdf_index.get("slices") or [] if isinstance(slice_, dict)]
    docx_slices = [_DocxSlice.from_raw(slice_) for slice_ in docx_index.get("slices") or [] if isinstance(slice_, dict)]
    warnings: list[str] = []
    fusion_slices: list[ExcellentBidFusionSlice] = []
    matched_count = 0
    ambiguous_count = 0
    fallback_count = 0
    unmatched_count = 0

    for pdf_slice in pdf_slices:
        pdf_meta = _PdfSlice.from_raw(pdf_slice)
        match = _best_docx_match(
            pdf_meta,
            docx_slices,
            min_match_score=min_match_score,
            ambiguity_delta=ambiguity_delta,
        )
        if match.status == "matched":
            matched_count += 1
        elif match.status == "ambiguous":
            ambiguous_count += 1
        elif match.status == "fallback":
            fallback_count += 1
        else:
            unmatched_count += 1
        docx_raw = match.raw if match.raw else None
        fusion_slices.append(_fusion_slice(pdf_slice, pdf_meta, match.info, docx_raw))

    table_count = sum(slice_.docx_table_count for slice_ in fusion_slices)
    image_count = sum(slice_.docx_image_count for slice_ in fusion_slices)
    pdf_table_count = sum(int(slice_.pdf_table_like_count or 0) for slice_ in fusion_slices)
    pdf_image_count = sum(int(slice_.pdf_image_count or 0) for slice_ in fusion_slices)

    if fallback_count:
        warnings.append(f"{fallback_count} 个 PDF 书签切片使用 DOCX 父章节子树素材兜底，需抽样复核。")
    if unmatched_count:
        warnings.append(f"{unmatched_count} 个 PDF 书签切片未匹配到转格式 DOCX 素材。")
    if ambiguous_count:
        warnings.append(f"{ambiguous_count} 个 PDF 书签切片存在多个相近 DOCX 匹配候选，需抽样复核。")

    return ExcellentBidFusionIndexResult(
        schema_version=SCHEMA_VERSION,
        source_pdf_path=str(pdf_index.get("source_path") or ""),
        source_docx_path=str(docx_index.get("source_path") or ""),
        pdf_slice_count=len(pdf_slices),
        docx_slice_count=len(docx_slices),
        fusion_slice_count=len(fusion_slices),
        matched_count=matched_count,
        ambiguous_count=ambiguous_count,
        fallback_count=fallback_count,
        unmatched_count=unmatched_count,
        table_count=table_count,
        image_count=image_count,
        pdf_table_like_count=pdf_table_count,
        pdf_image_count=pdf_image_count,
        boundary_precision=str(pdf_index.get("boundary_precision") or "pdf_bookmark_page_level"),
        slices=fusion_slices,
        warnings=warnings,
    )


def write_excellent_bid_fusion_index_outputs(
    result: ExcellentBidFusionIndexResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_excellent_bid_fusion_index_report(result), encoding="utf-8")


def render_excellent_bid_fusion_index_report(result: ExcellentBidFusionIndexResult) -> str:
    lines = [
        "# 优秀标书 PDF+DOCX 融合素材索引报告",
        "",
        f"- PDF 来源：`{result.source_pdf_path}`",
        f"- DOCX 来源：`{result.source_docx_path}`",
        f"- PDF 书签切片数：{result.pdf_slice_count}",
        f"- DOCX 素材切片数：{result.docx_slice_count}",
        f"- 融合切片数：{result.fusion_slice_count}",
        f"- 已匹配：{result.matched_count}",
        f"- 多候选匹配：{result.ambiguous_count}",
        f"- 父章节素材兜底：{result.fallback_count}",
        f"- 未匹配：{result.unmatched_count}",
        f"- DOCX 表格数：{result.table_count}",
        f"- DOCX 表内/段落图片数：{result.image_count}",
        f"- PDF 疑似表格数：{result.pdf_table_like_count}",
        f"- PDF 页级图片数：{result.pdf_image_count}",
        f"- 结构来源：{result.structure_source}",
        f"- 素材来源：{result.material_source}",
        f"- 边界精度：{result.boundary_precision}",
        "",
        "## 使用建议",
        "",
        "- 目录树和章节路径以 PDF 书签为准。",
        "- 表格、表内图片和行级样例优先使用已匹配的 DOCX 素材。",
        "- 未精确匹配但能定位到 DOCX 父章节子树时，使用父章节下的 DOCX 子章节素材作为候选。",
        "- 完全未匹配切片回退使用 PDF 页级文本、疑似表格和页级图片。",
        "- 多候选匹配切片建议抽样复核，尤其是只按标题匹配而非编号匹配的节点。",
        "",
        "## 匹配质量",
        "",
    ]
    unmatched = [slice_ for slice_ in result.slices if slice_.match.status == "unmatched"]
    ambiguous = [slice_ for slice_ in result.slices if slice_.match.status == "ambiguous"]
    fallback = [slice_ for slice_ in result.slices if slice_.match.status == "fallback"]
    if ambiguous:
        lines.append("- 多候选匹配样例：")
        for slice_ in ambiguous[:20]:
            lines.append(
                f"  - {slice_.fusion_slice_id} {slice_.match.method} score={slice_.match.score:.2f}: "
                f"{' > '.join(slice_.section_path)}"
            )
    if unmatched:
        lines.append("- 未匹配样例：")
        for slice_ in unmatched[:20]:
            lines.append(f"  - {slice_.fusion_slice_id}: {' > '.join(slice_.section_path)}")
    if fallback:
        lines.append("- 父章节素材兜底样例：")
        for slice_ in fallback[:20]:
            lines.append(
                f"  - {slice_.fusion_slice_id} candidates={slice_.match.candidate_count}: "
                f"{' > '.join(slice_.section_path)}"
            )
    if not ambiguous and not unmatched:
        lines.append("- 所有 PDF 书签切片均唯一匹配到 DOCX 素材。")

    lines.extend(["", "## 富素材切片", ""])
    rich_slices = sorted(
        result.slices,
        key=lambda slice_: (
            slice_.docx_table_count,
            slice_.docx_image_count,
            slice_.pdf_table_like_count,
            slice_.pdf_image_count,
        ),
        reverse=True,
    )
    for slice_ in rich_slices[:30]:
        lines.append(
            f"- {slice_.fusion_slice_id}: L{slice_.level} P{_page_range(slice_)} "
            f"DOCX T{slice_.docx_table_count}/I{slice_.docx_image_count}, "
            f"PDF T{slice_.pdf_table_like_count}/I{slice_.pdf_image_count}, "
            f"match={slice_.match.status}/{slice_.match.method}/{slice_.match.score:.2f}, "
            f"{' > '.join(slice_.section_path)}"
        )

    lines.extend(["", "## 切片预览", ""])
    for slice_ in result.slices[:180]:
        lines.append(
            f"- {slice_.fusion_slice_id}: L{slice_.level} P{_page_range(slice_)} "
            f"match={slice_.match.status}/{slice_.match.method}/{slice_.match.score:.2f} "
            f"DOCX={slice_.docx_slice_id or '-'} {' > '.join(slice_.section_path)}"
        )
        for paragraph in slice_.paragraphs[:2]:
            lines.append(f"  - P{paragraph.paragraph_index}: {paragraph.text_preview[:180]}")
        for table in slice_.tables[:2]:
            header = " | ".join(table.header_preview)
            lines.append(
                f"  - T{table.table_index}: rows={table.row_count}, "
                f"cols={table.max_column_count}, images={table.image_count}, header={header}"
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


class _PdfSlice:
    def __init__(
        self,
        *,
        raw: dict[str, Any],
        slice_id: str,
        level: int,
        title: str,
        clean_title: str,
        number: str | None,
        section_path: list[str],
        canonical_path: tuple[str, ...],
        number_path: tuple[str, ...],
    ) -> None:
        self.raw = raw
        self.slice_id = slice_id
        self.level = level
        self.title = title
        self.clean_title = clean_title
        self.number = number
        self.section_path = section_path
        self.canonical_path = canonical_path
        self.number_path = number_path

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "_PdfSlice":
        section_path = _path(raw.get("section_path"))
        title = str(raw.get("title") or (section_path[-1] if section_path else ""))
        section_path = _section_path_with_current_title(section_path, title)
        number = _optional_str(raw.get("number")) or _split_numbered_title(title)[0]
        clean_title = str(raw.get("clean_title") or _split_numbered_title(title)[1])
        return cls(
            raw=raw,
            slice_id=str(raw.get("slice_id") or ""),
            level=int(raw.get("level") or len(section_path) or 1),
            title=title,
            clean_title=clean_title,
            number=number,
            section_path=section_path,
            canonical_path=_canonical_path(section_path),
            number_path=_number_path(section_path),
        )


class _DocxSlice:
    def __init__(
        self,
        *,
        raw: dict[str, Any],
        slice_id: str,
        level: int,
        title: str,
        clean_title: str,
        number: str | None,
        section_path: list[str],
        canonical_path: tuple[str, ...],
        number_path: tuple[str, ...],
    ) -> None:
        self.raw = raw
        self.slice_id = slice_id
        self.level = level
        self.title = title
        self.clean_title = clean_title
        self.number = number
        self.section_path = section_path
        self.canonical_path = canonical_path
        self.number_path = number_path

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "_DocxSlice":
        section_path = _path(raw.get("section_path"))
        title = section_path[-1] if section_path else ""
        number, clean_title = _split_numbered_title(title)
        return cls(
            raw=raw,
            slice_id=str(raw.get("slice_id") or ""),
            level=int(raw.get("level") or len(section_path) or 1),
            title=title,
            clean_title=clean_title,
            number=number,
            section_path=section_path,
            canonical_path=_canonical_path(section_path),
            number_path=_number_path(section_path),
        )


class _Match:
    def __init__(self, *, info: FusionMatchInfo, raw: dict[str, Any] | None, status: str) -> None:
        self.info = info
        self.raw = raw
        self.status = status


def _best_docx_match(
    pdf: _PdfSlice,
    docx_slices: list[_DocxSlice],
    *,
    min_match_score: float,
    ambiguity_delta: float,
) -> _Match:
    scored: list[tuple[float, str, _DocxSlice]] = []
    for docx in docx_slices:
        score, method = _match_score(pdf, docx)
        if score >= min_match_score:
            scored.append((score, method, docx))
    if not scored:
        fallback = _fallback_docx_subtree_match(pdf, docx_slices)
        if fallback is not None:
            return fallback
        return _Match(
            status="unmatched",
            raw=None,
            info=FusionMatchInfo(
                status="unmatched",
                method=None,
                score=0,
                pdf_slice_id=pdf.slice_id,
                note="未找到足够可信的 DOCX 章节素材，回退使用 PDF 页级素材。",
            ),
        )
    scored.sort(key=lambda item: (-item[0], item[2].level, item[2].slice_id))
    best_score, best_method, best_docx = scored[0]
    close = [item for item in scored if best_score - item[0] <= ambiguity_delta]
    status = "ambiguous" if len(close) > 1 else "matched"
    note = "唯一匹配。" if status == "matched" else f"存在 {len(close)} 个相近候选，已取排序第一项。"
    return _Match(
        status=status,
        raw=best_docx.raw,
        info=FusionMatchInfo(
            status=status,
            method=best_method,
            score=best_score,
            pdf_slice_id=pdf.slice_id,
            docx_slice_id=best_docx.slice_id,
            note=note,
            candidate_count=len(close),
        ),
    )


def _fallback_docx_subtree_match(pdf: _PdfSlice, docx_slices: list[_DocxSlice]) -> _Match | None:
    if len(pdf.canonical_path) <= 1 or not _has_meaningful_title(pdf.clean_title):
        return None

    parent_path = pdf.canonical_path[:-1]
    candidates = [
        docx
        for docx in docx_slices
        if len(docx.canonical_path) > len(parent_path)
        and docx.canonical_path[: len(parent_path)] == parent_path
        and _has_direct_material(docx.raw)
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item.level, item.slice_id))
    merged_raw = _merge_docx_subtree_material(candidates, section_path=pdf.section_path)
    return _Match(
        status="fallback",
        raw=merged_raw,
        info=FusionMatchInfo(
            status="fallback",
            method="parent_subtree",
            score=0.68,
            pdf_slice_id=pdf.slice_id,
            docx_slice_id=merged_raw["slice_id"],
            note=(
                "未找到精确 DOCX 章节，已聚合 PDF 父路径下的 DOCX 子章节素材作为候选，"
                "需要人工抽样复核。"
            ),
            candidate_count=len(candidates),
        ),
    )


def _match_score(pdf: _PdfSlice, docx: _DocxSlice) -> tuple[float, str]:
    if pdf.number and docx.number and pdf.number == docx.number:
        if _canonical_text(pdf.clean_title) == _canonical_text(docx.clean_title):
            return 1.0, "number_and_title"
        if "." in pdf.number:
            return 0.9, "number"
        if _title_overlap(pdf.clean_title, docx.clean_title) >= 0.6:
            return 0.86, "short_number_with_title_overlap"
    if pdf.number_path and pdf.number_path == docx.number_path:
        return 0.96, "number_path"
    if _path_suffix_matches(pdf.canonical_path, docx.canonical_path):
        return 0.92, "path_suffix"
    if _canonical_text(pdf.clean_title) == _canonical_text(docx.clean_title):
        level_gap = abs(pdf.level - docx.level)
        return max(0.82 - level_gap * 0.03, 0.72), "title"
    overlap = _title_overlap(pdf.clean_title, docx.clean_title)
    if overlap >= 0.8:
        return 0.76, "title_overlap"
    return 0, "none"


def _fusion_slice(
    pdf_raw: dict[str, Any],
    pdf: _PdfSlice,
    match: FusionMatchInfo,
    docx_raw: dict[str, Any] | None,
) -> ExcellentBidFusionSlice:
    docx_tables = _section_tables(docx_raw.get("tables") if docx_raw else [])
    docx_images = _section_images(docx_raw.get("image_bindings") if docx_raw else [])
    docx_paragraphs = _section_paragraphs(docx_raw.get("paragraphs") if docx_raw else [])
    pdf_tables = _pdf_tables(pdf_raw.get("tables") or [])
    pdf_images = _pdf_images(pdf_raw.get("image_bindings") or [])
    paragraphs = docx_paragraphs or _section_paragraphs(pdf_raw.get("paragraphs") or [])
    confidence = float(pdf_raw.get("confidence") or 0.88)
    if match.status == "matched":
        confidence = min(0.98, confidence + 0.04)
    elif match.status == "ambiguous":
        confidence = min(confidence, 0.82)
    elif match.status == "fallback":
        confidence = min(confidence, 0.76)
    elif match.status == "unmatched":
        confidence = min(confidence, 0.72)
    return ExcellentBidFusionSlice(
        fusion_slice_id=f"FUS-{pdf.slice_id}",
        pdf_slice_id=pdf.slice_id,
        docx_slice_id=match.docx_slice_id,
        match=match,
        level=int(pdf_raw.get("level") or pdf.level),
        title=str(pdf_raw.get("title") or pdf.title),
        clean_title=str(pdf_raw.get("clean_title") or pdf.clean_title),
        number=_optional_str(pdf_raw.get("number")) or pdf.number,
        section_path=_path(pdf_raw.get("section_path")) or pdf.section_path,
        start_page=_optional_int(pdf_raw.get("start_page")),
        end_page=_optional_int(pdf_raw.get("end_page")),
        page_count=int(pdf_raw.get("page_count") or 0),
        paragraph_count=int(pdf_raw.get("paragraph_count") or 0),
        paragraph_char_count=int(pdf_raw.get("paragraph_char_count") or 0),
        pdf_table_like_count=int(pdf_raw.get("table_like_count") or 0),
        pdf_image_count=int(pdf_raw.get("image_count") or 0),
        docx_table_count=int((docx_raw or {}).get("table_count") or 0),
        docx_image_count=int((docx_raw or {}).get("image_count") or 0),
        docx_subtree_table_count=int((docx_raw or {}).get("subtree_table_count") or 0),
        docx_subtree_image_count=int((docx_raw or {}).get("subtree_image_count") or 0),
        reuse_level=str(pdf_raw.get("reuse_level") or "light_rewrite"),
        project_specific_risk=str(pdf_raw.get("project_specific_risk") or "medium"),
        confidence=confidence,
        paragraphs=paragraphs,
        tables=docx_tables,
        image_bindings=docx_images,
        pdf_tables=pdf_tables,
        pdf_image_bindings=pdf_images,
    )


def _section_paragraphs(items: Any) -> list[SectionParagraphRecord]:
    result: list[SectionParagraphRecord] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
            SectionParagraphRecord(
                paragraph_index=_optional_int(item.get("paragraph_index")),
                block_index=int(item.get("block_index") or 0),
                style=_optional_str(item.get("style")),
                char_count=int(item.get("char_count") or 0),
                text_preview=str(item.get("text_preview") or ""),
                image_count=int(item.get("image_count") or 0),
            )
        )
    return result


def _section_tables(items: Any) -> list[SectionTableRecord]:
    result: list[SectionTableRecord] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
            SectionTableRecord(
                table_index=int(item.get("table_index") or 0),
                block_index=int(item.get("block_index") or 0),
                section_path=_path(item.get("section_path")),
                section_level=_optional_int(item.get("section_level")),
                nearest_heading_index=_optional_int(item.get("nearest_heading_index")),
                nearest_heading_text=_optional_str(item.get("nearest_heading_text")),
                row_count=int(item.get("row_count") or 0),
                max_column_count=int(item.get("max_column_count") or 0),
                image_count=int(item.get("image_count") or 0),
                header_preview=[str(value or "") for value in item.get("header_preview") or []],
                row_previews=_row_previews(item.get("row_previews") or []),
            )
        )
    return result


def _row_previews(items: Any) -> list[TableIndexRowPreview]:
    rows: list[TableIndexRowPreview] = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            TableIndexRowPreview(
                row_index=int(row.get("row_index") or 0),
                cells=[
                    TableIndexCellPreview(
                        cell_index=int(cell.get("cell_index") or 0),
                        text_preview=str(cell.get("text_preview") or ""),
                        image_count=int(cell.get("image_count") or 0),
                    )
                    for cell in row.get("cells") or []
                    if isinstance(cell, dict)
                ],
            )
        )
    return rows


def _section_images(items: Any) -> list[SectionImageBinding]:
    images: list[SectionImageBinding] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        images.append(
            SectionImageBinding(
                rel_id=str(item.get("rel_id") or ""),
                target=str(item.get("target") or ""),
                part_name=_optional_str(item.get("part_name")),
                context=str(item.get("context") or ""),
                block_index=int(item.get("block_index") or 0),
                section_path=_path(item.get("section_path")),
                paragraph_index=_optional_int(item.get("paragraph_index")),
                table_index=_optional_int(item.get("table_index")),
                row_index=_optional_int(item.get("row_index")),
                cell_index=_optional_int(item.get("cell_index")),
            )
        )
    return images


def _has_direct_material(raw: dict[str, Any]) -> bool:
    return bool(raw.get("paragraphs") or raw.get("tables") or raw.get("image_bindings"))


def _has_meaningful_title(title: str) -> bool:
    text = str(title or "").strip()
    return bool(re.search(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", text))


def _merge_docx_subtree_material(candidates: list[_DocxSlice], *, section_path: list[str]) -> dict[str, Any]:
    paragraphs: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    image_bindings: list[dict[str, Any]] = []
    paragraph_count = 0
    paragraph_char_count = 0

    for candidate in candidates:
        raw = candidate.raw
        paragraph_count += int(raw.get("paragraph_count") or 0)
        paragraph_char_count += int(raw.get("paragraph_char_count") or 0)
        paragraphs.extend(raw.get("paragraphs") or [])
        tables.extend(raw.get("tables") or [])
        image_bindings.extend(raw.get("image_bindings") or [])

    return {
        "slice_id": f"FALLBACK:{candidates[0].slice_id}..{candidates[-1].slice_id}",
        "level": len(section_path) or candidates[0].level,
        "section_path": section_path,
        "paragraph_count": paragraph_count,
        "paragraph_char_count": paragraph_char_count,
        "table_count": len(tables),
        "image_count": len(image_bindings),
        "subtree_table_count": len(tables),
        "subtree_image_count": len(image_bindings),
        "paragraphs": paragraphs[:5],
        "tables": tables,
        "image_bindings": image_bindings,
    }


def _pdf_tables(items: Any) -> list[PdfTableLikeRecord]:
    tables: list[PdfTableLikeRecord] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        tables.append(
            PdfTableLikeRecord(
                table_id=str(item.get("table_id") or ""),
                table_index=int(item.get("table_index") or 0),
                page_no=int(item.get("page_no") or 0),
                row_count=int(item.get("row_count") or 0),
                max_column_count=int(item.get("max_column_count") or 0),
                text_char_count=int(item.get("text_char_count") or 0),
                header_preview=[str(value or "") for value in item.get("header_preview") or []],
                row_previews=_row_previews(item.get("row_previews") or []),
            )
        )
    return tables


def _pdf_images(items: Any) -> list[PdfImageBinding]:
    images: list[PdfImageBinding] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        images.append(
            PdfImageBinding(
                image_id=str(item.get("image_id") or ""),
                page_no=int(item.get("page_no") or 0),
                image_index=int(item.get("image_index") or 0),
                x0=_optional_float(item.get("x0")),
                top=_optional_float(item.get("top")),
                width=_optional_float(item.get("width")),
                height=_optional_float(item.get("height")),
                src_width=_optional_int(item.get("src_width")),
                src_height=_optional_int(item.get("src_height")),
                reuse_level=str(item.get("reuse_level") or "review_required"),
                risk_level=str(item.get("risk_level") or "medium"),
                notes=str(item.get("notes") or ""),
            )
        )
    return images


def _canonical_path(path: list[str]) -> tuple[str, ...]:
    return tuple(_canonical_segment(segment) for segment in path)


def _section_path_with_current_title(section_path: list[str], title: str) -> list[str]:
    """以当前切片标题修正路径尾节点，避免上游元数据不一致导致误匹配。"""

    title = str(title or "").strip()
    if not title:
        return section_path
    if not section_path:
        return [title]
    if _canonical_segment(section_path[-1]) == _canonical_segment(title):
        return section_path
    return [*section_path[:-1], title]


def _number_path(path: list[str]) -> tuple[str, ...]:
    numbers = []
    for segment in path:
        number, _ = _split_numbered_title(segment)
        if number:
            numbers.append(number)
    return tuple(numbers)


def _path_suffix_matches(pdf_path: tuple[str, ...], docx_path: tuple[str, ...]) -> bool:
    if not pdf_path or not docx_path or len(docx_path) < len(pdf_path):
        return False
    return docx_path[-len(pdf_path) :] == pdf_path


def _canonical_segment(segment: str) -> str:
    number, title = _split_numbered_title(segment)
    prefix = number or ""
    return f"{prefix}:{_canonical_text(title)}"


def _canonical_text(text: str) -> str:
    return re.sub(r"[\s　.．、，,。；;：:（）()【】\[\]_-]+", "", str(text or "")).lower()


def _title_overlap(a: str, b: str) -> float:
    a_tokens = set(_tokens(a))
    b_tokens = set(_tokens(b))
    if not a_tokens or not b_tokens:
        return 0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or "")
    tokens = [item for item in raw if len(item) >= 2]
    compact = _canonical_text(text)
    if compact:
        tokens.append(compact)
    return tokens


def _split_numbered_title(title: str) -> tuple[str | None, str]:
    match = _NUMBERED_TITLE_RE.match(str(title or ""))
    if not match:
        return None, str(title or "").strip()
    return match.group("number"), match.group("title").strip()


def _path(value: Any) -> list[str]:
    return [str(part).strip() for part in value or [] if str(part).strip()]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _page_range(slice_: ExcellentBidFusionSlice) -> str:
    if slice_.start_page is None:
        return "?"
    if slice_.end_page is None or slice_.end_page == slice_.start_page:
        return str(slice_.start_page)
    return f"{slice_.start_page}-{slice_.end_page}"


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
