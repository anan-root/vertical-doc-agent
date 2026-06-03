from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from construction_bidding_agent.document_parser.excellent_bid_image_promotion import (
    build_excellent_bid_image_promotion_package_from_file,
    write_excellent_bid_image_promotion_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="从图片 staging 结果生成候选入库包。")
    parser.add_argument("staging_json", help="图片 staging 诊断 JSON 路径。")
    parser.add_argument("--json-out", required=True, help="候选入库包 JSON 输出路径。")
    parser.add_argument("--report-out", required=True, help="候选入库包 Markdown 报告输出路径。")
    args = parser.parse_args()

    result = build_excellent_bid_image_promotion_package_from_file(args.staging_json)
    write_excellent_bid_image_promotion_outputs(result, args.json_out, args.report_out)
    summary = result["summary"]
    print(f"候选入库图片数：{summary['promote_image_count']}")
    print(f"候选入库套图组数：{summary['promote_group_count']}")
    print(f"候选入库单图数：{summary['promote_single_image_count']}")
    print(f"人工复核项数：{summary['review_item_count']}")
    print(f"报告：{args.report_out}")


if __name__ == "__main__":
    main()
