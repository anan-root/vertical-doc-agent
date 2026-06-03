"""优秀标书成熟图文块索引。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "excellent_bid_text_image_block_index_v1"

PROCESS_TOPICS = {
    "测量": ["测量", "控制网", "轴线", "放线", "监测", "沉降"],
    "钢筋": ["钢筋", "套筒", "直螺纹", "箍筋", "绑扎", "马凳"],
    "模板": ["模板", "支模", "支撑", "梁模", "柱模", "板模"],
    "混凝土": ["混凝土", "浇筑", "振捣", "养护", "温控", "大体积"],
    "防水": ["防水", "卷材", "涂膜", "止水", "地下室", "屋面"],
    "脚手架": ["脚手架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网"],
    "砌体": ["砌体", "砌筑", "砌块", "灰缝", "构造柱", "拉结筋"],
    "土方基坑": ["土方", "开挖", "基坑", "支护", "降水", "护坡"],
    "机电安装": ["机电", "电气", "给排水", "暖通", "管线", "桥架"],
    "文明环保": ["文明", "环保", "扬尘", "洗车", "围挡", "垃圾"],
    "质量管理": ["质量", "验收", "检查", "通病", "创优", "样板"],
    "安全管理": ["安全", "危险源", "应急", "防护", "临边", "洞口"],
    "BIM信息化": ["BIM", "信息化", "智慧工地", "监控", "平台", "数据"],
}

PROJECT_SPECIFIC_TERMS = ["总平面", "平面布置", "进度计划", "网络图", "横道图", "踏勘", "现状", "周边"]
PROCESS_PRIMARY_TOPICS = {"测量", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体", "土方基坑", "机电安装"}
MANAGEMENT_TOPICS = {"文明环保", "质量管理", "安全管理", "BIM信息化"}
STRICT_TARGET_TOPICS = {"测量", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体", "土方基坑"}
GENERAL_ANALYSIS_TERMS = ["重点", "难点", "对策", "项目概况", "工程概况", "施工部署", "总体安排", "编制依据"]
GENERIC_QUERY_TERMS = {
    "工程",
    "施工",
    "方案",
    "技术",
    "措施",
    "方法",
    "做法",
    "示意图",
    "流程",
    "节点",
    "控制",
}
SUBTOPIC_TERMS = {
    "混凝土": ["浇筑", "振捣", "养护", "温控", "大体积", "施工缝", "收面", "试块", "泵送"],
    "防水": ["地下室", "屋面", "卷材", "涂膜", "止水", "阴阳角", "穿墙套管", "泛水", "附加层", "搭接", "闭水"],
    "钢筋": ["加工", "连接", "绑扎", "套筒", "直螺纹", "箍筋", "马凳", "搭接", "锚固"],
    "模板": ["支设", "支撑", "加固", "拆除", "梁模", "柱模", "板模", "铝合金模板"],
    "脚手架": ["搭设", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "卸料平台"],
    "砌体": ["砌筑", "砌块", "灰缝", "构造柱", "拉结筋", "开槽", "填补"],
    "测量": ["控制网", "轴线", "放线", "标高", "沉降", "监测", "垂直度"],
}
SPECIFIC_PHRASE_TERMS = {
    "混凝土": [
        "大体积", "温控", "测温", "振捣", "分层浇筑", "泵送", "施工缝", "后浇带", "养护", "收面", "试块",
    ],
    "防水": [
        "地下室", "屋面", "阴阳角", "附加层", "卷材搭接", "穿墙套管", "止水钢板", "后浇带", "变形缝", "泛水", "闭水",
    ],
    "钢筋": [
        "钢筋加工", "钢筋连接", "钢筋绑扎", "直螺纹", "套筒", "箍筋", "马凳", "锚固", "搭接", "梁板钢筋", "墙柱钢筋",
    ],
    "模板": [
        "梁柱接头", "模板拼缝", "剪力墙", "模板拆除", "支撑体系", "梁模", "柱模", "板模", "后浇带", "楼梯模板", "吊模",
    ],
    "脚手架": [
        "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "卸料平台", "悬挑", "脚手板", "扫地杆",
    ],
    "砌体": [
        "构造柱", "拉结筋", "灰缝", "砌块", "开槽", "植筋", "顶砌", "塞缝", "门窗洞口",
    ],
    "测量": [
        "控制网", "轴线", "标高", "沉降观测", "垂直度", "引测", "平面控制", "高程控制",
    ],
}


def build_text_image_block_index(material_library: dict[str, Any], *, max_blocks: int | None = None) -> dict[str, Any]:
    """从统一素材库生成图文块索引，完整素材仍留在原素材库中。"""

    assets_by_material = _items_by(material_library.get("image_assets") or [], "material_slice_id")
    groups_by_material = _items_by(material_library.get("image_groups") or [], "material_slice_id")
    blocks: list[dict[str, Any]] = []
    for slice_ in material_library.get("slices") or []:
        if not isinstance(slice_, dict):
            continue
        material_id = str(slice_.get("material_slice_id") or "")
        if not material_id:
            continue
        image_assets = assets_by_material.get(material_id, [])
        image_groups = groups_by_material.get(material_id, [])
        block = _block_from_slice(slice_, image_assets, image_groups)
        if block is None:
            continue
        for fine_block in _fine_grained_blocks_from_slice(slice_, image_assets, image_groups):
            blocks.append(_drop_internal_block_fields(fine_block))
            if max_blocks is not None and len(blocks) >= max_blocks:
                break
        if max_blocks is not None and len(blocks) >= max_blocks:
            break
        blocks.append(block)
        if max_blocks is not None and len(blocks) >= max_blocks:
            break
    topic_counts = Counter(topic for block in blocks for topic in block.get("topics") or [])
    type_counts = Counter(str(block.get("block_type") or "unknown") for block in blocks)
    source_counts = Counter(str(block.get("source_id") or "unknown") for block in blocks)
    return {
        "schema_version": SCHEMA_VERSION,
        "library_id": material_library.get("library_id"),
        "source_count": len(material_library.get("sources") or []),
        "block_count": len(blocks),
        "block_type_counts": dict(type_counts),
        "topic_counts": dict(topic_counts),
        "source_counts": dict(source_counts),
        "blocks": blocks,
        "source_filter": material_library.get("source_filter") or {},
    }


def write_text_image_block_index_outputs(
    result: dict[str, Any],
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_text_image_block_index_report(result), encoding="utf-8")


def render_text_image_block_index_report(result: dict[str, Any]) -> str:
    lines = [
        "# 优秀标书图文块索引质检报告",
        "",
        "## 汇总",
        "",
        f"- 图文块数：{result.get('block_count', 0)}",
        f"- 类型分布：{_format_counts(result.get('block_type_counts') or {})}",
        f"- 主题分布：{_format_counts(result.get('topic_counts') or {})}",
        f"- 来源分布：{_format_counts(result.get('source_counts') or {})}",
        "",
        "## 高价值图文块预览",
        "",
        "| 序号 | block_id | 类型 | 主主题 | 主题 | 图片 | 套图 | 表格 | 复用 | 来源章节 |",
        "|---:|---|---|---|---|---:|---:|---:|---|---|",
    ]
    preview = sorted(
        result.get("blocks") or [],
        key=lambda item: (
            int(item.get("image_group_count") or 0),
            int(item.get("image_count") or 0),
            int(item.get("table_count") or 0),
        ),
        reverse=True,
    )[:120]
    for index, block in enumerate(preview, start=1):
        lines.append(
            f"| {index} | {block.get('block_id')} | {block.get('block_type')} | "
            f"{block.get('primary_topic') or ''} | {'、'.join(block.get('topics') or [])} | {block.get('image_count', 0)} | "
            f"{block.get('image_group_count', 0)} | {block.get('table_count', 0)} | "
            f"{block.get('reuse_level')} | {' > '.join(block.get('section_path') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def search_text_image_blocks(
    block_index: dict[str, Any],
    *,
    query: str,
    section_path: list[str] | None = None,
    top_k: int = 5,
    min_match_level: str = "moderate",
) -> list[dict[str, Any]]:
    query_text = " ".join([query, " ".join(section_path or [])])
    target_profile = _target_topic_profile(query_text)
    scored: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
    for block in block_index.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        match = _block_match(block, query_text, target_profile, section_path or [])
        if not _match_level_allowed(match.get("match_level"), min_match_level):
            continue
        score = float(match.get("score") or 0)
        if score <= 0:
            continue
        scored.append((score, str(block.get("block_id") or ""), block, match))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [_block_candidate(block, match) for score, _, block, match in scored[: max(top_k, 0)]]


def _block_from_slice(
    slice_: dict[str, Any],
    image_assets: list[dict[str, Any]],
    image_groups: list[dict[str, Any]],
) -> dict[str, Any] | None:
    image_count = _int(slice_.get("image_count") or slice_.get("docx_image_count"))
    table_count = _int(slice_.get("table_count") or slice_.get("docx_table_count"))
    paragraph_count = _int(slice_.get("paragraph_count"))
    if image_count <= 0 and table_count <= 0:
        return None
    text = _semantic_text(slice_, image_assets, image_groups)
    topic_profile = _block_topic_profile(slice_, image_assets, image_groups)
    topics = topic_profile["topics"]
    block_type = _block_type(image_count=image_count, table_count=table_count, group_count=len(image_groups), topics=topics)
    project_specific = _contains_any(text, PROJECT_SPECIFIC_TERMS)
    reuse_level = str(slice_.get("reuse_level") or "rewrite_reuse")
    if project_specific:
        reuse_level = "manual_review"
    elif block_type in {"image_group_block", "table_image_block", "process_block"} and reuse_level == "rewrite_reuse":
        reuse_level = "parameterized_reuse"
    block_id = f"TIB-{slice_.get('material_slice_id')}"
    return {
        "block_id": block_id,
        "block_type": block_type,
        "source_id": slice_.get("source_id"),
        "source_type": slice_.get("source_type"),
        "material_slice_id": slice_.get("material_slice_id"),
        "source_slice_id": slice_.get("source_slice_id"),
        "title": slice_.get("title") or slice_.get("clean_title"),
        "section_path": slice_.get("section_path") or [],
        "topics": topics,
        "primary_topic": topic_profile.get("primary_topic"),
        "secondary_topics": topic_profile.get("secondary_topics") or [],
        "topic_confidence": topic_profile.get("confidence"),
        "topic_evidence": topic_profile.get("evidence") or [],
        "summary": _summary(slice_, image_assets, image_groups),
        "paragraph_count": paragraph_count,
        "table_count": table_count,
        "image_count": image_count,
        "image_group_count": len(image_groups),
        "image_asset_ids": [item.get("image_asset_id") for item in image_assets if item.get("image_asset_id")],
        "image_group_ids": [item.get("image_group_id") for item in image_groups if item.get("image_group_id")],
        "captions": _captions(image_assets, image_groups),
        "reuse_level": reuse_level,
        "project_specific_risk": "high" if project_specific else slice_.get("project_specific_risk") or "medium",
        "use_policy": "whole_block_preferred" if image_count or image_groups else "reference_only",
        "llm_summary": _llm_summary(slice_, image_assets, image_groups, topics, reuse_level),
        "render_policy": {
            "preserve_image_order": True,
            "preserve_image_groups": bool(image_groups),
            "single_images_are_fallback": bool(image_count and not image_groups),
            "do_not_render_if_manual_review": reuse_level == "manual_review",
        },
    }


def _fine_grained_blocks_from_slice(
    slice_: dict[str, Any],
    image_assets: list[dict[str, Any]],
    image_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    row_blocks = _method_row_blocks_from_slice(slice_, image_assets, image_groups)
    group_blocks = _method_row_group_blocks(row_blocks)
    return [*row_blocks, *group_blocks]


def _drop_internal_block_fields(block: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in block.items() if not key.startswith("_")}


def _method_row_blocks_from_slice(
    slice_: dict[str, Any],
    image_assets: list[dict[str, Any]],
    image_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for asset in image_assets:
        if asset.get("table_index") is None or asset.get("row_index") is None:
            continue
        if not _asset_can_seed_method_row_block(asset):
            continue
        key = (
            str(asset.get("material_slice_id") or slice_.get("material_slice_id") or ""),
            int(asset.get("table_index") or 0),
            int(asset.get("row_index") or 0),
        )
        rows.setdefault(key, []).append(asset)

    groups_by_id = {str(group.get("image_group_id") or ""): group for group in image_groups if isinstance(group, dict)}
    blocks: list[dict[str, Any]] = []
    for (_material_id, table_index, row_index), assets in sorted(rows.items(), key=lambda item: item[0]):
        ordered_assets = _ordered_assets(assets)
        text = _row_block_text(slice_, ordered_assets)
        topic_profile = _row_block_topic_profile(slice_, ordered_assets)
        if not topic_profile.get("primary_topic"):
            continue
        image_group_ids = _safe_group_ids_for_assets(ordered_assets, groups_by_id)
        title = _row_block_title(slice_, ordered_assets)
        render_policy = {
            "preserve_image_order": True,
            "preserve_image_groups": bool(image_group_ids),
            "single_images_are_fallback": False,
            "do_not_render_if_manual_review": False,
            "row_level_context": True,
        }
        block = _block_from_parts(
            slice_,
            block_id=f"TIBR-{slice_.get('material_slice_id')}-T{table_index}-R{row_index}",
            block_type="method_row_block",
            title=title,
            summary=_clip(text, 520),
            llm_summary=_clip(_row_block_llm_summary(slice_, title, ordered_assets, topic_profile), 360),
            image_assets=ordered_assets,
            image_group_ids=image_group_ids,
            topic_profile=topic_profile,
            table_count=1,
            use_policy="row_block_preferred",
            render_policy=render_policy,
            row_scope={
                "table_index": table_index,
                "start_row_index": row_index,
                "end_row_index": row_index,
            },
        )
        blocks.append(block)
    return blocks


def _method_row_group_blocks(row_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_table: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for block in row_blocks:
        scope = block.get("row_scope") or {}
        table_index = scope.get("table_index")
        if table_index is None:
            continue
        by_table.setdefault((str(block.get("material_slice_id") or ""), int(table_index)), []).append(block)

    result: list[dict[str, Any]] = []
    for (_material_id, table_index), blocks in by_table.items():
        ordered = sorted(blocks, key=lambda block: int((block.get("row_scope") or {}).get("start_row_index") or 0))
        cluster: list[dict[str, Any]] = []
        for block in ordered:
            if not cluster or _row_blocks_can_merge(cluster[-1], block):
                cluster.append(block)
                continue
            result.extend(_merged_row_group_blocks(cluster, table_index))
            cluster = [block]
        result.extend(_merged_row_group_blocks(cluster, table_index))
    return result


def _merged_row_group_blocks(cluster: list[dict[str, Any]], table_index: int) -> list[dict[str, Any]]:
    if len(cluster) < 2:
        return []
    if sum(_int(block.get("image_count")) for block in cluster) < 2:
        return []
    first = cluster[0]
    primary_topic = first.get("primary_topic")
    if not primary_topic or any(block.get("primary_topic") != primary_topic for block in cluster):
        return []
    image_asset_ids = _unique(
        [
            str(asset_id)
            for block in cluster
            for asset_id in block.get("image_asset_ids") or []
            if str(asset_id).strip()
        ]
    )
    image_group_ids = _unique(
        [
            str(group_id)
            for block in cluster
            for group_id in block.get("image_group_ids") or []
            if str(group_id).strip()
        ]
    )
    if len(image_asset_ids) < 2:
        return []
    scope_rows = [
        int((block.get("row_scope") or {}).get("start_row_index") or 0)
        for block in cluster
    ]
    title = _clip(first.get("title") or first.get("primary_topic") or "施工工艺图文块", 80)
    render_policy = {
        **(first.get("render_policy") or {}),
        "row_group_context": True,
        "preserve_image_order": True,
    }
    return [
        {
            **first,
            "block_id": f"TIBRG-{first.get('material_slice_id')}-T{table_index}-R{min(scope_rows)}-{max(scope_rows)}",
            "block_type": "method_row_group_block",
            "title": title,
            "summary": _clip("；".join(str(block.get("summary") or "") for block in cluster), 520),
            "llm_summary": _clip("；".join(str(block.get("llm_summary") or "") for block in cluster), 360),
            "image_count": len(image_asset_ids),
            "image_group_count": len(image_group_ids),
            "image_asset_ids": image_asset_ids,
            "image_group_ids": image_group_ids,
            "captions": _unique(
                [
                    str(caption)
                    for block in cluster
                    for caption in block.get("captions") or []
                    if str(caption).strip()
                ]
            )[:16],
            "use_policy": "row_group_block_preferred",
            "render_policy": render_policy,
            "row_scope": {
                "table_index": table_index,
                "start_row_index": min(scope_rows),
                "end_row_index": max(scope_rows),
            },
        }
    ]


def _block_from_parts(
    slice_: dict[str, Any],
    *,
    block_id: str,
    block_type: str,
    title: str,
    summary: str,
    llm_summary: str,
    image_assets: list[dict[str, Any]],
    image_group_ids: list[str],
    topic_profile: dict[str, Any],
    table_count: int,
    use_policy: str,
    render_policy: dict[str, Any],
    row_scope: dict[str, Any],
) -> dict[str, Any]:
    topics = topic_profile.get("topics") or []
    reuse_level = _row_block_reuse_level(slice_, image_assets)
    project_specific = _contains_any(
        " ".join([summary, title, " ".join(str(item) for item in slice_.get("section_path") or [])]),
        PROJECT_SPECIFIC_TERMS,
    )
    if project_specific:
        reuse_level = "manual_review"
    return {
        "block_id": block_id,
        "block_type": block_type,
        "source_id": slice_.get("source_id"),
        "source_type": slice_.get("source_type"),
        "material_slice_id": slice_.get("material_slice_id"),
        "source_slice_id": slice_.get("source_slice_id"),
        "title": title,
        "section_path": slice_.get("section_path") or [],
        "topics": topics,
        "primary_topic": topic_profile.get("primary_topic"),
        "secondary_topics": topic_profile.get("secondary_topics") or [],
        "topic_confidence": topic_profile.get("confidence"),
        "topic_evidence": topic_profile.get("evidence") or [],
        "summary": summary,
        "paragraph_count": 0,
        "table_count": table_count,
        "image_count": len(image_assets),
        "image_group_count": len(image_group_ids),
        "image_asset_ids": [item.get("image_asset_id") for item in image_assets if item.get("image_asset_id")],
        "image_group_ids": image_group_ids,
        "captions": _captions(image_assets, []),
        "reuse_level": reuse_level,
        "project_specific_risk": "high" if project_specific else "medium",
        "use_policy": use_policy,
        "llm_summary": llm_summary,
        "render_policy": render_policy,
        "row_scope": row_scope,
        "_source_image_assets": image_assets,
    }


def _asset_can_seed_method_row_block(asset: dict[str, Any]) -> bool:
    if not asset.get("image_asset_id"):
        return False
    if asset.get("table_index") is None or asset.get("row_index") is None:
        return False
    if str(asset.get("project_specific_risk") or "").lower() == "high":
        return False
    if str(asset.get("reuse_level") or "") == "manual_review":
        return False
    text = " ".join(
        str(asset.get(key) or "")
        for key in [
            "caption_actual",
            "semantic_text",
            "nearby_text",
            "cell_text",
            "row_text",
            "previous_row_text",
            "left_cell_text",
            "right_cell_text",
        ]
    )
    return bool(text.strip())


def _ordered_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        assets,
        key=lambda asset: (
            _int(asset.get("row_index")),
            _int(asset.get("cell_index")),
            str(asset.get("image_asset_id") or ""),
        ),
    )


def _row_block_text(slice_: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for asset in assets:
        parts.extend(
            [
                str(asset.get("caption_actual") or ""),
                str(asset.get("semantic_text") or ""),
                str(asset.get("cell_text") or ""),
                str(asset.get("row_text") or ""),
                str(asset.get("previous_row_text") or ""),
                " ".join(str(item) for item in asset.get("previous_row_texts") or []),
                str(asset.get("left_cell_text") or ""),
                str(asset.get("right_cell_text") or ""),
                str(asset.get("nearby_text") or ""),
            ]
        )
    parts.extend(
        [
            str(slice_.get("title") or ""),
            " > ".join(str(item) for item in slice_.get("section_path") or []),
        ]
    )
    return " ".join(part for part in parts if part)


def _row_block_topic_profile(slice_: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for asset in assets:
        row_text = " ".join(
            [
                str(asset.get("caption_actual") or ""),
                str(asset.get("semantic_text") or ""),
                str(asset.get("cell_text") or ""),
                str(asset.get("row_text") or ""),
                str(asset.get("previous_row_text") or ""),
                " ".join(str(item) for item in asset.get("previous_row_texts") or []),
                str(asset.get("left_cell_text") or ""),
                str(asset.get("right_cell_text") or ""),
                str(asset.get("nearby_text") or ""),
            ]
        )
        _add_topic_evidence(evidence, row_text, source="table_row", weight=6)
        _add_topic_evidence(evidence, str(asset.get("group_title") or ""), source="image_group", weight=4)
    _add_topic_evidence(evidence, str(slice_.get("title") or ""), source="section_title", weight=2)
    _add_topic_evidence(
        evidence,
        " > ".join(str(item) for item in slice_.get("section_path") or []),
        source="section_path",
        weight=1,
    )
    scores: Counter[str] = Counter()
    for item in evidence:
        scores[str(item["topic"])] += int(item["weight"])
    topics = [topic for topic, _ in scores.most_common(8)]
    primary_topic = _primary_topic_from_scores(scores)
    secondary_topics = [topic for topic in topics if topic != primary_topic][:5]
    top_score = scores.get(primary_topic or "", 0)
    total_score = sum(scores.values())
    confidence = round(min(0.99, top_score / max(total_score, 1) + min(top_score, 12) / 24), 4) if primary_topic else 0
    return {
        "topics": topics,
        "primary_topic": primary_topic,
        "secondary_topics": secondary_topics,
        "confidence": confidence,
        "evidence": evidence[:12],
    }


def _safe_group_ids_for_assets(assets: list[dict[str, Any]], groups_by_id: dict[str, dict[str, Any]]) -> list[str]:
    asset_ids = {str(asset.get("image_asset_id") or "") for asset in assets}
    group_ids: list[str] = []
    for asset in assets:
        group_id = str(asset.get("image_group_id") or "")
        if not group_id or group_id in group_ids:
            continue
        group = groups_by_id.get(group_id)
        group_asset_ids = {str(item) for item in (group or {}).get("image_asset_ids") or [] if str(item).strip()}
        if group_asset_ids and group_asset_ids <= asset_ids:
            group_ids.append(group_id)
    return group_ids


def _row_block_title(slice_: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    candidates = _unique(
        [
            str(asset.get("caption_actual") or asset.get("semantic_text") or "").strip()
            for asset in assets
            if str(asset.get("caption_actual") or asset.get("semantic_text") or "").strip()
        ]
    )
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return _clip("、".join(candidates[:4]), 90)
    return str(slice_.get("title") or (slice_.get("section_path") or ["施工做法图文块"])[-1])


def _row_block_llm_summary(
    slice_: dict[str, Any],
    title: str,
    assets: list[dict[str, Any]],
    topic_profile: dict[str, Any],
) -> str:
    path = " > ".join(str(item) for item in slice_.get("section_path") or [])
    captions = _captions(assets, [])
    return "；".join(
        item
        for item in [
            f"来源章节：{path}" if path else "",
            f"行级图文块：{title}",
            f"主题：{topic_profile.get('primary_topic') or '未分类'}",
            f"图片{len(assets)}张",
            "关键图名：" + "；".join(captions[:6]) if captions else "",
        ]
        if item
    )


def _row_block_reuse_level(slice_: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    levels = {str(asset.get("reuse_level") or "") for asset in assets}
    if "manual_review" in levels:
        return "manual_review"
    if levels and levels <= {"direct_reuse"}:
        return "direct_reuse"
    if str(slice_.get("reuse_level") or "") == "direct_reuse":
        return "direct_reuse"
    return "parameterized_reuse"


def _row_blocks_can_merge(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous.get("material_slice_id") != current.get("material_slice_id"):
        return False
    if previous.get("primary_topic") != current.get("primary_topic"):
        return False
    previous_scope = previous.get("row_scope") or {}
    current_scope = current.get("row_scope") or {}
    if previous_scope.get("table_index") != current_scope.get("table_index"):
        return False
    previous_end = _int(previous_scope.get("end_row_index"))
    current_start = _int(current_scope.get("start_row_index"))
    return 0 <= current_start - previous_end <= 2


def _block_candidate(block: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    return {
        "block_id": block.get("block_id"),
        "block_type": block.get("block_type"),
        "source_id": block.get("source_id"),
        "material_slice_id": block.get("material_slice_id"),
        "title": block.get("title"),
        "section_path": block.get("section_path") or [],
        "topics": block.get("topics") or [],
        "primary_topic": block.get("primary_topic"),
        "secondary_topics": block.get("secondary_topics") or [],
        "topic_confidence": block.get("topic_confidence"),
        "match_level": match.get("match_level"),
        "match_confidence": match.get("confidence"),
        "match_reasons": match.get("match_reasons") or [],
        "risk_flags": match.get("risk_flags") or [],
        "summary": block.get("llm_summary") or block.get("summary"),
        "image_count": block.get("image_count"),
        "image_group_count": block.get("image_group_count"),
        "image_asset_ids": block.get("image_asset_ids") or [],
        "image_group_ids": block.get("image_group_ids") or [],
        "table_count": block.get("table_count"),
        "captions": (block.get("captions") or [])[:8],
        "reuse_level": block.get("reuse_level"),
        "project_specific_risk": block.get("project_specific_risk"),
        "use_policy": block.get("use_policy"),
        "render_policy": block.get("render_policy") or {},
        "row_scope": block.get("row_scope") or {},
        "retrieval_score": round(float(match.get("score") or 0), 4),
    }


def _block_match(
    block: dict[str, Any],
    query_text: str,
    target_profile: dict[str, Any],
    section_path: list[str],
) -> dict[str, Any]:
    block_text = " ".join(
        [
            str(block.get("title") or ""),
            " ".join(str(item) for item in block.get("section_path") or []),
            str(block.get("summary") or ""),
            " ".join(str(item) for item in block.get("topics") or []),
            " ".join(str(item) for item in block.get("captions") or []),
        ]
    )
    target_primary = target_profile.get("primary_topic")
    target_topics = set(target_profile.get("topics") or [])
    target_subtopics = set(target_profile.get("subtopics") or [])
    block_primary = block.get("primary_topic")
    block_topics = set(block.get("topics") or [])
    match_reasons: list[str] = []
    risk_flags: list[str] = []

    title_path_text = _title_path_text(block)
    caption_text = " ".join(str(item) for item in block.get("captions") or [])
    title_caption_text = " ".join([title_path_text, caption_text])
    title_path_terms = _matched_subtopics(str(target_primary or ""), title_path_text)
    caption_terms = _matched_subtopics(str(target_primary or ""), caption_text)
    if target_profile.get("general_analysis"):
        if block_primary in PROCESS_PRIMARY_TOPICS:
            return _match_result(0, "rejected", 0, [], ["general_analysis_rejects_process_block"])
        risk_flags.append("general_analysis")
    if target_primary:
        if not _block_allowed_for_primary_topic(block, target_primary, title_caption_text):
            return _match_result(
                0,
                "rejected",
                0,
                [],
                [f"primary_topic_mismatch:{target_primary}!={block_primary or 'unknown'}"],
            )
        if block_primary == target_primary:
            match_reasons.append(f"主主题匹配：{target_primary}")
        elif target_primary in block_topics:
            match_reasons.append(f"辅主题命中：{target_primary}")
            risk_flags.append("target_topic_is_secondary")

    score = 0.0
    if target_topics:
        overlap = block_topics & target_topics
        score += len(overlap) * 5.0
        if target_primary and block_primary == target_primary:
            score += 8.0
        elif target_primary and target_primary in block_topics:
            score += 2.0
        if not overlap and block_topics:
            score -= 6.0
    for part in section_path:
        if part and part in block_text:
            score += 1.5
            match_reasons.append("章节路径命中")
    title_path_has_primary = bool(target_primary and _contains_any(title_path_text, PROCESS_TOPICS.get(str(target_primary), [])))
    for token in _loose_tokens(query_text):
        if token and token in block_text:
            score += 0.8
            if token in title_path_text:
                score += 2.2
                match_reasons.append(f"标题/路径命中：{token}")
            elif token in caption_text:
                score += 0.8
                match_reasons.append(f"题注命中：{token}")
    if title_path_has_primary:
        score += 6.0
        match_reasons.append("标题或章节路径含目标强词")
    elif target_primary and _contains_any(caption_text, PROCESS_TOPICS.get(str(target_primary), [])):
        score += 1.5
        match_reasons.append("题注含目标强词")
        risk_flags.append("primary_topic_only_from_caption")
    if target_subtopics:
        title_overlap = target_subtopics & title_path_terms
        caption_overlap = target_subtopics & caption_terms
        if title_overlap:
            score += len(title_overlap) * 3.0
            match_reasons.append("标题/路径子主题命中：" + "、".join(sorted(title_overlap)))
        if caption_overlap:
            score += len(caption_overlap) * 1.2
            match_reasons.append("题注子主题命中：" + "、".join(sorted(caption_overlap)))
        if not title_overlap and caption_overlap:
            score -= 2.5
            risk_flags.append("subtopic_only_from_caption")
        if not title_overlap and not caption_overlap and target_primary in {"混凝土", "防水"}:
            score -= 4.0
            risk_flags.append("missing_target_subtopic")
    specific_terms = _specific_query_terms(query_text, str(target_primary or ""))
    specific_hits = {term for term in specific_terms if term in title_caption_text}
    strong_specific_terms = _strong_specific_query_terms(query_text, str(target_primary or ""))
    strong_specific_hits = {term for term in strong_specific_terms if term in title_caption_text}
    score += min(_int(block.get("image_group_count")), 3) * 1.2
    score += min(_int(block.get("image_count")), 8) * 0.25
    score += min(_int(block.get("table_count")), 4) * 0.2
    score += min(float(block.get("topic_confidence") or 0), 1.0) * 2.0
    block_type = str(block.get("block_type") or "")
    if block_type == "method_row_block":
        if strong_specific_terms and not strong_specific_hits:
            score -= 18.0
            risk_flags.append("row_block_missing_strong_specific_terms")
        elif specific_terms and not specific_hits:
            score -= 10.0
            risk_flags.append("row_block_missing_specific_query_terms")
        else:
            score += 3.0 + min(len(specific_hits), 3) * 1.5 + min(len(strong_specific_hits), 3) * 2.5
            if strong_specific_hits:
                match_reasons.append("行级细主题命中：" + "、".join(sorted(strong_specific_hits)))
        match_reasons.append("行级图文块精准命中")
    elif block_type == "method_row_group_block":
        row_scope = block.get("row_scope") or {}
        row_span = _int(row_scope.get("end_row_index")) - _int(row_scope.get("start_row_index")) + 1
        if row_span > 10 or _int(block.get("image_count")) > 12:
            score -= 12.0
            risk_flags.append("row_group_too_broad")
        if strong_specific_terms and not strong_specific_hits:
            score -= 14.0
            risk_flags.append("row_group_missing_strong_specific_terms")
        elif specific_terms and not specific_hits:
            score -= 7.0
            risk_flags.append("row_group_missing_specific_query_terms")
        else:
            score += 2.5 + min(len(specific_hits), 3) * 1.3 + min(len(strong_specific_hits), 3) * 2.0
            if strong_specific_hits:
                match_reasons.append("相邻行细主题命中：" + "、".join(sorted(strong_specific_hits)))
        match_reasons.append("相邻行图文块精准命中")
    elif _int(block.get("image_count")) > 12:
        score -= 3.0
        risk_flags.append("broad_image_block")
    if block.get("reuse_level") == "manual_review":
        score -= 8.0
        risk_flags.append("manual_review")
    if target_primary and block_primary and block_primary != target_primary and block_primary in PROCESS_PRIMARY_TOPICS:
        score -= 5.0
        risk_flags.append(f"other_process_primary_topic:{block_primary}")
    confidence = _match_confidence(score)
    match_level = _match_level(score, confidence, risk_flags)
    if not match_reasons and score > 0:
        match_reasons.append("弱关键词命中")
    return _match_result(score, match_level, confidence, match_reasons, risk_flags)


def _semantic_text(slice_: dict[str, Any], image_assets: list[dict[str, Any]], image_groups: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        str(slice_.get("title") or ""),
        str(slice_.get("clean_title") or ""),
        " ".join(str(item) for item in slice_.get("section_path") or []),
        str(slice_.get("search_text") or ""),
    ]
    for paragraph in slice_.get("paragraphs") or []:
        if isinstance(paragraph, dict):
            parts.append(str(paragraph.get("text_preview") or ""))
    for asset in image_assets:
        parts.extend(
            [
                str(asset.get("semantic_text") or ""),
                str(asset.get("caption_actual") or ""),
                str(asset.get("nearby_text") or ""),
            ]
        )
    for group in image_groups:
        parts.extend([str(group.get("group_title") or ""), str(group.get("semantic_text") or "")])
    return " ".join(part for part in parts if part)


def _summary(slice_: dict[str, Any], image_assets: list[dict[str, Any]], image_groups: list[dict[str, Any]]) -> str:
    pieces: list[str] = []
    path = " > ".join(str(item) for item in slice_.get("section_path") or [] if item)
    if path:
        pieces.append(path)
    for paragraph in slice_.get("paragraphs") or []:
        if isinstance(paragraph, dict) and paragraph.get("text_preview"):
            pieces.append(str(paragraph.get("text_preview")))
        if len(" ".join(pieces)) > 260:
            break
    captions = _captions(image_assets, image_groups)
    if captions:
        pieces.append("图片/套图：" + "；".join(captions[:6]))
    return _clip(" ".join(pieces), 520)


def _llm_summary(
    slice_: dict[str, Any],
    image_assets: list[dict[str, Any]],
    image_groups: list[dict[str, Any]],
    topics: list[str],
    reuse_level: str,
) -> str:
    title = slice_.get("title") or (slice_.get("section_path") or [""])[-1]
    captions = _captions(image_assets, image_groups)
    parts = [
        f"来源章节：{' > '.join(slice_.get('section_path') or []) or title}",
        f"主题：{'、'.join(topics) if topics else '未分类'}",
        f"表格{_int(slice_.get('table_count') or slice_.get('docx_table_count'))}个，图片{_int(slice_.get('image_count') or slice_.get('docx_image_count'))}张，套图{len(image_groups)}组",
        f"复用建议：{reuse_level}",
    ]
    if captions:
        parts.append("关键图名：" + "；".join(captions[:6]))
    return _clip("；".join(parts), 360)


def _captions(image_assets: list[dict[str, Any]], image_groups: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for group in image_groups:
        values.append(str(group.get("group_title") or group.get("semantic_text") or "").strip())
        values.extend(str(item).strip() for item in group.get("captions") or [])
    for asset in image_assets:
        values.append(str(asset.get("caption_actual") or asset.get("semantic_text") or asset.get("nearby_text") or "").strip())
    return _unique([item for item in values if item])[:16]


def _block_type(*, image_count: int, table_count: int, group_count: int, topics: list[str]) -> str:
    if group_count > 0:
        return "image_group_block"
    if image_count > 0 and table_count > 0:
        return "table_image_block"
    if image_count > 0 and any(topic in {"钢筋", "模板", "混凝土", "防水", "脚手架", "测量", "砌体", "土方基坑"} for topic in topics):
        return "process_block"
    if image_count > 0:
        return "image_block"
    return "table_block"


def _block_topic_profile(
    slice_: dict[str, Any],
    image_assets: list[dict[str, Any]],
    image_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    title_text = " ".join(
        [
            str(slice_.get("title") or ""),
            str(slice_.get("clean_title") or ""),
            " ".join(str(item) for item in slice_.get("section_path") or []),
        ]
    )
    _add_topic_evidence(evidence, title_text, source="title_path", weight=5)
    for group in image_groups:
        group_text = " ".join(
            [
                str(group.get("group_title") or ""),
                str(group.get("semantic_text") or ""),
                " ".join(str(item) for item in group.get("captions") or []),
            ]
        )
        _add_topic_evidence(evidence, group_text, source="image_group", weight=4)
    for asset in image_assets:
        asset_text = " ".join(
            [
                str(asset.get("caption_actual") or ""),
                str(asset.get("semantic_text") or ""),
                str(asset.get("nearby_text") or ""),
            ]
        )
        _add_topic_evidence(evidence, asset_text, source="image_caption", weight=4)
    _add_topic_evidence(evidence, str(slice_.get("search_text") or ""), source="search_text", weight=2)
    for paragraph in slice_.get("paragraphs") or []:
        if isinstance(paragraph, dict):
            _add_topic_evidence(evidence, str(paragraph.get("text_preview") or ""), source="paragraph", weight=1)
    scores: Counter[str] = Counter()
    for item in evidence:
        scores[str(item["topic"])] += int(item["weight"])
    topics = [topic for topic, _ in scores.most_common(8)]
    primary_topic = _primary_topic_from_scores(scores)
    secondary_topics = [topic for topic in topics if topic != primary_topic][:5]
    top_score = scores.get(primary_topic or "", 0)
    total_score = sum(scores.values())
    confidence = round(min(0.99, top_score / max(total_score, 1) + min(top_score, 10) / 20), 4) if primary_topic else 0
    return {
        "topics": topics,
        "primary_topic": primary_topic,
        "secondary_topics": secondary_topics,
        "confidence": confidence,
        "evidence": evidence[:12],
    }


def _add_topic_evidence(evidence: list[dict[str, Any]], text: str, *, source: str, weight: int) -> None:
    if not text:
        return
    for topic, terms in PROCESS_TOPICS.items():
        matched = [term for term in terms if term in text]
        if not matched:
            continue
        evidence.append(
            {
                "topic": topic,
                "source": source,
                "weight": weight,
                "matched_terms": matched[:5],
            }
        )


def _primary_topic_from_scores(scores: Counter[str]) -> str | None:
    if not scores:
        return None
    process_scores = {topic: score for topic, score in scores.items() if topic in PROCESS_PRIMARY_TOPICS}
    if process_scores:
        return max(process_scores.items(), key=lambda item: (item[1], item[0]))[0]
    return scores.most_common(1)[0][0]


def _target_topic_profile(text: str) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    _add_topic_evidence(evidence, text, source="target", weight=5)
    scores: Counter[str] = Counter()
    for item in evidence:
        scores[str(item["topic"])] += int(item["weight"])
    topics = [topic for topic, _ in scores.most_common(8)]
    primary_topic = _primary_topic_from_scores(scores)
    subtopics = _matched_subtopics(str(primary_topic or ""), text)
    return {
        "topics": topics,
        "primary_topic": primary_topic,
        "subtopics": sorted(subtopics),
        "general_analysis": any(term in text for term in GENERAL_ANALYSIS_TERMS),
    }


def _block_allowed_for_primary_topic(block: dict[str, Any], target_primary: str, title_caption_text: str) -> bool:
    block_primary = str(block.get("primary_topic") or "")
    block_topics = set(block.get("topics") or [])
    if target_primary not in STRICT_TARGET_TOPICS:
        return target_primary in block_topics or block_primary in MANAGEMENT_TOPICS or not block_primary
    target_terms = PROCESS_TOPICS.get(target_primary, [])
    if block_primary == target_primary:
        return True
    if target_primary not in block_topics:
        return False
    if _contains_any(title_caption_text, target_terms):
        return True
    return False


def _title_path_text(block: dict[str, Any]) -> str:
    return " ".join(
        [
            str(block.get("title") or ""),
            " ".join(str(item) for item in block.get("section_path") or []),
        ]
    )


def _matched_subtopics(primary_topic: str, text: str) -> set[str]:
    terms = SUBTOPIC_TERMS.get(primary_topic) or []
    return {term for term in terms if term and term in text}


def _match_result(
    score: float,
    match_level: str,
    confidence: float,
    match_reasons: list[str],
    risk_flags: list[str],
) -> dict[str, Any]:
    return {
        "score": round(score, 4),
        "match_level": match_level,
        "confidence": round(confidence, 4),
        "match_reasons": _unique(match_reasons)[:8],
        "risk_flags": _unique(risk_flags)[:8],
    }


def _match_confidence(score: float) -> float:
    if score <= 0:
        return 0.0
    return min(0.98, score / 28)


def _match_level(score: float, confidence: float, risk_flags: list[str]) -> str:
    if score <= 0:
        return "rejected"
    if any(flag.startswith("primary_topic_mismatch") for flag in risk_flags):
        return "rejected"
    if any(flag in {"row_block_missing_strong_specific_terms", "row_group_missing_strong_specific_terms"} for flag in risk_flags):
        return "weak"
    if any(flag.startswith("other_process_primary_topic") for flag in risk_flags):
        return "weak"
    if "row_group_too_broad" in risk_flags:
        return "weak"
    if confidence >= 0.75 and score >= 18:
        return "strong"
    if confidence >= 0.55 and score >= 10:
        return "moderate"
    return "weak"


def _match_level_allowed(match_level: Any, min_match_level: str) -> bool:
    order = {"rejected": 0, "weak": 1, "moderate": 2, "strong": 3}
    return order.get(str(match_level), 0) >= order.get(str(min_match_level), 2)


def _topics(text: str) -> list[str]:
    result = [topic for topic, terms in PROCESS_TOPICS.items() if any(term in text for term in terms)]
    return result[:6]


def _loose_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for topic, terms in PROCESS_TOPICS.items():
        for term in terms:
            if term in text:
                tokens.append(term)
    return _unique(tokens)


def _specific_query_terms(text: str, primary_topic: str) -> set[str]:
    generic_terms = _generic_specific_terms(primary_topic)
    terms: set[str] = set()
    for term in SPECIFIC_PHRASE_TERMS.get(primary_topic, []):
        if term in text and term not in generic_terms:
            terms.add(term)
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", text):
        terms.add(token)
    for term in SUBTOPIC_TERMS.get(primary_topic, []):
        if term in text and term not in generic_terms:
            terms.add(term)
    return terms


def _strong_specific_query_terms(text: str, primary_topic: str) -> set[str]:
    generic_terms = _generic_specific_terms(primary_topic)
    strong_terms = {
        term
        for term in SPECIFIC_PHRASE_TERMS.get(primary_topic, [])
        if term in text and term not in generic_terms
    }
    for term in SUBTOPIC_TERMS.get(primary_topic, []):
        if len(term) >= 3 and term in text and term not in generic_terms:
            strong_terms.add(term)
    return strong_terms


def _generic_specific_terms(primary_topic: str) -> set[str]:
    return {primary_topic, *GENERIC_QUERY_TERMS}


def _items_by(items: list[Any], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "")
        if not value:
            continue
        result.setdefault(value, []).append(item)
    return result


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = " ".join(str(item).split())
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{key}={value}" for key, value in counts.items())


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
