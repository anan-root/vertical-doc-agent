"""运行图文块自动插图后端小样验证。

该脚本只验证素材召回、图文块候选、系统自动插图和跨章节去重逻辑，
不调用 LLM，也不生成整本标书。
"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.chapter_generator.chapter_writer import (  # noqa: E402
    OUTPUT_SCHEMA_VERSION,
    dedupe_images_across_chapters,
    postprocess_chapter_images,
    validate_chapter_output,
)
from construction_bidding_agent.chapter_generator.input_builder import (  # noqa: E402
    build_chapter_generation_inputs,
    write_chapter_generation_inputs,
)
from construction_bidding_agent.chapter_generator.material_retrieval_input_builder import (  # noqa: E402
    build_chapter_material_retrieval_inputs,
    write_chapter_material_retrieval_inputs,
)


SAMPLE_SECTIONS = [
    {
        "node_id": "sample_process_001",
        "number": "1.1",
        "title": "钢筋工程施工方案",
        "category": "施工方案",
        "intent": "钢筋加工、直螺纹连接、钢筋绑扎流程示意图",
        "anchor": "钢筋加工、连接、绑扎",
        "paragraph_terms": "钢筋加工成型、直螺纹连接、箍筋绑扎、梁板钢筋安装和保护层控制。",
        "expect_images": True,
    },
    {
        "node_id": "sample_process_002",
        "number": "1.2",
        "title": "模板工程施工方案",
        "category": "施工方案",
        "intent": "模板支设、模板加固、支撑体系施工示意图",
        "anchor": "模板支设与加固",
        "paragraph_terms": "模板支设、木方背楞、对拉螺杆、满堂支撑架、梁板模板加固和拆模控制。",
        "expect_images": True,
    },
    {
        "node_id": "sample_process_003",
        "number": "1.3",
        "title": "混凝土浇筑及大体积温控措施",
        "category": "施工方案",
        "intent": "混凝土浇筑、振捣、养护和大体积温控措施示意图",
        "anchor": "混凝土浇筑振捣与温控",
        "paragraph_terms": "混凝土浇筑、分层振捣、测温监控、覆盖养护、大体积混凝土温控和裂缝控制。",
        "expect_images": True,
    },
    {
        "node_id": "sample_process_004",
        "number": "1.4",
        "title": "地下室及屋面防水施工技术",
        "category": "施工方案",
        "intent": "地下室防水、屋面防水、卷材铺贴和节点处理做法示意图",
        "anchor": "防水卷材铺贴与节点处理",
        "paragraph_terms": "地下室防水、屋面防水、卷材铺贴、阴阳角附加层、节点收头、闭水试验和成品保护。",
        "expect_images": True,
    },
    {
        "node_id": "sample_process_005",
        "number": "1.5",
        "title": "外脚手架搭设及安全防护措施",
        "category": "施工方案",
        "intent": "外脚手架搭设、连墙件、剪刀撑和安全防护措施示意图",
        "anchor": "外脚手架搭设与安全防护",
        "paragraph_terms": "脚手架立杆、纵横向水平杆、连墙件、剪刀撑、安全网、临边防护和验收挂牌。",
        "expect_images": True,
    },
    {
        "node_id": "sample_process_006",
        "number": "1.6",
        "title": "工程重点、难点分析及对策",
        "category": "施工方案",
        "intent": "工程重点难点分析及对策说明配图",
        "anchor": "工程重点难点分析",
        "paragraph_terms": "围绕工程特点、现场条件、组织协调、质量安全风险和施工部署难点提出针对性对策。",
        "expect_images": False,
    },
]


def main() -> int:
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    json_dir = ROOT / "outputs" / "json"
    report_dir = ROOT / "outputs" / "reports"
    json_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    library_path = json_dir / "excellent_bid_material_library_two_word_sources.json"
    parse_path = _latest_parse_result(json_dir)
    if not library_path.exists():
        raise FileNotFoundError(f"未找到两份 Word 素材库：{library_path}")
    if not parse_path.exists():
        raise FileNotFoundError("未找到招标文件解析结果。")

    outline = _sample_outline(now)
    parse_result = _read_json(parse_path)
    library = _read_json(library_path)

    outline_path = json_dir / "text_image_block_auto_insert_sample_outline.json"
    retrieval_path = json_dir / "text_image_block_auto_insert_sample_material_retrieval_inputs.json"
    retrieval_report_path = report_dir / "text_image_block_auto_insert_sample_material_retrieval_inputs.md"
    inputs_path = json_dir / "text_image_block_auto_insert_sample_generation_inputs.json"
    inputs_report_path = report_dir / "text_image_block_auto_insert_sample_generation_inputs.md"
    result_path = json_dir / "text_image_block_auto_insert_sample_result.json"
    report_path = report_dir / "text_image_block_auto_insert_sample_report.md"

    outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    retrieval_packages = build_chapter_material_retrieval_inputs(
        outline,
        library,
        include_domains=["construction"],
        top_k=5,
    )
    write_chapter_material_retrieval_inputs(retrieval_packages, retrieval_path, retrieval_report_path)
    retrieval_index = {
        "schema_version": "chapter_material_retrieval_input_index_v1",
        "package_count": len(retrieval_packages),
        "packages": retrieval_packages,
    }
    generation_packages = build_chapter_generation_inputs(
        outline,
        parse_result,
        material_retrieval_inputs=retrieval_index,
        include_domains=["construction"],
    )
    write_chapter_generation_inputs(generation_packages, inputs_path, inputs_report_path)

    chapters: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    for package in generation_packages:
        output = _synthesize_output(package)
        validation_before = validate_chapter_output(copy.deepcopy(output), package)
        processed = postprocess_chapter_images(output, package)
        validation_after = validate_chapter_output(copy.deepcopy(processed), package)
        chapters.append(processed)
        validations.append(
            {
                "title": processed.get("title"),
                "before": validation_before,
                "after": validation_after,
            }
        )

    pre_dedupe_summaries = [
        _chapter_metrics(package, chapter)
        for package, chapter in zip(generation_packages, chapters, strict=False)
    ]
    cross_dedupe = dedupe_images_across_chapters(chapters)
    post_dedupe_summaries = [
        _chapter_metrics(package, chapter)
        for package, chapter in zip(generation_packages, chapters, strict=False)
    ]

    repeated_after = _repeated_image_keys(chapters)
    result = {
        "schema_version": "text_image_block_auto_insert_sample_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "deterministic_backend_postprocess_sample_no_llm",
        "inputs": {
            "outline_json": str(outline_path),
            "parse_result_json": str(parse_path),
            "material_library_json": str(library_path),
            "material_retrieval_inputs_json": str(retrieval_path),
            "chapter_generation_inputs_json": str(inputs_path),
        },
        "summary": {
            "sample_chapter_count": len(chapters),
            "total_images_before_cross_chapter_dedupe": sum(
                item["final_image_count"] for item in pre_dedupe_summaries
            ),
            "total_images_after_cross_chapter_dedupe": sum(
                item["final_image_count"] for item in post_dedupe_summaries
            ),
            "chapters_with_images_after_dedupe": sum(
                1 for item in post_dedupe_summaries if item["final_image_count"] > 0
            ),
            "cross_chapter_dedupe": cross_dedupe,
            "repeated_image_keys_after_dedupe": repeated_after,
        },
        "pre_dedupe_chapters": pre_dedupe_summaries,
        "chapters": post_dedupe_summaries,
        "validation": validations,
        "draft_chapters": chapters,
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(result, report_path), encoding="utf-8")

    print(f"Result JSON: {result_path}")
    print(f"Report: {report_path}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    for item in post_dedupe_summaries:
        counts = item["candidate_counts"]
        print(
            item["title"],
            "images=",
            item["final_image_count"],
            "blocks=",
            counts["text_image_block_reuse_candidates"],
            "groups=",
            counts["image_group_candidate_pool"],
            "pool=",
            counts["image_candidate_pool"],
        )
    return 0


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_parse_result(json_dir: Path) -> Path:
    patterns = ["batch_tender_01_*parse_result_parallel.json", "batch_tender_01_*parse_result.json"]
    for pattern in patterns:
        candidates = sorted(json_dir.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
    return json_dir / "missing_parse_result.json"


def _sample_outline(now: str) -> dict[str, Any]:
    return {
        "schema_version": "technical_bid_outline_v1",
        "outline_id": f"text_image_block_auto_insert_sample_{now}",
        "project_type": "construction",
        "nodes": [
            {
                "node_id": "sample_l1_001",
                "number": "1",
                "title": "主要施工方案与技术措施",
                "category": "施工方案",
                "domain": "construction",
                "score_rule": "主要施工方案与技术措施完整、科学、可行，关键工艺措施针对性强。",
                "children": [
                    {
                        "node_id": item["node_id"],
                        "number": item["number"],
                        "title": item["title"],
                        "category": item["category"],
                        "domain": "construction",
                        "children": [],
                    }
                    for item in SAMPLE_SECTIONS
                ],
            }
        ],
    }


def _synthesize_output(package: dict[str, Any]) -> dict[str, Any]:
    unit = package.get("generation_unit") or {}
    title = (unit.get("chapter_path") or ["未命名章节"])[-1]
    spec = {item["title"]: item for item in SAMPLE_SECTIONS}.get(title, {})
    paragraph_terms = str(spec.get("paragraph_terms") or title)
    sections = [
        {
            "heading": title,
            "level": 3,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": f"{title}应结合本工程结构特点和施工组织安排实施，重点控制{paragraph_terms}",
                },
                {
                    "type": "paragraph",
                    "text": f"施工过程中按照技术交底、样板引路、过程检查和验收闭环组织实施，确保{paragraph_terms}",
                },
                {
                    "type": "rich_table",
                    "title": f"{title}控制要点表",
                    "columns": [
                        {"key": "col_1", "title": "序号"},
                        {"key": "col_2", "title": "控制项目"},
                        {"key": "col_3", "title": "控制措施"},
                    ],
                    "rows": [
                        {"cells": {"col_1": "1", "col_2": "技术准备", "col_3": f"结合{paragraph_terms}完成专项技术交底。"}},
                        {"cells": {"col_1": "2", "col_2": "过程控制", "col_3": "落实旁站检查、实测实量和隐蔽验收。"}},
                        {"cells": {"col_1": "3", "col_2": "质量验收", "col_3": "按规范、图纸和方案要求形成验收记录。"}},
                        {"cells": {"col_1": "4", "col_2": "成品保护", "col_3": "完成后采取覆盖、防碰撞和交叉作业保护措施。"}},
                    ],
                },
            ],
        }
    ]
    image_slots = []
    if spec.get("intent"):
        image_slots.append(
            {
                "section_heading": title,
                "anchor_text": spec.get("anchor") or title,
                "intent": spec["intent"],
                "preferred_type": "施工工艺示意图",
                "min_count": 2 if spec.get("expect_images") else 0,
                "max_count": 6,
                "group_preferred": True,
            }
        )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit.get("unit_id"),
        "target_node_id": unit.get("target_node_id"),
        "chapter_path": unit.get("chapter_path"),
        "title": title,
        "sections": sections,
        "image_slots": image_slots,
        "score_response_check": {
            "score_point_raw": (package.get("score_point") or {}).get("score_point_raw") or "主要施工方案与技术措施",
            "response_summary": f"已围绕{title}形成施工技术措施并提出插图意图。",
            "covered": True,
            "evidence_headings": [title],
        },
        "source_usage": [],
        "review_items": [],
    }


def _iter_image_refs(chapter: dict[str, Any]):
    for section in chapter.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "image_ref":
                yield section, block


def _ref_key(block: dict[str, Any]) -> str:
    for key in ["image_asset_id", "canonical_image_id", "source_part_name", "image_id"]:
        value = block.get(key)
        if value:
            return f"{key}:{value}"
    return ""


def _chapter_metrics(package: dict[str, Any], chapter: dict[str, Any]) -> dict[str, Any]:
    refs = list(_iter_image_refs(chapter))
    group_counts = Counter(str(block.get("image_group_id") or "") for _, block in refs if block.get("image_group_id"))
    split_groups = []
    for group_id, count in group_counts.items():
        expected = None
        for _, block in refs:
            if str(block.get("image_group_id") or "") == group_id:
                expected = int(block.get("group_member_count") or 0) or None
                break
        if expected and count != expected:
            split_groups.append({"image_group_id": group_id, "inserted": count, "expected": expected})
    duplicate_keys = [
        key
        for key, count in Counter(_ref_key(block) for _, block in refs if _ref_key(block)).items()
        if count > 1
    ]
    return {
        "title": chapter.get("title"),
        "chapter_path": chapter.get("chapter_path") or [],
        "candidate_counts": {
            "text_image_block_candidates": len(package.get("text_image_block_candidates") or []),
            "text_image_block_reuse_candidates": len(package.get("text_image_block_reuse_candidates") or []),
            "image_group_candidate_pool": len(package.get("image_group_candidate_pool") or []),
            "image_candidate_pool": len(package.get("image_candidate_pool") or []),
            "image_candidates_llm_slim": len(package.get("image_candidates") or []),
        },
        "policy": package.get("auto_image_reuse_policy") or {},
        "auto_image_reuse": chapter.get("auto_image_reuse") or {},
        "image_slot_reuse": chapter.get("image_slot_reuse") or {},
        "cross_chapter_image_dedup": chapter.get("cross_chapter_image_dedup") or {},
        "final_image_count": len(refs),
        "unique_group_count": len(group_counts),
        "split_groups": split_groups,
        "duplicate_image_keys_within_chapter": duplicate_keys,
        "inserted_images": [
            {
                "section_heading": section.get("heading"),
                "image_id": block.get("image_id"),
                "image_asset_id": block.get("image_asset_id"),
                "image_group_id": block.get("image_group_id"),
                "group_member_index": block.get("group_member_index"),
                "group_member_count": block.get("group_member_count"),
                "caption": block.get("caption"),
                "source_reuse_mode": block.get("source_reuse_mode"),
                "text_image_block_id": block.get("text_image_block_id"),
                "text_image_block_match_confidence": block.get("text_image_block_match_confidence"),
                "material_slice_id": block.get("material_slice_id"),
                "source_section_path": block.get("source_section_path") or block.get("section_path") or [],
                "semantic_text": block.get("semantic_text"),
            }
            for section, block in refs
        ],
        "top_text_image_block_reuse_candidates": [
            {
                "block_id": item.get("block_id"),
                "title": item.get("title"),
                "primary_topic": item.get("primary_topic"),
                "match_level": item.get("match_level"),
                "match_confidence": item.get("match_confidence"),
                "image_group_count": len(item.get("image_group_candidates") or []),
                "image_count": len(item.get("image_candidates") or []),
                "risk_flags": item.get("risk_flags") or [],
            }
            for item in (package.get("text_image_block_reuse_candidates") or [])[:5]
        ],
    }


def _repeated_image_keys(chapters: list[dict[str, Any]]) -> dict[str, int]:
    refs = [block for chapter in chapters for _, block in _iter_image_refs(chapter)]
    counts = Counter(_ref_key(block) for block in refs if _ref_key(block))
    return {key: count for key, count in counts.items() if count > 1}


def _render_report(result: dict[str, Any], report_path: Path) -> str:
    summary = result["summary"]
    cross_dedupe = summary.get("cross_chapter_dedupe") or {}
    repeated_after = summary.get("repeated_image_keys_after_dedupe") or {}
    lines = [
        "# 图文块自动插图小样验证报告",
        "",
        "## 验证范围",
        "",
        "- 验证方式：后端确定性小样，不调用 LLM，不跑整本标书。",
        "- 验证目标：确认强匹配图文块能进入系统自动插图，观察图片数量、套图完整性、重复图片和负向章节过滤。",
        f"- 素材库：`{Path(result['inputs']['material_library_json']).name}`",
        f"- 解析结果：`{Path(result['inputs']['parse_result_json']).name}`",
        "",
        "## 总览",
        "",
        f"- 小样章节数：{summary['sample_chapter_count']}",
        f"- 跨章节去重前图片数：{summary['total_images_before_cross_chapter_dedupe']}",
        f"- 跨章节去重后图片数：{summary['total_images_after_cross_chapter_dedupe']}",
        f"- 去重后有图章节数：{summary['chapters_with_images_after_dedupe']}",
        f"- 跨章节去重移除：{cross_dedupe.get('removed_count', 0)} 张/组成员",
        f"- 去重后重复图片键：{len(repeated_after)}",
        "",
        "## 分章节结果",
        "",
        "| 章节 | 图文块复用候选 | 套图池 | 图片池 | 插入图片 | 套图数 | 拆套风险 | 章内重复 | 说明 |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for item in result["chapters"]:
        title = item["title"]
        counts = item["candidate_counts"]
        split = "有" if item["split_groups"] else "无"
        duplicate = "有" if item["duplicate_image_keys_within_chapter"] else "无"
        if title == "工程重点、难点分析及对策":
            note = "负向对照：无施工工艺图，符合预期" if item["final_image_count"] == 0 else "负向对照异常：插入了图片"
        elif item["final_image_count"] == 0:
            note = "未插图，需要继续看召回或匹配阈值"
        elif item["top_text_image_block_reuse_candidates"]:
            note = "使用或具备强图文块候选"
        else:
            note = "主要依赖普通图片或套图池"
        lines.append(
            f"| {title} | {counts['text_image_block_reuse_candidates']} | "
            f"{counts['image_group_candidate_pool']} | {counts['image_candidate_pool']} | "
            f"{item['final_image_count']} | {item['unique_group_count']} | {split} | {duplicate} | {note} |"
        )
    lines.extend(["", "## 插入图片明细", ""])
    for item in result["chapters"]:
        lines.extend([f"### {item['title']}", ""])
        if item["top_text_image_block_reuse_candidates"]:
            lines.append("强图文块复用候选：")
            for block in item["top_text_image_block_reuse_candidates"]:
                lines.append(
                    f"- `{block['block_id']}`：{block.get('title') or ''}；"
                    f"主题={block.get('primary_topic') or ''}；置信度={block.get('match_confidence')}；"
                    f"套图={block.get('image_group_count')}；散图={block.get('image_count')}"
                )
        else:
            lines.append("强图文块复用候选：无")
        if item["inserted_images"]:
            lines.extend(["", "| 序号 | 题注 | 套图 | 图文块 | 来源小节 |", "|---:|---|---|---|---|"])
            for index, image in enumerate(item["inserted_images"], start=1):
                group = image.get("image_group_id") or ""
                if group:
                    group = f"{group} ({image.get('group_member_index')}/{image.get('group_member_count')})"
                source_path = " > ".join(str(part) for part in image.get("source_section_path") or [])
                lines.append(
                    f"| {index} | {image.get('caption') or ''} | {group} | "
                    f"{image.get('text_image_block_id') or ''} | {source_path} |"
                )
        else:
            lines.extend(["", "插入图片：无"])
        if item.get("cross_chapter_image_dedup"):
            lines.extend(
                [
                    "",
                    f"跨章节去重：移除 {item['cross_chapter_image_dedup'].get('removed_count', 0)} 张/组成员。",
                ]
            )
        lines.append("")
    lines.extend(["## 初步结论", ""])
    positive = [item for item in result["chapters"] if item["title"] != "工程重点、难点分析及对策"]
    zero_positive = [item["title"] for item in positive if item["final_image_count"] == 0]
    split_titles = [item["title"] for item in positive if item["split_groups"]]
    duplicate_titles = [item["title"] for item in positive if item["duplicate_image_keys_within_chapter"]]
    if not zero_positive:
        lines.append("- 典型施工工艺章节均能插入图片，说明图文块候选已经进入最终自动插图链路。")
    else:
        lines.append("- 以下施工工艺章节仍未插图：" + "、".join(zero_positive))
    if not split_titles:
        lines.append("- 当前小样未发现拆套图问题。")
    else:
        lines.append("- 以下章节存在拆套风险：" + "、".join(split_titles))
    if not duplicate_titles and not repeated_after:
        lines.append("- 当前小样未发现章内或跨章节重复图片。")
    else:
        lines.append("- 仍存在重复图片风险，需要继续治理。")
    negative = next((item for item in result["chapters"] if item["title"] == "工程重点、难点分析及对策"), None)
    if negative and negative["final_image_count"] == 0:
        lines.append("- “工程重点、难点分析及对策”作为负向对照未插入钢筋、模板等工艺图，说明通用分析类章节误配施工工艺图的问题有所收敛。")
    else:
        lines.append("- 负向对照异常，需要继续加强通用分析类章节过滤。")
    lines.extend(
        [
            "",
            "## 文件",
            "",
            f"- JSON 结果：`{ROOT / 'outputs' / 'json' / 'text_image_block_auto_insert_sample_result.json'}`",
            f"- Markdown 报告：`{report_path}`",
            f"- 小样目录：`{result['inputs']['outline_json']}`",
            f"- 素材检索包：`{result['inputs']['material_retrieval_inputs_json']}`",
            f"- 章节生成输入包：`{result['inputs']['chapter_generation_inputs_json']}`",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
