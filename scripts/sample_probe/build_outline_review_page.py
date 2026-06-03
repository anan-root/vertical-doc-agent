"""生成技术标目录人工复核静态 HTML 页面。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.outline_generator import write_outline_review_page  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="生成技术标目录人工复核静态 HTML 页面。")
    parser.add_argument(
        "--review-view-json",
        action="append",
        required=True,
        help="目录复核视图 JSON 路径。可重复传入多份。",
    )
    parser.add_argument("--html-out", required=True, help="HTML 输出路径。")
    args = parser.parse_args()

    views = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in args.review_view_json
    ]
    write_outline_review_page(views, args.html_out)
    print(f"Review page HTML: {Path(args.html_out).resolve()}")
    print(f"Project count: {len(views)}")


if __name__ == "__main__":
    main()
