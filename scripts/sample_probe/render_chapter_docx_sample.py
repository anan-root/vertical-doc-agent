"""渲染技术标章节 Word 样稿。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.chapter_docx_renderer import render_chapter_docx_from_file  # noqa: E402
from construction_bidding_agent.chapter_generator.chapter_writer import (  # noqa: E402
    apply_auto_image_reuse,
    enrich_image_refs,
    filter_mismatched_image_refs,
)


DEFAULT_GENERATION_RESULT = ROOT / "outputs" / "json" / "chapter_generation_result_auto_images_civil.json"
DEFAULT_CHAPTER_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_with_materials.json"
DEFAULT_LIBRARY = ROOT / "outputs" / "json" / "excellent_bid_material_library.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "docx" / "chapter_draft_civil_sample.docx"
DEFAULT_RENDER_PROFILE = ROOT / "configs" / "docx-render-profile.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染技术标章节 Word 样稿。")
    parser.add_argument("--generation-result", default=str(DEFAULT_GENERATION_RESULT), help="章节生成结果 JSON 路径。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_CHAPTER_INPUTS), help="章节生成输入包 JSON 路径，用于补跑自动插图后处理。")
    parser.add_argument("--title-contains", default="土建施工方案与技术措施", help="匹配输入包章节路径的关键字。")
    parser.add_argument("--material-library", default=str(DEFAULT_LIBRARY), help="优秀标书素材库 JSON 路径。")
    parser.add_argument("--render-profile", default=str(DEFAULT_RENDER_PROFILE), help="Word 渲染配置 JSON 路径。")
    parser.add_argument("--output-docx", default=str(DEFAULT_OUTPUT), help="Word 样稿输出路径。")
    args = parser.parse_args()

    generation_result = json.loads(Path(args.generation_result).read_text(encoding="utf-8"))
    if args.chapter_inputs:
        generation_result = _apply_current_image_policy(
            generation_result,
            Path(args.chapter_inputs),
            title_contains=args.title_contains,
        )
    temp_json = Path(args.output_docx).with_suffix(".json")
    temp_json.parent.mkdir(parents=True, exist_ok=True)
    temp_json.write_text(json.dumps(generation_result, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = render_chapter_docx_from_file(
        temp_json,
        args.output_docx,
        material_library_json=args.material_library,
        render_profile_json=args.render_profile,
        title="技术标章节 Word 样稿",
    )
    print(f"DOCX: {Path(args.output_docx).resolve()}")
    print(f"JSON: {temp_json.resolve()}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def _apply_current_image_policy(
    generation_result: dict,
    chapter_inputs_path: Path,
    *,
    title_contains: str,
) -> dict:
    data = json.loads(chapter_inputs_path.read_text(encoding="utf-8"))
    packages = data.get("packages") if isinstance(data, dict) else []
    package = _find_package(packages, title_contains)
    if not package:
        return generation_result
    chapters = generation_result.get("chapters") or []
    for index, chapter in enumerate(chapters):
        updated = json.loads(json.dumps(chapter, ensure_ascii=False))
        updated = filter_mismatched_image_refs(updated, package)
        updated = apply_auto_image_reuse(updated, package)
        updated = enrich_image_refs(updated, package)
        chapters[index] = updated
    generation_result["chapters"] = chapters
    generation_result.setdefault("warnings", [])
    generation_result["warnings"].append("DOCX 样稿渲染前按当前输入包补跑了自动插图后处理。")
    return generation_result


def _find_package(packages: list, title_contains: str) -> dict | None:
    keyword = title_contains.strip()
    for package in packages:
        if not isinstance(package, dict):
            continue
        unit = package.get("generation_unit") or {}
        chapter_path = " > ".join(str(part) for part in unit.get("chapter_path") or [])
        if keyword in chapter_path:
            return package
    return None


if __name__ == "__main__":
    raise SystemExit(main())
