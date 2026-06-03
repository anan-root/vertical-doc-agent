"""运行技术标质量修复离线回归样本。

本脚本不调用大模型，只验证目录约束、正文生成单元规划、image_slots 系统配图
和整本 Word 聚合质量闸门是否形成闭环。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from construction_bidding_agent.chapter_generator.chapter_writer import (
    OUTPUT_SCHEMA_VERSION,
    apply_auto_image_reuse,
    validate_chapter_output,
)
from construction_bidding_agent.chapter_generator.full_bid_docx_exporter import (
    build_full_bid_generation_result,
)
from construction_bidding_agent.chapter_generator.input_builder import build_chapter_generation_inputs
from construction_bidding_agent.outline_generator.refinement import (
    build_outline_refinement_inputs,
    validate_outline_refinement_output,
)


OUTPUT_JSON = Path("docs/product/technical-bid-quality-repair/regression-report.json")
OUTPUT_MD = Path("docs/product/technical-bid-quality-repair/regression-report.md")


def main() -> None:
    started = time.monotonic()
    outline_sample = _outline_sample()
    parse_result = _parse_result()
    outline_report = _run_outline_sample(outline_sample, parse_result)
    chapter_report = _run_process_chapter_samples()
    full_bid_report = _run_full_bid_quality_gate(chapter_report["packages"], chapter_report["chapters"])
    report = {
        "schema_version": "technical_bid_quality_repair_regression_v0.1",
        "duration_seconds": round(time.monotonic() - started, 4),
        "outline_sample": outline_report,
        "process_chapter_samples": {
            "sample_count": len(chapter_report["samples"]),
            "samples": chapter_report["samples"],
        },
        "full_bid_quality_gate": full_bid_report,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(_render_report(report), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")


def _run_outline_sample(outline: dict[str, Any], parse_result: dict[str, Any]) -> dict[str, Any]:
    packages = build_outline_refinement_inputs(outline, parse_result)
    construction_package = next(
        package
        for package in packages
        if (package.get("target_outline_node") or {}).get("category") == "施工方案"
    )
    overflowing_output = {
        "schema_version": "outline_refinement_v1",
        "target_node_id": construction_package["target_outline_node"]["node_id"],
        "level_1_title": construction_package["target_outline_node"]["level_1_title"],
        "level_1_title_unchanged": True,
        "domain": "construction",
        "category": "施工方案",
        "refined_children": [
            {
                "title": f"二级施工目录{i}",
                "children": [f"三级施工目录{i}-{j}" for j in range(1, 10)],
            }
            for i in range(1, 15)
        ],
    }
    validation = validate_outline_refinement_output(overflowing_output, construction_package)
    generation_packages = build_chapter_generation_inputs(outline, parse_result, excellent_bid_index={})
    return {
        "refinement_package_count": len(packages),
        "construction_rule": construction_package["granularity_rule"],
        "overflow_validation_valid": validation["valid"],
        "overflow_issue_types": [issue["type"] for issue in validation["issues"]],
        "cropped_level_2_count": len(overflowing_output["refined_children"]),
        "generation_unit_count": len(generation_packages),
        "generation_unit_types": _count_by(
            package["generation_unit"]["unit_type"] for package in generation_packages
        ),
        "generation_paths": [
            package["generation_unit"]["chapter_path"]
            for package in generation_packages
        ],
    }


def _run_process_chapter_samples() -> dict[str, Any]:
    topics = [
        ("steel", "钢筋工程施工", "钢筋加工、连接、绑扎流程示意图", "钢筋加工连接绑扎流程示意图"),
        ("formwork", "模板工程施工", "模板支设与加固体系示意图", "模板支设加固体系示意图"),
        ("concrete", "混凝土工程施工", "混凝土浇筑、振捣、养护和温控示意图", "混凝土浇筑振捣养护温控示意图"),
        ("waterproof", "防水工程施工", "地下室及屋面防水节点做法示意图", "地下室防水卷材节点做法示意图"),
        ("scaffold", "脚手架工程施工", "脚手架连墙件、剪刀撑搭设做法示意图", "脚手架连墙件剪刀撑搭设做法示意图"),
    ]
    packages: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for index, (key, heading, intent, caption) in enumerate(topics, start=1):
        package = _chapter_package(index, heading, caption)
        output = _chapter_output(package, heading, intent)
        validate_chapter_output(output, package)
        processed = apply_auto_image_reuse(output, package)
        validation = validate_chapter_output(processed, package)
        image_refs = [
            block
            for section in processed.get("sections") or []
            for block in section.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "image_ref"
        ]
        packages.append(package)
        chapters.append(processed)
        samples.append(
            {
                "key": key,
                "heading": heading,
                "valid": validation["valid"],
                "issue_types": [issue["type"] for issue in validation["issues"]],
                "image_slot_count": len(processed.get("image_slots") or []),
                "image_ref_count": len(image_refs),
                "image_ids": [block.get("image_id") for block in image_refs],
                "slot_reuse": processed.get("image_slot_reuse") or {},
            }
        )
    return {"packages": packages, "chapters": chapters, "samples": samples}


def _run_full_bid_quality_gate(packages: list[dict[str, Any]], chapters: list[dict[str, Any]]) -> dict[str, Any]:
    build = build_full_bid_generation_result(
        {"packages": packages},
        [{"provider": "offline", "model": "rule-regression", "chapters": chapters}],
        apply_current_image_policy=False,
        include_review_artifacts=False,
    )
    gate = build.summary.get("quality_gate_summary") or {}
    return {
        "status": gate.get("status"),
        "warning_issue_count": gate.get("warning_issue_count"),
        "total_image_ref_count": (gate.get("image_summary") or {}).get("total_image_ref_count"),
        "empty_heading_count": ((gate.get("empty_heading_summary") or {}).get("empty_heading_count")),
        "issues": gate.get("issues") or [],
    }


def _outline_sample() -> dict[str, Any]:
    civil_children = [
        "测量放线施工方案",
        "土方及基坑工程施工方案",
        "钢筋工程施工方案",
        "模板工程施工方案",
        "混凝土工程施工方案",
        "防水工程施工方案",
        "砌体工程施工方案",
        "脚手架工程施工方案",
        "后浇带及变形缝施工方案",
        "成品保护措施",
    ]
    return {
        "outline_id": "quality_repair_regression",
        "nodes": [
            {
                "node_id": "N-COMP",
                "title": "内容完整性",
                "domain": "general",
                "category": "技术标完整性说明",
                "template_source": "llm_required",
                "children": [],
            },
            {
                "node_id": "N-METHOD",
                "level": 1,
                "number": "1",
                "title": "主要施工方案与技术措施",
                "domain": "construction",
                "category": "施工方案",
                "score_rule": "主要施工方案完整，技术措施合理。",
                "template_source": "generated_from_requirement",
                "children": [
                    {
                        "node_id": "N-METHOD-001",
                        "level": 2,
                        "number": "1.1",
                        "title": "项目概况与施工总体认识",
                        "category": "施工方案",
                        "children": [{"level": 3, "title": f"概况分析{i}"} for i in range(1, 7)],
                    },
                    {
                        "node_id": "N-METHOD-002",
                        "level": 2,
                        "number": "1.2",
                        "title": "土建施工方案与技术措施",
                        "category": "施工方案",
                        "children": [
                            {"node_id": f"N-METHOD-002-{i:03d}", "level": 3, "title": title}
                            for i, title in enumerate(civil_children, start=1)
                        ],
                    },
                    {
                        "node_id": "N-METHOD-003",
                        "level": 2,
                        "number": "1.3",
                        "title": "工程重点难点分析及对策",
                        "category": "施工方案",
                        "children": [{"level": 3, "title": f"重点难点{i}"} for i in range(1, 7)],
                    },
                ],
            },
        ],
    }


def _parse_result() -> dict[str, Any]:
    return {
        "project_type": {"value": "construction"},
        "project_info": {
            "project_name": {"value": "质量修复回归项目"},
            "construction_location": {"value": "示例地点"},
            "construction_scale": {"value": "总建筑面积约50000平方米"},
            "tender_scope": {"value": "施工图纸及工程量清单范围"},
            "duration_requirement": {"value": "365日历天"},
            "quality_requirement": {"value": "合格"},
            "safety_civilization_requirement": {"value": "安全文明施工"},
        },
    }


def _chapter_package(index: int, heading: str, caption: str) -> dict[str, Any]:
    unit_id = f"GU-PROCESS-{index}"
    node_id = f"N-PROCESS-{index}"
    return {
        "task_type": "generate_technical_bid_chapter",
        "schema_version": "chapter_generation_input_v1",
        "project_info": {"project_name": "质量修复回归项目", "duration": "365日历天", "quality": "合格"},
        "generation_unit": {
            "unit_id": unit_id,
            "target_node_id": node_id,
            "chapter_path": ["主要施工方案与技术措施", heading],
            "child_headings": [],
            "domain": "construction",
            "category": "施工方案",
        },
        "score_point": {
            "score_point_raw": "主要施工方案与技术措施",
            "score_standard_raw": "主要施工方案完整，技术措施合理。",
        },
        "technical_requirements": [],
        "excellent_bid_references": [],
        "table_references": [],
        "image_candidate_pool": [
            {
                "image_id": f"IMG-{index}-MATCH",
                "caption": caption,
                "semantic_text": caption,
                "semantic_confidence": 0.92,
                "bound_section": heading,
                "source_section_path": ["优秀标书", heading],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "part_name": f"word/media/process-{index}.png",
                "material_slice_id": f"SRC0001-MPROCESS-{index}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
            },
            {
                "image_id": f"IMG-{index}-MISMATCH",
                "caption": "模板支设加固体系示意图" if "钢筋" in heading else "钢筋加工连接绑扎流程示意图",
                "semantic_text": "模板支设加固体系示意图" if "钢筋" in heading else "钢筋加工连接绑扎流程示意图",
                "semantic_confidence": 0.35,
                "bound_section": "不匹配章节",
                "source_section_path": ["优秀标书", "不匹配章节"],
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
                "part_name": f"word/media/mismatch-{index}.png",
                "material_slice_id": f"SRC0001-MMISMATCH-{index}",
                "source_bid_id": "SRC0001",
                "material_quality": "high",
            },
        ],
        "image_candidates": [],
        "auto_image_reuse_policy": {
            "enabled": True,
            "min_image_refs": 1,
            "target_image_refs": 1,
            "max_image_refs_total": 1,
            "max_images_per_section": 3,
        },
        "generation_constraints": {
            "generation_mode": "expanded",
            "forbidden_content": ["历史项目名称", "历史建设单位"],
        },
        "expanded_generation_policy": {
            "mode": "expanded",
            "section_type": "construction_process",
            "targets": {
                "min_sections": 1,
                "min_paragraphs_per_section": 1,
                "min_paragraphs_total": 1,
                "min_rich_tables": 0,
                "min_rows_per_rich_table": 0,
                "min_image_refs": 0,
                "min_image_placeholders": 0,
            },
            "reuse_level_policy": {},
            "writing_requirements": ["施工工艺章节回归验证。"],
        },
    }


def _chapter_output(package: dict[str, Any], heading: str, intent: str) -> dict[str, Any]:
    unit = package["generation_unit"]
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": heading,
        "sections": [
            {
                "heading": heading,
                "level": 3,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": f"{heading}应结合本工程结构特点和现场条件组织实施，明确施工准备、工艺流程、操作要点、质量控制、安全控制和成品保护要求。",
                    }
                ],
            }
        ],
        "image_slots": [
            {
                "section_heading": heading,
                "anchor_text": heading,
                "intent": intent,
                "preferred_type": "施工工艺示意图",
                "min_count": 1,
                "max_count": 1,
                "group_preferred": False,
            }
        ],
        "score_response_check": {
            "score_point_raw": "主要施工方案与技术措施",
            "response_summary": "已围绕主要施工方案与技术措施进行响应。",
            "covered": True,
            "evidence_headings": [heading],
        },
        "source_usage": [],
        "review_items": [],
    }


def _count_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        "# 技术标质量修复回归报告",
        "",
        f"- 总耗时秒：{report['duration_seconds']}",
        "",
        "## 目录小样",
        "",
        f"- 目录补强输入包数量：{report['outline_sample']['refinement_package_count']}",
        f"- 施工方案规则类型：{report['outline_sample']['construction_rule']['chapter_type']}",
        f"- 超限输出是否有效：{report['outline_sample']['overflow_validation_valid']}",
        f"- 裁剪后二级目录数量：{report['outline_sample']['cropped_level_2_count']}",
        f"- 正文生成单元数量：{report['outline_sample']['generation_unit_count']}",
        f"- 生成单元类型分布：{report['outline_sample']['generation_unit_types']}",
        "",
        "## 5 个施工工艺小节",
        "",
        "| 小节 | 校验 | 插图意图 | 图片数 | 图片ID |",
        "|---|---|---:|---:|---|",
    ]
    for sample in report["process_chapter_samples"]["samples"]:
        lines.append(
            f"| {sample['heading']} | {'通过' if sample['valid'] else '未通过'} | "
            f"{sample['image_slot_count']} | {sample['image_ref_count']} | {', '.join(sample['image_ids'])} |"
        )
    gate = report["full_bid_quality_gate"]
    lines.extend(
        [
            "",
            "## 质量闸门",
            "",
            f"- 状态：{gate.get('status')}",
            f"- 图片总数：{gate.get('total_image_ref_count')}",
            f"- 空标题数量：{gate.get('empty_heading_count')}",
            f"- warning 数：{gate.get('warning_issue_count')}",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
