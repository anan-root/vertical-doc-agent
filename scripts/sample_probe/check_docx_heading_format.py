"""检查 Word 标题编号和核心样式配置。"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCX = ROOT / "outputs" / "docx" / "full_bid_draft_full50_final_format_recheck.docx"
DEFAULT_OUTPUT = ROOT / "outputs" / "reports" / "docx_heading_format_full50_final_format_recheck.md"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Word 标题编号和样式。")
    parser.add_argument("--docx", default=str(DEFAULT_DOCX), help="待检查的 Word 文件。")
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT), help="Markdown 报告输出路径。")
    args = parser.parse_args()

    docx_path = Path(args.docx)
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = analyze(docx_path)
    output.write_text(render_report(docx_path, result), encoding="utf-8")
    print(f"Report: {output.resolve()}")
    print(
        f"heading1={result['heading1_count']}, heading2={result['heading2_count']}, "
        f"heading3={result['heading3_count']}, missing_number={len(result['missing_number_headings'])}"
    )
    return 1 if result["missing_number_headings"] else 0


def analyze(docx_path: Path) -> dict:
    with zipfile.ZipFile(docx_path) as archive:
        document_root = ET.fromstring(archive.read("word/document.xml"))
        styles_root = ET.fromstring(archive.read("word/styles.xml"))

    headings = []
    for paragraph in document_root.findall(".//w:p", NS):
        style = paragraph.find("./w:pPr/w:pStyle", NS)
        if style is None:
            continue
        style_id = style.attrib.get(f"{{{NS['w']}}}val", "")
        if style_id not in {"Heading1", "Heading2", "Heading3"}:
            continue
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
        headings.append({"style_id": style_id, "text": text})
    missing = [
        item
        for item in headings
        if item["style_id"] in {"Heading1", "Heading2", "Heading3"} and not _has_expected_number(item)
    ]
    return {
        "heading1_count": sum(1 for item in headings if item["style_id"] == "Heading1"),
        "heading2_count": sum(1 for item in headings if item["style_id"] == "Heading2"),
        "heading3_count": sum(1 for item in headings if item["style_id"] == "Heading3"),
        "sample_headings": headings[:80],
        "missing_number_headings": missing[:80],
        "styles": {
            "Normal": _style_summary(styles_root, "Normal"),
            "Heading1": _style_summary(styles_root, "Heading1"),
            "Heading2": _style_summary(styles_root, "Heading2"),
            "Heading3": _style_summary(styles_root, "Heading3"),
        },
    }


def _has_expected_number(item: dict) -> bool:
    text = item["text"]
    if item["style_id"] == "Heading1":
        return bool(__import__("re").match(r"^\d+\.", text))
    if item["style_id"] == "Heading2":
        return bool(__import__("re").match(r"^\d+\.\d+\.", text))
    return bool(__import__("re").match(r"^\d+\.\d+\.\d+\.", text))


def _style_summary(root: ET.Element, style_id: str) -> dict[str, str | None]:
    style = _style(root, style_id)
    rfonts = style.find(".//w:rFonts", NS)
    size = style.find(".//w:sz", NS)
    color = style.find(".//w:color", NS)
    return {
        "font": rfonts.attrib.get(f"{{{NS['w']}}}eastAsia") if rfonts is not None else None,
        "size": size.attrib.get(f"{{{NS['w']}}}val") if size is not None else None,
        "color": color.attrib.get(f"{{{NS['w']}}}val") if color is not None else None,
    }


def _style(root: ET.Element, style_id: str) -> ET.Element:
    for item in root.findall(".//w:style", NS):
        if item.attrib.get(f"{{{NS['w']}}}styleId") == style_id:
            return item
    raise ValueError(f"style not found: {style_id}")


def render_report(docx_path: Path, result: dict) -> str:
    lines = [
        "# Word 标题格式检查报告",
        "",
        f"- 文件：`{docx_path}`",
        f"- 一级标题数：{result['heading1_count']}",
        f"- 二级标题数：{result['heading2_count']}",
        f"- 三级标题数：{result['heading3_count']}",
        f"- 未带预期编号的标题数：{len(result['missing_number_headings'])}",
        "",
        "## 样式摘要",
        "",
        "| 样式 | 字体 | 字号(w:sz) | 颜色 |",
        "|---|---|---:|---|",
    ]
    for style_id, summary in result["styles"].items():
        lines.append(
            f"| {style_id} | {summary.get('font') or ''} | {summary.get('size') or ''} | {summary.get('color') or ''} |"
        )
    lines.extend(["", "## 标题样例", "", "| 样式 | 文本 |", "|---|---|"])
    for item in result["sample_headings"][:40]:
        lines.append(f"| {item['style_id']} | {_escape(item['text'])} |")
    if result["missing_number_headings"]:
        lines.extend(["", "## 编号异常标题", "", "| 样式 | 文本 |", "|---|---|"])
        for item in result["missing_number_headings"]:
            lines.append(f"| {item['style_id']} | {_escape(item['text'])} |")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
