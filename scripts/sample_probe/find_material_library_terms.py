from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="在素材库中查找关键词命中的切片。")
    parser.add_argument("--library", required=True)
    parser.add_argument("--terms", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    data = json.loads(Path(args.library).read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False)
    print("contains:", {term: (term in text) for term in args.terms})
    hits = []
    for slice_ in data.get("slices") or []:
        if not isinstance(slice_, dict):
            continue
        slice_text = json.dumps(slice_, ensure_ascii=False)
        matched = [term for term in args.terms if term in slice_text]
        if not matched:
            continue
        hits.append(
            {
                "material_slice_id": slice_.get("material_slice_id"),
                "title": slice_.get("title") or slice_.get("clean_title"),
                "section_path": slice_.get("section_path") or [],
                "image_count": slice_.get("image_count") or 0,
                "image_group_count": slice_.get("image_group_count") or 0,
                "matched_terms": matched,
            }
        )
    print("hit_count:", len(hits))
    for item in hits[: args.limit]:
        print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
