"""为二三级目录 LLM 补强构建输入包。"""

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
    build_outline_refinement_inputs,
    write_refinement_inputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="构建二三级目录 LLM 补强输入包。")
    parser.add_argument("--outline-json", required=True, help="技术标目录树 JSON 路径。")
    parser.add_argument("--parse-result", required=True, help="招标文件解析结果 JSON 路径。")
    parser.add_argument("--excellent-bid-index", help="优秀标书章节素材索引 JSON 路径。")
    parser.add_argument("--json-out", required=True, help="补强输入包 JSON 输出路径。")
    args = parser.parse_args()

    outline = json.loads(Path(args.outline_json).read_text(encoding="utf-8"))
    parse_result = json.loads(Path(args.parse_result).read_text(encoding="utf-8"))
    excellent_bid_index = (
        json.loads(Path(args.excellent_bid_index).read_text(encoding="utf-8"))
        if args.excellent_bid_index
        else None
    )
    packages = build_outline_refinement_inputs(
        outline,
        parse_result,
        excellent_bid_index=excellent_bid_index,
    )
    write_refinement_inputs(packages, args.json_out)
    print(f"Refinement inputs: {Path(args.json_out).resolve()}")
    print(f"Package count: {len(packages)}")


if __name__ == "__main__":
    main()
