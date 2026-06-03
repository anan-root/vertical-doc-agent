"""检查 Word 表格列宽可读性，不依赖 LibreOffice 渲染。"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCX = ROOT / "outputs" / "docx" / "full_bid_draft_full50_final_format_recheck.docx"
DEFAULT_OUTPUT = ROOT / "outputs" / "reports" / "docx_table_readability_full50_recheck.md"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
DXA_PER_CM = 1440 / 2.54


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 DOCX 表格列宽、长文本列和极窄列风险。")
    parser.add_argument("--docx", default=str(DEFAULT_DOCX), help="待检查的 Word 文件。")
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT), help="Markdown 报告输出路径。")
    parser.add_argument("--min-width-cm", type=float, default=0.75, help="硬性极窄列阈值。")
    parser.add_argument("--readable-width-cm", type=float, default=1.2, help="一般可读列宽阈值。")
    args = parser.parse_args()

    docx_path = Path(args.docx)
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)

    result = analyze_tables(docx_path, args.min_width_cm, args.readable_width_cm)
    output.write_text(render_report(result), encoding="utf-8")
    print(f"Report: {output.resolve()}")
    print(
        "tables={table_count}, high={high_risk_count}, medium={medium_risk_count}, "
        "tiny_columns={tiny_column_count}, narrow_text_columns={narrow_text_column_count}".format(**result)
    )
    return 1 if result["high_risk_count"] else 0


def analyze_tables(docx_path: Path, min_width_cm: float, readable_width_cm: float) -> dict[str, Any]:
    with zipfile.ZipFile(docx_path, "r") as zf:
        document_xml = zf.read("word/document.xml")
    root = ET.fromstring(document_xml)
    table_items = _body_table_items(root)
    results = []
    tiny_count = 0
    narrow_text_count = 0
    high_count = 0
    medium_count = 0

    for index, table_item in enumerate(table_items, start=1):
        table = table_item["table"]
        rows = table.findall("./w:tr", NS)
        if not rows:
            continue
        widths = _table_grid_widths_cm(table)
        if not widths:
            widths = _first_row_cell_widths_cm(rows[0])
        column_count = max(len(widths), _row_cell_count(rows[0]))
        texts_by_col = _texts_by_column(rows, column_count)
        issues = []

        for col_index, width in enumerate(widths, start=1):
            max_len = max((_display_text_len(text) for text in texts_by_col.get(col_index, [])), default=0)
            title = next((text for text in texts_by_col.get(col_index, []) if text.strip()), "")
            if width < min_width_cm:
                issues.append(
                    {
                        "severity": "high",
                        "column": col_index,
                        "width_cm": width,
                        "max_text_len": max_len,
                        "reason": "列宽低于硬性阈值",
                        "sample": title[:80],
                    }
                )
                tiny_count += 1
            elif width < readable_width_cm and max_len >= 4:
                issues.append(
                    {
                        "severity": "medium",
                        "column": col_index,
                        "width_cm": width,
                        "max_text_len": max_len,
                        "reason": "列宽偏窄，可能竖排",
                        "sample": title[:80],
                    }
                )
                tiny_count += 1
            elif width < 2.4 and max_len >= 18:
                issues.append(
                    {
                        "severity": "medium",
                        "column": col_index,
                        "width_cm": width,
                        "max_text_len": max_len,
                        "reason": "长文本列宽不足",
                        "sample": title[:80],
                    }
                )
                narrow_text_count += 1

        long_columns = [
            col_index
            for col_index, texts in texts_by_col.items()
            if max((_display_text_len(text) for text in texts), default=0) >= 25
        ]
        if column_count >= 5 and len(long_columns) >= 3:
            issues.append(
                {
                    "severity": "medium",
                    "column": 0,
                    "width_cm": sum(widths),
                    "max_text_len": 0,
                    "reason": "多列表含多个长文本列，建议拆表或横向页",
                    "sample": "",
                }
            )

        if issues:
            severity = "high" if any(issue["severity"] == "high" for issue in issues) else "medium"
            if severity == "high":
                high_count += 1
            else:
                medium_count += 1
            results.append(
                {
                    "index": index,
                    "severity": severity,
                    "column_count": column_count,
                    "row_count": len(rows),
                    "widths_cm": [round(width, 2) for width in widths],
                    "title": table_item["title"],
                    "issues": issues,
                }
            )

    return {
        "docx_path": str(docx_path),
        "table_count": len(table_items),
        "risk_count": len(results),
        "high_risk_count": high_count,
        "medium_risk_count": medium_count,
        "tiny_column_count": tiny_count,
        "narrow_text_column_count": narrow_text_count,
        "risks": results[:120],
    }


def _body_table_items(root: ET.Element) -> list[dict[str, Any]]:
    body = root.find("./w:body", NS)
    if body is None:
        return []
    items = []
    last_text = ""
    paragraph_tag = f"{{{NS['w']}}}p"
    table_tag = f"{{{NS['w']}}}tbl"
    for child in body:
        if child.tag == paragraph_tag:
            text = _text_of(child).strip()
            if text:
                last_text = text[:120]
        elif child.tag == table_tag:
            items.append({"table": child, "title": last_text})
    return items


def _table_grid_widths_cm(table: ET.Element) -> list[float]:
    widths = []
    for grid_col in table.findall("./w:tblGrid/w:gridCol", NS):
        value = grid_col.attrib.get(f"{{{NS['w']}}}w")
        if value and value.isdigit():
            widths.append(int(value) / DXA_PER_CM)
    return widths


def _first_row_cell_widths_cm(row: ET.Element) -> list[float]:
    widths = []
    for cell in row.findall("./w:tc", NS):
        width = cell.find("./w:tcPr/w:tcW", NS)
        value = width.attrib.get(f"{{{NS['w']}}}w") if width is not None else ""
        if value and value.isdigit():
            widths.append(int(value) / DXA_PER_CM)
    return widths


def _row_cell_count(row: ET.Element) -> int:
    return len(row.findall("./w:tc", NS))


def _texts_by_column(rows: list[ET.Element], column_count: int) -> dict[int, list[str]]:
    result = {index: [] for index in range(1, column_count + 1)}
    for row in rows:
        for index, cell in enumerate(row.findall("./w:tc", NS), start=1):
            result.setdefault(index, []).append(_text_of(cell).strip())
    return result


def _text_of(element: ET.Element) -> str:
    return "".join(text.text or "" for text in element.findall(".//w:t", NS))


def _display_text_len(text: str) -> int:
    value = str(text or "")
    ascii_count = sum(1 for char in value if ord(char) < 128)
    return len(value) - ascii_count + (ascii_count + 1) // 2


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Word 表格可读性检查报告",
        "",
        f"- 文件：`{result['docx_path']}`",
        f"- 表格总数：{result['table_count']}",
        f"- 高风险表格：{result['high_risk_count']}",
        f"- 中风险表格：{result['medium_risk_count']}",
        f"- 极窄/偏窄列：{result['tiny_column_count']}",
        f"- 长文本窄列：{result['narrow_text_column_count']}",
        "",
        "## 风险表格",
        "",
    ]
    if not result["risks"]:
        lines.append("- 未发现明显列宽可读性风险。")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| 表格序号 | 风险 | 前置标题/说明 | 列数 | 行数 | 列宽(cm) | 问题 |",
            "|---:|---|---|---:|---:|---|---|",
        ]
    )
    for item in result["risks"]:
        issue_text = "; ".join(
            f"第{issue['column']}列 {issue['reason']}({issue['width_cm']:.2f}cm, 文本{issue['max_text_len']})"
            if issue["column"]
            else issue["reason"]
            for issue in item["issues"][:4]
        )
        lines.append(
            f"| {item['index']} | {item['severity']} | {_escape(item['title'])} | {item['column_count']} | "
            f"{item['row_count']} | {item['widths_cm']} | {_escape(issue_text)} |"
        )
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
