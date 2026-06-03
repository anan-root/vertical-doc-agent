"""检查整本技术标 Word 初稿的结构质量，不依赖 LibreOffice 渲染。"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCX = ROOT / "outputs" / "docx" / "full_bid_draft_full50_quality_recheck.docx"
DEFAULT_EXPORT_JSON = ROOT / "outputs" / "json" / "full_bid_export_result_full50_quality_recheck.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "reports" / "docx_structural_quality_full50_quality_recheck.md"

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

EMU_PER_CM = 360000


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 DOCX 内部图片、表格、占位符等结构质量。")
    parser.add_argument("--docx", default=str(DEFAULT_DOCX), help="待检查的 Word 文件。")
    parser.add_argument("--export-json", default=str(DEFAULT_EXPORT_JSON), help="整本技术标导出结果 JSON。")
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT), help="Markdown 报告输出路径。")
    parser.add_argument("--max-width-cm", type=float, default=15.8, help="建议图片最大宽度。")
    parser.add_argument("--max-height-cm", type=float, default=10.5, help="建议图片最大高度。")
    args = parser.parse_args()

    docx_path = Path(args.docx)
    export_json_path = Path(args.export_json)
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)

    result = analyze_docx(docx_path, export_json_path, args.max_width_cm, args.max_height_cm)
    output.write_text(render_report(result), encoding="utf-8")

    print(f"Report: {output.resolve()}")
    print(
        "media={media_count}, drawings={drawing_count}, tables={table_count}, "
        "missing_media={missing_target_count}, reused_media={reused_target_group_count}, "
        "oversized={oversized_image_count}, placeholders={placeholder_hit_count}".format(**result)
    )
    return 1 if result["missing_target_count"] else 0


def analyze_docx(
    docx_path: Path,
    export_json_path: Path,
    max_width_cm: float,
    max_height_cm: float,
) -> dict[str, Any]:
    with zipfile.ZipFile(docx_path, "r") as zf:
        media_names = sorted(name for name in zf.namelist() if name.startswith("word/media/"))
        media_sizes = {name: len(zf.read(name)) for name in media_names}
        media_hashes: dict[str, list[str]] = defaultdict(list)
        for name in media_names:
            media_hashes[hashlib.sha256(zf.read(name)).hexdigest()].append(name)
        document_xml = zf.read("word/document.xml")
        rels_xml = zf.read("word/_rels/document.xml.rels")

    document_root = ET.fromstring(document_xml)
    rel_targets = _parse_relationship_targets(rels_xml)
    paragraphs = document_root.findall(".//w:p", NS)
    tables = document_root.findall(".//w:tbl", NS)
    images = _collect_drawings(document_root, rel_targets)

    placeholder_hits = []
    for index, paragraph in enumerate(paragraphs, start=1):
        text = _text_of(paragraph).strip()
        if "待补充" in text:
            placeholder_hits.append({"paragraph_index": index, "text": text[:180]})

    used_media = {image["target"] for image in images if image["target"]}
    missing_targets = [image for image in images if image["target"] and image["target"] not in media_sizes]
    unused_media = sorted(set(media_names) - used_media)
    duplicate_hash_groups = [names for names in media_hashes.values() if len(names) > 1]

    by_target: dict[str, list[int]] = defaultdict(list)
    for image in images:
        if image["target"]:
            by_target[image["target"]].append(image["index"])
    reused_target_groups = {
        target: indexes for target, indexes in by_target.items() if len(indexes) > 1
    }

    oversized_images = [
        image
        for image in images
        if image["width_cm"] > max_width_cm or image["height_cm"] > max_height_cm
    ]
    very_large_images = [
        image for image in images if image["width_cm"] > 17.0 or image["height_cm"] > 12.0
    ]

    export_summary = {}
    if export_json_path.exists():
        export_data = json.loads(export_json_path.read_text(encoding="utf-8"))
        export_summary = export_data.get("full_bid_export_summary", {})

    target_extensions = Counter(Path(name).suffix.lower() for name in media_names)

    return {
        "docx_path": str(docx_path),
        "docx_size_mb": docx_path.stat().st_size / 1024 / 1024,
        "export_summary": export_summary,
        "media_count": len(media_names),
        "media_extensions": dict(sorted(target_extensions.items())),
        "drawing_count": len(images),
        "table_count": len(tables),
        "paragraph_count": len(paragraphs),
        "missing_target_count": len(missing_targets),
        "unused_media_count": len(unused_media),
        "duplicate_hash_group_count": len(duplicate_hash_groups),
        "reused_target_group_count": len(reused_target_groups),
        "oversized_image_count": len(oversized_images),
        "very_large_image_count": len(very_large_images),
        "placeholder_hit_count": len(placeholder_hits),
        "missing_targets": missing_targets[:80],
        "unused_media": unused_media[:80],
        "duplicate_hash_groups": duplicate_hash_groups[:40],
        "reused_target_groups": _top_reused_targets(reused_target_groups),
        "oversized_images": oversized_images[:80],
        "very_large_images": very_large_images[:80],
        "placeholder_hits": placeholder_hits[:80],
    }


def _parse_relationship_targets(rels_xml: bytes) -> dict[str, str]:
    rel_targets: dict[str, str] = {}
    root = ET.fromstring(rels_xml)
    for rel in root:
        relationship_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not relationship_id or not target:
            continue
        if not target.startswith("word/"):
            target = "word/" + target.lstrip("/")
        rel_targets[relationship_id] = target
    return rel_targets


def _collect_drawings(root: ET.Element, rel_targets: dict[str, str]) -> list[dict[str, Any]]:
    images = []
    for index, drawing in enumerate(root.findall(".//w:drawing", NS), start=1):
        extent = drawing.find(".//wp:extent", NS)
        blip = drawing.find(".//a:blip", NS)
        docpr = drawing.find(".//wp:docPr", NS)
        embed_key = f"{{{NS['r']}}}embed"
        relationship_id = blip.attrib.get(embed_key, "") if blip is not None else ""
        width_cm = 0.0
        height_cm = 0.0
        if extent is not None:
            width_cm = int(extent.attrib.get("cx", 0)) / EMU_PER_CM
            height_cm = int(extent.attrib.get("cy", 0)) / EMU_PER_CM
        images.append(
            {
                "index": index,
                "relationship_id": relationship_id,
                "target": rel_targets.get(relationship_id, ""),
                "width_cm": width_cm,
                "height_cm": height_cm,
                "name": docpr.attrib.get("name", "") if docpr is not None else "",
                "description": docpr.attrib.get("descr", "") if docpr is not None else "",
            }
        )
    return images


def _text_of(element: ET.Element) -> str:
    return "".join(text.text or "" for text in element.findall(".//w:t", NS))


def _top_reused_targets(reused_target_groups: dict[str, list[int]]) -> list[dict[str, Any]]:
    rows = []
    for target, indexes in sorted(reused_target_groups.items(), key=lambda item: len(item[1]), reverse=True):
        rows.append({"target": target, "count": len(indexes), "indexes": indexes[:40]})
    return rows[:80]


def render_report(result: dict[str, Any]) -> str:
    summary = result.get("export_summary", {})
    lines = [
        "# Word 结构质量检查报告",
        "",
        f"- 文件：`{result['docx_path']}`",
        f"- DOCX 大小：{result['docx_size_mb']:.2f} MB",
        (
            f"- 生成覆盖：{summary.get('generated_package_count', '-')}/"
            f"{summary.get('package_count', '-')}，覆盖率：{summary.get('coverage_ratio', '-')}"
        ),
        (
            f"- 导出统计：段落 {summary.get('paragraph_count', '-')}，"
            f"表格 {summary.get('table_count', '-')}，图片引用 {summary.get('image_ref_count', '-')}，"
            f"已渲染图片 {summary.get('rendered_image_count', '-')}，"
            f"缺失图片 {summary.get('missing_image_count', '-')}，"
            f"占位符 {summary.get('placeholder_count', '-')}"
        ),
        "",
        "## DOCX 包检查",
        "",
        f"- 媒体文件数：{result['media_count']}",
        f"- 媒体类型：{result['media_extensions']}",
        f"- drawing 节点数：{result['drawing_count']}",
        f"- 表格节点数：{result['table_count']}",
        f"- 段落节点数：{result['paragraph_count']}",
        f"- drawing 指向缺失媒体：{result['missing_target_count']}",
        f"- 未被 drawing 使用的媒体：{result['unused_media_count']}",
        f"- 完全相同媒体哈希组：{result['duplicate_hash_group_count']}",
        f"- 同一媒体被多处复用：{result['reused_target_group_count']}",
        "",
        "## 图片尺寸检查",
        "",
        f"- 超过建议尺寸阈值：{result['oversized_image_count']}",
        f"- 明显过大：{result['very_large_image_count']}",
    ]
    if result["oversized_images"]:
        lines.extend(["", "| 序号 | 宽(cm) | 高(cm) | 媒体 |", "|---:|---:|---:|---|"])
        for image in result["oversized_images"]:
            lines.append(
                f"| {image['index']} | {image['width_cm']:.2f} | "
                f"{image['height_cm']:.2f} | `{_escape(image['target'])}` |"
            )

    lines.extend(["", "## 占位符检查", "", f"- 含“待补充”文本的段落数：{result['placeholder_hit_count']}"])
    if result["placeholder_hits"]:
        lines.extend(["", "| 段落序号 | 文本 |", "|---:|---|"])
        for item in result["placeholder_hits"]:
            lines.append(f"| {item['paragraph_index']} | {_escape(item['text'])} |")

    lines.extend(["", "## 重复媒体检查", ""])
    if result["reused_target_groups"]:
        lines.extend(["| 媒体 | 使用次数 | drawing 序号 |", "|---|---:|---|"])
        for item in result["reused_target_groups"]:
            indexes = ", ".join(str(index) for index in item["indexes"])
            lines.append(f"| `{_escape(item['target'])}` | {item['count']} | {indexes} |")
    else:
        lines.append("- 未发现同一媒体文件被多个 drawing 复用。")

    lines.extend(["", "## 结论", ""])
    if result["missing_target_count"]:
        lines.append("- 存在 drawing 指向缺失媒体，需要修复。")
    else:
        lines.append("- drawing 到媒体文件的结构关系完整。")
    if result["very_large_image_count"]:
        lines.append("- 存在明显过大的图片，建议继续调整图片尺寸策略。")
    else:
        lines.append("- 未发现明显过大的图片。")
    if result["reused_target_group_count"]:
        lines.append("- 存在同一媒体被多处复用，需要结合业务语义判断是否合理。")
    else:
        lines.append("- 未发现同一媒体被多处复用。")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
