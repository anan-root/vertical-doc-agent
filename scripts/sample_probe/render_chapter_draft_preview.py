"""渲染编标人员可读的章节正文预览稿。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import write_chapter_draft_preview  # noqa: E402


DEFAULT_RESULT = ROOT / "outputs" / "json" / "batch_tender_01_chapter_generation_result_typical_4.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染章节正文预览稿。")
    parser.add_argument("--generation-result", default=str(DEFAULT_RESULT), help="章节生成结果 JSON 路径。")
    parser.add_argument(
        "--preview-output",
        default=str(ROOT / "outputs" / "reports" / "chapter_draft_preview_typical_4.md"),
        help="正文预览稿 Markdown 输出路径。",
    )
    args = parser.parse_args()

    write_chapter_draft_preview(args.generation_result, args.preview_output)
    print(f"Preview: {Path(args.preview_output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
