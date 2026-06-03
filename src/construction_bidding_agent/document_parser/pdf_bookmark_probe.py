"""探测带书签 PDF 优秀标书的章节树与正文可解析性。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .models import (
    PdfBookmarkProbeItem,
    PdfBookmarkProbeResult,
    PdfHeaderFooterCandidate,
    SectionParagraphRecord,
)


_NUMBERED_TITLE_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)*)(?:[.．、]\s*|\s+)(?P<title>\S.*)$")


def build_pdf_bookmark_probe(
    path: str | Path,
    *,
    sample_pages: int = 30,
    header_footer_top_ratio: float = 0.10,
    header_footer_bottom_ratio: float = 0.10,
) -> PdfBookmarkProbeResult:
    """读取 PDF 书签、页码映射和页眉页脚候选。"""

    source = Path(path)
    if not source.exists():
        return PdfBookmarkProbeResult(
            source_path=str(source),
            page_count=0,
            bookmark_count=0,
            max_bookmark_level=0,
            mapped_bookmark_count=0,
            unmapped_bookmark_count=0,
            text_page_count=0,
            scanned_like=False,
            warnings=[f"File not found: {source}"],
        )

    try:
        import pdfplumber
    except ModuleNotFoundError:
        return PdfBookmarkProbeResult(
            source_path=str(source),
            page_count=0,
            bookmark_count=0,
            max_bookmark_level=0,
            mapped_bookmark_count=0,
            unmapped_bookmark_count=0,
            text_page_count=0,
            scanned_like=False,
            warnings=["pdfplumber is not installed."],
        )

    warnings: list[str] = []
    with pdfplumber.open(str(source)) as pdf:
        page_count = len(pdf.pages)
        page_objid_to_page_no = _page_objid_to_page_no(pdf)
        raw_outlines = list(pdf.doc.get_outlines()) if hasattr(pdf.doc, "get_outlines") else []
        bookmarks = _bookmark_items(raw_outlines, page_objid_to_page_no, page_count)
        text_page_count, page_samples = _page_text_samples(pdf, sample_pages=sample_pages)
        header_footer_candidates = _header_footer_candidates(
            pdf,
            sample_pages=sample_pages,
            top_ratio=header_footer_top_ratio,
            bottom_ratio=header_footer_bottom_ratio,
        )

    mapped_count = sum(1 for item in bookmarks if item.page_no is not None)
    unmapped_count = len(bookmarks) - mapped_count
    if not raw_outlines:
        warnings.append("PDF 未读取到书签。")
    if unmapped_count:
        warnings.append(f"{unmapped_count} 个书签未能映射到页码。")
    if page_count and text_page_count == 0:
        warnings.append("未抽取到正文文本，PDF 可能是扫描件。")

    level_counts = Counter(item.level for item in bookmarks)
    return PdfBookmarkProbeResult(
        source_path=str(source),
        page_count=page_count,
        bookmark_count=len(bookmarks),
        max_bookmark_level=max(level_counts.keys(), default=0),
        mapped_bookmark_count=mapped_count,
        unmapped_bookmark_count=unmapped_count,
        text_page_count=text_page_count,
        scanned_like=page_count > 0 and text_page_count == 0,
        bookmarks=bookmarks,
        level_counts=dict(sorted(level_counts.items())),
        header_footer_candidates=header_footer_candidates,
        page_text_samples=page_samples,
        warnings=warnings,
    )


def write_pdf_bookmark_probe_outputs(
    result: PdfBookmarkProbeResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_pdf_bookmark_probe_report(result), encoding="utf-8")


def render_pdf_bookmark_probe_report(result: PdfBookmarkProbeResult) -> str:
    lines = [
        "# PDF 优秀标书书签探测报告",
        "",
        f"- 文件：`{result.source_path}`",
        f"- 页数：{result.page_count}",
        f"- 书签数：{result.bookmark_count}",
        f"- 最大书签层级：{result.max_bookmark_level}",
        f"- 已映射页码书签数：{result.mapped_bookmark_count}",
        f"- 未映射页码书签数：{result.unmapped_bookmark_count}",
        f"- 可抽取文本页数：{result.text_page_count}",
        f"- 是否疑似扫描件：{'是' if result.scanned_like else '否'}",
        "- 书签层级分布：" + _format_level_counts(result.level_counts),
        "",
        "## 入库建议",
        "",
    ]
    if result.bookmark_count and result.unmapped_bookmark_count == 0 and not result.scanned_like:
        lines.extend(
            [
                "- 建议入库：是。",
                "- 结构来源：PDF 书签。",
                "- 目录范式权限：可参与二三级目录补强，但一级目录仍以招标文件评分点原文为准。",
                "- 正文素材权限：可作为章节正文参考素材。",
                "- 表格与图片权限：先作为章节级候选素材，后续再做精细结构化。",
            ]
        )
    else:
        lines.extend(
            [
                "- 建议入库：谨慎。",
                "- 原因：书签或文本抽取存在问题，需要人工确认后再作为优秀标书素材。",
            ]
        )

    lines.extend(["", "## 书签预览（前 80 个）", ""])
    if not result.bookmarks:
        lines.append("- 未读取到书签。")
    for item in result.bookmarks[:80]:
        indent = "  " * max(item.level - 1, 0)
        page_range = _page_range(item)
        lines.append(f"- {indent}L{item.level} B{item.bookmark_index} {page_range} {item.title}")
    if len(result.bookmarks) > 80:
        lines.append("")
        lines.append(f"... 仅展示前 80 个书签，完整书签见 JSON。")

    tail_items = result.bookmarks[-30:] if len(result.bookmarks) > 110 else []
    if tail_items:
        lines.extend(["", "## 书签预览（后 30 个）", ""])
        for item in tail_items:
            indent = "  " * max(item.level - 1, 0)
            lines.append(f"- {indent}L{item.level} B{item.bookmark_index} {_page_range(item)} {item.title}")

    lines.extend(["", "## 页眉页脚候选", ""])
    if result.header_footer_candidates:
        lines.extend(["| 位置 | 出现次数 | 样例页 | 文本 |", "|---|---:|---|---|"])
        for candidate in result.header_footer_candidates[:30]:
            pages = ", ".join(str(page) for page in candidate.sample_pages[:8])
            lines.append(
                f"| {candidate.position} | {candidate.occurrence_count} | {pages} | {_cell(candidate.text)} |"
            )
    else:
        lines.append("- 未发现高频页眉页脚候选。")

    lines.extend(["", "## 正文抽样", ""])
    if result.page_text_samples:
        for sample in result.page_text_samples:
            lines.append(f"- 第 {sample.paragraph_index} 页：{sample.text_preview[:220]}")
    else:
        lines.append("- 未抽取到正文样例。")

    if result.warnings:
        lines.extend(["", "## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def _bookmark_items(
    raw_outlines: list[tuple[Any, ...]],
    page_objid_to_page_no: dict[int, int],
    page_count: int,
) -> list[PdfBookmarkProbeItem]:
    items: list[PdfBookmarkProbeItem] = []
    stack: list[PdfBookmarkProbeItem] = []
    for index, raw in enumerate(raw_outlines):
        level = int(raw[0] or 1)
        title = str(raw[1] or "").strip()
        destination = raw[2] if len(raw) > 2 else None
        destination_objid = _destination_objid(destination)
        page_no = page_objid_to_page_no.get(destination_objid) if destination_objid is not None else None
        number, clean_title = _split_numbered_title(title)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent_index = stack[-1].bookmark_index if stack else None
        path = [*(stack[-1].path if stack else []), title]
        item = PdfBookmarkProbeItem(
            bookmark_index=index,
            level=level,
            title=title,
            clean_title=clean_title,
            number=number,
            page_no=page_no,
            start_page=page_no,
            end_page=None,
            parent_index=parent_index,
            path=path,
            destination_objid=destination_objid,
        )
        if stack:
            stack[-1].child_count += 1
        items.append(item)
        stack.append(item)

    _fill_bookmark_end_pages(items, page_count)
    return items


def _fill_bookmark_end_pages(items: list[PdfBookmarkProbeItem], page_count: int) -> None:
    for index, item in enumerate(items):
        if item.start_page is None:
            continue
        next_page = None
        for candidate in items[index + 1 :]:
            if candidate.level <= item.level and candidate.start_page is not None:
                next_page = candidate.start_page
                break
        if next_page is None:
            item.end_page = page_count
        else:
            item.end_page = max(item.start_page, next_page - 1)


def _page_objid_to_page_no(pdf) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for page_no, page in enumerate(pdf.pages, start=1):
        pageid = getattr(page.page_obj, "pageid", None)
        if isinstance(pageid, int):
            mapping[pageid] = page_no
    return mapping


def _destination_objid(destination: Any) -> int | None:
    if isinstance(destination, (list, tuple)) and destination:
        first = destination[0]
        objid = getattr(first, "objid", None)
        return int(objid) if isinstance(objid, int) else None
    objid = getattr(destination, "objid", None)
    return int(objid) if isinstance(objid, int) else None


def _page_text_samples(pdf, *, sample_pages: int) -> tuple[int, list[SectionParagraphRecord]]:
    total_text_pages = 0
    samples: list[SectionParagraphRecord] = []
    page_numbers = _sample_page_numbers(len(pdf.pages), sample_pages)
    for page_no, page in enumerate(pdf.pages, start=1):
        text = _normalize_text(page.extract_text() or "")
        if text:
            total_text_pages += 1
        if page_no in page_numbers and text:
            samples.append(
                SectionParagraphRecord(
                    paragraph_index=page_no,
                    block_index=page_no,
                    style=None,
                    char_count=len(text),
                    text_preview=text[:300],
                )
            )
    return total_text_pages, samples[:sample_pages]


def _header_footer_candidates(
    pdf,
    *,
    sample_pages: int,
    top_ratio: float,
    bottom_ratio: float,
) -> list[PdfHeaderFooterCandidate]:
    page_numbers = _sample_page_numbers(len(pdf.pages), sample_pages)
    counter: Counter[tuple[str, str]] = Counter()
    pages_by_key: dict[tuple[str, str], list[int]] = {}
    for page_no in page_numbers:
        page = pdf.pages[page_no - 1]
        height = float(page.height)
        regions = [
            ("top", page.crop((0, 0, page.width, height * top_ratio))),
            ("bottom", page.crop((0, height * (1 - bottom_ratio), page.width, height))),
        ]
        for position, region in regions:
            text = _normalize_text(region.extract_text() or "")
            for line in _candidate_lines(text):
                key = (position, line)
                counter[key] += 1
                pages_by_key.setdefault(key, []).append(page_no)
    threshold = max(3, min(8, len(page_numbers) // 4))
    candidates = [
        PdfHeaderFooterCandidate(
            text=text,
            occurrence_count=count,
            sample_pages=pages_by_key.get((position, text), [])[:10],
            position=position,
        )
        for (position, text), count in counter.items()
        if count >= threshold
    ]
    candidates.sort(key=lambda item: (-item.occurrence_count, item.position, item.text))
    return candidates[:50]


def _sample_page_numbers(page_count: int, sample_pages: int) -> set[int]:
    if page_count <= 0 or sample_pages <= 0:
        return set()
    if page_count <= sample_pages:
        return set(range(1, page_count + 1))
    step = max(page_count // sample_pages, 1)
    numbers = {1, page_count}
    numbers.update(range(1, page_count + 1, step))
    return set(sorted(numbers)[:sample_pages])


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        normalized = _normalize_text(line)
        if not normalized:
            continue
        if len(normalized) > 80:
            continue
        lines.append(normalized)
    return lines


def _split_numbered_title(title: str) -> tuple[str | None, str]:
    match = _NUMBERED_TITLE_RE.match(title)
    if not match:
        return None, title
    return match.group("number"), match.group("title").strip()


def _normalize_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _format_level_counts(level_counts: dict[int, int]) -> str:
    if not level_counts:
        return "无"
    return "，".join(f"L{level}={count}" for level, count in sorted(level_counts.items()))


def _page_range(item: PdfBookmarkProbeItem) -> str:
    if item.start_page is None:
        return "page=?"
    if item.end_page is None or item.end_page == item.start_page:
        return f"page={item.start_page}"
    return f"page={item.start_page}-{item.end_page}"


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")
