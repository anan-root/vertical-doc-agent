"""把技术标目录树转换为前端人工复核视图 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.outline_generator import (  # noqa: E402
    build_outline_review_view,
    refresh_outline_confirmation,
    write_outline_review_view,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成技术标目录人工复核视图 JSON。")
    parser.add_argument("--outline-json", required=True, help="补强后的技术标目录树 JSON 路径。")
    parser.add_argument("--json-out", required=True, help="前端复核视图 JSON 输出路径。")
    args = parser.parse_args()

    outline = json.loads(Path(args.outline_json).read_text(encoding="utf-8"))
    refresh_outline_confirmation(outline)
    view = build_outline_review_view(outline)
    write_outline_review_view(view, args.json_out)
    print(f"Review view JSON: {Path(args.json_out).resolve()}")
    print(
        "Summary: "
        f"status={view['status']}, "
        f"level_1={view['summary']['level_1_count']}, "
        f"nodes={view['summary']['node_count']}, "
        f"pending={view['summary']['pending_review_count']}"
    )


if __name__ == "__main__":
    main()
