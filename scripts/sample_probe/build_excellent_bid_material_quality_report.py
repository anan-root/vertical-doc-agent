"""生成优秀标书素材库质检报告。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY = ROOT / "outputs" / "json" / "excellent_bid_material_library_with_zhenggui_yunting_full.json"
DEFAULT_REPORT = ROOT / "outputs" / "reports" / "excellent_bid_material_quality_report_zhenggui_full.md"
DEFAULT_JSON = ROOT / "outputs" / "json" / "excellent_bid_material_quality_report_zhenggui_full.json"

WEAK_CAPTION_TERMS = {
    "图片",
    "图示",
    "示意图",
    "照片",
    "现场图",
    "效果图",
    "施工图示",
    "质量实例",
    "第一步",
    "第二步",
    "第三步",
    "第四步",
    "第五步",
    "第六步",
    "主要施工方法",
    "施工方法",
    "控制要点",
    "质量考评",
}

STRONG_CAPTION_TERMS = [
    "图",
    "示意",
    "做法",
    "流程",
    "节点",
    "大样",
    "详图",
    "照片",
    "布置",
    "体系",
    "组织机构",
    "控制网",
    "平面",
    "立面",
    "剖面",
    "成型",
    "绑扎",
    "支设",
    "浇筑",
    "防水",
    "脚手架",
    "防护",
    "样板",
]

PROJECT_SPECIFIC_TERMS = [
    "施工总平面",
    "总平面布置",
    "平面布置图",
    "施工进度",
    "进度计划",
    "网络图",
    "横道图",
    "现场踏勘",
    "踏勘",
    "现状照片",
    "现状图",
    "现场照片",
    "实景图",
    "航拍",
    "周边环境",
    "周边道路",
    "周边管线",
    "救援路线",
    "医院",
    "建设单位",
    "楼栋号",
]

DOMAIN_RULES: list[tuple[str, list[str]]] = [
    ("测量与监测", ["测量", "控制网", "轴线", "标高", "沉降", "监测"]),
    ("土方基坑", ["土方", "基坑", "降水", "支护", "开挖"]),
    ("钢筋工程", ["钢筋", "箍筋", "绑扎", "直螺纹"]),
    ("模板工程", ["模板", "支模", "支撑体系", "吊模"]),
    ("混凝土工程", ["混凝土", "浇筑", "振捣", "养护", "大体积"]),
    ("防水工程", ["防水", "止水", "后浇带", "施工缝", "屋面", "地下室"]),
    ("脚手架与防护", ["脚手架", "外架", "防护棚", "临边", "洞口"]),
    ("砌体工程", ["砌体", "砌筑", "构造柱", "灰缝"]),
    ("装饰装修", ["装饰", "装修", "抹灰", "涂料", "吊顶", "门窗", "地面"]),
    ("机电安装", ["机电", "给排水", "电气", "暖通", "通风", "空调", "管道", "桥架", "电缆"]),
    ("质量管理", ["质量", "创优", "样板", "三检", "通病", "验收"]),
    ("安全管理", ["安全", "危险源", "应急", "防护", "责任制"]),
    ("文明环保", ["文明", "环保", "扬尘", "绿色施工", "垃圾", "围挡", "喷淋", "洗车"]),
    ("工期进度", ["工期", "进度", "横道图", "网络图", "计划"]),
    ("资源配置", ["资源", "劳动力", "机械", "设备", "材料"]),
    ("BIM与信息化", ["BIM", "信息化", "智慧工地", "监控", "数据"]),
    ("风险管理", ["风险", "应急", "预案", "事故", "隐患"]),
    ("总平面与临设", ["总平面", "平面布置", "临设", "临时道路", "办公区", "生活区"]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成优秀标书素材库质检报告。")
    parser.add_argument("--library-json", default=str(DEFAULT_LIBRARY), help="优秀标书素材库 JSON 路径。")
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT), help="Markdown 质检报告输出路径。")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON), help="JSON 质检摘要输出路径。")
    args = parser.parse_args()

    library_path = Path(args.library_json)
    report_path = Path(args.report_output)
    json_path = Path(args.json_output)
    library = json.loads(library_path.read_text(encoding="utf-8"))

    summary = build_quality_summary(library)
    report = render_quality_report(library_path, library, summary)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Report: {report_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    print(
        "Counts: "
        f"sources={summary['overview']['source_count']}, "
        f"slices={summary['overview']['slice_count']}, "
        f"images={summary['overview']['image_asset_count']}, "
        f"groups={summary['overview']['image_group_count']}, "
        f"review_images={summary['image_quality']['review_required_count']}, "
        f"weak_caption_images={summary['image_quality']['weak_caption_count']}, "
        f"project_specific_images={summary['image_quality']['project_specific_suspect_count']}"
    )
    return 0


def build_quality_summary(library: dict[str, Any]) -> dict[str, Any]:
    sources = list(library.get("sources") or [])
    slices = list(library.get("slices") or [])
    images = list(library.get("image_assets") or [])
    groups = list(library.get("image_groups") or [])

    overview = {
        "library_id": library.get("library_id"),
        "source_count": len(sources),
        "slice_count": len(slices),
        "table_count": library.get("table_count", 0),
        "image_count": library.get("image_count", 0),
        "docx_table_count": library.get("docx_table_count", 0),
        "docx_image_count": library.get("docx_image_count", 0),
        "pdf_fallback_table_count": library.get("pdf_fallback_table_count", 0),
        "pdf_fallback_image_count": library.get("pdf_fallback_image_count", 0),
        "pdf_reference_table_like_count": library.get("pdf_reference_table_like_count", 0),
        "pdf_reference_image_count": library.get("pdf_reference_image_count", 0),
        "image_asset_count": len(images),
        "image_group_count": len(groups),
        "warnings_count": len(library.get("warnings") or []),
    }

    source_quality = [_source_summary(source, slices, images, groups) for source in sources]
    slice_quality = _slice_quality(slices)
    image_quality = _image_quality(images)
    group_quality = _group_quality(groups, images)
    domain_quality = _domain_quality(slices, images, groups)
    issue_examples = _issue_examples(slices, images, groups)

    return {
        "overview": overview,
        "source_quality": source_quality,
        "slice_quality": slice_quality,
        "image_quality": image_quality,
        "group_quality": group_quality,
        "domain_quality": domain_quality,
        "issue_examples": issue_examples,
        "recommendations": _recommendations(slice_quality, image_quality, group_quality, domain_quality),
    }


def _source_summary(
    source: dict[str, Any],
    slices: list[dict[str, Any]],
    images: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    source_id = source.get("source_id")
    source_slices = [item for item in slices if item.get("source_id") == source_id]
    source_images = [item for item in images if item.get("source_id") == source_id]
    source_groups = [item for item in groups if item.get("source_id") == source_id]
    review_images = [item for item in source_images if item.get("review_required")]
    weak_caption_images = [item for item in source_images if _is_weak_caption(item.get("caption_actual"))]
    project_images = [item for item in source_images if _is_project_specific_image(item)]
    reusable_images = [
        item
        for item in source_images
        if item.get("reuse_level") in {"direct_reuse", "candidate_reuse"}
        and item.get("project_specific_risk") != "high"
        and not item.get("review_required")
    ]
    return {
        "source_id": source_id,
        "source_name": source.get("source_name"),
        "source_type": source.get("source_type"),
        "slice_count": len(source_slices),
        "table_count": int(source.get("table_count") or 0),
        "image_count": int(source.get("image_count") or len(source_images)),
        "group_count": len(source_groups),
        "high_quality_slice_count": sum(1 for item in source_slices if item.get("material_quality") == "high"),
        "manual_review_slice_count": sum(1 for item in source_slices if item.get("reuse_level") == "manual_review"),
        "review_image_count": len(review_images),
        "weak_caption_image_count": len(weak_caption_images),
        "project_specific_image_count": len(project_images),
        "auto_reusable_image_count": len(reusable_images),
        "fallback_count": source.get("fallback_count", 0),
        "unmatched_count": source.get("unmatched_count", 0),
        "warning_count": len(source.get("warnings") or []),
    }


def _slice_quality(slices: list[dict[str, Any]]) -> dict[str, Any]:
    quality_counts = Counter(str(item.get("material_quality") or "unknown") for item in slices)
    reuse_counts = Counter(str(item.get("reuse_level") or "unknown") for item in slices)
    risk_counts = Counter(str(item.get("project_specific_risk") or "unknown") for item in slices)
    empty_slices = [
        item
        for item in slices
        if int(item.get("paragraph_char_count") or 0) < 80
        and int(item.get("table_count") or 0) == 0
        and int(item.get("image_count") or 0) == 0
    ]
    rich_slices = sorted(
        slices,
        key=lambda item: (
            int(item.get("table_count") or 0) + int(item.get("image_count") or 0),
            int(item.get("paragraph_char_count") or 0),
        ),
        reverse=True,
    )[:30]
    return {
        "quality_counts": dict(sorted(quality_counts.items())),
        "reuse_counts": dict(sorted(reuse_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "empty_or_weak_slice_count": len(empty_slices),
        "rich_slice_examples": [_slice_brief(item) for item in rich_slices],
        "weak_slice_examples": [_slice_brief(item) for item in empty_slices[:30]],
    }


def _image_quality(images: list[dict[str, Any]]) -> dict[str, Any]:
    reuse_counts = Counter(str(item.get("reuse_level") or "unknown") for item in images)
    risk_counts = Counter(str(item.get("project_specific_risk") or "unknown") for item in images)
    confidence_counts = Counter(_confidence_bucket(item.get("semantic_confidence")) for item in images)
    review_images = [item for item in images if item.get("review_required")]
    review_reason_counts = Counter(_review_reason_bucket(item) for item in review_images)
    missing_caption = [item for item in images if not _clean(item.get("caption_actual"))]
    weak_caption = [item for item in images if _is_weak_caption(item.get("caption_actual"))]
    project_specific = [item for item in images if _is_project_specific_image(item)]
    grouped_images = [item for item in images if item.get("image_group_id")]
    auto_reusable = [
        item
        for item in images
        if item.get("reuse_level") in {"direct_reuse", "candidate_reuse"}
        and item.get("project_specific_risk") != "high"
        and not item.get("review_required")
    ]
    return {
        "reuse_counts": dict(sorted(reuse_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "semantic_confidence_counts": dict(sorted(confidence_counts.items())),
        "review_required_count": len(review_images),
        "review_reason_counts": dict(sorted(review_reason_counts.items())),
        "missing_caption_count": len(missing_caption),
        "weak_caption_count": len(weak_caption),
        "project_specific_suspect_count": len(project_specific),
        "grouped_image_count": len(grouped_images),
        "ungrouped_image_count": len(images) - len(grouped_images),
        "auto_reusable_image_count": len(auto_reusable),
        "auto_reusable_ratio": _ratio(len(auto_reusable), len(images)),
        "review_examples": [_image_brief(item) for item in review_images[:30]],
        "weak_caption_examples": [_image_brief(item) for item in weak_caption[:30]],
        "project_specific_examples": [_image_brief(item) for item in project_specific[:30]],
    }


def _group_quality(groups: list[dict[str, Any]], images: list[dict[str, Any]]) -> dict[str, Any]:
    size_counts = Counter(int(item.get("member_count") or 0) for item in groups)
    detection_counts = Counter(str(item.get("detection_method") or "unknown") for item in groups)
    review_groups = [item for item in groups if item.get("review_required")]
    must_keep = [item for item in groups if item.get("must_keep_together")]
    large_groups = [item for item in groups if int(item.get("member_count") or 0) >= 4]
    image_group_ids = {item.get("image_group_id") for item in images if item.get("image_group_id")}
    empty_groups = [item for item in groups if item.get("image_group_id") not in image_group_ids]
    return {
        "size_counts": dict(sorted(size_counts.items())),
        "detection_counts": dict(sorted(detection_counts.items())),
        "review_required_count": len(review_groups),
        "must_keep_together_count": len(must_keep),
        "large_group_count": len(large_groups),
        "empty_group_count": len(empty_groups),
        "large_group_examples": [_group_brief(item) for item in large_groups[:40]],
        "review_group_examples": [_group_brief(item) for item in review_groups[:30]],
    }


def _domain_quality(
    slices: list[dict[str, Any]],
    images: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    domain_map: dict[str, dict[str, Any]] = {
        name: {
            "domain": name,
            "slice_count": 0,
            "table_count": 0,
            "image_count": 0,
            "group_count": 0,
            "auto_reusable_image_count": 0,
            "review_image_count": 0,
            "high_risk_image_count": 0,
            "examples": [],
        }
        for name, _ in DOMAIN_RULES
    }
    domain_map["其他"] = {
        "domain": "其他",
        "slice_count": 0,
        "table_count": 0,
        "image_count": 0,
        "group_count": 0,
        "auto_reusable_image_count": 0,
        "review_image_count": 0,
        "high_risk_image_count": 0,
        "examples": [],
    }

    for item in slices:
        domain = _domain_for_text(_slice_text(item))
        bucket = domain_map[domain]
        bucket["slice_count"] += 1
        bucket["table_count"] += int(item.get("table_count") or 0)
        bucket["image_count"] += int(item.get("image_count") or 0)
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(_slice_brief(item))

    for item in images:
        domain = _domain_for_text(_image_text(item))
        bucket = domain_map[domain]
        if item.get("review_required"):
            bucket["review_image_count"] += 1
        if item.get("project_specific_risk") == "high":
            bucket["high_risk_image_count"] += 1
        if (
            item.get("reuse_level") in {"direct_reuse", "candidate_reuse"}
            and item.get("project_specific_risk") != "high"
            and not item.get("review_required")
        ):
            bucket["auto_reusable_image_count"] += 1

    for item in groups:
        domain = _domain_for_text(_group_text(item))
        domain_map[domain]["group_count"] += 1

    result = list(domain_map.values())
    result.sort(key=lambda item: (item["slice_count"], item["image_count"], item["table_count"]), reverse=True)
    return result


def _issue_examples(
    slices: list[dict[str, Any]],
    images: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    high_risk_slices = [item for item in slices if item.get("project_specific_risk") == "high"]
    manual_slices = [item for item in slices if item.get("reuse_level") == "manual_review"]
    no_material_slices = [
        item
        for item in slices
        if int(item.get("table_count") or 0) == 0
        and int(item.get("image_count") or 0) == 0
        and int(item.get("paragraph_char_count") or 0) < 120
    ]
    duplicate_caption_groups = _duplicate_caption_group_examples(groups)
    return {
        "high_risk_slice_examples": [_slice_brief(item) for item in high_risk_slices[:30]],
        "manual_review_slice_examples": [_slice_brief(item) for item in manual_slices[:30]],
        "weak_material_slice_examples": [_slice_brief(item) for item in no_material_slices[:30]],
        "duplicate_caption_group_examples": duplicate_caption_groups[:30],
    }


def _recommendations(
    slice_quality: dict[str, Any],
    image_quality: dict[str, Any],
    group_quality: dict[str, Any],
    domain_quality: list[dict[str, Any]],
) -> list[str]:
    recommendations = []
    if image_quality["review_required_count"]:
        recommendations.append(
            f"优先治理 {image_quality['review_required_count']} 张需复核图片，重点处理弱题注、项目专属图和缺少邻近语义的图片。"
        )
    if image_quality["weak_caption_count"]:
        recommendations.append(
            f"对 {image_quality['weak_caption_count']} 张弱题注图片执行题注清洗与重写，避免生成时只看到“示意图/第一步”等泛化标题。"
        )
    if group_quality["large_group_count"]:
        recommendations.append(
            f"抽查 {group_quality['large_group_count']} 组 4 张及以上套图，确认套图边界和组名准确，生成阶段应整组使用。"
        )
    sparse_domains = [
        item
        for item in domain_quality
        if item["slice_count"] > 0 and item["auto_reusable_image_count"] == 0 and item["domain"] != "其他"
    ]
    if sparse_domains:
        names = "、".join(item["domain"] for item in sparse_domains[:8])
        recommendations.append(f"补强或人工确认以下领域的可自动复用图片：{names}。")
    if slice_quality["empty_or_weak_slice_count"]:
        recommendations.append(
            f"清理 {slice_quality['empty_or_weak_slice_count']} 个内容较弱切片，避免检索时召回空标题、空章节或无效小节。"
        )
    recommendations.append("新增优秀标书入库后，先跑本质检报告，再进入正文生成，避免低质量图片和弱切片污染生成结果。")
    return recommendations


def render_quality_report(library_path: Path, library: dict[str, Any], summary: dict[str, Any]) -> str:
    overview = summary["overview"]
    lines = [
        "# 优秀标书素材库质检报告",
        "",
        "## 一、报告对象",
        "",
        f"- 素材库文件：`{library_path}`",
        f"- 素材库 ID：`{overview['library_id']}`",
        f"- 来源文件数：{overview['source_count']}",
        f"- 章节素材切片数：{overview['slice_count']}",
        f"- 图片资产数：{overview['image_asset_count']}",
        f"- 套图组数：{overview['image_group_count']}",
        f"- 表格素材数：{overview['table_count']}，其中 DOCX 精确表格 {overview['docx_table_count']}，PDF 兜底表格 {overview['pdf_fallback_table_count']}",
        f"- 图片素材数：{overview['image_count']}，其中 DOCX 精确图片 {overview['docx_image_count']}，PDF 兜底图片 {overview['pdf_fallback_image_count']}",
        "",
        "## 二、总体结论",
        "",
    ]
    lines.extend(_overall_conclusion(summary))

    lines.extend(["", "## 三、来源质检", ""])
    lines.extend(
        [
            "| 来源 | 类型 | 切片 | 表格 | 图片 | 套图 | 高质量切片 | 自动可用图片 | 需复核图片 | 弱题注图片 | 项目专属疑似图 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary["source_quality"]:
        lines.append(
            f"| {item['source_id']} {item['source_name']} | {item['source_type']} | {item['slice_count']} | "
            f"{item['table_count']} | {item['image_count']} | {item['group_count']} | "
            f"{item['high_quality_slice_count']} | {item['auto_reusable_image_count']} | "
            f"{item['review_image_count']} | {item['weak_caption_image_count']} | {item['project_specific_image_count']} |"
        )

    lines.extend(["", "## 四、章节素材质量", ""])
    lines.append(f"- 素材质量分布：{_format_counter(summary['slice_quality']['quality_counts'])}")
    lines.append(f"- 复用等级分布：{_format_counter(summary['slice_quality']['reuse_counts'])}")
    lines.append(f"- 项目专属风险分布：{_format_counter(summary['slice_quality']['risk_counts'])}")
    lines.append(f"- 内容较弱切片：{summary['slice_quality']['empty_or_weak_slice_count']}")
    lines.extend(["", "### 富素材切片示例", ""])
    lines.extend(_slice_table(summary["slice_quality"]["rich_slice_examples"][:15]))
    lines.extend(["", "### 内容较弱切片示例", ""])
    lines.extend(_slice_table(summary["slice_quality"]["weak_slice_examples"][:15]))

    lines.extend(["", "## 五、图片资产质量", ""])
    image_quality = summary["image_quality"]
    lines.append(f"- 图片复用等级分布：{_format_counter(image_quality['reuse_counts'])}")
    lines.append(f"- 图片项目专属风险分布：{_format_counter(image_quality['risk_counts'])}")
    lines.append(f"- 图片语义置信度分布：{_format_counter(image_quality['semantic_confidence_counts'])}")
    lines.append(f"- 可自动复用图片：{image_quality['auto_reusable_image_count']}，占比 {image_quality['auto_reusable_ratio']}")
    lines.append(f"- 需人工复核图片：{image_quality['review_required_count']}")
    lines.append(f"- 需复核原因分布：{_format_counter(image_quality['review_reason_counts'])}")
    lines.append(f"- 缺失题注图片：{image_quality['missing_caption_count']}")
    lines.append(f"- 弱题注图片：{image_quality['weak_caption_count']}")
    lines.append(f"- 疑似项目专属图片：{image_quality['project_specific_suspect_count']}")
    lines.append(f"- 已归入套图图片：{image_quality['grouped_image_count']}，未归入套图图片：{image_quality['ungrouped_image_count']}")
    lines.extend(["", "### 需复核图片示例", ""])
    lines.extend(_image_table(image_quality["review_examples"][:15]))
    lines.extend(["", "### 弱题注图片示例", ""])
    lines.extend(_image_table(image_quality["weak_caption_examples"][:15]))
    lines.extend(["", "### 项目专属疑似图片示例", ""])
    lines.extend(_image_table(image_quality["project_specific_examples"][:15]))

    lines.extend(["", "## 六、套图质量", ""])
    group_quality = summary["group_quality"]
    lines.append(f"- 套图尺寸分布：{_format_counter(group_quality['size_counts'])}")
    lines.append(f"- 套图识别方式分布：{_format_counter(group_quality['detection_counts'])}")
    lines.append(f"- 必须整组使用的套图：{group_quality['must_keep_together_count']}")
    lines.append(f"- 4 张及以上大套图：{group_quality['large_group_count']}")
    lines.append(f"- 需人工复核套图：{group_quality['review_required_count']}")
    lines.append(f"- 空套图组：{group_quality['empty_group_count']}")
    lines.extend(["", "### 大套图示例", ""])
    lines.extend(_group_table(group_quality["large_group_examples"][:20]))
    lines.extend(["", "### 疑似重复题注套图示例", ""])
    lines.extend(_group_table(summary["issue_examples"]["duplicate_caption_group_examples"][:20]))

    lines.extend(["", "## 七、专业领域覆盖", ""])
    lines.extend(
        [
            "| 领域 | 切片 | 表格 | 图片 | 套图 | 自动可用图片 | 需复核图片 | 高风险图片 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary["domain_quality"]:
        if not any(item[key] for key in ["slice_count", "table_count", "image_count", "group_count"]):
            continue
        lines.append(
            f"| {item['domain']} | {item['slice_count']} | {item['table_count']} | {item['image_count']} | "
            f"{item['group_count']} | {item['auto_reusable_image_count']} | {item['review_image_count']} | "
            f"{item['high_risk_image_count']} |"
        )

    lines.extend(["", "## 八、治理建议", ""])
    for index, item in enumerate(summary["recommendations"], start=1):
        lines.append(f"{index}. {item}")

    lines.extend(["", "## 九、建议人工抽查口径", ""])
    lines.extend(
        [
            "- 先抽查 4 张及以上套图，确认组名、边界和顺序是否正确。",
            "- 再抽查弱题注图片，重点看“第一步、第二步、示意图、现场图”等是否需要重写为正式图名。",
            "- 对项目专属疑似图片执行人工确认，特别是总平面图、进度图、现场照片、救援路线、医院路线等。",
            "- 对没有自动可用图片的专业领域，优先从优秀标书中补充或重新标注图片。",
            "- 对内容较弱切片进行清理，避免只有标题或少量文字的切片参与检索。",
        ]
    )

    lines.append("")
    return "\n".join(lines)


def _overall_conclusion(summary: dict[str, Any]) -> list[str]:
    overview = summary["overview"]
    image_quality = summary["image_quality"]
    group_quality = summary["group_quality"]
    slice_quality = summary["slice_quality"]
    conclusions = [
        f"- 当前素材库已经具备整本生成可用的规模：{overview['slice_count']} 个章节切片、{overview['table_count']} 个表格、{overview['image_asset_count']} 张图片、{overview['image_group_count']} 个套图组。",
        f"- 图片侧仍是主要质量风险：{image_quality['review_required_count']} 张图片需要复核，{image_quality['weak_caption_count']} 张图片题注偏弱，{image_quality['project_specific_suspect_count']} 张图片疑似项目专属。",
        f"- 套图识别已经形成基础能力：共有 {overview['image_group_count']} 个套图组，其中 {group_quality['large_group_count']} 组为 4 张及以上大套图，应作为后续生成整组复用的重点对象。",
        f"- 章节素材以可改写复用为主：复用等级分布为 {_format_counter(slice_quality['reuse_counts'])}，说明正文生成仍应以“参考后重写 + 局部直接复用”为主。",
    ]
    if overview["pdf_fallback_image_count"] or overview["pdf_fallback_table_count"]:
        conclusions.append(
            f"- PDF 兜底素材仍需人工关注：PDF 兜底表格 {overview['pdf_fallback_table_count']}、兜底图片 {overview['pdf_fallback_image_count']}，建议优先使用 DOCX 精确素材。"
        )
    return conclusions


def _slice_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| 切片 | 来源 | 章节 | 质量 | 复用 | 风险 | 表格 | 图片 |", "|---|---|---|---|---|---|---:|---:|"]
    if not items:
        lines.append("| - | - | - | - | - | - | - | - |")
        return lines
    for item in items:
        lines.append(
            f"| {item['material_slice_id']} | {item['source_id']} | {_escape(item['path'])} | "
            f"{item['quality']} | {item['reuse']} | {item['risk']} | {item['table_count']} | {item['image_count']} |"
        )
    return lines


def _image_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| 图片 | 来源 | 章节 | 题注 | 复用 | 风险 | 原因 |", "|---|---|---|---|---|---|---|"]
    if not items:
        lines.append("| - | - | - | - | - | - | - |")
        return lines
    for item in items:
        lines.append(
            f"| {item['image_asset_id']} | {item['source_id']} | {_escape(item['path'])} | {_escape(item['caption'])} | "
            f"{item['reuse']} | {item['risk']} | {_escape(item['reason'])} |"
        )
    return lines


def _group_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| 套图 | 来源 | 张数 | 章节 | 组名 | 复用 | 风险 |", "|---|---|---:|---|---|---|---|"]
    if not items:
        lines.append("| - | - | - | - | - | - | - |")
        return lines
    for item in items:
        lines.append(
            f"| {item['image_group_id']} | {item['source_id']} | {item['member_count']} | {_escape(item['path'])} | "
            f"{_escape(item['title'])} | {item['reuse']} | {item['risk']} |"
        )
    return lines


def _slice_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_slice_id": item.get("material_slice_id"),
        "source_id": item.get("source_id"),
        "path": " > ".join(str(part) for part in item.get("section_path") or []) or str(item.get("title") or ""),
        "quality": item.get("material_quality"),
        "reuse": item.get("reuse_level"),
        "risk": item.get("project_specific_risk"),
        "table_count": int(item.get("table_count") or 0),
        "image_count": int(item.get("image_count") or 0),
    }


def _image_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_asset_id": item.get("image_asset_id"),
        "source_id": item.get("source_id"),
        "path": " > ".join(str(part) for part in item.get("section_path") or []) or str(item.get("title") or ""),
        "caption": item.get("caption_actual") or item.get("semantic_text") or "-",
        "reuse": item.get("reuse_level"),
        "risk": item.get("project_specific_risk"),
        "reason": item.get("review_reason") or _image_issue_reason(item),
    }


def _group_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_group_id": item.get("image_group_id"),
        "source_id": item.get("source_id"),
        "member_count": int(item.get("member_count") or 0),
        "path": " > ".join(str(part) for part in item.get("section_path") or []) or str(item.get("title") or ""),
        "title": item.get("group_title") or item.get("semantic_text") or "-",
        "reuse": item.get("reuse_level"),
        "risk": item.get("project_specific_risk"),
    }


def _duplicate_caption_group_examples(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in groups:
        captions = [_clean(value) for value in item.get("captions") or [] if _clean(value)]
        if len(captions) < 2:
            continue
        counts = Counter(captions)
        if counts and counts.most_common(1)[0][1] >= 2:
            result.append(_group_brief(item))
    result.sort(key=lambda item: item["member_count"], reverse=True)
    return result


def _image_issue_reason(item: dict[str, Any]) -> str:
    if item.get("project_specific_risk") == "high" or _is_project_specific_image(item):
        return "疑似项目专属图"
    if not _clean(item.get("caption_actual")):
        return "缺失题注"
    if _is_weak_caption(item.get("caption_actual")):
        return "题注偏弱"
    if not _clean(item.get("nearby_text")):
        return "缺少邻近语义"
    return "-"


def _review_reason_bucket(item: dict[str, Any]) -> str:
    reason = _clean(item.get("review_reason"))
    if "高风险" in reason or "项目事实" in reason or item.get("project_specific_risk") == "high":
        return "项目专属/高风险"
    if "说明过于泛化" in reason or _is_weak_caption(item.get("caption_actual")):
        return "题注偏弱"
    if "未提取" in reason or not _clean(item.get("caption_actual")):
        return "缺失题注"
    if "邻近文字" in reason or not _clean(item.get("nearby_text")):
        return "缺少邻近语义"
    return "其他"


def _domain_for_text(text: str) -> str:
    compact = _canonical(text)
    for name, terms in DOMAIN_RULES:
        if any(_canonical(term) in compact for term in terms):
            return name
    return "其他"


def _slice_text(item: dict[str, Any]) -> str:
    parts = [item.get("title"), item.get("clean_title"), " ".join(item.get("section_path") or [])]
    parts.extend(str(value) for value in item.get("keywords") or [])
    return " ".join(_clean(part) for part in parts)


def _image_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("caption_actual"),
        item.get("semantic_text"),
        item.get("nearby_text"),
        item.get("group_title"),
        item.get("group_semantic_text"),
        " ".join(item.get("section_path") or []),
    ]
    return " ".join(_clean(part) for part in parts)


def _group_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("group_title"),
        item.get("semantic_text"),
        item.get("nearby_text"),
        " ".join(item.get("captions") or []),
        " ".join(item.get("section_path") or []),
    ]
    return " ".join(_clean(part) for part in parts)


def _is_project_specific_image(item: dict[str, Any]) -> bool:
    text = _image_text(item)
    compact = _canonical(text)
    return bool(item.get("project_specific_risk") == "high" or any(_canonical(term) in compact for term in PROJECT_SPECIFIC_TERMS))


def _is_weak_caption(value: Any) -> bool:
    text = _clean(value)
    compact = _canonical(text)
    if not compact:
        return False
    if compact in {_canonical(term) for term in WEAK_CAPTION_TERMS}:
        return True
    if re.fullmatch(r"第?[一二三四五六七八九十百\d]+步", compact):
        return True
    if _looks_like_heading_caption(text):
        return True
    parts = [part for part in re.split(r"[|、，,/\s]+", text) if part.strip()]
    return bool(parts) and all(re.fullmatch(r"第?[一二三四五六七八九十百\d]+步", _canonical(part)) for part in parts)


def _looks_like_heading_caption(text: str) -> bool:
    value = _clean(text)
    if not value:
        return False
    if any(term in value for term in STRONG_CAPTION_TERMS):
        return False
    numbered = re.match(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fffA-Za-z].*$", value)
    if numbered and len(value) <= 28:
        return True
    if len(value) <= 12 and any(term in value for term in ["工程", "措施", "方法", "方案", "考评"]):
        return True
    return False


def _confidence_bucket(value: Any) -> str:
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        score = 0
    if score >= 0.85:
        return "high>=0.85"
    if score >= 0.65:
        return "medium>=0.65"
    if score > 0:
        return "low>0"
    return "missing"


def _ratio(part: int, total: int) -> str:
    if not total:
        return "0.00%"
    return f"{part / total * 100:.2f}%"


def _format_counter(counter: dict[Any, Any]) -> str:
    if not counter:
        return "-"
    return "，".join(f"{key}={value}" for key, value in sorted(counter.items(), key=lambda item: str(item[0])))


def _canonical(value: Any) -> str:
    return re.sub(r"[\s。．.、，,；;：:（）()\[\]【】_\-]+", "", str(value or "")).lower()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _escape(value: Any) -> str:
    return _clean(value).replace("|", "\\|").replace("\n", " ")[:120]


if __name__ == "__main__":
    raise SystemExit(main())
