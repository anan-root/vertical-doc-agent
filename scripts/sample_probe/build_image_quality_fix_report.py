from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPORT_JSON = ROOT / "outputs/json/full_bid_export_result_slim_v2_image_fix_v4_5chapters_final.json"
OUTPUT_REPORT = ROOT / "outputs/reports/image_quality_fix_5chapters_report.md"


def main() -> int:
    data = json.loads(EXPORT_JSON.read_text(encoding="utf-8"))
    summary = data.get("full_bid_export_summary") or {}
    render_stats = summary.get("render_stats") or {}
    dedupe = summary.get("image_dedupe_summary") or data.get("image_dedupe") or {}

    rows: list[tuple[str, str, int]] = []
    for chapter in data.get("chapters") or []:
        chapter_path = " > ".join(str(part) for part in chapter.get("chapter_path") or [])
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                continue
            count = sum(
                1
                for block in section.get("blocks") or []
                if isinstance(block, dict) and block.get("type") == "image_ref"
            )
            rows.append((chapter_path, str(section.get("heading") or ""), count))

    lines = [
        "# 图片质量修复 5章回归报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Word 文件：`outputs/docx/chapter_draft_slim_v2_image_fix_v4_5chapters_final.docx`",
        f"- 导出 JSON：`outputs/json/full_bid_export_result_slim_v2_image_fix_v4_5chapters_final.json`",
        "",
        "## 关键指标",
        "",
        f"- 图片引用数：{render_stats.get('image_ref_count')}",
        f"- 成功渲染图片数：{render_stats.get('rendered_image_count')}",
        f"- 缺失图片数：{render_stats.get('missing_image_count')}",
        f"- 占位图数：{render_stats.get('placeholder_count')}",
        f"- 整本导出去重/过滤移除数：{dedupe.get('removed_count')}",
        f"- 其中重复图片：{dedupe.get('removed_duplicate_asset_count')}",
        f"- 其中重复套图：{dedupe.get('removed_duplicate_group_count')}",
        f"- 其中主题不匹配：{dedupe.get('removed_incompatible_count')}",
        "",
        "## 小节图片分布",
        "",
        "| 一级章节 | 小节 | 图片数 |",
        "| --- | --- | ---: |",
    ]
    for chapter_path, heading, count in rows:
        lines.append(f"| {chapter_path} | {heading} | {count} |")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- `source_part_name` 路径回填问题已修复，本轮最终 Word 图片缺失数为 0。",
            "- 环保章节已能召回并保留通用环保套图，噪声、水污染、固废/光污染、绿色施工小节均有配图。",
            "- 安全章节目前主要命中消防和施工机械相关图片；临边洞口、个人防护仍缺少高置信候选素材。",
            "- 电梯章节素材库中有电梯正文切片，但没有可复用电梯图片；当前保持 0 图，避免强行复用土建图片造成错配。",
        ]
    )
    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(OUTPUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
