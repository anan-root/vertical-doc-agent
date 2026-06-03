"""从招标文件核心区域构建可送入 LLM 的切片。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import (
    TenderDetectedSection,
    TenderDocumentBlock,
    TenderDocumentIndexResult,
    TenderRegionSlice,
    TenderRegionSliceIndexResult,
)
from .tender_document_index import (
    TECHNICAL_SECTION_KEY,
    build_tender_document_index,
    _render_source_refs,
)


SCHEMA_VERSION = "tender_region_slices_v0.1"

CORE_REGION_ORDER = [
    "chapter_1_notice",
    "bidder_instructions_preface_table",
    "evaluation_method_preface_table",
    TECHNICAL_SECTION_KEY,
]

RECOMMENDED_LLM_TASKS = {
    "chapter_1_notice": ["project_info_extraction"],
    "bidder_instructions_preface_table": [
        "project_info_extraction",
        "technical_bid_requirements_extraction",
    ],
    "evaluation_method_preface_table": ["technical_score_points_extraction"],
    TECHNICAL_SECTION_KEY: ["technical_standards_extraction"],
}


def build_tender_region_slices_from_path(
    path: str | Path,
    *,
    file_id: str | None = None,
    max_blocks_per_slice: int | None = None,
) -> TenderRegionSliceIndexResult:
    document_index = build_tender_document_index(path, file_id=file_id)
    return build_tender_region_slices(document_index, max_blocks_per_slice=max_blocks_per_slice)


def build_tender_region_slices(
    document_index: TenderDocumentIndexResult,
    *,
    max_blocks_per_slice: int | None = None,
) -> TenderRegionSliceIndexResult:
    warnings: list[str] = []
    slices: list[TenderRegionSlice] = []
    sections_by_key = {section.section_key: section for section in document_index.detected_sections}
    boundary_blocks = _boundary_blocks(document_index.detected_sections)

    for region_key in CORE_REGION_ORDER:
        section = sections_by_key.get(region_key)
        if section is None or not section.found:
            warnings.append(f"Core region not found: {region_key}")
            continue
        start_block = _section_start_block(section)
        if start_block is None:
            warnings.append(f"Core region has no source block: {region_key}")
            continue
        end_block = _slice_end_block(region_key, start_block, document_index.blocks, sections_by_key, boundary_blocks)
        blocks = _blocks_in_range(document_index.blocks, start_block, end_block)
        if max_blocks_per_slice is not None and len(blocks) > max_blocks_per_slice:
            blocks = blocks[:max_blocks_per_slice]
            end_block = blocks[-1].block_index if blocks else start_block
            warnings.append(f"Slice truncated by max_blocks_per_slice: {region_key}")
        slices.append(_make_slice(section, start_block, end_block, blocks))

    return TenderRegionSliceIndexResult(
        schema_version=SCHEMA_VERSION,
        source_path=document_index.source_path,
        file_id=document_index.file_id,
        file_name=document_index.file_name,
        file_type=document_index.file_type,
        slice_count=len(slices),
        slices=slices,
        warnings=warnings,
    )


def write_tender_region_slice_outputs(
    result: TenderRegionSliceIndexResult,
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
    report_target.write_text(render_tender_region_slice_report(result), encoding="utf-8")


def render_tender_region_slice_report(result: TenderRegionSliceIndexResult) -> str:
    lines = [
        "# 招标文件核心抽取区域切片报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 文件类型：{result.file_type}",
        f"- 切片数：{result.slice_count}",
        "",
        "## 切片概览",
        "",
        "| 区域 | 来源类型 | 起止块 | 块数 | 段落数 | 表格数 | 字符数 | 推荐任务 | 是否需复核 |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]

    for region_slice in result.slices:
        start_end = f"B{region_slice.slice_start_block}-B{region_slice.slice_end_block}"
        lines.append(
            f"| {region_slice.region_title} | {region_slice.source_type or ''} | {start_end} | "
            f"{region_slice.block_count} | {region_slice.paragraph_count} | {region_slice.table_count} | "
            f"{region_slice.text_char_count} | {', '.join(region_slice.recommended_llm_tasks)} | "
            f"{'是' if region_slice.review_required else '否'} |"
        )

    lines.extend(["", "## 切片明细", ""])
    for region_slice in result.slices:
        lines.extend(
            [
                f"### {region_slice.region_title}",
                "",
                f"- 区域键：`{region_slice.region_key}`",
                f"- 主候选：`{region_slice.primary_candidate_id or ''}`",
                f"- 来源位置：{_render_source_refs(region_slice.source_refs)}",
                f"- 起止块：B{region_slice.slice_start_block}-B{region_slice.slice_end_block}",
                f"- 推荐 LLM 任务：{', '.join(region_slice.recommended_llm_tasks)}",
                f"- 补充候选：{', '.join(region_slice.supplemental_candidate_ids) or '无'}",
                f"- 备注：{region_slice.note or '无'}",
                "",
            ]
        )
        for block in region_slice.blocks[:30]:
            location = _block_location(block)
            lines.append(
                f"- B{block.block_index} `{block.block_type}` {location} "
                f"chars={block.char_count}: {block.text_preview[:180]}"
            )
        if len(region_slice.blocks) > 30:
            lines.append(f"- ... 仅展示前 30 个块，完整切片见 JSON。")
        lines.append("")

    if result.warnings:
        lines.extend(["## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def _make_slice(
    section: TenderDetectedSection,
    start_block: int,
    end_block: int,
    blocks: list[TenderDocumentBlock],
) -> TenderRegionSlice:
    paragraph_count = sum(1 for block in blocks if block.block_type == "paragraph")
    table_count = sum(1 for block in blocks if block.block_type == "table")
    text_char_count = sum(block.char_count for block in blocks)
    supplemental_candidate_ids = [
        candidate.candidate_id
        for candidate in section.candidates
        if candidate.candidate_id != section.primary_candidate_id
    ]
    return TenderRegionSlice(
        region_key=section.section_key,
        region_title=section.title,
        region_role=section.region_role,
        primary_candidate_id=section.primary_candidate_id,
        source_type=section.detection_mode,
        source_refs=section.source_refs,
        slice_start_block=start_block,
        slice_end_block=end_block,
        block_count=len(blocks),
        paragraph_count=paragraph_count,
        table_count=table_count,
        text_char_count=text_char_count,
        recommended_llm_tasks=RECOMMENDED_LLM_TASKS.get(section.section_key, []),
        blocks=blocks,
        supplemental_candidate_ids=supplemental_candidate_ids,
        review_required=section.review_required,
        note=section.note,
    )


def _slice_end_block(
    region_key: str,
    start_block: int,
    blocks: list[TenderDocumentBlock],
    sections_by_key: dict[str, TenderDetectedSection],
    boundary_blocks: dict[str, int],
) -> int:
    document_end = blocks[-1].block_index if blocks else start_block
    if region_key == "chapter_1_notice":
        return _end_before_next(start_block, [boundary_blocks.get("chapter_2_bidder_instructions")], document_end)
    if region_key == "bidder_instructions_preface_table":
        chapter_3 = boundary_blocks.get("chapter_3_evaluation")
        evaluation_preface = _section_start_block(sections_by_key.get("evaluation_method_preface_table"))
        bidder_body = _first_likely_bidder_instructions_body_start(start_block, blocks)
        return _end_before_next(start_block, [bidder_body, evaluation_preface, chapter_3], document_end)
    if region_key == "evaluation_method_preface_table":
        technical = _section_start_block(sections_by_key.get(TECHNICAL_SECTION_KEY))
        nearby_end = _first_likely_evaluation_preface_end(start_block, blocks)
        return _end_before_next(start_block, [nearby_end, technical], document_end)
    if region_key == TECHNICAL_SECTION_KEY:
        content_start = _technical_content_start(start_block, blocks)
        return _technical_region_end(content_start, blocks, document_end)
    return document_end


def _technical_region_end(start_block: int, blocks: list[TenderDocumentBlock], document_end: int) -> int:
    later_heading = [
        block.block_index
        for block in blocks
        if block.block_index > start_block and _looks_like_major_chapter_heading(block)
    ]
    return _end_before_next(start_block, later_heading, document_end)


def _technical_content_start(start_block: int, blocks: list[TenderDocumentBlock]) -> int:
    start_positions = {block.block_index: block for block in blocks}
    content_start = start_block
    for block_index in range(start_block + 1, min(start_block + 8, blocks[-1].block_index if blocks else start_block) + 1):
        block = start_positions.get(block_index)
        if block is None:
            continue
        compact = "".join(block.text_preview.split())
        if compact in {"第七章技术标准和要求", "第八章技术标准和要求"}:
            content_start = block.block_index
            continue
        if block.block_type == "table" and ("技术标准和要求" in compact or "第八章" in compact or "第七章" in compact):
            content_start = block.block_index
            continue
        if compact.isdigit():
            content_start = block.block_index
            continue
        break
    return content_start


def _first_likely_bidder_instructions_body_start(
    start_block: int,
    blocks: list[TenderDocumentBlock],
) -> int | None:
    markers = ("投标人须知正文部分", "1.总则", "1．总则", "1. 总则", "1． 总则")
    for block in blocks:
        if block.block_index <= start_block:
            continue
        compact = "".join(block.text_preview.split())
        if len(compact) > 40:
            continue
        if any(compact.startswith(marker.replace(" ", "")) for marker in markers):
            return block.block_index
    return None


def _first_likely_evaluation_preface_end(start_block: int, blocks: list[TenderDocumentBlock]) -> int | None:
    end_markers = ("附件", "1.评标方法", "1．评标方法", "1. 评标方法", "第四章")
    for block in blocks:
        if block.block_index <= start_block:
            continue
        compact = "".join(block.text_preview.split())
        if len(compact) > 80:
            continue
        if any(compact.startswith(marker) for marker in end_markers):
            return block.block_index
    return None


def _looks_like_major_chapter_heading(block: TenderDocumentBlock) -> bool:
    text = "".join(block.text_preview.split())
    if block.block_type != "paragraph":
        return False
    if len(text) > 40:
        return False
    return bool(text.startswith("第") and "章" in text[:8])


def _end_before_next(start_block: int, candidates: list[int | None], document_end: int) -> int:
    later = [candidate for candidate in candidates if candidate is not None and candidate > start_block]
    if not later:
        return document_end
    return min(later) - 1


def _boundary_blocks(sections: list[TenderDetectedSection]) -> dict[str, int]:
    return {
        section.section_key: block_index
        for section in sections
        if section.region_role == "boundary_section"
        for block_index in [_section_start_block(section)]
        if block_index is not None
    }


def _section_start_block(section: TenderDetectedSection | None) -> int | None:
    if section is None or not section.source_refs:
        return None
    return section.source_refs[0].block_index


def _blocks_in_range(
    blocks: list[TenderDocumentBlock],
    start_block: int,
    end_block: int,
) -> list[TenderDocumentBlock]:
    return [block for block in blocks if start_block <= block.block_index <= end_block]


def _block_location(block: TenderDocumentBlock) -> str:
    parts: list[str] = []
    if block.page_no is not None:
        parts.append(f"page={block.page_no}")
    if block.paragraph_index is not None:
        parts.append(f"paragraph={block.paragraph_index}")
    if block.table_index is not None:
        parts.append(f"table={block.table_index}")
    if block.row_count is not None:
        parts.append(f"rows={block.row_count}")
    if block.max_column_count is not None:
        parts.append(f"cols={block.max_column_count}")
    return ", ".join(parts)
