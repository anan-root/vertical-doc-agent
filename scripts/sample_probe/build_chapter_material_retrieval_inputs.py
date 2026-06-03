"""构建章节生成前的优秀标书素材检索输入包。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator import (  # noqa: E402
    build_chapter_material_retrieval_inputs_from_files,
    write_chapter_material_retrieval_inputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建章节生成素材检索输入包。")
    parser.add_argument("--outline-json", required=True, help="技术标目录树 JSON 路径。")
    parser.add_argument("--material-library-json", required=True, help="优秀标书统一素材库 JSON 路径。")
    parser.add_argument(
        "--include-domain",
        action="append",
        dest="include_domains",
        help="只构建指定领域输入包，可重复传入，如 construction / design。",
    )
    parser.add_argument("--top-k", type=int, default=5, help="每个章节最多保留多少个素材切片。")
    parser.add_argument("--max-packages", type=int, help="最多输出多少个输入包，便于抽样调试。")
    parser.add_argument("--json-out", required=True, help="JSON 输出路径。")
    parser.add_argument("--report-out", help="Markdown 报告输出路径。")
    args = parser.parse_args()

    packages = build_chapter_material_retrieval_inputs_from_files(
        args.outline_json,
        args.material_library_json,
        include_domains=args.include_domains,
        max_packages=args.max_packages,
        top_k=args.top_k,
    )
    write_chapter_material_retrieval_inputs(packages, args.json_out, args.report_out)
    print(f"Material retrieval inputs: {Path(args.json_out).resolve()}")
    if args.report_out:
        print(f"Report: {Path(args.report_out).resolve()}")
    print(f"Package count: {len(packages)}")
    print(
        "Counts: "
        f"materials={sum(len(pkg.get('matched_materials') or []) for pkg in packages)}, "
        f"paragraph_refs={sum(len(pkg.get('paragraph_references') or []) for pkg in packages)}, "
        f"table_refs={sum(len(pkg.get('table_references') or []) for pkg in packages)}, "
        f"image_refs={sum(len(pkg.get('image_references') or []) for pkg in packages)}, "
        f"warnings={sum(len(pkg.get('reuse_warnings') or []) for pkg in packages)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
