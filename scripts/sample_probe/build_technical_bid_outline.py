"""根据招标文件解析结果生成技术标目录树。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.outline_generator import (
    build_outline_from_files,
    write_outline_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="根据 tender_parse_result.json 生成技术标目录树。")
    parser.add_argument("--parse-result", required=True, help="招标文件解析结果 JSON 路径。")
    parser.add_argument("--excellent-bid-index", help="优秀标书章节素材索引 JSON 路径。")
    parser.add_argument("--json-out", required=True, help="目录树 JSON 输出路径。")
    parser.add_argument("--report-out", required=True, help="目录生成 Markdown 报告输出路径。")
    args = parser.parse_args()

    outline = build_outline_from_files(
        args.parse_result,
        excellent_bid_index_json=args.excellent_bid_index,
    )
    write_outline_outputs(outline, Path(args.json_out), Path(args.report_out))
    print(f"Outline JSON: {Path(args.json_out).resolve()}")
    print(f"Outline report: {Path(args.report_out).resolve()}")
    print(f"Status: {outline['status']}, level_1_count={outline['level_1_count']}")


if __name__ == "__main__":
    main()
