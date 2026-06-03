"""汇总真实 LLM 6 个章节小样的图文质量指标。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = ROOT / "outputs" / "json" / "text_image_block_llm_6chapters_sample_result.json"
REPORT_PATH = ROOT / "outputs" / "reports" / "text_image_block_llm_6chapters_quality_summary.md"
DOCX_PATH = ROOT / "outputs" / "docx" / "text_image_block_llm_6chapters_sample.docx"


def main() -> int:
    data = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    chapters = [chapter for chapter in data.get("chapters") or [] if isinstance(chapter, dict)]
    rows = [_chapter_row(chapter) for chapter in chapters]
    repeated = _repeated_image_keys(chapters)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(data, rows, repeated), encoding="utf-8")
    print(REPORT_PATH)
    for row in rows:
        print(
            row["title"],
            "chars=",
            row["chars"],
            "paras=",
            row["paragraphs"],
            "tables=",
            row["tables"],
            "images=",
            row["images"],
            "groups=",
            row["groups"],
        )
    print("repeated_image_keys=", len(repeated))
    return 0


def _iter_blocks(chapter: dict[str, Any]):
    for section in chapter.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if isinstance(block, dict):
                yield section, block


def _image_key(block: dict[str, Any]) -> str:
    for key in ["image_asset_id", "canonical_image_id", "source_part_name", "image_id"]:
        value = block.get(key)
        if value:
            return f"{key}:{value}"
    return ""


def _chapter_row(chapter: dict[str, Any]) -> dict[str, Any]:
    paragraphs: list[str] = []
    table_count = 0
    images: list[tuple[dict[str, Any], dict[str, Any]]] = []
    group_counts: Counter[str] = Counter()
    for section, block in _iter_blocks(chapter):
        block_type = block.get("type")
        if block_type == "paragraph":
            paragraphs.append(str(block.get("text") or ""))
        elif block_type == "rich_table":
            table_count += 1
        elif block_type == "image_ref":
            images.append((section, block))
            group_id = str(block.get("image_group_id") or "")
            if group_id:
                group_counts[group_id] += 1

    split_groups = []
    for group_id, count in group_counts.items():
        expected = None
        for _, block in images:
            if str(block.get("image_group_id") or "") == group_id:
                expected = int(block.get("group_member_count") or 0) or None
                break
        if expected and expected != count:
            split_groups.append(f"{group_id}:{count}/{expected}")

    image_keys = [_image_key(block) for _, block in images if _image_key(block)]
    duplicates = [key for key, count in Counter(image_keys).items() if count > 1]
    return {
        "title": chapter.get("title") or "未命名章节",
        "paragraphs": len(paragraphs),
        "chars": sum(len(item) for item in paragraphs),
        "tables": table_count,
        "images": len(images),
        "groups": len(group_counts),
        "split_groups": split_groups,
        "duplicate_image_keys": duplicates,
        "auto_image_reuse": chapter.get("auto_image_reuse") or {},
        "image_slot_reuse": chapter.get("image_slot_reuse") or {},
        "captions": [block.get("caption") for _, block in images[:14]],
        "text_image_block_ids": sorted(
            {str(block.get("text_image_block_id")) for _, block in images if block.get("text_image_block_id")}
        ),
    }


def _repeated_image_keys(chapters: list[dict[str, Any]]) -> dict[str, int]:
    keys = []
    for chapter in chapters:
        keys.extend(_image_key(block) for _, block in _iter_blocks(chapter) if block.get("type") == "image_ref")
    counts = Counter(key for key in keys if key)
    return {key: count for key, count in counts.items() if count > 1}


def _render_report(data: dict[str, Any], rows: list[dict[str, Any]], repeated: dict[str, int]) -> str:
    lines = [
        "# 真实 LLM 6 个章节小样质量摘要",
        "",
        "## 总览",
        "",
        f"- 模型：{data.get('model')}",
        f"- 并发数：{data.get('max_workers')}",
        f"- 任务数：{data.get('task_count')}，完成：{data.get('completed_count')}，失败：{data.get('failed_count')}",
        f"- LLM 生成耗时：{float(data.get('duration_seconds') or 0):.2f} 秒",
        f"- 章节数：{len(rows)}",
        f"- 总字数：{sum(row['chars'] for row in rows)}",
        f"- 总段落数：{sum(row['paragraphs'] for row in rows)}",
        f"- 总表格数：{sum(row['tables'] for row in rows)}",
        f"- 总图片数：{sum(row['images'] for row in rows)}",
        f"- 跨章节重复图片键：{len(repeated)}",
        "",
        "## 分章节统计",
        "",
        "| 章节 | 字数 | 段落 | 表格 | 图片 | 套图数 | 拆套风险 | 章内重复 | 图文块ID |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['title']} | {row['chars']} | {row['paragraphs']} | {row['tables']} | "
            f"{row['images']} | {row['groups']} | {'有' if row['split_groups'] else '无'} | "
            f"{'有' if row['duplicate_image_keys'] else '无'} | {', '.join(row['text_image_block_ids']) or '-'} |"
        )
    lines.extend(["", "## 图片题注抽样", ""])
    for row in rows:
        lines.extend([f"### {row['title']}", ""])
        if row["captions"]:
            lines.extend(f"- {caption}" for caption in row["captions"])
        else:
            lines.append("- 无图片")
        lines.append("")

    lines.extend(["## 风险观察", ""])
    negative = next((row for row in rows if row["title"] == "工程重点、难点分析及对策"), None)
    if negative and negative["images"]:
        lines.append(f"- 负向对照章节仍插入 {negative['images']} 张图片，需要人工重点检查是否误配施工工艺图。")
    elif negative:
        lines.append("- 负向对照章节未插图，符合预期。")
    if repeated:
        lines.append(f"- 跨章节存在 {len(repeated)} 个重复图片键，需要继续检查去重逻辑。")
    else:
        lines.append("- 未发现跨章节重复图片键。")
    split_rows = [row for row in rows if row["split_groups"]]
    if split_rows:
        lines.append(
            "- 存在拆套风险："
            + "；".join(f"{row['title']}({','.join(row['split_groups'])})" for row in split_rows)
        )
    else:
        lines.append("- 未发现拆套风险。")
    lines.extend(
        [
            "",
            "## 文件",
            "",
            f"- Word 小样：`{DOCX_PATH}`",
            f"- LLM 结果 JSON：`{RESULT_PATH}`",
            f"- LLM 运行报告：`{ROOT / 'outputs' / 'reports' / 'text_image_block_llm_6chapters_sample_report.md'}`",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
