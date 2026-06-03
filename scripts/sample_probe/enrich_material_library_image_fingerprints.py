"""为优秀标书素材库补充图片指纹并输出治理报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.document_parser.image_fingerprints import (  # noqa: E402
    enrich_material_library_image_fingerprints,
    render_material_library_image_fingerprint_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="为优秀标书素材库补充图片指纹并输出治理报告。")
    parser.add_argument(
        "--material-library",
        default=str(ROOT / "outputs" / "json" / "excellent_bid_material_library_with_zhenggui_yunting_full.json"),
        help="原始优秀标书素材库 JSON 路径。",
    )
    parser.add_argument(
        "--output-json",
        default=str(
            ROOT
            / "outputs"
            / "json"
            / "excellent_bid_material_library_with_zhenggui_yunting_full_fingerprinted.json"
        ),
        help="补齐图片指纹后的素材库 JSON 输出路径。",
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "outputs" / "reports" / "material_library_image_fingerprints.md"),
        help="图片指纹治理报告输出路径。",
    )
    parser.add_argument(
        "--raw-root",
        default=str(ROOT / "data" / "raw"),
        help="原始投标文件根目录，用于回查 DOCX 内部图片。",
    )
    args = parser.parse_args()

    library_path = Path(args.material_library)
    data = json.loads(library_path.read_text(encoding="utf-8"))
    stats = enrich_material_library_image_fingerprints(data, raw_root=args.raw_root)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report_output = Path(args.report_output)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(render_material_library_image_fingerprint_report(data, stats), encoding="utf-8")

    print(f"JSON: {output_json.resolve()}")
    print(f"Report: {report_output.resolve()}")
    print(
        "Stats: "
        f"assets={stats['asset_count']}, "
        f"fingerprinted={stats['fingerprinted_asset_count']}, "
        f"new={stats['newly_enriched_count']}, "
        f"missing={stats['missing_count']}, "
        f"exact_duplicate_groups={stats['exact_duplicate_group_count']}, "
        f"cross_source_duplicate_groups={stats['cross_source_duplicate_group_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
