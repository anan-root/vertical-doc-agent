"""阶段 0 文档探测的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ParagraphProbe:
    index: int
    text: str
    style: str | None = None
    in_header_footer: bool = False


@dataclass(slots=True)
class TableCellProbe:
    row_index: int
    cell_index: int
    text: str
    image_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableRowProbe:
    row_index: int
    cells: list[TableCellProbe] = field(default_factory=list)


@dataclass(slots=True)
class TableProbe:
    index: int
    rows: list[TableRowProbe] = field(default_factory=list)
    image_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ImageProbe:
    rel_id: str
    target: str
    part_name: str | None = None
    context: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None


@dataclass(slots=True)
class DocxProbeResult:
    source_path: str
    paragraphs: list[ParagraphProbe] = field(default_factory=list)
    tables: list[TableProbe] = field(default_factory=list)
    images: list[ImageProbe] = field(default_factory=list)
    header_footer_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def image_count(self) -> int:
        return len(self.images)


@dataclass(slots=True)
class TableIndexCellPreview:
    cell_index: int
    text_preview: str
    image_count: int = 0


@dataclass(slots=True)
class TableIndexRowPreview:
    row_index: int
    cells: list[TableIndexCellPreview] = field(default_factory=list)


@dataclass(slots=True)
class TableImageBinding:
    rel_id: str
    target: str
    part_name: str | None
    table_index: int
    row_index: int
    cell_index: int
    cell_text: str = ""
    row_text: str = ""
    header_text: str = ""
    previous_row_text: str = ""
    previous_row_texts: list[str] = field(default_factory=list)
    next_row_text: str = ""
    previous_non_empty_cell_text: str = ""
    next_non_empty_cell_text: str = ""
    left_cell_text: str = ""
    right_cell_text: str = ""
    above_cell_text: str = ""
    below_cell_text: str = ""
    nearby_text: str = ""
    caption_candidates: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableIndexTable:
    table_index: int
    row_count: int
    max_column_count: int
    image_count: int
    header_preview: list[str] = field(default_factory=list)
    row_previews: list[TableIndexRowPreview] = field(default_factory=list)


@dataclass(slots=True)
class DocxTableIndexResult:
    source_path: str
    table_count: int
    document_image_ref_count: int
    table_image_ref_count: int
    header_footer_text_count: int
    tables: list[TableIndexTable] = field(default_factory=list)
    image_bindings: list[TableImageBinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SectionHeading:
    heading_index: int
    paragraph_index: int
    block_index: int
    level: int
    text: str
    style: str | None = None
    number: str | None = None


@dataclass(slots=True)
class SectionTableRecord:
    table_index: int
    block_index: int
    section_path: list[str]
    section_level: int | None
    nearest_heading_index: int | None
    nearest_heading_text: str | None
    row_count: int
    max_column_count: int
    image_count: int
    header_preview: list[str] = field(default_factory=list)
    row_previews: list[TableIndexRowPreview] = field(default_factory=list)


@dataclass(slots=True)
class SectionTableSummary:
    section_path: list[str]
    level: int
    table_count: int = 0
    image_count: int = 0
    first_table_index: int | None = None
    last_table_index: int | None = None


@dataclass(slots=True)
class DocxSectionTableIndexResult:
    source_path: str
    heading_count: int
    table_count: int
    unassigned_table_count: int
    document_image_ref_count: int
    table_image_ref_count: int
    header_footer_text_count: int
    headings: list[SectionHeading] = field(default_factory=list)
    sections: list[SectionTableSummary] = field(default_factory=list)
    tables: list[SectionTableRecord] = field(default_factory=list)
    image_bindings: list[TableImageBinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SectionParagraphRecord:
    paragraph_index: int | None
    block_index: int
    style: str | None
    char_count: int
    text_preview: str
    image_count: int = 0


@dataclass(slots=True)
class SectionImageBinding:
    rel_id: str
    target: str
    part_name: str | None
    context: str
    block_index: int
    section_path: list[str]
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    cell_text: str = ""
    row_text: str = ""
    header_text: str = ""
    previous_row_text: str = ""
    previous_row_texts: list[str] = field(default_factory=list)
    next_row_text: str = ""
    previous_non_empty_cell_text: str = ""
    next_non_empty_cell_text: str = ""
    left_cell_text: str = ""
    right_cell_text: str = ""
    above_cell_text: str = ""
    below_cell_text: str = ""
    nearby_text: str = ""
    caption_candidates: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SectionMaterialSlice:
    slice_id: str
    heading_index: int | None
    level: int | None
    section_path: list[str]
    start_block_index: int | None = None
    end_block_index: int | None = None
    paragraph_count: int = 0
    paragraph_char_count: int = 0
    table_count: int = 0
    image_count: int = 0
    subtree_paragraph_count: int = 0
    subtree_table_count: int = 0
    subtree_image_count: int = 0
    descendant_slice_count: int = 0
    paragraphs: list[SectionParagraphRecord] = field(default_factory=list)
    tables: list[SectionTableRecord] = field(default_factory=list)
    image_bindings: list[SectionImageBinding] = field(default_factory=list)


@dataclass(slots=True)
class DocxSectionMaterialIndexResult:
    source_path: str
    heading_count: int
    slice_count: int
    material_paragraph_count: int
    material_paragraph_char_count: int
    table_count: int
    document_image_ref_count: int
    table_image_ref_count: int
    paragraph_image_ref_count: int
    header_footer_text_count: int
    headings: list[SectionHeading] = field(default_factory=list)
    slices: list[SectionMaterialSlice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PdfBookmarkProbeItem:
    bookmark_index: int
    level: int
    title: str
    clean_title: str
    number: str | None
    page_no: int | None
    start_page: int | None
    end_page: int | None
    parent_index: int | None
    path: list[str] = field(default_factory=list)
    child_count: int = 0
    destination_objid: int | None = None


@dataclass(slots=True)
class PdfHeaderFooterCandidate:
    text: str
    occurrence_count: int
    sample_pages: list[int] = field(default_factory=list)
    position: str = "unknown"


@dataclass(slots=True)
class PdfBookmarkProbeResult:
    source_path: str
    page_count: int
    bookmark_count: int
    max_bookmark_level: int
    mapped_bookmark_count: int
    unmapped_bookmark_count: int
    text_page_count: int
    scanned_like: bool
    bookmarks: list[PdfBookmarkProbeItem] = field(default_factory=list)
    level_counts: dict[int, int] = field(default_factory=dict)
    header_footer_candidates: list[PdfHeaderFooterCandidate] = field(default_factory=list)
    page_text_samples: list[SectionParagraphRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PdfTableLikeRecord:
    table_id: str
    table_index: int
    page_no: int
    row_count: int
    max_column_count: int
    text_char_count: int = 0
    header_preview: list[str] = field(default_factory=list)
    row_previews: list[TableIndexRowPreview] = field(default_factory=list)


@dataclass(slots=True)
class PdfImageBinding:
    image_id: str
    page_no: int
    image_index: int
    x0: float | None = None
    top: float | None = None
    width: float | None = None
    height: float | None = None
    src_width: int | None = None
    src_height: int | None = None
    reuse_level: str = "review_required"
    risk_level: str = "medium"
    notes: str = ""


@dataclass(slots=True)
class PdfPageMaterialSummary:
    page_no: int
    paragraph_count: int
    text_char_count: int
    table_like_count: int
    image_count: int
    text_preview: str = ""
    tables: list[PdfTableLikeRecord] = field(default_factory=list)
    image_bindings: list[PdfImageBinding] = field(default_factory=list)


@dataclass(slots=True)
class PdfBookmarkMaterialSlice:
    slice_id: str
    bookmark_index: int
    level: int
    title: str
    clean_title: str
    number: str | None
    section_path: list[str]
    start_page: int | None
    end_page: int | None
    page_count: int
    paragraph_count: int = 0
    paragraph_char_count: int = 0
    table_like_count: int = 0
    image_count: int = 0
    child_count: int = 0
    descendant_slice_count: int = 0
    reuse_level: str = "rewrite_reuse"
    project_specific_risk: str = "medium"
    confidence: float = 0.9
    paragraphs: list[SectionParagraphRecord] = field(default_factory=list)
    tables: list[PdfTableLikeRecord] = field(default_factory=list)
    image_bindings: list[PdfImageBinding] = field(default_factory=list)


@dataclass(slots=True)
class PdfBookmarkMaterialIndexResult:
    source_path: str
    page_count: int
    bookmark_count: int
    slice_count: int
    text_page_count: int
    material_paragraph_count: int
    material_paragraph_char_count: int
    table_like_count: int
    image_count: int
    header_footer_ignored: bool = True
    boundary_precision: str = "page_level"
    bookmark_level_counts: dict[int, int] = field(default_factory=dict)
    slices: list[PdfBookmarkMaterialSlice] = field(default_factory=list)
    page_summaries: list[PdfPageMaterialSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FusionMatchInfo:
    status: str
    method: str | None
    score: float
    pdf_slice_id: str
    docx_slice_id: str | None = None
    note: str = ""
    candidate_count: int = 0


@dataclass(slots=True)
class ExcellentBidFusionSlice:
    fusion_slice_id: str
    pdf_slice_id: str
    docx_slice_id: str | None
    match: FusionMatchInfo
    level: int
    title: str
    clean_title: str
    number: str | None
    section_path: list[str]
    start_page: int | None
    end_page: int | None
    page_count: int
    paragraph_count: int
    paragraph_char_count: int
    pdf_table_like_count: int
    pdf_image_count: int
    docx_table_count: int = 0
    docx_image_count: int = 0
    docx_subtree_table_count: int = 0
    docx_subtree_image_count: int = 0
    reuse_level: str = "rewrite_reuse"
    project_specific_risk: str = "medium"
    confidence: float = 0.0
    paragraphs: list[SectionParagraphRecord] = field(default_factory=list)
    tables: list[SectionTableRecord] = field(default_factory=list)
    image_bindings: list[SectionImageBinding] = field(default_factory=list)
    pdf_tables: list[PdfTableLikeRecord] = field(default_factory=list)
    pdf_image_bindings: list[PdfImageBinding] = field(default_factory=list)


@dataclass(slots=True)
class ExcellentBidFusionIndexResult:
    schema_version: str
    source_pdf_path: str
    source_docx_path: str
    pdf_slice_count: int
    docx_slice_count: int
    fusion_slice_count: int
    matched_count: int
    ambiguous_count: int
    fallback_count: int
    unmatched_count: int
    table_count: int
    image_count: int
    pdf_table_like_count: int
    pdf_image_count: int
    boundary_precision: str = "pdf_bookmark_page_level"
    structure_source: str = "pdf_bookmark"
    material_source: str = "docx_when_matched_else_pdf"
    slices: list[ExcellentBidFusionSlice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExcellentBidLibrarySource:
    source_id: str
    source_name: str
    source_type: str
    source_index_path: str
    source_paths: list[str] = field(default_factory=list)
    source_schema_version: str | None = None
    slice_count: int = 0
    table_count: int = 0
    image_count: int = 0
    matched_count: int = 0
    ambiguous_count: int = 0
    fallback_count: int = 0
    unmatched_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExcellentBidMaterialSlice:
    material_slice_id: str
    source_id: str
    source_type: str
    source_slice_id: str
    title: str
    clean_title: str = ""
    number: str | None = None
    level: int | None = None
    section_path: list[str] = field(default_factory=list)
    section_key: str = ""
    search_text: str = ""
    keywords: list[str] = field(default_factory=list)
    primary_material_source: str = "docx"
    material_quality: str = "usable"
    paragraph_count: int = 0
    paragraph_char_count: int = 0
    table_count: int = 0
    image_count: int = 0
    subtree_table_count: int = 0
    subtree_image_count: int = 0
    docx_table_count: int = 0
    docx_image_count: int = 0
    pdf_table_like_count: int = 0
    pdf_image_count: int = 0
    match_status: str | None = None
    match_method: str | None = None
    match_score: float | None = None
    confidence: float = 0.0
    reuse_level: str = "rewrite_reuse"
    project_specific_risk: str = "medium"
    start_page: int | None = None
    end_page: int | None = None
    page_count: int = 0
    start_block_index: int | None = None
    end_block_index: int | None = None
    paragraphs: list[SectionParagraphRecord] = field(default_factory=list)
    tables: list[SectionTableRecord] = field(default_factory=list)
    image_bindings: list[SectionImageBinding] = field(default_factory=list)
    pdf_tables: list[PdfTableLikeRecord] = field(default_factory=list)
    pdf_image_bindings: list[PdfImageBinding] = field(default_factory=list)


@dataclass(slots=True)
class ExcellentBidImageAsset:
    image_asset_id: str
    image_id: str
    source_id: str
    source_type: str
    source_slice_id: str
    material_slice_id: str
    title: str
    section_path: list[str]
    section_key: str
    rel_id: str
    target: str
    part_name: str | None
    context: str
    canonical_image_id: str = ""
    sha256: str = ""
    perceptual_hash: str = ""
    fingerprint_source: str = ""
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    image_group_id: str | None = None
    group_title: str = ""
    group_semantic_text: str = ""
    group_member_index: int | None = None
    group_member_count: int = 0
    must_keep_with_group: bool = False
    caption_actual: str = ""
    caption_candidates: list[str] = field(default_factory=list)
    semantic_sources: list[dict[str, Any]] = field(default_factory=list)
    semantic_text: str = ""
    semantic_confidence: float = 0.0
    nearby_text: str = ""
    cell_text: str = ""
    row_text: str = ""
    header_text: str = ""
    previous_row_text: str = ""
    previous_row_texts: list[str] = field(default_factory=list)
    next_row_text: str = ""
    previous_non_empty_cell_text: str = ""
    next_non_empty_cell_text: str = ""
    left_cell_text: str = ""
    right_cell_text: str = ""
    above_cell_text: str = ""
    below_cell_text: str = ""
    tags: list[str] = field(default_factory=list)
    reuse_level: str = "manual_review"
    project_specific_risk: str = "medium"
    confidence: float = 0.0
    review_required: bool = True
    review_reason: str = ""


@dataclass(slots=True)
class ExcellentBidImageGroup:
    image_group_id: str
    source_id: str
    source_type: str
    source_slice_id: str
    material_slice_id: str
    title: str
    group_title: str
    section_path: list[str]
    section_key: str
    table_index: int | None = None
    start_row_index: int | None = None
    end_row_index: int | None = None
    member_count: int = 0
    image_asset_ids: list[str] = field(default_factory=list)
    image_ids: list[str] = field(default_factory=list)
    canonical_image_ids: list[str] = field(default_factory=list)
    sha256_values: list[str] = field(default_factory=list)
    perceptual_hash_values: list[str] = field(default_factory=list)
    group_canonical_image_key: str = ""
    fingerprint_source: str = ""
    captions: list[str] = field(default_factory=list)
    semantic_sources: list[dict[str, Any]] = field(default_factory=list)
    semantic_text: str = ""
    semantic_confidence: float = 0.0
    nearby_text: str = ""
    tags: list[str] = field(default_factory=list)
    reuse_level: str = "manual_review"
    project_specific_risk: str = "medium"
    confidence: float = 0.0
    review_required: bool = True
    review_reason: str = ""
    detection_method: str = "same_table_contiguous_images"
    must_keep_together: bool = True


@dataclass(slots=True)
class ExcellentBidMaterialSearchHit:
    material_slice_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    slice: ExcellentBidMaterialSlice | None = None


@dataclass(slots=True)
class ExcellentBidMaterialLibraryResult:
    schema_version: str
    library_id: str
    source_count: int
    slice_count: int
    table_count: int
    image_count: int
    docx_table_count: int = 0
    docx_image_count: int = 0
    pdf_fallback_table_count: int = 0
    pdf_fallback_image_count: int = 0
    pdf_reference_table_like_count: int = 0
    pdf_reference_image_count: int = 0
    image_asset_count: int = 0
    image_group_count: int = 0
    sources: list[ExcellentBidLibrarySource] = field(default_factory=list)
    slices: list[ExcellentBidMaterialSlice] = field(default_factory=list)
    image_assets: list[ExcellentBidImageAsset] = field(default_factory=list)
    image_groups: list[ExcellentBidImageGroup] = field(default_factory=list)
    source_type_counts: dict[str, int] = field(default_factory=dict)
    material_quality_counts: dict[str, int] = field(default_factory=dict)
    image_fingerprint_summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TenderSourceRef:
    file_id: str
    file_name: str
    file_type: str
    block_index: int | None = None
    page_no: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    text_excerpt: str = ""


@dataclass(slots=True)
class TenderDocumentBlock:
    block_index: int
    block_type: str
    text_preview: str
    char_count: int
    text_content: str = ""
    page_no: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    row_count: int | None = None
    max_column_count: int | None = None
    style: str | None = None
    row_index_map: list[int] | None = None


@dataclass(slots=True)
class TenderSectionCandidate:
    candidate_id: str
    detected_title: str
    source_type: str
    source_refs: list[TenderSourceRef] = field(default_factory=list)
    evidence_terms: list[str] = field(default_factory=list)
    confidence: float = 0.0
    review_required: bool = False
    note: str = ""


@dataclass(slots=True)
class TenderDetectedSection:
    section_key: str
    title: str
    found: bool
    region_role: str = "core_region"
    primary_candidate_id: str | None = None
    detection_mode: str | None = None
    confidence: float = 0.0
    review_required: bool = False
    candidates: list[TenderSectionCandidate] = field(default_factory=list)
    source_refs: list[TenderSourceRef] = field(default_factory=list)
    matched_text: str | None = None
    note: str = ""


@dataclass(slots=True)
class TenderDocumentProfile:
    file_count: int
    has_word: bool
    has_pdf: bool
    has_scanned_pdf: bool
    paragraph_count: int
    table_count: int
    image_count: int
    page_count: int | None = None
    header_footer_ignored: bool = True
    toc_detected: bool = False


@dataclass(slots=True)
class TenderDocumentIndexResult:
    schema_version: str
    source_path: str
    file_id: str
    file_name: str
    file_type: str
    document_profile: TenderDocumentProfile
    detected_sections: list[TenderDetectedSection] = field(default_factory=list)
    blocks: list[TenderDocumentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TenderRegionSlice:
    region_key: str
    region_title: str
    region_role: str
    primary_candidate_id: str | None
    source_type: str | None
    source_refs: list[TenderSourceRef] = field(default_factory=list)
    slice_start_block: int | None = None
    slice_end_block: int | None = None
    block_count: int = 0
    paragraph_count: int = 0
    table_count: int = 0
    text_char_count: int = 0
    recommended_llm_tasks: list[str] = field(default_factory=list)
    blocks: list[TenderDocumentBlock] = field(default_factory=list)
    supplemental_candidate_ids: list[str] = field(default_factory=list)
    review_required: bool = False
    note: str = ""


@dataclass(slots=True)
class TenderRegionSliceIndexResult:
    schema_version: str
    source_path: str
    file_id: str
    file_name: str
    file_type: str
    slice_count: int
    slices: list[TenderRegionSlice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TenderExtractionInputRegion:
    region_key: str
    region_title: str
    region_role: str
    source_type: str | None
    slice_start_block: int | None = None
    slice_end_block: int | None = None
    block_count: int = 0
    paragraph_count: int = 0
    table_count: int = 0
    text_char_count: int = 0
    source_refs: list[TenderSourceRef] = field(default_factory=list)
    review_required: bool = False
    note: str = ""


@dataclass(slots=True)
class TenderExtractionInputBlockRef:
    block_index: int
    block_type: str
    text_char_count: int
    text_preview: str = ""
    page_no: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    row_count: int | None = None
    max_column_count: int | None = None


@dataclass(slots=True)
class TenderExtractionInputCellRef:
    cell_id: str
    text_raw: str
    block_index: int
    table_index: int | None
    row_index: int
    cell_index: int
    page_no: int | None = None


@dataclass(slots=True)
class TenderExtractionInputPackage:
    task_key: str
    task_title: str
    task_description: str
    input_profile: str
    source_path: str
    file_id: str
    file_name: str
    file_type: str
    region_keys: list[str] = field(default_factory=list)
    regions: list[TenderExtractionInputRegion] = field(default_factory=list)
    source_refs: list[TenderSourceRef] = field(default_factory=list)
    block_refs: list[TenderExtractionInputBlockRef] = field(default_factory=list)
    cell_refs: list[TenderExtractionInputCellRef] = field(default_factory=list)
    block_count: int = 0
    source_text_char_count: int = 0
    included_block_count: int = 0
    dropped_duplicate_block_count: int = 0
    input_unit_count: int = 0
    included_text_char_count: int = 0
    text_char_count: int = 0
    estimated_tokens: int = 0
    input_text: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TenderExtractionInputIndexResult:
    schema_version: str
    source_path: str
    file_id: str
    file_name: str
    file_type: str
    input_profile: str
    package_count: int
    packages: list[TenderExtractionInputPackage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
