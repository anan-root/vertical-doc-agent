"""检查正式版 Word 是否残留复核内容，并扫描综合分析类小节配图。"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCX = ROOT / "outputs" / "docx" / "full_bid_draft_full50_final_format_recheck.docx"
DEFAULT_JSON = ROOT / "outputs" / "json" / "full_bid_export_result_full50_final_format_recheck.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "reports" / "final_docx_artifact_check_full50_final_format_recheck.md"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
REVIEW_TEXT_MARKERS = ["评分点响应摘要", "人工复核清单", "[medium]", "[low]", "AI生成", "生成模型", "大模型", "模型输出"]
GENERAL_ANALYSIS_TERMS = [
    "工程重点",
    "重点难点",
    "难点分析",
    "重点、难点",
    "项目概况",
    "工程概况",
    "施工条件",
    "现场环境",
    "环境分析",
    "特点分析",
    "现状分析",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查正式版 Word 残留内容和综合分析小节配图。")
    parser.add_argument("--docx", default=str(DEFAULT_DOCX), help="正式版 Word 文件。")
    parser.add_argument("--export-json", default=str(DEFAULT_JSON), help="正式版导出 JSON。")
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT), help="Markdown 报告输出路径。")
    args = parser.parse_args()

    docx_path = Path(args.docx)
    json_path = Path(args.export_json)
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)

    text = _docx_text(docx_path)
    marker_hits = {marker: marker in text for marker in REVIEW_TEXT_MARKERS}
    analysis_image_hits = _analysis_image_hits(json_path)

    output.write_text(_render_report(docx_path, json_path, marker_hits, analysis_image_hits), encoding="utf-8")
    print(f"Report: {output.resolve()}")
    print(
        "review_marker_hits={hits}, analysis_sections_with_images={count}".format(
            hits=sum(1 for matched in marker_hits.values() if matched),
            count=len(analysis_image_hits),
        )
    )
    return 1 if any(marker_hits.values()) or analysis_image_hits else 0


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.findall(".//w:p", NS):
        paragraphs.append("".join(node.text or "" for node in paragraph.findall(".//w:t", NS)))
    return "\n".join(paragraphs)


def _analysis_image_hits(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    hits: list[dict[str, Any]] = []
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapter_path = [str(part) for part in chapter.get("chapter_path") or []]
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "")
            if not _is_general_analysis_heading(heading):
                continue
            refs = [
                block
                for block in section.get("blocks") or []
                if isinstance(block, dict) and block.get("type") == "image_ref"
            ]
            if refs:
                hits.append(
                    {
                        "chapter_path": chapter_path,
                        "heading": heading,
                        "refs": refs,
                    }
                )
    return hits


def _is_general_analysis_heading(heading: str) -> bool:
    value = "".join(str(heading or "").split())
    return any(term in value for term in GENERAL_ANALYSIS_TERMS)


def _render_report(
    docx_path: Path,
    json_path: Path,
    marker_hits: dict[str, bool],
    analysis_image_hits: list[dict[str, Any]],
) -> str:
    lines = [
        "# 正式版 Word 残留内容检查报告",
        "",
        f"- Word 文件：`{docx_path}`",
        f"- 导出 JSON：`{json_path}`",
        "",
        "## 复核内容残留",
        "",
    ]
    for marker, matched in marker_hits.items():
        lines.append(f"- `{marker}`：{'命中' if matched else '未命中'}")

    lines.extend(["", "## 综合分析类小节配图", "", f"- 命中小节数：{len(analysis_image_hits)}"])
    if analysis_image_hits:
        lines.extend(["", "| 章节 | 小节 | 图片说明 | 语义文本 | 源图片 |", "|---|---|---|---|---|"])
        for item in analysis_image_hits:
            chapter = " > ".join(item["chapter_path"])
            heading = str(item["heading"])
            for ref in item["refs"][:12]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _escape(chapter),
                            _escape(heading),
                            _escape(str(ref.get("caption") or "")),
                            _escape(str(ref.get("semantic_text") or "")),
                            _escape(str(ref.get("source_part_name") or ref.get("part_name") or "")),
                        ]
                    )
                    + " |"
                )
    lines.extend(["", "## 结论", ""])
    if any(marker_hits.values()):
        lines.append("- 正式版仍残留复核/调试文本，需要继续清理。")
    else:
        lines.append("- 未发现评分点响应摘要、人工复核清单等复核/调试文本。")
    if analysis_image_hits:
        lines.append("- 综合分析类小节仍存在图片，需要继续复核是否误配。")
    else:
        lines.append("- 综合分析类小节未发现自动插入图片。")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
