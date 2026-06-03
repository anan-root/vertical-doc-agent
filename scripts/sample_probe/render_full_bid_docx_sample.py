"""渲染整本技术标 Word 初稿样例。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.full_bid_docx_exporter import (  # noqa: E402
    export_full_bid_docx_from_files,
)


DEFAULT_CHAPTER_INPUTS = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_inputs_image_assets.json"
DEFAULT_GENERATION_RESULT = ROOT / "outputs" / "json" / "chapter_generation_result_civil_current.json"
DEFAULT_LIBRARY = ROOT / "outputs" / "json" / "excellent_bid_material_library_with_image_assets.json"
DEFAULT_RENDER_PROFILE = ROOT / "configs" / "docx-render-profile.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "docx" / "full_bid_draft_current.docx"
DEFAULT_JSON_OUTPUT = ROOT / "outputs" / "json" / "full_bid_export_result_current.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染整本技术标 Word 初稿样例。")
    parser.add_argument("--chapter-inputs", default=str(DEFAULT_CHAPTER_INPUTS), help="章节生成输入包 JSON 路径。")
    parser.add_argument(
        "--generation-result",
        action="append",
        default=[],
        help="已生成章节结果 JSON 路径，可重复传入多个文件。",
    )
    parser.add_argument("--material-library", default=str(DEFAULT_LIBRARY), help="优秀标书素材库 JSON 路径。")
    parser.add_argument("--render-profile", default=str(DEFAULT_RENDER_PROFILE), help="Word 渲染配置 JSON 路径。")
    parser.add_argument("--output-docx", default=str(DEFAULT_OUTPUT), help="整本 Word 初稿输出路径。")
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_OUTPUT), help="整本草稿中间 JSON 输出路径。")
    parser.add_argument("--title", default="技术标整本 Word 初稿", help="Word 文档标题。")
    parser.add_argument(
        "--output-mode",
        choices=["review", "final"],
        default="review",
        help="Word 导出模式：review 保留评分响应摘要和人工复核清单；final 输出正式成稿样式。",
    )
    parser.add_argument(
        "--no-current-image-policy",
        action="store_true",
        help="不在整本导出前执行当前图片后处理策略。",
    )
    args = parser.parse_args()

    generation_results = args.generation_result or [str(DEFAULT_GENERATION_RESULT)]
    summary = export_full_bid_docx_from_files(
        args.chapter_inputs,
        generation_results,
        args.output_docx,
        output_json=args.output_json,
        material_library_json=args.material_library,
        render_profile_json=args.render_profile,
        title=args.title,
        apply_current_image_policy=not args.no_current_image_policy,
        output_mode=args.output_mode,
    )
    print(f"DOCX: {Path(args.output_docx).resolve()}")
    print(f"JSON: {Path(args.output_json).resolve()}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
