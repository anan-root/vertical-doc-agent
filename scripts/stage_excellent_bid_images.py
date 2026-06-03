from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from construction_bidding_agent.document_parser.excellent_bid_image_staging import (
    build_excellent_bid_image_staging_from_docx,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成优秀标书图片入库前 staging 诊断报告。")
    parser.add_argument("docx_path", help="待 staging 的优秀技术标 DOCX 文件路径。")
    parser.add_argument(
        "--existing-library",
        default="outputs/json/excellent_bid_material_library_with_image_assets.json",
        help="现有正式素材库 JSON 路径。",
    )
    parser.add_argument("--json-out", required=True, help="staging 诊断 JSON 输出路径。")
    parser.add_argument("--report-out", required=True, help="staging 诊断 Markdown 报告输出路径。")
    parser.add_argument("--index-json-out", help="中间 DOCX 章节素材索引 JSON 输出路径。")
    parser.add_argument("--index-report-out", help="中间 DOCX 章节素材索引 Markdown 报告输出路径。")
    parser.add_argument("--library-id", help="staging 素材库 ID。")
    parser.add_argument(
        "--root-dir",
        default=".",
        help="相对路径解析根目录，默认当前项目根目录。",
    )
    args = parser.parse_args()

    docx_path = Path(args.docx_path)
    result = build_excellent_bid_image_staging_from_docx(
        docx_path,
        existing_library_path=args.existing_library,
        library_id=args.library_id,
        root_dir=args.root_dir,
        index_json_path=args.index_json_out,
        index_report_path=args.index_report_out,
        staging_json_path=args.json_out,
        staging_report_path=args.report_out,
    )
    summary = result["summary"]
    print(f"图片数：{summary['image_count']}")
    print(f"套图组数：{summary['group_count']}")
    print(f"候选复用：{summary['decision_counts'].get('candidate_reuse', 0)}")
    print(f"正式库完全重复：{summary['exact_duplicate_existing_count']}")
    print(f"需要人工复核：{summary['review_required_count']}")
    print(f"报告：{args.report_out}")


if __name__ == "__main__":
    main()
