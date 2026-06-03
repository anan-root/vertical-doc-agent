from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import chapter_writer as cw  # noqa: E402


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _chapter_key(chapter: dict) -> str:
    return " > ".join(str(part) for part in chapter.get("chapter_path") or [])


def _package_key(package: dict) -> str:
    unit = package.get("generation_unit") or {}
    return " > ".join(str(part) for part in unit.get("chapter_path") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断章节图片后处理效果。")
    parser.add_argument("--chapter-inputs", required=True)
    parser.add_argument("--generation-result", required=True)
    parser.add_argument("--output-report")
    args = parser.parse_args()

    inputs = _load(args.chapter_inputs)
    generation = _load(args.generation_result)
    lookup = {_chapter_key(chapter): chapter for chapter in generation.get("chapters") or [] if isinstance(chapter, dict)}
    lines: list[str] = []
    lines.append("# 图片后处理诊断")
    lines.append("")
    for package in inputs.get("packages") or []:
        if not isinstance(package, dict):
            continue
        key = _package_key(package)
        source_chapter = lookup.get(key)
        if not source_chapter:
            continue
        chapter = cw.postprocess_chapter_images(copy.deepcopy(source_chapter), package)
        reusable = cw._auto_reusable_image_candidates(package)
        groups = cw._auto_reusable_image_group_candidates(package)
        lines.append(f"## {key}")
        lines.append("")
        lines.append(f"- image_candidate_pool：{len(package.get('image_candidate_pool') or [])}")
        lines.append(f"- auto reusable images：{len(reusable)}")
        lines.append(f"- auto reusable groups：{len(groups)}")
        lines.append(f"- auto_image_reuse：`{json.dumps(chapter.get('auto_image_reuse'), ensure_ascii=False)}`")
        lines.append(f"- image_ref_filter：`{json.dumps(chapter.get('image_ref_filter'), ensure_ascii=False)[:1200]}`")
        lines.append("")
        lines.append("| 小节 | 图片数 |")
        lines.append("| --- | ---: |")
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                continue
            image_count = sum(
                1
                for block in section.get("blocks") or []
                if isinstance(block, dict) and block.get("type") == "image_ref"
            )
            lines.append(f"| {section.get('heading')} | {image_count} |")
        lines.append("")
        lines.append("候选预览：")
        for item in reusable[:12]:
            topics = ",".join(sorted(cw._candidate_primary_topics(item)))
            lines.append(
                f"- {item.get('caption')} | semantic={item.get('semantic_text')} | "
                f"bound={item.get('bound_section')} | topics={topics} | part={item.get('part_name')}"
            )
        lines.append("")
        lines.append("套图候选预览：")
        for group in groups[:12]:
            topics = ",".join(sorted(cw._candidate_primary_topics(group)))
            lines.append(
                f"- {group.get('group_title') or group.get('caption')} | semantic={group.get('semantic_text')} | "
                f"bound={group.get('bound_section')} | topics={topics} | members={group.get('member_count')}"
            )
        lines.append("")

    text = "\n".join(lines)
    if args.output_report:
        target = Path(args.output_report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
