"""构建优秀标书统一素材库。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.excellent_bid_material_library import (  # noqa: E402
    build_excellent_bid_material_library_from_files,
    search_excellent_bid_materials,
    write_excellent_bid_material_library_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建优秀标书统一素材库。")
    parser.add_argument(
        "--index-json",
        action="append",
        required=True,
        help="优秀标书索引 JSON，可重复传入。支持 DOCX 素材索引和 PDF+DOCX 融合索引。",
    )
    parser.add_argument(
        "--library-id",
        default="default_excellent_bid_library",
        help="素材库 ID。",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "outputs" / "json" / "excellent_bid_material_library.json"),
        help="统一素材库 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "excellent_bid_material_library.md"),
        help="统一素材库 Markdown 报告输出路径。",
    )
    parser.add_argument(
        "--query",
        default="",
        help="可选：构建后顺便测试一次关键词检索。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="关键词检索返回数量。",
    )
    args = parser.parse_args()

    result = build_excellent_bid_material_library_from_files(
        [Path(path) for path in args.index_json],
        library_id=args.library_id,
    )
    write_excellent_bid_material_library_outputs(result, args.json_output, args.report_output)

    print(f"JSON: {Path(args.json_output).resolve()}")
    print(f"Report: {Path(args.report_output).resolve()}")
    print(
        "Counts: "
        f"sources={result.source_count}, "
        f"slices={result.slice_count}, "
        f"usable_tables={result.table_count}, "
        f"usable_images={result.image_count}, "
        f"docx_tables={result.docx_table_count}, "
        f"docx_images={result.docx_image_count}, "
        f"pdf_fallback_tables={result.pdf_fallback_table_count}, "
        f"pdf_fallback_images={result.pdf_fallback_image_count}, "
        f"pdf_reference_tables={result.pdf_reference_table_like_count}, "
        f"pdf_reference_images={result.pdf_reference_image_count}, "
        f"source_types={result.source_type_counts}, "
        f"qualities={result.material_quality_counts}"
    )

    if args.query:
        hits = search_excellent_bid_materials(result, query=args.query, top_k=args.top_k)
        print("Search:")
        for hit in hits:
            material = hit.slice
            path = " > ".join(material.section_path) if material else ""
            print(f"- {hit.material_slice_id} score={hit.score} reasons={hit.reasons} {path}")

    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
