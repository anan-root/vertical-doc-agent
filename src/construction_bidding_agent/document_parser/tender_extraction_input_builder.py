"""基于招标文件关键区域切片构建分任务 LLM 抽取输入包。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .models import (
    TenderDocumentBlock,
    TenderExtractionInputBlockRef,
    TenderExtractionInputCellRef,
    TenderExtractionInputIndexResult,
    TenderExtractionInputPackage,
    TenderExtractionInputRegion,
    TenderRegionSlice,
    TenderRegionSliceIndexResult,
    TenderSourceRef,
)
from .tender_document_index import _render_source_refs
from .tender_region_slicer import build_tender_region_slices_from_path


SCHEMA_VERSION = "tender_extraction_inputs_v0.1"
TOKEN_CHAR_RATIO = 1.8
DEFAULT_TOKEN_WARNING_THRESHOLD = 30_000
PDF_DUPLICATE_MIN_CHARS = 8
PDF_DUPLICATE_CHUNK_SIZE = 12
PDF_DUPLICATE_HIT_RATIO = 0.75
INPUT_PROFILES = {"full", "balanced"}
PROJECT_INFO_KEYWORDS = (
    "项目名称",
    "工程名称",
    "建设地点",
    "项目地点",
    "建设规模",
    "工程规模",
    "建筑面积",
    "招标范围",
    "承包范围",
    "计划工期",
    "工期要求",
    "工期",
    "质量要求",
    "质量标准",
    "安全文明",
    "安全生产",
    "文明施工",
    "EPC",
    "工程总承包",
    "设计",
)
TECHNICAL_REQUIREMENT_KEYWORDS = (
    "技术标准",
    "技术要求",
    "发包人要求",
    "施工组织设计",
    "技术标",
    "施工方案",
    "质量",
    "安全",
    "文明施工",
    "绿色施工",
    "扬尘",
    "进度",
    "工期",
    "验收",
    "规范",
    "标准",
    "设计",
    "BIM",
    "创优",
)


@dataclass(frozen=True, slots=True)
class ExtractionTaskSpec:
    task_key: str
    task_title: str
    task_description: str
    region_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractionInputUnit:
    unit_type: str
    blocks: tuple[TenderDocumentBlock, ...]
    text: str


@dataclass(frozen=True, slots=True)
class PreparedRegionInput:
    region_slice: TenderRegionSlice
    units: tuple[ExtractionInputUnit, ...]
    included_blocks: tuple[TenderDocumentBlock, ...]
    dropped_duplicate_block_count: int
    included_text_char_count: int
    cell_refs: tuple[TenderExtractionInputCellRef, ...]


EXTRACTION_TASK_SPECS: tuple[ExtractionTaskSpec, ...] = (
    ExtractionTaskSpec(
        task_key="project_info_extraction_input",
        task_title="项目信息抽取输入包",
        task_description=(
            "用于抽取项目名称、建设地点、建设规模、招标范围、工期要求、质量要求、"
            "安全文明要求和项目类型。"
        ),
        region_keys=("chapter_1_notice", "bidder_instructions_preface_table"),
    ),
    ExtractionTaskSpec(
        task_key="score_points_extraction_input",
        task_title="技术标评分点抽取输入包",
        task_description="用于抽取技术标评分点；一级目录名称必须保留招标文件原文表述。",
        region_keys=("evaluation_method_preface_table",),
    ),
    ExtractionTaskSpec(
        task_key="technical_requirements_extraction_input",
        task_title="技术标准与编制要求抽取输入包",
        task_description=(
            "用于抽取技术标准、发包人要求、技术标编制要求、格式/内容约束，"
            "并提示需要人工关注的投标合规风险。"
        ),
        region_keys=("bidder_instructions_preface_table", "technical_standards_and_requirements"),
    ),
)


def build_tender_extraction_inputs_from_path(
    path: str | Path,
    *,
    file_id: str | None = None,
    max_blocks_per_slice: int | None = None,
    token_warning_threshold: int = DEFAULT_TOKEN_WARNING_THRESHOLD,
    input_profile: str = "full",
) -> TenderExtractionInputIndexResult:
    slice_result = build_tender_region_slices_from_path(
        path,
        file_id=file_id,
        max_blocks_per_slice=max_blocks_per_slice,
    )
    return build_tender_extraction_inputs(
        slice_result,
        token_warning_threshold=token_warning_threshold,
        input_profile=input_profile,
    )


def build_tender_extraction_inputs(
    slice_result: TenderRegionSliceIndexResult,
    *,
    token_warning_threshold: int = DEFAULT_TOKEN_WARNING_THRESHOLD,
    input_profile: str = "full",
) -> TenderExtractionInputIndexResult:
    _validate_input_profile(input_profile)
    packages = [
        _build_package(
            slice_result,
            spec,
            token_warning_threshold=token_warning_threshold,
            input_profile=input_profile,
        )
        for spec in EXTRACTION_TASK_SPECS
    ]
    warnings = list(slice_result.warnings)
    for package in packages:
        warnings.extend(f"{package.task_key}: {warning}" for warning in package.warnings)
    return TenderExtractionInputIndexResult(
        schema_version=SCHEMA_VERSION,
        source_path=slice_result.source_path,
        file_id=slice_result.file_id,
        file_name=slice_result.file_name,
        file_type=slice_result.file_type,
        input_profile=input_profile,
        package_count=len(packages),
        packages=packages,
        warnings=warnings,
    )


def write_tender_extraction_input_outputs(
    result: TenderExtractionInputIndexResult,
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
    report_target.write_text(render_tender_extraction_input_report(result), encoding="utf-8")


def render_tender_extraction_input_report(result: TenderExtractionInputIndexResult) -> str:
    lines = [
        "# 招标文件抽取输入包报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 文件类型：{result.file_type}",
        f"- 输入模式：{result.input_profile}",
        f"- 输入包数量：{result.package_count}",
        "",
        "## 输入包概览",
        "",
        "| 输入包 | 区域 | 原始块数 | 保留块数 | 去重块数 | 输入单元 | 字符数 | 估算 tokens | 警告数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for package in result.packages:
        lines.append(
            f"| {package.task_title} | {', '.join(package.region_keys)} | "
            f"{package.block_count} | {package.included_block_count} | "
            f"{package.dropped_duplicate_block_count} | {package.input_unit_count} | "
            f"{package.text_char_count} | "
            f"{package.estimated_tokens} | {len(package.warnings)} |"
        )

    lines.extend(["", "## 输入包明细", ""])
    for package in result.packages:
        preview = package.input_text[:800].replace("\n", " / ")
        lines.extend(
            [
                f"### {package.task_title}",
                "",
                f"- 任务键：`{package.task_key}`",
                f"- 输入模式：{package.input_profile}",
                f"- 用途：{package.task_description}",
                f"- 区域：{', '.join(package.region_keys)}",
                f"- 原始块数：{package.block_count}",
                f"- 保留块数：{package.included_block_count}",
                f"- 去重块数：{package.dropped_duplicate_block_count}",
                f"- 输入单元数：{package.input_unit_count}",
                f"- 原始区域字符数：{package.source_text_char_count}",
                f"- 保留原文字符数：{package.included_text_char_count}",
                f"- 字符数：{package.text_char_count}",
                f"- 估算 tokens：{package.estimated_tokens}",
                f"- 来源位置：{_render_source_refs(package.source_refs)}",
                "",
                "| 区域 | 块范围 | 块数 | 表格数 | 字符数 | 是否需复核 | 来源类型 |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        )
        for region in package.regions:
            block_range = f"B{region.slice_start_block}-B{region.slice_end_block}"
            lines.append(
                f"| {region.region_title} | {block_range} | {region.block_count} | "
                f"{region.table_count} | {region.text_char_count} | "
                f"{'是' if region.review_required else '否'} | {region.source_type or ''} |"
            )
        lines.extend(["", "#### 输入预览", "", "```text", preview, "```", ""])
        if package.warnings:
            lines.extend(["#### 警告", ""])
            for warning in package.warnings:
                lines.append(f"- {warning}")
            lines.append("")

    if result.warnings:
        lines.extend(["## 汇总警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def _build_package(
    slice_result: TenderRegionSliceIndexResult,
    spec: ExtractionTaskSpec,
    *,
    token_warning_threshold: int,
    input_profile: str,
) -> TenderExtractionInputPackage:
    slices_by_key = {region_slice.region_key: region_slice for region_slice in slice_result.slices}
    selected_slices = [slices_by_key[key] for key in spec.region_keys if key in slices_by_key]
    prepared_regions = [
        _prepare_region_input(
            region_slice,
            file_type=slice_result.file_type,
            include_cell_refs=True,
            task_key=spec.task_key,
            input_profile=input_profile,
        )
        for region_slice in selected_slices
    ]
    warnings = [f"Missing required region: {key}" for key in spec.region_keys if key not in slices_by_key]
    input_text = _render_package_input_text(spec, prepared_regions, slice_result)
    text_char_count = len(input_text)
    estimated_tokens = _estimate_tokens(text_char_count)
    if estimated_tokens > token_warning_threshold:
        warnings.append(
            f"Estimated token count {estimated_tokens} exceeds warning threshold {token_warning_threshold}."
        )
    return TenderExtractionInputPackage(
        task_key=spec.task_key,
        task_title=spec.task_title,
        task_description=spec.task_description,
        input_profile=input_profile,
        source_path=slice_result.source_path,
        file_id=slice_result.file_id,
        file_name=slice_result.file_name,
        file_type=slice_result.file_type,
        region_keys=list(spec.region_keys),
        regions=[_region_summary(region_slice) for region_slice in selected_slices],
        source_refs=_dedupe_source_refs(
            source_ref
            for region_slice in selected_slices
            for source_ref in region_slice.source_refs
        ),
        block_refs=[
            _block_ref(block)
            for prepared_region in prepared_regions
            for block in prepared_region.included_blocks
        ],
        cell_refs=[
            cell_ref
            for prepared_region in prepared_regions
            for cell_ref in prepared_region.cell_refs
        ],
        block_count=sum(region_slice.block_count for region_slice in selected_slices),
        source_text_char_count=sum(region_slice.text_char_count for region_slice in selected_slices),
        included_block_count=sum(len(prepared_region.included_blocks) for prepared_region in prepared_regions),
        dropped_duplicate_block_count=sum(
            prepared_region.dropped_duplicate_block_count
            for prepared_region in prepared_regions
        ),
        input_unit_count=sum(len(prepared_region.units) for prepared_region in prepared_regions),
        included_text_char_count=sum(
            prepared_region.included_text_char_count
            for prepared_region in prepared_regions
        ),
        text_char_count=text_char_count,
        estimated_tokens=estimated_tokens,
        input_text=input_text,
        warnings=warnings,
    )


def _render_package_input_text(
    spec: ExtractionTaskSpec,
    prepared_regions: list[PreparedRegionInput],
    slice_result: TenderRegionSliceIndexResult,
) -> str:
    lines = [
        f"# TASK: {spec.task_title}",
        f"TASK_KEY: {spec.task_key}",
        f"SOURCE_FILE: {slice_result.file_name}",
        f"SOURCE_FILE_ID: {slice_result.file_id}",
        f"SOURCE_FILE_TYPE: {slice_result.file_type}",
        f"PURPOSE: {spec.task_description}",
        "",
        "## EXTRACTION_REGIONS",
    ]
    if not prepared_regions:
        lines.append("(no region content available)")
        return "\n".join(lines).strip()

    for prepared_region in prepared_regions:
        lines.extend(_render_region_text(prepared_region))
    return "\n".join(lines).strip()


def _render_region_text(prepared_region: PreparedRegionInput) -> list[str]:
    region_slice = prepared_region.region_slice
    block_range = f"B{region_slice.slice_start_block}-B{region_slice.slice_end_block}"
    lines = [
        "",
        f"# REGION: {region_slice.region_title} ({region_slice.region_key})",
        f"REGION_ROLE: {region_slice.region_role}",
        f"SOURCE_TYPE: {region_slice.source_type or ''}",
        f"BLOCK_RANGE: {block_range}",
        f"SOURCE_REFS: {_render_source_refs(region_slice.source_refs)}",
        f"INPUT_UNITS: {len(prepared_region.units)}",
        f"CELL_REFS: {len(prepared_region.cell_refs)}",
        f"DROPPED_DUPLICATE_BLOCKS: {prepared_region.dropped_duplicate_block_count}",
        "",
    ]
    if prepared_region.cell_refs:
        lines.extend(
            [
                "## STRUCTURED_TABLE_CELLS",
                "Use these exact cell_id values for cell refs. Do not invent table/cell ids.",
                "",
            ]
        )
        current_block: int | None = None
        current_row: int | None = None
        row_cells: list[str] = []
        for cell_ref in prepared_region.cell_refs:
            if (current_block, current_row) != (cell_ref.block_index, cell_ref.row_index):
                if row_cells:
                    lines.append(" | ".join(row_cells))
                current_block = cell_ref.block_index
                current_row = cell_ref.row_index
                row_cells = [f"[{cell_ref.cell_id}] {cell_ref.text_raw}"]
                continue
            row_cells.append(f"[{cell_ref.cell_id}] {cell_ref.text_raw}")
        if row_cells:
            lines.append(" | ".join(row_cells))
        lines.append("")

    for unit in prepared_region.units:
        location = _unit_location(unit.blocks)
        block_ids = ",".join(f"B{block.block_index}" for block in unit.blocks)
        lines.extend(
            [
                f"[{block_ids} {unit.unit_type} {location}]",
                unit.text,
                "",
            ]
        )
    return lines


def _region_summary(region_slice: TenderRegionSlice) -> TenderExtractionInputRegion:
    return TenderExtractionInputRegion(
        region_key=region_slice.region_key,
        region_title=region_slice.region_title,
        region_role=region_slice.region_role,
        source_type=region_slice.source_type,
        slice_start_block=region_slice.slice_start_block,
        slice_end_block=region_slice.slice_end_block,
        block_count=region_slice.block_count,
        paragraph_count=region_slice.paragraph_count,
        table_count=region_slice.table_count,
        text_char_count=region_slice.text_char_count,
        source_refs=region_slice.source_refs,
        review_required=region_slice.review_required,
        note=region_slice.note,
    )


def _prepare_region_input(
    region_slice: TenderRegionSlice,
    *,
    file_type: str,
    include_cell_refs: bool,
    task_key: str,
    input_profile: str,
) -> PreparedRegionInput:
    included_blocks, dropped_duplicate_block_count = _dedupe_pdf_text_table_overlap(
        region_slice.blocks,
        enabled=file_type == "pdf",
    )
    included_blocks = _apply_input_profile_filter(
        included_blocks,
        task_key=task_key,
        region_key=region_slice.region_key,
        input_profile=input_profile,
    )
    units = _merge_adjacent_blocks(included_blocks)
    return PreparedRegionInput(
        region_slice=region_slice,
        units=tuple(units),
        included_blocks=tuple(included_blocks),
        dropped_duplicate_block_count=dropped_duplicate_block_count,
        included_text_char_count=sum(len(unit.text) for unit in units),
        cell_refs=tuple(_build_cell_refs(included_blocks) if include_cell_refs else []),
    )


def _validate_input_profile(input_profile: str) -> None:
    if input_profile not in INPUT_PROFILES:
        raise ValueError(f"Unsupported input_profile: {input_profile}. Expected one of {sorted(INPUT_PROFILES)}.")


def _apply_input_profile_filter(
    blocks: list[TenderDocumentBlock],
    *,
    task_key: str,
    region_key: str,
    input_profile: str,
) -> list[TenderDocumentBlock]:
    if input_profile == "full" or task_key == "score_points_extraction_input":
        return blocks
    if task_key == "project_info_extraction_input":
        return _filter_blocks_by_keywords(blocks, PROJECT_INFO_KEYWORDS)
    if (
        task_key == "technical_requirements_extraction_input"
        and region_key == "bidder_instructions_preface_table"
    ):
        return _filter_blocks_by_keywords(blocks, TECHNICAL_REQUIREMENT_KEYWORDS)
    return blocks


def _filter_blocks_by_keywords(
    blocks: list[TenderDocumentBlock],
    keywords: tuple[str, ...],
) -> list[TenderDocumentBlock]:
    filtered: list[TenderDocumentBlock] = []
    for block in blocks:
        if block.block_type == "table":
            slim_block = _filter_table_block_rows(block, keywords)
            if slim_block is not None:
                filtered.append(slim_block)
            continue
        if _contains_keyword(_block_text(block), keywords):
            filtered.append(block)
    return filtered or blocks


def _filter_table_block_rows(
    block: TenderDocumentBlock,
    keywords: tuple[str, ...],
) -> TenderDocumentBlock | None:
    selected_rows = [
        (row_index, row)
        for row_index, row in enumerate(_block_text(block).splitlines())
        if _contains_keyword(row, keywords)
    ]
    rows = [row for _row_index, row in selected_rows]
    if not rows:
        return None
    text_content = "\n".join(rows)
    return TenderDocumentBlock(
        block_index=block.block_index,
        block_type=block.block_type,
        text_preview=text_content[:200],
        char_count=len(text_content),
        text_content=text_content,
        page_no=block.page_no,
        paragraph_index=block.paragraph_index,
        table_index=block.table_index,
        row_count=len(rows),
        max_column_count=block.max_column_count,
        style=block.style,
        row_index_map=[row_index for row_index, _row in selected_rows],
    )


def _contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    compact_text = _normalize_keyword_text(text)
    return any(_normalize_keyword_text(keyword) in compact_text for keyword in keywords)


def _normalize_keyword_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _build_cell_refs(blocks: list[TenderDocumentBlock]) -> list[TenderExtractionInputCellRef]:
    cell_refs: list[TenderExtractionInputCellRef] = []
    for block in blocks:
        if block.block_type != "table":
            continue
        row_index_map = block.row_index_map or []
        for local_row_index, row_text in enumerate(_block_text(block).splitlines()):
            row_index = row_index_map[local_row_index] if local_row_index < len(row_index_map) else local_row_index
            cells = [_normalize_cell_text(cell) for cell in row_text.split("|")]
            for cell_index, cell_text in enumerate(cells):
                if not cell_text:
                    continue
                cell_refs.append(
                    TenderExtractionInputCellRef(
                        cell_id=f"B{block.block_index}_R{row_index}_C{cell_index}",
                        text_raw=cell_text,
                        block_index=block.block_index,
                        table_index=block.table_index,
                        row_index=row_index,
                        cell_index=cell_index,
                        page_no=block.page_no,
                    )
                )
    return cell_refs


def _normalize_cell_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _dedupe_pdf_text_table_overlap(
    blocks: list[TenderDocumentBlock],
    *,
    enabled: bool,
) -> tuple[list[TenderDocumentBlock], int]:
    if not enabled:
        return list(blocks), 0

    table_text_by_page: dict[int | None, str] = {}
    for block in blocks:
        if block.block_type != "table":
            continue
        table_text_by_page[block.page_no] = (
            table_text_by_page.get(block.page_no, "") + _compact_text(_block_text(block))
        )

    included: list[TenderDocumentBlock] = []
    dropped_count = 0
    for block in blocks:
        if block.block_type == "paragraph" and _is_duplicate_of_page_table(
            _block_text(block),
            table_text_by_page.get(block.page_no, ""),
        ):
            dropped_count += 1
            continue
        included.append(block)
    return included, dropped_count


def _is_duplicate_of_page_table(text: str, page_table_text: str) -> bool:
    compact = _compact_text(text)
    if len(compact) < PDF_DUPLICATE_MIN_CHARS or not page_table_text:
        return False
    if compact in page_table_text:
        return True
    chunks = _text_chunks(compact, PDF_DUPLICATE_CHUNK_SIZE)
    if not chunks:
        return False
    hit_count = sum(1 for chunk in chunks if chunk in page_table_text)
    return hit_count / len(chunks) >= PDF_DUPLICATE_HIT_RATIO


def _merge_adjacent_blocks(blocks: list[TenderDocumentBlock]) -> list[ExtractionInputUnit]:
    units: list[ExtractionInputUnit] = []
    current: list[TenderDocumentBlock] = []
    current_type: str | None = None
    current_page: int | None = None

    for block in blocks:
        text = _block_text(block)
        if not text:
            continue
        merge_type = block.block_type
        if (
            current
            and current_type == merge_type
            and current_page == block.page_no
            and current[-1].block_index + 1 == block.block_index
        ):
            current.append(block)
            continue
        if current:
            units.append(_input_unit(current))
        current = [block]
        current_type = merge_type
        current_page = block.page_no

    if current:
        units.append(_input_unit(current))
    return units


def _input_unit(blocks: list[TenderDocumentBlock]) -> ExtractionInputUnit:
    unit_type = blocks[0].block_type if len(blocks) == 1 else f"merged_{blocks[0].block_type}"
    return ExtractionInputUnit(
        unit_type=unit_type,
        blocks=tuple(blocks),
        text="\n".join(_block_text(block) for block in blocks if _block_text(block)),
    )


def _block_ref(block: TenderDocumentBlock) -> TenderExtractionInputBlockRef:
    text = _block_text(block)
    return TenderExtractionInputBlockRef(
        block_index=block.block_index,
        block_type=block.block_type,
        text_char_count=len(text),
        text_preview=block.text_preview,
        page_no=block.page_no,
        paragraph_index=block.paragraph_index,
        table_index=block.table_index,
        row_count=block.row_count,
        max_column_count=block.max_column_count,
    )


def _block_text(block: TenderDocumentBlock) -> str:
    return block.text_content or block.text_preview


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


def _unit_location(blocks: tuple[TenderDocumentBlock, ...]) -> str:
    if not blocks:
        return ""
    first = blocks[0]
    last = blocks[-1]
    parts = [_block_location(first)]
    if len(blocks) > 1:
        parts.append(f"end={_block_location(last)}")
    return "; ".join(part for part in parts if part)


def _dedupe_source_refs(source_refs) -> list[TenderSourceRef]:
    deduped: list[TenderSourceRef] = []
    seen: set[tuple] = set()
    for source_ref in source_refs:
        key = (
            source_ref.file_id,
            source_ref.block_index,
            source_ref.page_no,
            source_ref.paragraph_index,
            source_ref.table_index,
            source_ref.row_index,
            source_ref.cell_index,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source_ref)
    return deduped


def _estimate_tokens(text_char_count: int) -> int:
    return math.ceil(text_char_count / TOKEN_CHAR_RATIO) if text_char_count > 0 else 0


def _compact_text(text: str) -> str:
    return re.sub(r"[\s|｜:：,，;；、()（）\[\]【】]+", "", text)


def _text_chunks(text: str, chunk_size: int) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
