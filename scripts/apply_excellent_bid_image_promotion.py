from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from construction_bidding_agent.document_parser.excellent_bid_image_library_apply import (
    apply_excellent_bid_image_promotion_from_files,
    write_excellent_bid_image_library_apply_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="将优秀标书图片候选入库包应用为正式素材库预览。")
    parser.add_argument("material_library_json", help="现有正式素材库 JSON 路径。")
    parser.add_argument("promotion_package_json", help="图片候选入库包 JSON 路径。")
    parser.add_argument("--json-out", required=True, help="预览素材库 JSON 输出路径。")
    parser.add_argument("--report-out", required=True, help="预览报告 Markdown 输出路径。")
    parser.add_argument("--library-id", default=None, help="预览素材库 ID，未指定时自动生成。")
    args = parser.parse_args()

    result = apply_excellent_bid_image_promotion_from_files(
        args.material_library_json,
        args.promotion_package_json,
        output_library_id=args.library_id,
    )
    write_excellent_bid_image_library_apply_outputs(result, args.json_out, args.report_out)
    summary = result["summary"]
    print(f"新增图片资产数：{summary['promoted_image_asset_count']}")
    print(f"新增套图数：{summary['promoted_image_group_count']}")
    print(f"新增图片切片数：{summary['promoted_slice_count']}")
    print(f"跳过项数：{summary['skipped_item_count']}")
    print(f"预览库：{args.json_out}")
    print(f"报告：{args.report_out}")


if __name__ == "__main__":
    main()
