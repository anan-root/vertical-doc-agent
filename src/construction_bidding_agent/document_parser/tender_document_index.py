"""面向 DOCX 和文本型 PDF 招标文件的结构索引。"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .docx_probe import (
    NS,
    _image_rel_ids,
    _iter_body_blocks,
    _node_text,
    _paragraph_style,
    _read_header_footer_texts,
    _read_relationships,
    _read_xml,
)
from .models import (
    TenderDetectedSection,
    TenderDocumentBlock,
    TenderDocumentIndexResult,
    TenderDocumentProfile,
    TenderSectionCandidate,
    TenderSourceRef,
)


SCHEMA_VERSION = "tender_document_index_v0.1"
_DOCX_SUFFIX = ".docx"
_PDF_SUFFIX = ".pdf"
_TEXT_SPACES_RE = re.compile(r"\s+")
TECHNICAL_SECTION_KEY = "technical_standards_and_requirements"

SECTION_PATTERNS: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("chapter_1_notice", "招标公告", "core_region", ("第一章 招标公告",), ("招标公告",)),
    ("chapter_2_bidder_instructions", "第二章 投标人须知", "boundary_section", ("第二章 投标人须知",), ("投标人须知",)),
    (
        "bidder_instructions_preface_table",
        "投标人须知前附表",
        "core_region",
        ("投标人须知前附表",),
        (),
    ),
    ("chapter_3_evaluation", "第三章 评标办法", "boundary_section", ("第三章 评标办法",), ("评标办法",)),
    (
        "evaluation_method_preface_table",
        "评标办法前附表",
        "core_region",
        ("评标办法前附表",),
        (),
    ),
    (
        "technical_standards_and_requirements",
        "技术标准和要求",
        "core_region",
        ("第七章 技术标准和要求", "第七章技术标准和要求", "第八章 技术标准和要求", "第八章技术标准和要求"),
        ("技术标准", "技术要求"),
    ),
)


def build_tender_document_index(path: str | Path, *, file_id: str | None = None) -> TenderDocumentIndexResult:
    source = Path(path)
    resolved_file_id = file_id or _default_file_id(source)
    suffix = source.suffix.lower()

    if not source.exists():
        return _empty_result(
            source,
            resolved_file_id,
            _file_type_from_suffix(suffix),
            warnings=[f"File not found: {source}"],
        )
    if suffix == _DOCX_SUFFIX:
        return _build_docx_index(source, resolved_file_id)
    if suffix == _PDF_SUFFIX:
        return _build_pdf_index(source, resolved_file_id)
    return _empty_result(
        source,
        resolved_file_id,
        _file_type_from_suffix(suffix),
        warnings=[f"Unsupported file type: {suffix or '(none)'}"],
    )


def write_tender_document_index_outputs(
    result: TenderDocumentIndexResult,
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
    report_target.write_text(render_tender_document_index_report(result), encoding="utf-8")


def render_tender_document_index_report(result: TenderDocumentIndexResult) -> str:
    profile = result.document_profile
    lines = [
        "# 招标文件结构索引报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 文件类型：{result.file_type}",
        f"- 段落数：{profile.paragraph_count}",
        f"- 表格数：{profile.table_count}",
        f"- 图片数：{profile.image_count}",
        f"- 页数：{profile.page_count if profile.page_count is not None else '未统计'}",
        f"- 是否疑似扫描件：{_yes_no(profile.has_scanned_pdf)}",
        f"- 是否检测到目录：{_yes_no(profile.toc_detected)}",
        "",
        "## 核心抽取区域",
        "",
        "| 章节/区域 | 是否识别 | 主候选 | 识别方式 | 置信度 | 是否需复核 | 来源位置 | 备注 |",
        "|---|---|---|---|---:|---|---|---|",
    ]

    for section in _sections_by_role(result.detected_sections, "core_region"):
        lines.append(_render_section_row(section))

    boundary_sections = _sections_by_role(result.detected_sections, "boundary_section")
    if boundary_sections:
        lines.extend(
            [
                "",
                "## 边界章节",
                "",
                "| 章节 | 是否识别 | 主候选 | 识别方式 | 置信度 | 来源位置 | 备注 |",
                "|---|---|---|---|---:|---|---|",
            ]
        )
        for section in boundary_sections:
            source = _render_source_refs(section.source_refs)
            lines.append(
                f"| {section.title} | {_yes_no(section.found)} | {section.matched_text or ''} | "
                f"{section.detection_mode or ''} | {section.confidence:.2f} | {source} | {section.note} |"
            )

    technical_section = _find_section(result.detected_sections, TECHNICAL_SECTION_KEY)
    if technical_section and technical_section.candidates:
        lines.extend(
            [
                "",
                "## 技术标准和要求候选区域",
                "",
                "| 候选ID | 识别标题/内容 | 来源类型 | 置信度 | 是否需复核 | 证据词 | 来源位置 | 备注 |",
                "|---|---|---|---:|---|---|---|---|",
            ]
        )
        for candidate in technical_section.candidates[:30]:
            lines.append(
                f"| {candidate.candidate_id} | {candidate.detected_title} | {candidate.source_type} | "
                f"{candidate.confidence:.2f} | {_yes_no(candidate.review_required)} | "
                f"{', '.join(candidate.evidence_terms)} | {_render_source_refs(candidate.source_refs)} | "
                f"{candidate.note} |"
            )

    lines.extend(["", "## 文档块预览", ""])
    for block in result.blocks[:160]:
        location = _render_block_location(block)
        lines.append(
            f"- B{block.block_index} `{block.block_type}` {location} "
            f"chars={block.char_count}: {block.text_preview[:160]}"
        )
    if len(result.blocks) > 160:
        lines.append("")
        lines.append(f"... 仅展示前 160 个文档块，完整索引见 JSON。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def detect_key_sections(
    blocks: list[TenderDocumentBlock],
    *,
    file_id: str,
    file_name: str,
    file_type: str,
) -> list[TenderDetectedSection]:
    detected: list[TenderDetectedSection] = []
    for section_key, title, region_role, strong_patterns, fallback_patterns in SECTION_PATTERNS:
        if section_key == TECHNICAL_SECTION_KEY:
            detected.append(
                _detect_technical_standards_region(
                    blocks,
                    file_id=file_id,
                    file_name=file_name,
                    file_type=file_type,
                )
            )
            continue
        patterns = strong_patterns + fallback_patterns
        matches = [
            block
            for block in blocks
            if any(_text_contains(block.text_preview, pattern) for pattern in patterns)
        ]
        if matches:
            strong_matches = [
                block
                for block in matches
                if any(_text_contains(block.text_preview, pattern) for pattern in strong_patterns)
            ]
            fallback_matches = [
                block
                for block in matches
                if block not in strong_matches
                and any(_text_contains(block.text_preview, pattern) for pattern in fallback_patterns)
            ]
            first = _choose_section_match(strong_matches, fallback_matches, patterns)
            candidate = _candidate_from_block(
                candidate_id=f"{section_key}_001",
                block=first,
                file_id=file_id,
                file_name=file_name,
                file_type=file_type,
                source_type="canonical_chapter" if strong_matches else "alias_section",
                evidence_terms=[_best_matched_text(first.text_preview, patterns)],
                confidence=0.92 if strong_matches else 0.72,
                review_required=not bool(strong_matches),
                note=_match_note(matches, strong_matches, first),
            )
            detected.append(
                TenderDetectedSection(
                    section_key=section_key,
                    title=title,
                    found=True,
                    region_role=region_role,
                    primary_candidate_id=candidate.candidate_id,
                    detection_mode=candidate.source_type,
                    confidence=candidate.confidence,
                    review_required=candidate.review_required,
                    candidates=[candidate],
                    source_refs=[_source_ref_from_block(first, file_id, file_name, file_type)],
                    matched_text=_best_matched_text(first.text_preview, patterns),
                    note=candidate.note,
                )
            )
            continue
        detected.append(
            TenderDetectedSection(
                section_key=section_key,
                title=title,
                found=False,
                region_role=region_role,
                detection_mode="not_found",
                review_required=True,
                note="未识别到候选位置。",
            )
        )
    return detected


def _detect_technical_standards_region(
    blocks: list[TenderDocumentBlock],
    *,
    file_id: str,
    file_name: str,
    file_type: str,
) -> TenderDetectedSection:
    candidates: list[TenderSectionCandidate] = []
    seen_blocks: set[int] = set()

    def add_candidate(
        block: TenderDocumentBlock,
        *,
        source_type: str,
        evidence_terms: list[str],
        confidence: float,
        review_required: bool,
        note: str = "",
    ) -> None:
        if block.block_index in seen_blocks:
            return
        seen_blocks.add(block.block_index)
        candidates.append(
            _candidate_from_block(
                candidate_id=f"TSR{len(candidates) + 1:03d}",
                block=block,
                file_id=file_id,
                file_name=file_name,
                file_type=file_type,
                source_type=source_type,
                evidence_terms=evidence_terms,
                confidence=confidence,
                review_required=review_required,
                note=note,
            )
        )

    canonical_terms = [
        "第七章技术标准和要求",
        "第八章技术标准和要求",
        "第五章技术标准和要求",
        "第六章技术标准和要求",
    ]
    owner_terms = ["发包人要求", "发包方要求"]
    alias_terms = ["技术要求", "技术规范", "工程规范和技术说明", "工程建设标准", "材料设备技术要求", "技术标准"]
    embedded_terms = ["质量标准", "安全文明施工要求", "绿色施工", "验收标准", "材料要求", "施工质量验收规范"]
    content_terms = ["国家现行规范", "强制性标准", "施工工艺", "材料、设备", "质量验收", "安全文明施工"]

    for block in blocks:
        compact = _compact_text(block.text_preview)
        if any(term in compact for term in canonical_terms) and _looks_like_body_section_match(block, tuple(canonical_terms)):
            add_candidate(
                block,
                source_type="canonical_chapter",
                evidence_terms=[term for term in canonical_terms if term in compact],
                confidence=0.95,
                review_required=False,
            )

    for block in blocks:
        compact = _compact_text(block.text_preview)
        if any(_compact_text(term) in compact for term in owner_terms) and _looks_like_region_heading(block, owner_terms):
            add_candidate(
                block,
                source_type="owner_requirement",
                evidence_terms=[term for term in owner_terms if _compact_text(term) in compact],
                confidence=0.86,
                review_required=False,
            )

    for block in blocks:
        compact = _compact_text(block.text_preview)
        if any(_compact_text(term) in compact for term in alias_terms) and _looks_like_region_heading(block, alias_terms):
            add_candidate(
                block,
                source_type="alias_section",
                evidence_terms=[term for term in alias_terms if _compact_text(term) in compact],
                confidence=0.78,
                review_required=True,
            )

    for block in blocks:
        compact = _compact_text(block.text_preview)
        terms = [term for term in embedded_terms if _compact_text(term) in compact]
        if terms:
            add_candidate(
                block,
                source_type="embedded_subsection",
                evidence_terms=terms,
                confidence=0.64,
                review_required=True,
                note="技术要求疑似嵌入其他章节，需人工确认范围。",
            )

    for block in blocks:
        compact = _compact_text(block.text_preview)
        terms = [term for term in content_terms if _compact_text(term) in compact]
        if len(terms) >= 2:
            add_candidate(
                block,
                source_type="content_cluster",
                evidence_terms=terms,
                confidence=0.52,
                review_required=True,
                note="未见明确标题，按技术要求关键词密度识别。",
            )

    candidates.sort(key=lambda candidate: _candidate_rank(candidate), reverse=True)
    if not candidates:
        return TenderDetectedSection(
            section_key=TECHNICAL_SECTION_KEY,
            title="技术标准和要求",
            found=False,
            region_role="core_region",
            detection_mode="not_found",
            review_required=True,
            note="未识别到技术标准和要求候选区域。",
        )

    primary = candidates[0]
    return TenderDetectedSection(
        section_key=TECHNICAL_SECTION_KEY,
        title="技术标准和要求",
        found=True,
        region_role="core_region",
        primary_candidate_id=primary.candidate_id,
        detection_mode=primary.source_type,
        confidence=primary.confidence,
        review_required=primary.review_required or len(candidates) > 1 and primary.source_type != "canonical_chapter",
        candidates=candidates,
        source_refs=primary.source_refs,
        matched_text=primary.detected_title,
        note=f"发现 {len(candidates)} 个候选区域，主候选为 {primary.candidate_id}。",
    )


def _build_docx_index(source: Path, file_id: str) -> TenderDocumentIndexResult:
    warnings: list[str] = []
    blocks: list[TenderDocumentBlock] = []
    paragraph_count = 0
    table_count = 0
    image_count = 0
    toc_detected = False

    try:
        with zipfile.ZipFile(source) as package:
            rels = _read_relationships(package)
            document_root = _read_xml(package, "word/document.xml")
            if document_root is None:
                return _empty_result(source, file_id, "docx", warnings=["Missing word/document.xml"])
            body = document_root.find("w:body", NS)
            if body is None:
                return _empty_result(source, file_id, "docx", warnings=["Missing w:body"])

            header_footer_texts = _read_header_footer_texts(package)
            for block_node in _iter_body_blocks(body):
                if _is_paragraph(block_node):
                    text = _normalize_text(_node_text(block_node))
                    if not text:
                        continue
                    style = _paragraph_style(block_node)
                    if style and style.upper().startswith("TOC"):
                        toc_detected = True
                        paragraph_count += 1
                        continue
                    image_refs = _image_rel_ids(block_node)
                    image_count += len(image_refs)
                    blocks.append(
                        TenderDocumentBlock(
                            block_index=len(blocks),
                            block_type="paragraph",
                            text_preview=text[:300],
                            char_count=len(text),
                            text_content=text,
                            paragraph_index=paragraph_count,
                            style=style,
                        )
                    )
                    paragraph_count += 1
                    continue

                if not _is_table(block_node):
                    continue
                table_block, table_image_count = _summarize_docx_table(
                    block_node,
                    block_index=len(blocks),
                    table_index=table_count,
                )
                blocks.append(table_block)
                image_count += table_image_count
                table_count += 1

            if header_footer_texts:
                warnings.append(f"Ignored {len(header_footer_texts)} header/footer text part(s).")
            if rels and image_count < len(_image_rel_ids(document_root)):
                warnings.append("Some image references are outside indexed paragraphs/tables.")
    except zipfile.BadZipFile:
        return _empty_result(source, file_id, "docx", warnings=["Invalid DOCX package."])

    profile = TenderDocumentProfile(
        file_count=1,
        has_word=True,
        has_pdf=False,
        has_scanned_pdf=False,
        paragraph_count=paragraph_count,
        table_count=table_count,
        image_count=image_count,
        page_count=None,
        header_footer_ignored=True,
        toc_detected=toc_detected,
    )
    return TenderDocumentIndexResult(
        schema_version=SCHEMA_VERSION,
        source_path=str(source),
        file_id=file_id,
        file_name=source.name,
        file_type="docx",
        document_profile=profile,
        detected_sections=detect_key_sections(blocks, file_id=file_id, file_name=source.name, file_type="docx"),
        blocks=blocks,
        warnings=warnings,
    )


def _build_pdf_index(source: Path, file_id: str) -> TenderDocumentIndexResult:
    warnings: list[str] = []
    try:
        import pdfplumber
    except ModuleNotFoundError:
        return _empty_result(source, file_id, "pdf", warnings=["pdfplumber is not installed."])

    blocks: list[TenderDocumentBlock] = []
    paragraph_count = 0
    table_count = 0
    page_count = 0
    nonempty_pages = 0

    with pdfplumber.open(str(source)) as pdf:
        page_count = len(pdf.pages)
        for page_index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            normalized_page_text = _normalize_pdf_text(page_text)
            if normalized_page_text:
                nonempty_pages += 1
                for paragraph in _split_pdf_paragraphs(normalized_page_text):
                    blocks.append(
                        TenderDocumentBlock(
                            block_index=len(blocks),
                            block_type="paragraph",
                            text_preview=paragraph[:300],
                            char_count=len(paragraph),
                            text_content=paragraph,
                            page_no=page_index,
                            paragraph_index=paragraph_count,
                        )
                    )
                    paragraph_count += 1
            try:
                tables = page.extract_tables() or []
            except Exception as exc:  # pragma: no cover - depends on PDF internals
                warnings.append(f"Page {page_index}: table extraction failed: {exc}")
                tables = []
            for table in tables:
                table_text, row_count, max_column_count = _summarize_pdf_table_text(table)
                if not table_text:
                    continue
                blocks.append(
                    TenderDocumentBlock(
                        block_index=len(blocks),
                        block_type="table",
                        text_preview=_preview_text(table_text),
                        char_count=len(table_text),
                        text_content=table_text,
                        page_no=page_index,
                        table_index=table_count,
                        row_count=row_count,
                        max_column_count=max_column_count,
                    )
                )
                table_count += 1

    has_scanned_pdf = page_count > 0 and nonempty_pages == 0
    if has_scanned_pdf:
        warnings.append("No extractable text found; PDF may be scanned.")

    profile = TenderDocumentProfile(
        file_count=1,
        has_word=False,
        has_pdf=True,
        has_scanned_pdf=has_scanned_pdf,
        paragraph_count=paragraph_count,
        table_count=table_count,
        image_count=0,
        page_count=page_count,
        header_footer_ignored=False,
        toc_detected=any("目录" in block.text_preview[:20] for block in blocks[:20]),
    )
    return TenderDocumentIndexResult(
        schema_version=SCHEMA_VERSION,
        source_path=str(source),
        file_id=file_id,
        file_name=source.name,
        file_type="pdf",
        document_profile=profile,
        detected_sections=detect_key_sections(blocks, file_id=file_id, file_name=source.name, file_type="pdf"),
        blocks=blocks,
        warnings=warnings,
    )


def _summarize_docx_table(
    table_node: ET.Element,
    *,
    block_index: int,
    table_index: int,
    preview_rows: int = 3,
) -> tuple[TenderDocumentBlock, int]:
    preview_rows_text: list[str] = []
    all_rows_text: list[str] = []
    row_count = 0
    max_column_count = 0
    image_count = 0
    for row_index, row_node in enumerate(table_node.findall("w:tr", NS)):
        row_count += 1
        cell_nodes = row_node.findall("w:tc", NS)
        max_column_count = max(max_column_count, len(cell_nodes))
        cell_texts: list[str] = []
        for cell_node in cell_nodes:
            image_count += len(_image_rel_ids(cell_node))
            cell_texts.append(_normalize_text(_node_text(cell_node)))
        row_text = " | ".join(text for text in cell_texts if text)
        if row_text:
            all_rows_text.append(row_text)
        if row_index < preview_rows:
            preview_rows_text.append(row_text)
    preview_text = " / ".join(row for row in preview_rows_text if row)
    text = "\n".join(all_rows_text)
    return (
        TenderDocumentBlock(
            block_index=block_index,
            block_type="table",
            text_preview=_preview_text(preview_text or text),
            char_count=len(text),
            text_content=text,
            table_index=table_index,
            row_count=row_count,
            max_column_count=max_column_count,
        ),
        image_count,
    )


def _summarize_pdf_table_text(table) -> tuple[str, int, int]:
    rows: list[str] = []
    row_count = 0
    max_column_count = 0
    for row in table:
        row_count += 1
        cells = [_normalize_text(str(cell or "")) for cell in row]
        max_column_count = max(max_column_count, len(cells))
        row_text = " | ".join(cell for cell in cells if cell)
        if row_text:
            rows.append(row_text)
    return "\n".join(rows), row_count, max_column_count


def _source_ref_from_block(
    block: TenderDocumentBlock,
    file_id: str,
    file_name: str,
    file_type: str,
) -> TenderSourceRef:
    return TenderSourceRef(
        file_id=file_id,
        file_name=file_name,
        file_type=file_type,
        block_index=block.block_index,
        page_no=block.page_no,
        paragraph_index=block.paragraph_index,
        table_index=block.table_index,
        text_excerpt=block.text_preview[:120],
    )


def _candidate_from_block(
    *,
    candidate_id: str,
    block: TenderDocumentBlock,
    file_id: str,
    file_name: str,
    file_type: str,
    source_type: str,
    evidence_terms: list[str],
    confidence: float,
    review_required: bool,
    note: str = "",
) -> TenderSectionCandidate:
    return TenderSectionCandidate(
        candidate_id=candidate_id,
        detected_title=block.text_preview[:80],
        source_type=source_type,
        source_refs=[_source_ref_from_block(block, file_id, file_name, file_type)],
        evidence_terms=[term for term in evidence_terms if term],
        confidence=confidence,
        review_required=review_required,
        note=note,
    )


def _empty_result(
    source: Path,
    file_id: str,
    file_type: str,
    *,
    warnings: list[str],
) -> TenderDocumentIndexResult:
    return TenderDocumentIndexResult(
        schema_version=SCHEMA_VERSION,
        source_path=str(source),
        file_id=file_id,
        file_name=source.name,
        file_type=file_type,
        document_profile=TenderDocumentProfile(
            file_count=1,
            has_word=file_type == "docx",
            has_pdf=file_type == "pdf",
            has_scanned_pdf=False,
            paragraph_count=0,
            table_count=0,
            image_count=0,
        ),
        detected_sections=detect_key_sections([], file_id=file_id, file_name=source.name, file_type=file_type),
        warnings=warnings,
    )


def _best_matched_text(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        if _text_contains(text, pattern):
            return pattern
    return ""


def _choose_section_match(
    strong_matches: list[TenderDocumentBlock],
    fallback_matches: list[TenderDocumentBlock],
    patterns: tuple[str, ...],
) -> TenderDocumentBlock:
    if strong_matches:
        body_like = [block for block in strong_matches if _looks_like_body_section_match(block, patterns)]
        return body_like[0] if body_like else strong_matches[0]
    body_like = [block for block in fallback_matches if _looks_like_body_section_match(block, patterns)]
    return body_like[0] if body_like else fallback_matches[0]


def _looks_like_body_section_match(block: TenderDocumentBlock, patterns: tuple[str, ...]) -> bool:
    text = _compact_text(block.text_preview)
    if block.block_type == "table":
        return False
    if len(text) > 40:
        return False
    if re.search(r"\d$", text):
        return False
    match_positions = [text.find(_compact_text(pattern)) for pattern in patterns]
    match_positions = [position for position in match_positions if position >= 0]
    return bool(match_positions and min(match_positions) <= 3)


def _looks_like_region_heading(block: TenderDocumentBlock, terms: list[str]) -> bool:
    text = _compact_text(block.text_preview)
    if block.block_type == "table":
        return False
    if len(text) > 60:
        return False
    if re.search(r"[。；;，,]$", text):
        return False
    positions = [text.find(_compact_text(term)) for term in terms]
    positions = [position for position in positions if position >= 0]
    return bool(positions and min(positions) <= 8)


def _candidate_rank(candidate: TenderSectionCandidate) -> tuple[int, float]:
    type_rank = {
        "canonical_chapter": 6,
        "owner_requirement": 5,
        "alias_section": 4,
        "embedded_subsection": 3,
        "content_cluster": 2,
        "reference_only": 1,
    }
    return type_rank.get(candidate.source_type, 0), candidate.confidence


def _find_section(
    sections: list[TenderDetectedSection],
    section_key: str,
) -> TenderDetectedSection | None:
    for section in sections:
        if section.section_key == section_key:
            return section
    return None


def _match_note(
    matches: list[TenderDocumentBlock],
    strong_matches: list[TenderDocumentBlock],
    selected: TenderDocumentBlock,
) -> str:
    if len(matches) == 1:
        return ""
    if strong_matches:
        return f"发现 {len(matches)} 处候选，优先取正文标题式强匹配 B{selected.block_index}。"
    return f"发现 {len(matches)} 处候选，未发现强匹配，取疑似正文标题 B{selected.block_index}。"


def _text_contains(text: str, pattern: str) -> bool:
    return _compact_text(pattern) in _compact_text(text)


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _normalize_text(text: str) -> str:
    return _TEXT_SPACES_RE.sub(" ", text).strip()


def _preview_text(text: str, limit: int = 300) -> str:
    return text.replace("\n", " / ")[:limit]


def _normalize_pdf_text(text: str) -> str:
    lines = [_normalize_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _split_pdf_paragraphs(text: str) -> list[str]:
    return [line for line in (_normalize_text(line) for line in text.splitlines()) if line]


def _render_source_refs(source_refs: list[TenderSourceRef]) -> str:
    if not source_refs:
        return "未识别"
    return "; ".join(_render_source_ref(source_ref) for source_ref in source_refs)


def _render_section_row(section: TenderDetectedSection) -> str:
    source = _render_source_refs(section.source_refs)
    return (
        f"| {section.title} | {_yes_no(section.found)} | {section.matched_text or ''} | "
        f"{section.detection_mode or ''} | {section.confidence:.2f} | "
        f"{_yes_no(section.review_required)} | {source} | {section.note} |"
    )


def _sections_by_role(
    sections: list[TenderDetectedSection],
    region_role: str,
) -> list[TenderDetectedSection]:
    return [section for section in sections if section.region_role == region_role]


def _render_source_ref(source_ref: TenderSourceRef) -> str:
    parts = [source_ref.file_name]
    if source_ref.page_no is not None:
        parts.append(f"第{source_ref.page_no}页")
    if source_ref.paragraph_index is not None:
        parts.append(f"P{source_ref.paragraph_index}")
    if source_ref.table_index is not None:
        parts.append(f"T{source_ref.table_index}")
    if source_ref.row_index is not None:
        parts.append(f"R{source_ref.row_index}")
    if source_ref.cell_index is not None:
        parts.append(f"C{source_ref.cell_index}")
    return " / ".join(parts)


def _render_block_location(block: TenderDocumentBlock) -> str:
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
    if block.style:
        parts.append(f"style={block.style}")
    return ", ".join(parts)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _default_file_id(source: Path) -> str:
    stem = re.sub(r"\W+", "_", source.stem, flags=re.UNICODE).strip("_")
    return f"file_{stem or 'unknown'}"


def _file_type_from_suffix(suffix: str) -> str:
    if suffix == _DOCX_SUFFIX:
        return "docx"
    if suffix == _PDF_SUFFIX:
        return "pdf"
    return "unknown"


def _is_paragraph(node: ET.Element) -> bool:
    return node.tag.endswith("}p")


def _is_table(node: ET.Element) -> bool:
    return node.tag.endswith("}tbl")
