"""优秀标书统一素材库构建与检索。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .excellent_bid_fusion_index import _page_range
from .models import (
    ExcellentBidImageAsset,
    ExcellentBidImageGroup,
    ExcellentBidLibrarySource,
    ExcellentBidMaterialLibraryResult,
    ExcellentBidMaterialSearchHit,
    ExcellentBidMaterialSlice,
    SectionImageBinding,
    SectionParagraphRecord,
    SectionTableRecord,
    TableIndexCellPreview,
    TableIndexRowPreview,
)


SCHEMA_VERSION = "excellent_bid_material_library_v1"

REUSE_LEVELS = {"direct_reuse", "rewrite_reuse", "parameterized_reuse", "manual_review"}
PROJECT_SPECIFIC_RISK_LEVELS = {"low", "medium", "high"}
LEGACY_REUSE_LEVEL_MAP = {
    "direct": "direct_reuse",
    "direct_use": "direct_reuse",
    "light_rewrite": "rewrite_reuse",
    "rewrite": "rewrite_reuse",
    "review_required": "manual_review",
}

MANUAL_REVIEW_TERMS = [
    "施工总平面",
    "总平面布置",
    "平面布置图",
    "施工平面图",
    "施工进度网络图",
    "施工进度横道图",
    "计划开竣工日期",
    "计划开、竣工日期",
    "横道图",
    "网络图",
    "项目概况",
    "工程概况",
    "建设单位",
    "发包人",
    "楼栋号",
    "施工段划分图",
]

PROJECT_FACT_IMAGE_TERMS = [
    "踏勘",
    "现场踏勘",
    "现状照片",
    "现状图",
    "场地现状",
    "周边环境",
    "周边道路",
    "周边管线",
    "航拍",
    "实景图",
    "总平面",
    "平面布置",
    "进度网络图",
    "横道图",
    "交通组织",
]

GENERIC_PRACTICE_IMAGE_TERMS = [
    "优秀做法",
    "标准化做法",
    "标准化防护",
    "成品保护",
    "样板",
    "工艺",
    "做法",
    "防护",
    "扬尘",
    "喷淋",
    "洗车",
    "围挡",
    "材料堆放",
    "标识标牌",
    "安全文明",
    "绿色施工",
    "环境保护",
]

PARAMETERIZED_REUSE_TERMS = [
    "钢筋",
    "模板",
    "混凝土",
    "防水",
    "砌体",
    "脚手架",
    "土方",
    "基坑",
    "降水",
    "临水",
    "临电",
    "垂直运输",
    "机械设备",
    "劳动力",
    "季节性施工",
    "雨季",
    "冬季",
    "高温施工",
    "深基坑",
    "塔吊",
    "施工电梯",
]

DIRECT_REUSE_TERMS = [
    "质量管理制度",
    "质量保证体系",
    "安全管理制度",
    "安全保证体系",
    "文明施工",
    "优秀做法",
    "标准化做法",
    "标准化防护",
    "环境保护",
    "扬尘治理",
    "绿色施工",
    "成品保护",
    "应急管理",
    "应急预案",
    "技术交底",
    "三检制",
    "样板引路",
    "资料管理",
    "信息化管理",
    "农民工工资",
]

PROJECT_SPECIFIC_TERMS = [
    *MANUAL_REVIEW_TERMS,
    *PROJECT_FACT_IMAGE_TERMS,
    "项目名称",
    "工程名称",
    "建设地点",
    "施工地址",
    "总建筑面积",
    "建筑面积",
    "结构形式",
    "地下",
    "地上",
    "厂房",
    "实验楼",
    "幼儿园",
    "学校",
    "棚户区",
    "产业园",
]

IMAGE_CAPTION_SUFFIXES = (
    "示意图",
    "流程图",
    "布置图",
    "节点图",
    "大样图",
    "详图",
    "平面图",
    "立面图",
    "剖面图",
    "做法图",
    "关系图",
    "控制图",
    "效果图",
    "照片",
)

IMAGE_CAPTION_START_ANCHORS = (
    "大体积混凝土",
    "平面控制网",
    "工程测量",
    "测量控制",
    "钢筋加工",
    "钢筋绑扎",
    "典型梁板",
    "梁柱接头",
    "模板拼缝",
    "模板拆除",
    "混凝土浇筑",
    "电子测温仪",
    "测温点",
    "温度分布",
    "防水卷材",
    "阴阳角",
    "地下室",
    "屋面",
    "脚手架",
    "连墙件",
    "剪刀撑",
    "砌体",
    "后浇带",
    "变形缝",
    "施工缝",
    "成品保护",
    "临边洞口",
    "扬尘治理",
    "喷淋",
    "洗车",
    "消防",
    "混凝土",
    "钢筋",
    "模板",
    "防水",
    "测量",
    "砌筑",
)

WEAK_ROW_IMAGE_CAPTION_TERMS = {
    "序号",
    "编号",
    "项目",
    "名称",
    "内容",
    "措施",
    "原因",
    "说明",
    "设计说明",
    "主要内容",
    "控制内容",
    "检查内容",
    "具体措施",
    "做法说明",
    "约束条件",
    "水泥水化热",
    "材料选择",
    "施工方法",
    "控制方法",
    "质量要求",
    "注意事项",
}


def build_excellent_bid_material_library_from_files(
    index_paths: list[str | Path],
    *,
    library_id: str = "default_excellent_bid_library",
) -> ExcellentBidMaterialLibraryResult:
    indexes = [(Path(path), _read_json(path)) for path in index_paths]
    return build_excellent_bid_material_library(indexes, library_id=library_id)


def build_excellent_bid_material_library(
    indexes: list[tuple[str | Path, dict[str, Any]]],
    *,
    library_id: str = "default_excellent_bid_library",
) -> ExcellentBidMaterialLibraryResult:
    sources: list[ExcellentBidLibrarySource] = []
    slices: list[ExcellentBidMaterialSlice] = []
    warnings: list[str] = []

    for source_no, (index_path, index) in enumerate(indexes, start=1):
        source_type = _detect_source_type(index)
        source_id = str(index.get("source_bid_id") or index.get("source_id") or f"SRC{source_no:04d}")
        source = _build_source_record(
            source_id,
            source_type,
            Path(index_path),
            index,
        )
        sources.append(source)
        if source_type == "docx_only":
            source_slices = _docx_only_slices(source, index)
        elif source_type == "pdf_docx_fusion":
            source_slices = _fusion_slices(source, index)
        else:
            warnings.append(f"无法识别优秀标书索引类型：{index_path}")
            continue
        slices.extend(source_slices)

    docx_table_count = sum(slice_.docx_table_count for slice_ in slices)
    docx_image_count = sum(slice_.docx_image_count for slice_ in slices)
    pdf_fallback_table_count = sum(
        slice_.pdf_table_like_count for slice_ in slices if slice_.material_quality == "pdf_fallback"
    )
    pdf_fallback_image_count = sum(
        slice_.pdf_image_count for slice_ in slices if slice_.material_quality == "pdf_fallback"
    )
    pdf_reference_table_count = sum(
        slice_.pdf_table_like_count for slice_ in slices if slice_.material_quality != "pdf_fallback"
    )
    pdf_reference_image_count = sum(
        slice_.pdf_image_count for slice_ in slices if slice_.material_quality != "pdf_fallback"
    )
    table_count = docx_table_count + pdf_fallback_table_count
    image_count = docx_image_count + pdf_fallback_image_count
    image_assets = _build_image_assets(slices)
    image_groups = _build_image_groups(slices, image_assets)

    return ExcellentBidMaterialLibraryResult(
        schema_version=SCHEMA_VERSION,
        library_id=library_id,
        source_count=len(sources),
        slice_count=len(slices),
        table_count=table_count,
        image_count=image_count,
        docx_table_count=docx_table_count,
        docx_image_count=docx_image_count,
        pdf_fallback_table_count=pdf_fallback_table_count,
        pdf_fallback_image_count=pdf_fallback_image_count,
        pdf_reference_table_like_count=pdf_reference_table_count,
        pdf_reference_image_count=pdf_reference_image_count,
        image_asset_count=len(image_assets),
        image_group_count=len(image_groups),
        sources=sources,
        slices=slices,
        image_assets=image_assets,
        image_groups=image_groups,
        source_type_counts=dict(Counter(source.source_type for source in sources)),
        material_quality_counts=dict(Counter(slice_.material_quality for slice_ in slices)),
        warnings=warnings,
    )


def write_excellent_bid_material_library_outputs(
    result: ExcellentBidMaterialLibraryResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_excellent_bid_material_library_report(result), encoding="utf-8")


def render_excellent_bid_material_library_report(result: ExcellentBidMaterialLibraryResult) -> str:
    lines = [
        "# 优秀标书统一素材库报告",
        "",
        f"- 素材库 ID：`{result.library_id}`",
        f"- 来源文件数：{result.source_count}",
        f"- 统一素材切片数：{result.slice_count}",
        f"- 可取用表格数：{result.table_count}",
        f"- 可取用图片数：{result.image_count}",
        f"- DOCX 精确素材表格数：{result.docx_table_count}",
        f"- DOCX 精确素材图片数：{result.docx_image_count}",
        f"- PDF 未匹配兜底表格数：{result.pdf_fallback_table_count}",
        f"- PDF 未匹配兜底图片数：{result.pdf_fallback_image_count}",
        f"- PDF 页级参考疑似表格数：{result.pdf_reference_table_like_count}",
        f"- PDF 页级参考图片数：{result.pdf_reference_image_count}",
        f"- 图片资产数：{result.image_asset_count}",
        f"- 套图组数：{result.image_group_count}",
        f"- 来源类型分布：{_format_counter(result.source_type_counts)}",
        f"- 素材质量分布：{_format_counter(result.material_quality_counts)}",
        f"- 复用等级分布：{_format_counter(dict(Counter(slice_.reuse_level for slice_ in result.slices)))}",
        f"- 项目专属性风险分布：{_format_counter(dict(Counter(slice_.project_specific_risk for slice_ in result.slices)))}",
        "",
        "## 来源清单",
        "",
    ]

    for source in result.sources:
        lines.append(
            f"- {source.source_id}: {source.source_name} "
            f"type={source.source_type}, slices={source.slice_count}, "
            f"tables={source.table_count}, images={source.image_count}, "
            f"matched={source.matched_count}, fallback={source.fallback_count}, unmatched={source.unmatched_count}"
        )

    lines.extend(["", "## 富素材切片", ""])
    rich_slices = sorted(
        result.slices,
        key=lambda slice_: (slice_.table_count, slice_.image_count, slice_.paragraph_char_count),
        reverse=True,
    )
    for slice_ in rich_slices[:40]:
        lines.append(
            f"- {slice_.material_slice_id}: {slice_.source_type} "
            f"T{slice_.table_count}/I{slice_.image_count}, quality={slice_.material_quality}, "
            f"reuse={slice_.reuse_level}, risk={slice_.project_specific_risk}, "
            f"match={slice_.match_status or '-'} "
            f"{' > '.join(slice_.section_path)}"
        )

    lines.extend(["", "## 图片资产预览", ""])
    if result.image_assets:
        for asset in result.image_assets[:80]:
            lines.append(
                f"- {asset.image_asset_id}: reuse={asset.reuse_level}, risk={asset.project_specific_risk}, "
                f"review={asset.review_required}, caption={asset.caption_actual or '-'}, "
                f"part={asset.part_name or '-'}, path={' > '.join(asset.section_path)}"
            )
            if asset.nearby_text:
                lines.append(f"  - nearby: {asset.nearby_text[:180]}")
            if asset.caption_candidates:
                lines.append(f"  - candidates: {'；'.join(asset.caption_candidates[:4])}")
    else:
        lines.append("- 暂无图片资产。")

    lines.extend(["", "## 套图组预览", ""])
    if result.image_groups:
        for group in result.image_groups[:80]:
            lines.append(
                f"- {group.image_group_id}: members={group.member_count}, reuse={group.reuse_level}, "
                f"risk={group.project_specific_risk}, title={group.group_title or '-'}, "
                f"path={' > '.join(group.section_path)}"
            )
            if group.semantic_text:
                lines.append(f"  - semantic: {group.semantic_text[:180]}")
            if group.captions:
                lines.append(f"  - captions: {'；'.join(group.captions[:8])}")
    else:
        lines.append("- 暂无套图组。")

    lines.extend(["", "## 切片预览", ""])
    for slice_ in result.slices[:220]:
        page = _library_page_range(slice_)
        page_text = f" P{page}" if page else ""
        lines.append(
            f"- {slice_.material_slice_id}: L{slice_.level}{page_text} "
            f"T{slice_.table_count}/I{slice_.image_count}, "
            f"quality={slice_.material_quality}, reuse={slice_.reuse_level}, "
            f"risk={slice_.project_specific_risk}, {slice_.title}"
        )
        for paragraph in slice_.paragraphs[:2]:
            lines.append(f"  - P{paragraph.paragraph_index}: {paragraph.text_preview[:180]}")
        for table in slice_.tables[:2]:
            header = " | ".join(table.header_preview)
            lines.append(
                f"  - T{table.table_index}: rows={table.row_count}, "
                f"cols={table.max_column_count}, images={table.image_count}, header={header}"
            )

    if len(result.slices) > 220:
        lines.append("")
        lines.append(f"... 仅展示前 220 个切片，完整索引见 JSON。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def search_excellent_bid_materials(
    library: ExcellentBidMaterialLibraryResult | dict[str, Any],
    *,
    query: str = "",
    section_path: list[str] | None = None,
    top_k: int = 10,
    source_types: set[str] | None = None,
    min_quality: set[str] | None = None,
) -> list[ExcellentBidMaterialSearchHit]:
    slices = _library_slices(library)
    query_tokens = _tokens(query)
    target_path_key = _section_key(section_path or [])
    intent_seed = " ".join(section_path or []) or query
    query_intents = _query_intents(intent_seed) or _query_intents(query)
    hits: list[ExcellentBidMaterialSearchHit] = []

    for slice_ in slices:
        if source_types and slice_.source_type not in source_types:
            continue
        if min_quality and slice_.material_quality not in min_quality:
            continue

        score = 0.0
        reasons: list[str] = []
        search_text = slice_.search_text
        if target_path_key:
            if slice_.section_key == target_path_key:
                score += 3.0
                reasons.append("section_path_exact")
            elif (
                slice_.section_key.endswith(target_path_key)
                or target_path_key.endswith(slice_.section_key)
                or slice_.section_key.startswith(f"{target_path_key} > ")
            ):
                score += 1.6
                reasons.append("section_path_related")

        if query_tokens:
            search_tokens = set(_tokens(search_text))
            if query_intents:
                intent_score = _intent_match_score(query_intents, _intent_text(slice_))
                if not intent_score:
                    continue
                score += intent_score
                reasons.append("intent_match")
                leaf_intent_score = _intent_match_score(query_intents, _intent_leaf_text(slice_))
                if leaf_intent_score:
                    score += leaf_intent_score
                    reasons.append("intent_leaf_match")
            overlap = len(set(query_tokens) & search_tokens)
            if overlap:
                score += overlap / max(len(set(query_tokens)), 1)
                reasons.append("keyword_overlap")
            phrase_score = _phrase_overlap_score(query, search_text)
            if phrase_score:
                score += phrase_score
                reasons.append("phrase_overlap")

        if (query_tokens or target_path_key) and not reasons:
            continue

        score += min(slice_.table_count, 5) * 0.03
        score += min(slice_.image_count, 5) * 0.02
        if slice_.material_quality == "high":
            score += 0.25
        elif slice_.material_quality == "review_required":
            score -= 0.2

        if score > 0:
            hits.append(
                ExcellentBidMaterialSearchHit(
                    material_slice_id=slice_.material_slice_id,
                    score=round(score, 4),
                    reasons=reasons,
                    slice=slice_,
                )
            )

    hits.sort(key=lambda hit: (-hit.score, hit.material_slice_id))
    return hits[:top_k]


def _detect_source_type(index: dict[str, Any]) -> str:
    schema_version = str(index.get("schema_version") or "")
    if schema_version.startswith("excellent_bid_fusion_index") or "fusion_slice_count" in index:
        return "pdf_docx_fusion"
    if "source_path" in index and "slices" in index and "heading_count" in index:
        return "docx_only"
    return "unknown"


def _build_source_record(
    source_id: str,
    source_type: str,
    index_path: Path,
    index: dict[str, Any],
) -> ExcellentBidLibrarySource:
    source_paths = [path for path in [index.get("source_path"), index.get("source_pdf_path"), index.get("source_docx_path")] if path]
    source_name = _source_name(source_paths, index_path)
    slice_count = int(index.get("slice_count") or index.get("fusion_slice_count") or 0)
    table_count = int(index.get("table_count") or 0)
    image_count = _index_image_count(index)
    return ExcellentBidLibrarySource(
        source_id=source_id,
        source_name=source_name,
        source_type=source_type,
        source_index_path=str(index_path),
        source_paths=[str(path) for path in source_paths],
        source_schema_version=_optional_str(index.get("schema_version")),
        slice_count=slice_count,
        table_count=table_count,
        image_count=image_count,
        matched_count=int(index.get("matched_count") or 0),
        ambiguous_count=int(index.get("ambiguous_count") or 0),
        fallback_count=int(index.get("fallback_count") or 0),
        unmatched_count=int(index.get("unmatched_count") or 0),
        warnings=[str(warning) for warning in index.get("warnings") or []],
    )


def _docx_only_slices(source: ExcellentBidLibrarySource, index: dict[str, Any]) -> list[ExcellentBidMaterialSlice]:
    result: list[ExcellentBidMaterialSlice] = []
    for order, raw in enumerate(index.get("slices") or []):
        if not isinstance(raw, dict):
            continue
        section_path = _path(raw.get("section_path"))
        title = section_path[-1] if section_path else raw.get("slice_id") or ""
        number, clean_title = _split_numbered_title(title)
        table_count = int(raw.get("table_count") or 0)
        image_count = int(raw.get("image_count") or 0)
        paragraphs = _section_paragraphs(raw.get("paragraphs") or [])
        tables = _section_tables(raw.get("tables") or [])
        images = _section_images(raw.get("image_bindings") or [])
        material_quality = _quality_for_docx_slice(table_count, image_count)
        reuse_level, project_specific_risk = _reuse_control_for_slice(
            section_path=section_path,
            title=str(title),
            paragraphs=paragraphs,
            tables=tables,
            material_quality=material_quality,
            source_type=source.source_type,
            raw_reuse_level=raw.get("reuse_level"),
            raw_project_specific_risk=raw.get("project_specific_risk"),
        )
        result.append(
            ExcellentBidMaterialSlice(
                material_slice_id=f"{source.source_id}-M{order:05d}",
                source_id=source.source_id,
                source_type=source.source_type,
                source_slice_id=str(raw.get("slice_id") or ""),
                title=str(title),
                clean_title=clean_title,
                number=number,
                level=_optional_int(raw.get("level")),
                section_path=section_path,
                section_key=_section_key(section_path),
                search_text=_search_text(section_path, paragraphs, tables),
                keywords=_keywords(section_path),
                primary_material_source="docx",
                material_quality=material_quality,
                paragraph_count=int(raw.get("paragraph_count") or 0),
                paragraph_char_count=int(raw.get("paragraph_char_count") or 0),
                table_count=table_count,
                image_count=image_count,
                subtree_table_count=int(raw.get("subtree_table_count") or 0),
                subtree_image_count=int(raw.get("subtree_image_count") or 0),
                docx_table_count=table_count,
                docx_image_count=image_count,
                confidence=0.9,
                reuse_level=reuse_level,
                project_specific_risk=project_specific_risk,
                start_block_index=_optional_int(raw.get("start_block_index")),
                end_block_index=_optional_int(raw.get("end_block_index")),
                paragraphs=paragraphs,
                tables=tables,
                image_bindings=images,
            )
        )
    return result


def _fusion_slices(source: ExcellentBidLibrarySource, index: dict[str, Any]) -> list[ExcellentBidMaterialSlice]:
    result: list[ExcellentBidMaterialSlice] = []
    for order, raw in enumerate(index.get("slices") or []):
        if not isinstance(raw, dict):
            continue
        section_path = _path(raw.get("section_path"))
        title = str(raw.get("title") or (section_path[-1] if section_path else raw.get("fusion_slice_id") or ""))
        clean_title = str(raw.get("clean_title") or _split_numbered_title(title)[1])
        number = _optional_str(raw.get("number")) or _split_numbered_title(title)[0]
        match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
        docx_table_count = int(raw.get("docx_table_count") or 0)
        docx_image_count = int(raw.get("docx_image_count") or 0)
        pdf_table_count = int(raw.get("pdf_table_like_count") or 0)
        pdf_image_count = int(raw.get("pdf_image_count") or 0)
        table_count = docx_table_count or pdf_table_count
        image_count = docx_image_count or pdf_image_count
        paragraphs = _section_paragraphs(raw.get("paragraphs") or [])
        tables = _section_tables(raw.get("tables") or [])
        images = _section_images(raw.get("image_bindings") or [])
        pdf_tables = raw.get("pdf_tables") or []
        pdf_images = raw.get("pdf_image_bindings") or []
        material_quality = _quality_for_fusion_slice(match, docx_table_count, docx_image_count)
        reuse_level, project_specific_risk = _reuse_control_for_slice(
            section_path=section_path,
            title=title,
            paragraphs=paragraphs,
            tables=tables,
            material_quality=material_quality,
            source_type=source.source_type,
            raw_reuse_level=raw.get("reuse_level"),
            raw_project_specific_risk=raw.get("project_specific_risk"),
        )
        result.append(
            ExcellentBidMaterialSlice(
                material_slice_id=f"{source.source_id}-M{order:05d}",
                source_id=source.source_id,
                source_type=source.source_type,
                source_slice_id=str(raw.get("fusion_slice_id") or ""),
                title=title,
                clean_title=clean_title,
                number=number,
                level=_optional_int(raw.get("level")),
                section_path=section_path,
                section_key=_section_key(section_path),
                search_text=_search_text(section_path, paragraphs, tables),
                keywords=_keywords(section_path),
                primary_material_source="docx" if docx_table_count or docx_image_count or images else "pdf",
                material_quality=material_quality,
                paragraph_count=int(raw.get("paragraph_count") or 0),
                paragraph_char_count=int(raw.get("paragraph_char_count") or 0),
                table_count=table_count,
                image_count=image_count,
                subtree_table_count=int(raw.get("docx_subtree_table_count") or 0),
                subtree_image_count=int(raw.get("docx_subtree_image_count") or 0),
                docx_table_count=docx_table_count,
                docx_image_count=docx_image_count,
                pdf_table_like_count=pdf_table_count,
                pdf_image_count=pdf_image_count,
                match_status=_optional_str(match.get("status")),
                match_method=_optional_str(match.get("method")),
                match_score=_optional_float(match.get("score")),
                confidence=float(raw.get("confidence") or 0),
                reuse_level=reuse_level,
                project_specific_risk=project_specific_risk,
                start_page=_optional_int(raw.get("start_page")),
                end_page=_optional_int(raw.get("end_page")),
                page_count=int(raw.get("page_count") or 0),
                paragraphs=paragraphs,
                tables=tables,
                image_bindings=images,
                pdf_tables=pdf_tables,
                pdf_image_bindings=pdf_images,
            )
        )
    return result


def _quality_for_docx_slice(table_count: int, image_count: int) -> str:
    if table_count or image_count:
        return "high"
    return "usable"


def _quality_for_fusion_slice(match: dict[str, Any], docx_table_count: int, docx_image_count: int) -> str:
    status = str(match.get("status") or "")
    if status == "matched" and (docx_table_count or docx_image_count):
        return "high"
    if status in {"fallback", "ambiguous"}:
        return "review_required"
    if status == "unmatched":
        return "pdf_fallback"
    return "usable"


def _reuse_control_for_slice(
    *,
    section_path: list[str],
    title: str,
    paragraphs: list[SectionParagraphRecord],
    tables: list[SectionTableRecord],
    material_quality: str,
    source_type: str,
    raw_reuse_level: Any = None,
    raw_project_specific_risk: Any = None,
    preserve_explicit_rewrite: bool = False,
) -> tuple[str, str]:
    text = _reuse_classification_text(section_path, title, paragraphs, tables)
    risk = _normalize_project_specific_risk(raw_project_specific_risk)
    if risk is None:
        risk = _project_specific_risk(text, material_quality=material_quality, source_type=source_type)

    explicit_level = _normalize_reuse_level(raw_reuse_level)
    if explicit_level and explicit_level != "rewrite_reuse":
        return explicit_level, _risk_for_reuse_level(explicit_level, risk)
    if explicit_level == "rewrite_reuse" and preserve_explicit_rewrite:
        return explicit_level, risk

    if _is_project_fact_material(text) or material_quality == "pdf_fallback":
        return "manual_review", "high"

    if material_quality == "review_required":
        if _contains_any(text, PARAMETERIZED_REUSE_TERMS):
            return "parameterized_reuse", _max_risk(risk, "medium")
        return "rewrite_reuse", _max_risk(risk, "medium")

    if _contains_any(text, PARAMETERIZED_REUSE_TERMS):
        return "parameterized_reuse", _max_risk(risk, "medium")

    if _contains_any(text, DIRECT_REUSE_TERMS):
        if risk == "high":
            return "rewrite_reuse", "high"
        if risk == "medium":
            return "rewrite_reuse", "medium"
        return "direct_reuse", "low"

    if explicit_level == "rewrite_reuse":
        return explicit_level, risk
    return "rewrite_reuse", risk


def _reuse_classification_text(
    section_path: list[str],
    title: str,
    paragraphs: list[SectionParagraphRecord],
    tables: list[SectionTableRecord],
) -> str:
    parts: list[str] = [title, *section_path]
    parts.extend(paragraph.text_preview for paragraph in paragraphs[:5])
    for table in tables[:5]:
        parts.extend(table.header_preview)
        if table.nearest_heading_text:
            parts.append(table.nearest_heading_text)
    return _canonical_text(" ".join(part for part in parts if part))


def _normalize_reuse_level(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = LEGACY_REUSE_LEVEL_MAP.get(raw, raw)
    if normalized in REUSE_LEVELS:
        return normalized
    return None


def _normalize_project_specific_risk(value: Any) -> str | None:
    raw = str(value or "").strip()
    if raw in PROJECT_SPECIFIC_RISK_LEVELS:
        return raw
    return None


def _project_specific_risk(text: str, *, material_quality: str, source_type: str) -> str:
    if material_quality == "pdf_fallback":
        return "high"
    if _is_project_fact_material(text):
        return "high"
    project_hits = sum(1 for term in PROJECT_SPECIFIC_TERMS if _canonical_text(term) in text)
    if project_hits >= 2:
        return "high"
    if project_hits == 1 or material_quality == "review_required" or source_type == "pdf_docx_fusion":
        return "medium"
    return "low"


def _risk_for_reuse_level(reuse_level: str, current_risk: str) -> str:
    if reuse_level == "manual_review":
        return "high"
    if reuse_level == "parameterized_reuse":
        return _max_risk(current_risk, "medium")
    if reuse_level == "direct_reuse":
        return current_risk
    return current_risk


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 1) >= order.get(right, 1) else right


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(_canonical_text(term) in text for term in terms)


def _is_project_fact_material(text: str) -> bool:
    if _contains_any(text, MANUAL_REVIEW_TERMS):
        return True
    if _contains_any(text, PROJECT_FACT_IMAGE_TERMS):
        return not _contains_any(text, GENERIC_PRACTICE_IMAGE_TERMS)
    return False


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
    result: list[TableIndexRowPreview] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
            TableIndexRowPreview(
                row_index=int(item.get("row_index") or 0),
                cells=_row_preview_cells(item.get("cells") or []),
            )
        )
    return result


def _row_preview_cells(items: Any) -> list[TableIndexCellPreview]:
    cells: list[TableIndexCellPreview] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cells.append(
            TableIndexCellPreview(
                cell_index=int(item.get("cell_index") or 0),
                text_preview=str(item.get("text_preview") or ""),
                image_count=int(item.get("image_count") or 0),
            )
        )
    return cells


def _section_images(items: Any) -> list[SectionImageBinding]:
    result: list[SectionImageBinding] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
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
                cell_text=str(item.get("cell_text") or ""),
                row_text=str(item.get("row_text") or ""),
                header_text=str(item.get("header_text") or ""),
                previous_row_text=str(item.get("previous_row_text") or ""),
                previous_row_texts=[
                    str(value)
                    for value in item.get("previous_row_texts") or []
                    if str(value or "").strip()
                ],
                next_row_text=str(item.get("next_row_text") or ""),
                previous_non_empty_cell_text=str(item.get("previous_non_empty_cell_text") or ""),
                next_non_empty_cell_text=str(item.get("next_non_empty_cell_text") or ""),
                left_cell_text=str(item.get("left_cell_text") or ""),
                right_cell_text=str(item.get("right_cell_text") or ""),
                above_cell_text=str(item.get("above_cell_text") or ""),
                below_cell_text=str(item.get("below_cell_text") or ""),
                nearby_text=str(item.get("nearby_text") or ""),
                caption_candidates=[str(value) for value in item.get("caption_candidates") or []],
            )
        )
    return result


def _build_image_assets(slices: list[ExcellentBidMaterialSlice]) -> list[ExcellentBidImageAsset]:
    assets: list[ExcellentBidImageAsset] = []
    for slice_ in slices:
        for index, binding in enumerate(slice_.image_bindings):
            semantic_sources = _image_semantic_sources(binding, slice_)
            caption_candidates = _image_caption_candidates(binding, slice_, semantic_sources)
            semantic_text = _semantic_text(semantic_sources)
            semantic_confidence = _semantic_confidence(semantic_sources)
            caption = caption_candidates[0] if caption_candidates else semantic_text
            nearby_text = binding.nearby_text or _image_nearby_text(binding)
            classification_text = _canonical_text(" ".join([caption, nearby_text, slice_.title, *slice_.section_path]))
            reuse_level, risk = _reuse_control_for_image(
                classification_text,
                slice_reuse_level=slice_.reuse_level,
                slice_risk=slice_.project_specific_risk,
            )
            review_required, review_reason = _image_review_decision(
                caption=caption,
                nearby_text=nearby_text,
                reuse_level=reuse_level,
                risk=risk,
            )
            image_id = _image_id(slice_, binding, index)
            assets.append(
                ExcellentBidImageAsset(
                    image_asset_id=f"{slice_.material_slice_id}-IMG{index:04d}",
                    image_id=image_id,
                    source_id=slice_.source_id,
                    source_type=slice_.source_type,
                    source_slice_id=slice_.source_slice_id,
                    material_slice_id=slice_.material_slice_id,
                    title=slice_.title,
                    section_path=list(slice_.section_path),
                    section_key=slice_.section_key,
                    rel_id=binding.rel_id,
                    target=binding.target,
                    part_name=binding.part_name,
                    context=binding.context,
                    table_index=binding.table_index,
                    row_index=binding.row_index,
                    cell_index=binding.cell_index,
                    caption_actual=caption,
                    caption_candidates=caption_candidates,
                    semantic_sources=semantic_sources,
                    semantic_text=semantic_text,
                    semantic_confidence=semantic_confidence,
                    nearby_text=nearby_text,
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
                    tags=_image_tags(" ".join([caption, nearby_text, slice_.title, *slice_.section_path])),
                    reuse_level=reuse_level,
                    project_specific_risk=risk,
                    confidence=semantic_confidence or (0.75 if caption else 0.45),
                    review_required=review_required,
                    review_reason=review_reason,
                )
            )
    return assets


def _build_image_groups(
    slices: list[ExcellentBidMaterialSlice],
    assets: list[ExcellentBidImageAsset],
) -> list[ExcellentBidImageGroup]:
    slice_by_id = {slice_.material_slice_id: slice_ for slice_ in slices}
    grouped_assets: dict[tuple[str, int | None, str], list[ExcellentBidImageAsset]] = {}
    for asset in assets:
        if asset.table_index is None:
            continue
        cluster_key = _table_image_group_cluster_key(asset)
        if not cluster_key:
            continue
        bucket_key = (
            asset.material_slice_id,
            asset.table_index,
            cluster_key,
        )
        grouped_assets.setdefault(bucket_key, []).append(asset)

    groups: list[ExcellentBidImageGroup] = []
    for (material_slice_id, table_index, cluster_key), members in grouped_assets.items():
        ordered = sorted(
            members,
            key=lambda asset: (
                asset.row_index if asset.row_index is not None else 10**9,
                asset.cell_index if asset.cell_index is not None else 10**9,
                asset.image_asset_id,
            ),
        )
        if len(ordered) < 2:
            continue
        slice_ = slice_by_id.get(material_slice_id)
        if slice_ is None:
            continue
        if not _should_create_image_group(slice_, ordered):
            continue
        group_id = f"{material_slice_id}-G{len(groups):04d}"
        group_title = _image_group_title(slice_, ordered, cluster_key)
        semantic_sources = _image_group_semantic_sources(group_title, slice_, ordered)
        semantic_text = _semantic_text(semantic_sources)
        semantic_confidence = _semantic_confidence(semantic_sources)
        captions = _clean_caption_candidates([asset.caption_actual for asset in ordered])
        nearby_text = _image_group_nearby_text(ordered)
        reuse_level, risk = _image_group_reuse_control(slice_, ordered)
        review_required, review_reason = _image_group_review_decision(
            members=ordered,
            reuse_level=reuse_level,
            risk=risk,
        )
        group = ExcellentBidImageGroup(
            image_group_id=group_id,
            source_id=slice_.source_id,
            source_type=slice_.source_type,
            source_slice_id=slice_.source_slice_id,
            material_slice_id=material_slice_id,
            title=slice_.title,
            group_title=group_title,
            section_path=list(slice_.section_path),
            section_key=slice_.section_key,
            table_index=table_index,
            start_row_index=_min_optional(asset.row_index for asset in ordered),
            end_row_index=_max_optional(asset.row_index for asset in ordered),
            member_count=len(ordered),
            image_asset_ids=[asset.image_asset_id for asset in ordered],
            image_ids=[asset.image_id for asset in ordered],
            captions=captions,
            semantic_sources=semantic_sources,
            semantic_text=semantic_text,
            semantic_confidence=semantic_confidence,
            nearby_text=nearby_text,
            tags=_image_group_tags(slice_, ordered, group_title),
            reuse_level=reuse_level,
            project_specific_risk=risk,
            confidence=semantic_confidence or max((asset.confidence for asset in ordered), default=0.45),
            review_required=review_required,
            review_reason=review_reason,
            detection_method="same_table_contiguous_images" if cluster_key else "same_table_images",
            must_keep_together=True,
        )
        for member_index, asset in enumerate(ordered, start=1):
            asset.image_group_id = group_id
            asset.group_title = group_title
            asset.group_semantic_text = semantic_text
            asset.group_member_index = member_index
            asset.group_member_count = len(ordered)
            asset.must_keep_with_group = True
        groups.append(group)
    groups = _merge_related_image_groups(slices, groups, assets)
    return _merge_table_flow_image_groups(slices, groups, assets)


def _table_image_group_cluster_key(asset: ExcellentBidImageAsset) -> str:
    row_text = _semantic_row_group_text(asset)
    if row_text:
        return row_text
    caption = asset.semantic_text or asset.caption_actual
    title = _strip_heading_number(asset.title)
    if _is_step_label(caption) or _is_weak_image_caption(caption):
        if _is_specific_image_group_title(title):
            return title
        return ""
    if _is_specific_image_group_title(title):
        return title
    return ""


def _semantic_row_group_text(asset: ExcellentBidImageAsset) -> str:
    rows = [
        asset.previous_row_text,
        *asset.previous_row_texts,
        asset.row_text,
    ]
    candidates = _clean_caption_candidates(_semantic_row_group_candidates(rows))
    for candidate in candidates:
        if _is_weak_image_caption(candidate):
            continue
        if _is_generic_table_header_caption(candidate):
            continue
        return candidate
    return ""


def _semantic_row_group_candidates(rows: list[str]) -> list[str]:
    candidates: list[str] = []
    for row in rows:
        candidates.append(_compact_row_caption(row))
        topic = _row_topic_text(row)
        if topic:
            candidates.append(topic)
    return candidates


def _row_topic_text(row_text: str) -> str:
    parts = [part.strip() for part in re.split(r"[|；;]", str(row_text or "")) if part.strip()]
    for part in parts:
        value = re.sub(r"^\s*\d+[.)、．）\s]*", "", part).strip()
        value = re.sub(r"^[（(]?\d+[）)]\s*", "", value).strip()
        if not value or re.fullmatch(r"\d+", value):
            continue
        if _is_step_label(value) or _is_generic_table_header_caption(value):
            continue
        value = re.split(r"[：:，,。；;]", value, maxsplit=1)[0].strip()
        if 2 <= len(value) <= 40:
            return value
    return ""


def _is_generic_table_header_caption(text: str) -> bool:
    value = _canonical_text(text)
    if not value:
        return False
    generic_parts = [
        "序号",
        "编号",
        "项目",
        "名称",
        "内容",
        "措施",
        "标准化做法",
        "通病防治措施",
        "施工内容",
        "施工要求",
        "图片",
        "图示",
        "示意图",
        "质量实例",
        "问题分析",
        "管控措施",
        "原因分析",
        "基本要求",
    ]
    compact_parts = [_canonical_text(part) for part in generic_parts]
    return all(part in compact_parts for part in _split_semantic_caption_parts(text))


def _split_semantic_caption_parts(text: str) -> list[str]:
    return [
        _canonical_text(part)
        for part in re.split(r"[|；;、，,\s]+", str(text or ""))
        if _canonical_text(part)
    ]


def _is_specific_image_group_title(text: str) -> bool:
    if not text or _is_weak_image_caption(text) or _is_generic_table_header_caption(text):
        return False
    value = str(text)
    strong_terms = [
        "流程",
        "示意",
        "套图",
        "施工方法",
        "主要施工方法",
        "加工示意",
        "绑扎流程",
        "平面控制网",
        "工艺流程",
    ]
    return any(term in value for term in strong_terms)


def _should_create_image_group(slice_: ExcellentBidMaterialSlice, members: list[ExcellentBidImageAsset]) -> bool:
    if len(members) < 2:
        return False
    if any(_is_project_fact_material(asset.nearby_text or asset.caption_actual or "") for asset in members):
        return False
    if all(asset.review_required for asset in members):
        return False
    semantic_title = _image_group_title(slice_, members, _semantic_row_group_text(members[0]))
    if _is_weak_image_caption(semantic_title) or _is_generic_table_header_caption(semantic_title):
        return False
    row_indexes = [asset.row_index for asset in members if asset.row_index is not None]
    if row_indexes and max(row_indexes) - min(row_indexes) <= max(2, len(set(row_indexes))):
        return True
    captions = [asset.caption_actual for asset in members if asset.caption_actual and not _is_weak_image_caption(asset.caption_actual)]
    if len(captions) >= 2:
        return True
    title_text = " ".join([slice_.title, *slice_.section_path])
    return _contains_any(title_text, ["流程", "示意", "做法", "工艺", "控制", "加工", "绑扎", "安装"])


def _image_group_title(
    slice_: ExcellentBidMaterialSlice,
    members: list[ExcellentBidImageAsset],
    cluster_key: str,
) -> str:
    candidates = _clean_caption_candidates(
        [
            cluster_key,
            members[0].group_title,
            members[0].previous_row_text,
            *members[0].previous_row_texts,
            members[0].header_text,
            slice_.title,
        ]
    )
    for candidate in candidates:
        if not _is_weak_image_caption(candidate):
            return _strip_heading_number(candidate)
    return _strip_heading_number(slice_.title) or "施工做法套图"


def _image_group_semantic_sources(
    group_title: str,
    slice_: ExcellentBidMaterialSlice,
    members: list[ExcellentBidImageAsset],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []

    def add(source_type: str, text: str, confidence: float) -> None:
        cleaned = _clean_semantic_text(text)
        if not cleaned:
            return
        if any(item["text"] == cleaned and item["source_type"] == source_type for item in sources):
            return
        sources.append({"source_type": source_type, "text": cleaned, "confidence": confidence})

    add("group_title", group_title, 0.92)
    captions = "；".join(asset.caption_actual for asset in members if asset.caption_actual)
    add("member_captions", captions, 0.82)
    for asset in members[:8]:
        add("member_semantic", asset.semantic_text, min(float(asset.semantic_confidence or 0), 0.78))
    add("section_heading", slice_.title, 0.52)
    if slice_.section_path:
        add("section_path", " > ".join(slice_.section_path[-2:]), 0.42)
    return sources


def _image_group_nearby_text(members: list[ExcellentBidImageAsset]) -> str:
    values: list[str] = []
    for asset in members:
        values.extend(
            [
                asset.caption_actual,
                asset.semantic_text,
                asset.nearby_text,
                asset.previous_row_text,
                *asset.previous_row_texts,
            ]
        )
    return "；".join(_clean_caption_candidates(values)[:12])


def _image_group_tags(
    slice_: ExcellentBidMaterialSlice,
    members: list[ExcellentBidImageAsset],
    group_title: str,
) -> list[str]:
    text = " ".join(
        [
            group_title,
            slice_.title,
            *slice_.section_path,
            *[asset.caption_actual for asset in members],
            *[asset.nearby_text for asset in members],
        ]
    )
    return _image_tags(text)


def _image_group_reuse_control(
    slice_: ExcellentBidMaterialSlice,
    members: list[ExcellentBidImageAsset],
) -> tuple[str, str]:
    if any(asset.reuse_level == "manual_review" or asset.project_specific_risk == "high" for asset in members):
        return "manual_review", "high"
    if any(asset.reuse_level == "candidate_reuse" for asset in members):
        return "candidate_reuse", _max_risk(slice_.project_specific_risk, "medium")
    if all(asset.reuse_level == "direct_reuse" for asset in members):
        return "candidate_reuse", slice_.project_specific_risk
    return "candidate_reuse", slice_.project_specific_risk


def _image_group_review_decision(
    *,
    members: list[ExcellentBidImageAsset],
    reuse_level: str,
    risk: str,
) -> tuple[bool, str]:
    if reuse_level == "manual_review" or risk == "high":
        return True, "套图中包含需人工复核或高风险图片，不能自动复用。"
    if any(asset.review_required for asset in members):
        return True, "套图中存在单图语义不稳定成员，需人工确认后使用。"
    return False, ""


def _merge_related_image_groups(
    slices: list[ExcellentBidMaterialSlice],
    groups: list[ExcellentBidImageGroup],
    assets: list[ExcellentBidImageAsset],
) -> list[ExcellentBidImageGroup]:
    slice_by_id = {slice_.material_slice_id: slice_ for slice_ in slices}
    asset_by_id = {asset.image_asset_id: asset for asset in assets}
    buckets: dict[tuple[str, int | None, str], list[ExcellentBidImageGroup]] = {}
    for group in groups:
        parent_topic = _parent_topic_for_group(group, asset_by_id)
        if not parent_topic:
            continue
        buckets.setdefault((group.material_slice_id, group.table_index, parent_topic), []).append(group)

    merged_group_ids: set[str] = set()
    merged: list[ExcellentBidImageGroup] = []
    for (material_slice_id, table_index, parent_topic), bucket_groups in buckets.items():
        if not _is_specific_image_group_title(parent_topic):
            continue
        if len(bucket_groups) < 2:
            continue
        members = _assets_for_groups(bucket_groups, asset_by_id)
        if len(members) < 4:
            continue
        if not _groups_are_nearby(bucket_groups):
            continue
        slice_ = slice_by_id.get(material_slice_id)
        if slice_ is None:
            continue
        group_id = f"{material_slice_id}-G{len(groups) + len(merged):04d}"
        ordered = sorted(
            members,
            key=lambda asset: (
                asset.row_index if asset.row_index is not None else 10**9,
                asset.cell_index if asset.cell_index is not None else 10**9,
                asset.image_asset_id,
            ),
        )
        semantic_sources = _image_group_semantic_sources(parent_topic, slice_, ordered)
        semantic_text = _semantic_text(semantic_sources)
        semantic_confidence = _semantic_confidence(semantic_sources)
        captions = _clean_caption_candidates([asset.caption_actual for asset in ordered])
        nearby_text = _image_group_nearby_text(ordered)
        reuse_level, risk = _image_group_reuse_control(slice_, ordered)
        review_required, review_reason = _image_group_review_decision(
            members=ordered,
            reuse_level=reuse_level,
            risk=risk,
        )
        merged_group = ExcellentBidImageGroup(
            image_group_id=group_id,
            source_id=slice_.source_id,
            source_type=slice_.source_type,
            source_slice_id=slice_.source_slice_id,
            material_slice_id=material_slice_id,
            title=slice_.title,
            group_title=parent_topic,
            section_path=list(slice_.section_path),
            section_key=slice_.section_key,
            table_index=table_index,
            start_row_index=_min_optional(asset.row_index for asset in ordered),
            end_row_index=_max_optional(asset.row_index for asset in ordered),
            member_count=len(ordered),
            image_asset_ids=[asset.image_asset_id for asset in ordered],
            image_ids=[asset.image_id for asset in ordered],
            captions=captions,
            semantic_sources=semantic_sources,
            semantic_text=semantic_text,
            semantic_confidence=semantic_confidence,
            nearby_text=nearby_text,
            tags=_image_group_tags(slice_, ordered, parent_topic),
            reuse_level=reuse_level,
            project_specific_risk=risk,
            confidence=semantic_confidence or max((asset.confidence for asset in ordered), default=0.45),
            review_required=review_required,
            review_reason=review_reason,
            detection_method="same_table_parent_topic_merge",
            must_keep_together=True,
        )
        for member_index, asset in enumerate(ordered, start=1):
            asset.image_group_id = group_id
            asset.group_title = parent_topic
            asset.group_semantic_text = semantic_text
            asset.group_member_index = member_index
            asset.group_member_count = len(ordered)
            asset.must_keep_with_group = True
        merged.extend([merged_group])
        merged_group_ids.update(group.image_group_id for group in bucket_groups)

    if not merged:
        return groups
    return [group for group in groups if group.image_group_id not in merged_group_ids] + merged


def _merge_table_flow_image_groups(
    slices: list[ExcellentBidMaterialSlice],
    groups: list[ExcellentBidImageGroup],
    assets: list[ExcellentBidImageAsset],
) -> list[ExcellentBidImageGroup]:
    """把同一表格内连续表达流程/示意的图片合并为完整套图。"""

    slice_by_id = {slice_.material_slice_id: slice_ for slice_ in slices}
    asset_by_id = {asset.image_asset_id: asset for asset in assets}
    groups_by_table: dict[tuple[str, int | None], list[ExcellentBidImageGroup]] = {}
    for group in groups:
        groups_by_table.setdefault((group.material_slice_id, group.table_index), []).append(group)

    merged_group_ids: set[str] = set()
    merged: list[ExcellentBidImageGroup] = []
    for (material_slice_id, table_index), table_groups in groups_by_table.items():
        if len(table_groups) < 2:
            continue
        slice_ = slice_by_id.get(material_slice_id)
        if slice_ is None:
            continue
        members = _assets_for_groups(table_groups, asset_by_id)
        if len(members) < 4:
            continue
        ordered = sorted(
            members,
            key=lambda asset: (
                asset.row_index if asset.row_index is not None else 10**9,
                asset.cell_index if asset.cell_index is not None else 10**9,
                asset.image_asset_id,
            ),
        )
        if not _is_table_flow_image_set(slice_, table_groups, ordered):
            continue
        group_title = _table_flow_group_title(slice_, ordered, table_groups)
        group_id = f"{material_slice_id}-G{len(groups) + len(merged):04d}"
        semantic_sources = _image_group_semantic_sources(group_title, slice_, ordered)
        semantic_text = _semantic_text(semantic_sources)
        semantic_confidence = _semantic_confidence(semantic_sources)
        captions = _clean_caption_candidates([asset.caption_actual for asset in ordered])
        nearby_text = _image_group_nearby_text(ordered)
        reuse_level, risk = _image_group_reuse_control(slice_, ordered)
        review_required, review_reason = _image_group_review_decision(
            members=ordered,
            reuse_level=reuse_level,
            risk=risk,
        )
        merged_group = ExcellentBidImageGroup(
            image_group_id=group_id,
            source_id=slice_.source_id,
            source_type=slice_.source_type,
            source_slice_id=slice_.source_slice_id,
            material_slice_id=material_slice_id,
            title=slice_.title,
            group_title=group_title,
            section_path=list(slice_.section_path),
            section_key=slice_.section_key,
            table_index=table_index,
            start_row_index=_min_optional(asset.row_index for asset in ordered),
            end_row_index=_max_optional(asset.row_index for asset in ordered),
            member_count=len(ordered),
            image_asset_ids=[asset.image_asset_id for asset in ordered],
            image_ids=[asset.image_id for asset in ordered],
            captions=captions,
            semantic_sources=semantic_sources,
            semantic_text=semantic_text,
            semantic_confidence=semantic_confidence,
            nearby_text=nearby_text,
            tags=_image_group_tags(slice_, ordered, group_title),
            reuse_level=reuse_level,
            project_specific_risk=risk,
            confidence=semantic_confidence or max((asset.confidence for asset in ordered), default=0.45),
            review_required=review_required,
            review_reason=review_reason,
            detection_method="same_table_flow_merge",
            must_keep_together=True,
        )
        for member_index, asset in enumerate(ordered, start=1):
            asset.image_group_id = group_id
            asset.group_title = group_title
            asset.group_semantic_text = semantic_text
            asset.group_member_index = member_index
            asset.group_member_count = len(ordered)
            asset.must_keep_with_group = True
        merged.append(merged_group)
        merged_group_ids.update(group.image_group_id for group in table_groups)

    if not merged:
        return groups
    return [group for group in groups if group.image_group_id not in merged_group_ids] + merged


def _is_table_flow_image_set(
    slice_: ExcellentBidMaterialSlice,
    groups: list[ExcellentBidImageGroup],
    members: list[ExcellentBidImageAsset],
) -> bool:
    row_indexes = [asset.row_index for asset in members if asset.row_index is not None]
    if not row_indexes or max(row_indexes) - min(row_indexes) > 8:
        return False
    text = " ".join(
        [
            slice_.title,
            *slice_.section_path,
            *[group.group_title for group in groups],
            *[asset.caption_actual for asset in members],
            *[asset.nearby_text for asset in members],
            *[asset.header_text for asset in members],
        ]
    )
    return _contains_any(text, ["流程", "工序", "步骤", "施工方法", "加工", "绑扎", "安装", "示意"])


def _table_flow_group_title(
    slice_: ExcellentBidMaterialSlice,
    members: list[ExcellentBidImageAsset],
    groups: list[ExcellentBidImageGroup],
) -> str:
    candidates = _clean_caption_candidates(
        [
            *[group.group_title for group in groups],
            *[asset.previous_row_text for asset in members],
            slice_.title,
        ]
    )
    for candidate in candidates:
        if _is_specific_image_group_title(candidate):
            return _strip_heading_number(candidate)
    return f"{_strip_heading_number(slice_.title) or '施工'}流程示意图"


def _parent_topic_for_group(
    group: ExcellentBidImageGroup,
    asset_by_id: dict[str, ExcellentBidImageAsset],
) -> str:
    topics: list[str] = []
    for asset_id in group.image_asset_ids:
        asset = asset_by_id.get(asset_id)
        if asset is None:
            continue
        for row_text in [asset.header_text, *asset.previous_row_texts]:
            topic = _row_topic_text(row_text)
            if topic and _is_specific_image_group_title(topic):
                topics.append(topic)
    if not topics:
        return ""
    counts = Counter(topics)
    topic, count = counts.most_common(1)[0]
    if count < 2 and len(group.image_asset_ids) < 3:
        return ""
    return topic


def _assets_for_groups(
    groups: list[ExcellentBidImageGroup],
    asset_by_id: dict[str, ExcellentBidImageAsset],
) -> list[ExcellentBidImageAsset]:
    result: list[ExcellentBidImageAsset] = []
    seen: set[str] = set()
    for group in groups:
        for asset_id in group.image_asset_ids:
            if asset_id in seen or asset_id not in asset_by_id:
                continue
            seen.add(asset_id)
            result.append(asset_by_id[asset_id])
    return result


def _groups_are_nearby(groups: list[ExcellentBidImageGroup]) -> bool:
    rows = [
        value
        for group in groups
        for value in [group.start_row_index, group.end_row_index]
        if value is not None
    ]
    if not rows:
        return False
    return max(rows) - min(rows) <= 8


def _min_optional(values: Any) -> int | None:
    numbers = [value for value in values if value is not None]
    return min(numbers) if numbers else None


def _max_optional(values: Any) -> int | None:
    numbers = [value for value in values if value is not None]
    return max(numbers) if numbers else None


def _image_id(slice_: ExcellentBidMaterialSlice, binding: SectionImageBinding, index: int) -> str:
    parts = [
        "EBIMG",
        slice_.source_id,
        slice_.material_slice_id.replace("-", "_"),
        binding.rel_id or f"IMG{index:04d}",
        str(binding.table_index) if binding.table_index is not None else "P",
        str(binding.row_index) if binding.row_index is not None else "X",
        str(binding.cell_index) if binding.cell_index is not None else "X",
    ]
    return "_".join(re.sub(r"[^A-Za-z0-9_]+", "_", part) for part in parts if part)


def _image_semantic_sources(binding: SectionImageBinding, slice_: ExcellentBidMaterialSlice) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []

    def add(source_type: str, text: str, confidence: float) -> None:
        cleaned = _clean_semantic_text(text)
        if not cleaned:
            return
        if any(item["text"] == cleaned and item["source_type"] == source_type for item in sources):
            return
        sources.append({"source_type": source_type, "text": cleaned, "confidence": confidence})

    if binding.context == "paragraph":
        add("paragraph_caption", binding.cell_text or binding.nearby_text, 0.86)
    for embedded_caption in _embedded_image_caption_candidates(binding.cell_text):
        add("embedded_same_cell_caption", embedded_caption, 0.96)
    add("below_cell_caption", binding.below_cell_text, 0.92)
    add("same_cell_caption", binding.cell_text, 0.9)
    add("same_row_item", _row_item_text(binding), 0.84)
    add("same_row_text", _compact_row_caption(binding.row_text), 0.74)
    for embedded_caption in _embedded_image_caption_candidates(binding.row_text):
        add("embedded_row_caption", embedded_caption, 0.82)
    add("above_cell_caption", binding.above_cell_text, 0.7)
    for offset, previous_text in enumerate(_previous_row_semantic_texts(binding), start=1):
        add(f"previous_row_{offset}_item", previous_text, max(0.66 - (offset - 1) * 0.06, 0.5))
    add("previous_non_empty_cell", binding.previous_non_empty_cell_text, 0.58)
    add("section_heading", slice_.title, 0.46)
    if slice_.section_path:
        add("section_path", " > ".join(slice_.section_path[-2:]), 0.38)
    return sources


def _row_item_text(binding: SectionImageBinding) -> str:
    parts = [
        binding.previous_non_empty_cell_text,
        binding.left_cell_text,
        binding.right_cell_text,
        binding.next_non_empty_cell_text,
    ]
    candidates = _clean_caption_candidates(parts)
    return candidates[0] if candidates else ""


def _previous_row_semantic_texts(binding: SectionImageBinding) -> list[str]:
    rows = list(binding.previous_row_texts)
    if binding.previous_row_text and binding.previous_row_text not in rows:
        rows.insert(0, binding.previous_row_text)
    candidates = [_compact_row_caption(row) for row in rows]
    return _clean_caption_candidates(candidates)


def _semantic_text(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ""
    return str(max(sources, key=lambda item: float(item.get("confidence") or 0)).get("text") or "")


def _semantic_confidence(sources: list[dict[str, Any]]) -> float:
    if not sources:
        return 0.0
    return round(max(float(item.get("confidence") or 0) for item in sources), 3)


def _clean_semantic_text(value: str) -> str:
    candidates = _clean_caption_candidates([value])
    return candidates[0] if candidates else ""


def _image_caption_candidates(
    binding: SectionImageBinding,
    slice_: ExcellentBidMaterialSlice,
    semantic_sources: list[dict[str, Any]] | None = None,
) -> list[str]:
    candidates = [str(item.get("text") or "") for item in (semantic_sources or [])]
    candidates.extend(_embedded_image_caption_candidates(binding.cell_text))
    candidates.extend(_embedded_image_caption_candidates(binding.row_text))
    candidates.extend(binding.caption_candidates)
    candidates.extend(
        [
            binding.cell_text,
            binding.below_cell_text,
            binding.above_cell_text,
            binding.previous_non_empty_cell_text,
            binding.next_non_empty_cell_text,
            binding.left_cell_text,
            binding.right_cell_text,
            *[_compact_row_caption(row) for row in binding.previous_row_texts],
            _compact_row_caption(binding.previous_row_text),
            _compact_row_caption(binding.next_row_text),
        ]
    )
    if binding.context == "paragraph":
        candidates.append(slice_.title)
    return _clean_caption_candidates(candidates)


def _embedded_image_caption_candidates(text: str) -> list[str]:
    value = re.sub(r"\s+", "", str(text or ""))
    if not value:
        return []
    if len(value) <= 50 and not re.search(r"[|；;。！？!?：:，,、]", value):
        return []
    result: list[str] = []
    for suffix in IMAGE_CAPTION_SUFFIXES:
        for match in re.finditer(re.escape(suffix), value):
            tail_end = _caption_parenthetical_tail_end(value, match.end())
            caption = _embedded_caption_before_suffix(value, match.start()) + value[match.start() : tail_end]
            caption = _trim_embedded_caption(caption)
            if _looks_like_embedded_image_caption(caption):
                result.append(caption)
    return _clean_caption_candidates(result)


def _caption_parenthetical_tail_end(value: str, start: int) -> int:
    if start < len(value) and value[start] in "(（":
        closing = ")" if value[start] == "(" else "）"
        close_index = value.find(closing, start + 1)
        if close_index != -1 and close_index - start <= 42:
            return close_index + 1
    return start


def _embedded_caption_before_suffix(value: str, suffix_start: int) -> str:
    prefix = value[:suffix_start]
    if not prefix:
        return ""
    fragments = [part for part in re.split(r"[|；;。！？!?：:，,、\s]", prefix) if part]
    latest_fragment = fragments[-1] if fragments else ""
    if 4 <= len(latest_fragment) <= 50:
        return latest_fragment
    anchored: list[tuple[int, str]] = []
    for anchor in IMAGE_CAPTION_START_ANCHORS:
        index = prefix.rfind(anchor)
        if index >= 0:
            anchored.append((index, prefix[index:]))
    if anchored:
        return min(anchored, key=lambda item: item[0])[1]
    return latest_fragment or prefix[-30:]


def _trim_embedded_caption(text: str) -> str:
    value = str(text or "").strip(" |；;，,。.")
    value = re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", value)
    value = re.sub(r"^(序号|项目|名称|原因|措施|内容|说明)[:：|；;、，,]+", "", value)
    if len(value) <= 90:
        return value
    anchored = [
        value[index:]
        for anchor in IMAGE_CAPTION_START_ANCHORS
        if (index := value.find(anchor)) >= 0 and len(value[index:]) <= 90
    ]
    if anchored:
        return min(anchored, key=len)
    return value[-90:]


def _looks_like_embedded_image_caption(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or ""))
    if not value or len(value) < 5 or len(value) > 90:
        return False
    if _is_weak_image_caption(value):
        return False
    return any(suffix in value for suffix in IMAGE_CAPTION_SUFFIXES)


def _clean_caption_candidates(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" |；;，,")
        if not text:
            continue
        if re.fullmatch(r"\d+", text):
            continue
        if len(text) > 90:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    strong = [text for text in result if not _is_weak_image_caption(text)]
    weak = [text for text in result if _is_weak_image_caption(text)]
    return [*strong, *weak][:6]


def _compact_row_caption(text: str) -> str:
    parts = [part.strip() for part in re.split(r"[|；;]", str(text or "")) if part.strip()]
    if len(parts) == 1:
        return parts[0]
    short_parts = [part for part in parts if not re.fullmatch(r"\d+", part) and len(part) <= 40]
    return "；".join(short_parts[:3])


def _image_nearby_text(binding: SectionImageBinding) -> str:
    return "；".join(
        value
        for value in _clean_caption_candidates(
            [
                binding.cell_text,
                binding.above_cell_text,
                binding.below_cell_text,
                binding.row_text,
                binding.header_text,
                binding.previous_row_text,
                *binding.previous_row_texts,
                binding.next_row_text,
                binding.previous_non_empty_cell_text,
                binding.next_non_empty_cell_text,
                binding.left_cell_text,
                binding.right_cell_text,
            ]
        )
    )


def _reuse_control_for_image(
    text: str,
    *,
    slice_reuse_level: str,
    slice_risk: str,
) -> tuple[str, str]:
    if _is_project_fact_material(text):
        return "manual_review", "high"
    if _contains_any(text, GENERIC_PRACTICE_IMAGE_TERMS) or slice_reuse_level == "direct_reuse":
        return "direct_reuse", "low" if slice_risk == "low" else slice_risk
    if _contains_any(text, PARAMETERIZED_REUSE_TERMS):
        return "candidate_reuse", _max_risk(slice_risk, "medium")
    if slice_reuse_level == "parameterized_reuse":
        return "candidate_reuse", _max_risk(slice_risk, "medium")
    if slice_reuse_level == "manual_review":
        return "manual_review", "high"
    return "candidate_reuse", slice_risk


def _image_review_decision(*, caption: str, nearby_text: str, reuse_level: str, risk: str) -> tuple[bool, str]:
    if reuse_level == "manual_review" or risk == "high":
        return True, "项目事实或高风险图片，需人工确认。"
    if not caption:
        return True, "未提取到稳定图片说明，需人工确认。"
    if _is_weak_image_caption(caption):
        return True, "图片说明过于泛化，仅包含步骤、序号或通用图名，需人工确认。"
    if not nearby_text:
        return True, "缺少图片邻近文字，需人工确认。"
    return False, ""


def _is_weak_image_caption(caption: str) -> bool:
    text = re.sub(r"\s+", "", str(caption or ""))
    if not text:
        return False
    weak_terms = {"图片", "图示", "示意图", "照片", "现场图", "效果图", "施工图示", "质量实例"}
    row_item_terms = {re.sub(r"\s+", "", term) for term in WEAK_ROW_IMAGE_CAPTION_TERMS}
    if text in weak_terms or text in row_item_terms:
        return True
    if re.fullmatch(r"(第[一二三四五六七八九十百\d]+步)+", text):
        return True
    parts = [
        part
        for part in re.split(r"[|；;、，,\s/]+", str(caption or ""))
        if part.strip()
    ]
    return bool(parts) and all(_is_step_label(part) for part in parts)


def _is_step_label(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or "")).strip("：:.-—_")
    return bool(
        re.fullmatch(r"第?[一二三四五六七八九十百\d]+步", value)
        or re.fullmatch(r"步骤[一二三四五六七八九十百\d]+", value)
    )


def _image_tags(text: str) -> list[str]:
    compact = _canonical_text(text)
    terms = [
        "测量",
        "轴线",
        "标高",
        "钢筋",
        "箍筋",
        "模板",
        "混凝土",
        "防水",
        "脚手架",
        "砌体",
        "后浇带",
        "成品保护",
        "环境保护",
        "扬尘",
        "安全文明",
        "总平面",
        "进度",
    ]
    return [term for term in terms if _canonical_text(term) in compact]


def _library_slices(library: ExcellentBidMaterialLibraryResult | dict[str, Any]) -> list[ExcellentBidMaterialSlice]:
    if isinstance(library, ExcellentBidMaterialLibraryResult):
        return library.slices
    result: list[ExcellentBidMaterialSlice] = []
    for item in library.get("slices") or []:
        if not isinstance(item, dict):
            continue
        section_path = _path(item.get("section_path"))
        paragraphs = _section_paragraphs(item.get("paragraphs") or [])
        tables = _section_tables(item.get("tables") or [])
        material_quality = str(item.get("material_quality") or "usable")
        reuse_level, project_specific_risk = _reuse_control_for_slice(
            section_path=section_path,
            title=str(item.get("title") or ""),
            paragraphs=paragraphs,
            tables=tables,
            material_quality=material_quality,
            source_type=str(item.get("source_type") or ""),
            raw_reuse_level=item.get("reuse_level"),
            raw_project_specific_risk=item.get("project_specific_risk"),
            preserve_explicit_rewrite=True,
        )
        search_text = str(item.get("search_text") or "") or _search_text(section_path, paragraphs, tables)
        result.append(
            ExcellentBidMaterialSlice(
                material_slice_id=str(item.get("material_slice_id") or ""),
                source_id=str(item.get("source_id") or ""),
                source_type=str(item.get("source_type") or ""),
                source_slice_id=str(item.get("source_slice_id") or ""),
                title=str(item.get("title") or ""),
                clean_title=str(item.get("clean_title") or ""),
                number=_optional_str(item.get("number")),
                level=_optional_int(item.get("level")),
                section_path=section_path,
                section_key=_section_key(section_path),
                search_text=search_text,
                keywords=[str(value) for value in item.get("keywords") or []],
                primary_material_source=str(item.get("primary_material_source") or "docx"),
                material_quality=str(item.get("material_quality") or "usable"),
                paragraph_count=int(item.get("paragraph_count") or 0),
                paragraph_char_count=int(item.get("paragraph_char_count") or 0),
                table_count=int(item.get("table_count") or 0),
                image_count=int(item.get("image_count") or 0),
                docx_table_count=int(item.get("docx_table_count") or 0),
                docx_image_count=int(item.get("docx_image_count") or 0),
                pdf_table_like_count=int(item.get("pdf_table_like_count") or 0),
                pdf_image_count=int(item.get("pdf_image_count") or 0),
                match_status=_optional_str(item.get("match_status")),
                match_method=_optional_str(item.get("match_method")),
                match_score=_optional_float(item.get("match_score")),
                confidence=float(item.get("confidence") or 0),
                reuse_level=reuse_level,
                project_specific_risk=project_specific_risk,
                start_page=_optional_int(item.get("start_page")),
                end_page=_optional_int(item.get("end_page")),
                page_count=int(item.get("page_count") or 0),
                start_block_index=_optional_int(item.get("start_block_index")),
                end_block_index=_optional_int(item.get("end_block_index")),
                paragraphs=paragraphs,
                tables=tables,
                image_bindings=_section_images(item.get("image_bindings") or []),
            )
        )
    return result


def _search_text(
    section_path: list[str],
    paragraphs: list[SectionParagraphRecord],
    tables: list[SectionTableRecord],
) -> str:
    parts: list[str] = [*section_path]
    parts.extend(paragraph.text_preview for paragraph in paragraphs[:3])
    for table in tables[:3]:
        parts.extend(table.header_preview)
    return " ".join(part for part in parts if part)


def _keywords(section_path: list[str]) -> list[str]:
    return sorted(set(_tokens(" ".join(section_path))))


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", str(text or ""))
    compact = _canonical_text(text)
    tokens = [item.lower() for item in raw if len(item) >= 2]
    if compact:
        tokens.append(compact)
    return tokens


def _phrase_overlap_score(query: str, text: str) -> float:
    query_phrases = _chinese_phrases(query)
    text_compact = _canonical_text(text)
    if not query_phrases or not text_compact:
        return 0
    hits = 0
    for phrase in query_phrases:
        if phrase in text_compact or any(part and part in text_compact for part in _phrase_parts(phrase)):
            hits += 1
    if not hits:
        return 0
    return min(0.8, hits / max(len(query_phrases), 1) * 0.8)


def _query_intents(text: str) -> list[str]:
    compact = _canonical_text(text)
    if not compact:
        return []
    intents: list[str] = []
    for name, profile in _INTENT_PROFILES.items():
        if any(term in compact for term in profile["triggers"]):
            intents.append(name)
    return intents


def _intent_match_score(intents: list[str], text: str) -> float:
    compact = _canonical_text(text)
    if not compact:
        return 0
    total = 0.0
    for intent in intents:
        profile = _INTENT_PROFILES.get(intent, {})
        required_terms = profile.get("required_terms", [])
        if required_terms and not any(term in compact for term in required_terms):
            return 0
        terms = profile.get("match_terms", [])
        hits = sum(1 for term in terms if term in compact)
        if not hits:
            return 0
        total += min(0.9, 0.35 + hits * 0.12)
    return min(1.2, total)


def _intent_text(slice_: ExcellentBidMaterialSlice) -> str:
    parts = [slice_.title, slice_.clean_title, *slice_.section_path]
    for table in slice_.tables[:3]:
        parts.extend(table.header_preview)
    return " ".join(part for part in parts if part)


def _intent_leaf_text(slice_: ExcellentBidMaterialSlice) -> str:
    leaf_title = slice_.section_path[-1] if slice_.section_path else ""
    parts = [slice_.title, slice_.clean_title, leaf_title]
    return " ".join(part for part in parts if part)


_INTENT_PROFILES = {
    "schedule": {
        "triggers": [
            "施工进度",
            "进度计划",
            "进度表",
            "网络图",
            "横道图",
            "工期计划",
            "计划开竣工",
        ],
        "match_terms": [
            "施工进度",
            "工程进度",
            "进度计划",
            "总进度",
            "总工期",
            "计划工期",
            "工期计划",
            "工期保证",
            "工期目标",
            "工期延误",
            "工期管理",
            "网络图",
            "横道图",
            "关键线路",
            "计划开竣工",
            "开竣工日期",
        ],
        "required_terms": [
            "施工进度",
            "工程进度",
            "进度计划",
            "总进度",
            "网络图",
            "横道图",
            "关键线路",
            "计划开竣工",
            "开竣工日期",
            "总工期",
            "计划工期",
            "工期计划",
            "工期保证",
            "工期目标",
            "工期延误",
            "工期管理",
        ],
    },
    "site_layout": {
        "triggers": [
            "施工总平面",
            "总平面布置",
            "平面布置图",
            "施工平面",
        ],
        "match_terms": [
            "施工总平面",
            "总平面",
            "平面布置",
            "施工平面",
            "施工现场",
            "临时设施",
            "临时道路",
            "施工道路",
            "材料堆场",
            "堆场",
            "临水",
            "临电",
            "办公区",
            "生活区",
            "加工区",
            "围墙",
            "库房",
            "厕所",
            "标示标牌",
        ],
    },
    "risk": {
        "triggers": [
            "风险评估",
            "风险动态",
            "风险管理",
            "风险控制",
            "风险识别",
        ],
        "match_terms": [
            "风险",
            "评估",
            "动态",
            "识别",
            "控制",
            "应急",
            "预案",
            "防范",
        ],
        "required_terms": [
            "风险",
            "应急",
            "预案",
        ],
    },
}


def _chinese_phrases(text: str) -> list[str]:
    phrases = []
    for raw in re.findall(r"[\u4e00-\u9fff]{4,}", str(text or "")):
        compact = _canonical_text(raw)
        if len(compact) >= 4:
            phrases.append(compact)
    return phrases[:20]


def _phrase_parts(phrase: str) -> list[str]:
    suffixes = ["表", "图", "布置图", "措施", "方案", "计划", "管理", "体系", "专项", "实施"]
    parts = {phrase}
    for suffix in suffixes:
        if phrase.endswith(suffix) and len(phrase) - len(suffix) >= 4:
            parts.add(phrase[: -len(suffix)])
    return sorted(parts, key=len, reverse=True)


def _section_key(section_path: list[str]) -> str:
    return " > ".join(_canonical_segment(part) for part in section_path)


def _canonical_segment(segment: str) -> str:
    number, title = _split_numbered_title(segment)
    return f"{number or ''}:{_canonical_text(title)}"


def _canonical_text(text: str) -> str:
    return re.sub(r"[\s　.．、，,。；;：:（）()【】\[\]_-]+", "", str(text or "")).lower()


def _split_numbered_title(title: str) -> tuple[str | None, str]:
    match = re.match(r"^\s*(?P<number>\d+(?:\.\d+)*)(?:[.．、\s]+)(?P<title>\S.*)$", str(title or ""))
    if not match:
        return None, str(title or "").strip()
    return match.group("number"), match.group("title").strip()


def _strip_heading_number(title: str) -> str:
    return _split_numbered_title(title)[1].strip()


def _format_counter(counter: dict[str, int]) -> str:
    if not counter:
        return "-"
    return "，".join(f"{key}={value}" for key, value in sorted(counter.items()))


def _library_page_range(slice_: ExcellentBidMaterialSlice) -> str:
    if slice_.start_page is None:
        return ""
    return _page_range(slice_)


def _source_name(source_paths: list[Any], index_path: Path) -> str:
    if source_paths:
        first = Path(str(source_paths[0]))
        return first.stem or str(first)
    return index_path.stem


def _index_image_count(index: dict[str, Any]) -> int:
    if index.get("image_count") is not None:
        return int(index.get("image_count") or 0)
    return int(index.get("table_image_ref_count") or 0) + int(index.get("paragraph_image_ref_count") or 0)


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


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
