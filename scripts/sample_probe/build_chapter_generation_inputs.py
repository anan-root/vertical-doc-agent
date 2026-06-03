"""为技术标章节正文生成构建输入包。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import (  # noqa: E402
    build_chapter_generation_inputs_from_files,
    write_chapter_generation_inputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="构建技术标章节正文生成输入包。")
    parser.add_argument("--outline-json", required=True, help="补强后的技术标目录树 JSON 路径。")
    parser.add_argument("--parse-result", required=True, help="招标文件解析结果 JSON 路径。")
    parser.add_argument("--excellent-bid-index", help="优秀标书章节素材索引 JSON 路径。")
    parser.add_argument("--material-retrieval-inputs", help="章节生成素材检索输入包 JSON 路径。")
    parser.add_argument(
        "--include-domain",
        action="append",
        dest="include_domains",
        help="只构建指定领域的输入包，可重复传入，如 construction、design、management。",
    )
    parser.add_argument(
        "--no-split-core-level2",
        action="store_true",
        help="不按二级目录拆分核心长章节，全部按一级目录生成。",
    )
    parser.add_argument("--max-packages", type=int, help="最多输出多少个输入包，便于抽样调试。")
    parser.add_argument("--json-out", required=True, help="章节生成输入包 JSON 输出路径。")
    parser.add_argument("--report-out", help="章节生成输入包 Markdown 报告输出路径。")
    args = parser.parse_args()

    packages = build_chapter_generation_inputs_from_files(
        args.outline_json,
        args.parse_result,
        excellent_bid_index_json=args.excellent_bid_index,
        material_retrieval_inputs_json=args.material_retrieval_inputs,
        include_domains=args.include_domains,
        split_core_level2=not args.no_split_core_level2,
        max_packages=args.max_packages,
    )
    write_chapter_generation_inputs(packages, args.json_out, args.report_out)
    print(f"Chapter generation inputs: {Path(args.json_out).resolve()}")
    if args.report_out:
        print(f"Report: {Path(args.report_out).resolve()}")
    print(f"Package count: {len(packages)}")


if __name__ == "__main__":
    main()
