import zipfile
from pathlib import Path

from PIL import Image

from construction_bidding_agent.chapter_generator.full_bid_docx_exporter import (
    PACKAGE_PLACEHOLDER_TEXT,
    build_full_bid_generation_result,
    export_full_bid_docx_from_files,
)


def test_build_full_bid_generation_result_groups_generated_and_placeholder_packages():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["主要施工方案与技术措施", "土建工程施工方案与技术措施"],
                ["测量放线", "钢筋工程"],
            ),
            _package(
                "U2",
                "N2",
                ["主要施工方案与技术措施", "装饰工程施工方案与技术措施"],
                ["抹灰工程"],
            ),
            _package("U3", "N3", ["施工进度表"], ["进度计划说明"]),
        ]
    }
    generation_results = [
        {
            "provider": "test",
            "model": "test-model",
            "chapters": [
                {
                    "unit_id": "U1",
                    "target_node_id": "N1",
                    "chapter_path": ["主要施工方案与技术措施", "土建工程施工方案与技术措施"],
                    "title": "土建工程施工方案与技术措施",
                    "sections": [
                        {
                            "heading": "测量放线",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "测量放线正文。"}],
                        }
                    ],
                    "score_response_check": {"response_summary": "已覆盖土建施工方案评分要求。"},
                    "review_items": [{"severity": "low", "message": "复核测量基准点。"}],
                }
            ],
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )

    summary = build.summary
    assert summary["package_count"] == 3
    assert summary["level1_chapter_count"] == 2
    assert summary["generated_package_count"] == 1
    assert summary["placeholder_package_count"] == 2
    assert summary["quality_gate_summary"]["schema_version"] == "technical_bid_quality_gate_v0.1"
    assert summary["quality_gate_summary"]["image_summary"]["total_image_ref_count"] == 0

    chapters = build.generation_result["chapters"]
    main_chapter = chapters[0]
    assert main_chapter["chapter_path"] == ["主要施工方案与技术措施"]
    assert main_chapter["score_response_check"]["covered"] is False
    assert [section["heading"] for section in main_chapter["sections"][:3]] == [
        "土建工程施工方案与技术措施",
        "测量放线",
        "装饰工程施工方案与技术措施",
    ]
    assert main_chapter["sections"][0]["blocks"][0]["text"] == "已覆盖土建施工方案评分要求。"
    assert main_chapter["sections"][2]["blocks"][0]["text"] == PACKAGE_PLACEHOLDER_TEXT
    assert "主要施工方案与技术措施 > 土建工程施工方案与技术措施" in main_chapter["review_items"][0]["message"]

    schedule_chapter = chapters[1]
    assert schedule_chapter["chapter_path"] == ["施工进度表"]
    assert schedule_chapter["sections"][0]["heading"] == "进度计划说明"
    assert schedule_chapter["sections"][0]["level"] == 2


def test_build_full_bid_generation_result_can_omit_review_artifacts():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["主要施工方案与技术措施", "土建工程施工方案与技术措施"],
                ["测量放线"],
            )
        ]
    }
    generation_results = [
        {
            "provider": "test",
            "model": "test-model",
            "chapters": [
                {
                    "unit_id": "U1",
                    "target_node_id": "N1",
                    "chapter_path": ["主要施工方案与技术措施", "土建工程施工方案与技术措施"],
                    "sections": [
                        {
                            "heading": "测量放线",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "测量放线正文。"}],
                        }
                    ],
                    "score_response_check": {"response_summary": "已覆盖土建施工方案评分要求。"},
                    "review_items": [{"severity": "low", "message": "复核测量基准点。"}],
                }
            ],
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        include_review_artifacts=False,
    )

    chapter = build.generation_result["chapters"][0]
    assert chapter["score_response_check"]["response_summary"] == ""
    assert chapter["review_items"] == []
    assert [section["heading"] for section in chapter["sections"]] == ["土建工程施工方案与技术措施", "测量放线"]
    assert chapter["sections"][1]["blocks"][0]["type"] == "paragraph"


def test_build_full_bid_generation_result_avoids_duplicate_empty_level2_when_generated_heading_matches_path():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["主要施工方案与技术措施", "钢筋工程施工"],
                [],
            )
        ]
    }
    generation_results = [
        {
            "chapters": [
                {
                    "unit_id": "U1",
                    "target_node_id": "N1",
                    "chapter_path": ["主要施工方案与技术措施", "钢筋工程施工"],
                    "sections": [
                        {
                            "heading": "钢筋工程施工",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "钢筋工程正文。"}],
                        }
                    ],
                }
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        include_review_artifacts=False,
    )

    sections = build.generation_result["chapters"][0]["sections"]
    assert [section["heading"] for section in sections] == ["钢筋工程施工"]
    assert sections[0]["level"] == 2
    assert build.summary["empty_heading_summary"]["empty_heading_count"] == 0


def test_build_full_bid_generation_result_assembles_level3_split_units_under_parent_level2():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1-1-1",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "测量放线施工方案"],
                [],
            ),
            _package(
                "U2",
                "N1-1-2",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "钢筋工程施工方案"],
                [],
            ),
        ]
    }
    generation_results = [
        {
            "provider": "test",
            "model": "test-model",
            "chapters": [
                {
                    "unit_id": "U1",
                    "target_node_id": "N1-1-1",
                    "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施", "测量放线施工方案"],
                    "title": "测量放线施工方案",
                    "sections": [
                        {
                            "heading": "控制网复核",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "测量控制网复核正文。"}],
                        }
                    ],
                },
                {
                    "unit_id": "U2",
                    "target_node_id": "N1-1-2",
                    "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施", "钢筋工程施工方案"],
                    "title": "钢筋工程施工方案",
                    "sections": [
                        {
                            "heading": "钢筋加工与连接",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "钢筋加工与连接正文。"}],
                        }
                    ],
                },
            ],
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        include_review_artifacts=False,
    )

    sections = build.generation_result["chapters"][0]["sections"]
    assert [section["heading"] for section in sections] == [
        "土建施工方案与技术措施",
        "测量放线施工方案",
        "钢筋工程施工方案",
    ]
    assert [section["level"] for section in sections] == [2, 3, 3]
    assert sections[1]["blocks"][0] == {"type": "internal_heading", "text": "控制网复核"}
    assert sections[2]["blocks"][0] == {"type": "internal_heading", "text": "钢筋加工与连接"}


def test_build_full_bid_generation_result_filters_empty_generated_headings():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1-1-1",
                ["主要施工方案与技术措施", "土建施工方案与技术措施", "测量放线施工方案"],
                [],
            )
        ]
    }
    generation_results = [
        {
            "chapters": [
                {
                    "unit_id": "U1",
                    "target_node_id": "N1-1-1",
                    "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施", "测量放线施工方案"],
                    "sections": [
                        {"heading": "测量放线施工方案", "level": 3, "blocks": []},
                        {
                            "heading": "控制网复核",
                            "level": 3,
                            "blocks": [{"type": "paragraph", "text": "测量控制网复核正文。"}],
                        },
                    ],
                }
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        include_review_artifacts=False,
    )

    sections = build.generation_result["chapters"][0]["sections"]
    assert [section["heading"] for section in sections] == ["土建施工方案与技术措施", "测量放线施工方案"]
    assert sections[1]["blocks"][0] == {"type": "internal_heading", "text": "控制网复核"}
    assert all(section.get("blocks") for section in sections[1:])
    assert build.summary["empty_heading_summary"]["empty_heading_count"] == 0
    assert build.summary["empty_heading_summary"]["consecutive_empty_heading_count"] == 0


def test_build_full_bid_generation_result_dedupes_same_group_across_sections():
    members = [
        _image_ref(
            f"IMG-G{index}",
            f"ASSET-G{index}",
            f"钢筋加工示意{index}",
            group_id="GROUP-STEEL",
            member_index=index,
            member_count=2,
        )
        for index in range(1, 3)
    ]
    chapter_inputs = {
            "packages": [
                _package("U1", "N1", ["主要施工方案与技术措施", "土建施工方案与技术措施"], ["钢筋工程"]),
                _package("U2", "N2", ["主要施工方案与技术措施", "钢筋工程专项施工方案"], ["钢筋工程质量控制"]),
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter("U1", "N1", ["主要施工方案与技术措施", "土建施工方案与技术措施"], "钢筋工程制作安装及连接技术", members),
                _generated_chapter("U2", "N2", ["主要施工方案与技术措施", "钢筋工程专项施工方案"], "钢筋工程加工与安装质量控制", members),
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-G1", "IMG-G2"]
    assert build.summary["image_dedupe_summary"]["removed_duplicate_group_count"] == 2
    assert build.summary["quality_gate_summary"]["dedupe_summary"]["removed_duplicate_group_count"] == 2
    assert any(issue["type"] == "duplicate_images_removed" for issue in build.summary["quality_gate_summary"]["issues"])


def test_build_full_bid_generation_result_quality_gate_warns_for_low_construction_images():
    chapter_inputs = {
        "packages": [
            _package("U1", "N1", ["主要施工方案与技术措施", "土建施工方案与技术措施"], ["钢筋工程"])
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                    "钢筋工程施工",
                    [_image_ref("IMG-STEEL", "ASSET-STEEL", "钢筋加工示意图", semantic_text="钢筋加工示意图")],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )

    gate = build.summary["quality_gate_summary"]
    assert gate["status"] == "warning"
    assert gate["image_summary"]["total_image_ref_count"] == 1
    assert any(issue["type"] == "construction_method_images_low" for issue in gate["issues"])


def test_build_full_bid_generation_result_removes_incompatible_elevator_images():
    chapter_inputs = {
        "packages": [
            _package("U1", "N1", ["主要施工方案与技术措施", "电梯工程施工方案与技术措施"], ["电梯井道验收"])
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["主要施工方案与技术措施", "电梯工程施工方案与技术措施"],
                    "电梯井道验收及预埋件复核",
                    [
                        _image_ref("IMG-STEEL", "ASSET-STEEL", "绑扎楼板钢筋", semantic_text="绑扎楼板钢筋"),
                        _image_ref("IMG-ELEVATOR", "ASSET-ELEVATOR", "电梯导轨安装校正示意图", semantic_text="电梯导轨安装校正示意图"),
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-ELEVATOR"]
    assert build.summary["image_dedupe_summary"]["removed_incompatible_count"] == 1


def test_build_full_bid_generation_result_removes_environment_subtopic_mismatch():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                ["噪声污染控制措施"],
            )
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                    "噪声污染控制措施",
                    [
                        _image_ref(
                            "IMG-NOISE",
                            "ASSET-NOISE",
                            "小型切割机外防护罩防噪声防火花溅射示意图",
                            semantic_text="小型切割机外防护罩防噪声防火花溅射",
                        ),
                        _image_ref(
                            "IMG-WATER",
                            "ASSET-WATER",
                            "水污染控制措施示意图",
                            semantic_text="水污染控制措施",
                        ),
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-NOISE"]
    assert build.summary["image_dedupe_summary"]["removed_incompatible_count"] == 1


def test_build_full_bid_generation_result_uses_heading_strict_subtopic_over_parent_title():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                ["噪声污染控制措施"],
            )
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                    "噪声污染控制措施",
                    [
                        _image_ref(
                            "IMG-NOISE-1",
                            "ASSET-NOISE-1",
                            "小型切割机外防护罩防噪声防火花溅射示意图",
                            semantic_text="小型切割机外防护罩防噪声防火花溅射示意图",
                            group_id="GROUP-NOISE",
                            group_title="噪声污染控制措施",
                            member_index=1,
                            member_count=2,
                        ),
                        _image_ref(
                            "IMG-NOISE-2",
                            "ASSET-NOISE-2",
                            "降噪棚设置示意图",
                            semantic_text="降噪棚设置及噪声控制示意图",
                            group_id="GROUP-NOISE",
                            group_title="噪声污染控制措施",
                            member_index=2,
                            member_count=2,
                        ),
                        _image_ref(
                            "IMG-AIR-1",
                            "ASSET-AIR-1",
                            "大气污染示意图",
                            semantic_text="大气污染及扬尘喷淋降尘示意图",
                            group_id="GROUP-AIR",
                            group_title="大气污染控制措施",
                            member_index=1,
                            member_count=2,
                        ),
                        _image_ref(
                            "IMG-AIR-2",
                            "ASSET-AIR-2",
                            "全自动喷水冲洗系统示意图",
                            semantic_text="出入口全自动喷水冲洗系统及洗车槽示意图",
                            group_id="GROUP-AIR",
                            group_title="大气污染控制措施",
                            member_index=2,
                            member_count=2,
                        ),
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-NOISE-1", "IMG-NOISE-2"]
    assert build.summary["image_dedupe_summary"]["removed_incompatible_count"] == 2


def test_build_full_bid_generation_result_ignores_noisy_nearby_text_for_strict_subtopic():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                ["噪声污染控制措施"],
            )
        ]
    }
    wrong_image = _image_ref(
        "IMG-AIR",
        "ASSET-AIR",
        "全自动喷水冲洗系统示意图",
        semantic_text="全自动喷水冲洗系统示意图",
        group_id="GROUP-AIR",
        group_title="大气污染控制措施",
        member_index=1,
        member_count=1,
    )
    wrong_image["nearby_text"] = "噪声控制措施，施工扰民和民扰控制措施。"
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["文明施工、环境保护管理体系及施工现场扬尘治理措施", "环境保护措施"],
                    "噪声污染控制措施",
                    [
                        _image_ref(
                            "IMG-NOISE",
                            "ASSET-NOISE",
                            "降噪棚设置示意图",
                            semantic_text="降噪棚设置及噪声控制示意图",
                        ),
                        wrong_image,
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-NOISE"]
    assert build.summary["image_dedupe_summary"]["removed_incompatible_count"] == 1


def test_build_full_bid_generation_result_removes_specific_process_topic_mismatch():
    chapter_inputs = {
        "packages": [
            _package(
                "U1",
                "N1",
                ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                ["外脚手架搭设及安全防护措施"],
            )
        ]
    }
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                    "外脚手架搭设及安全防护措施",
                    [
                        _image_ref(
                            "IMG-SCAFFOLD",
                            "ASSET-SCAFFOLD",
                            "外脚手架连墙件设置示意图",
                            semantic_text="脚手架连墙件、剪刀撑和安全网搭设示意图",
                        ),
                        _image_ref(
                            "IMG-MEASURE",
                            "ASSET-MEASURE",
                            "一级控制点埋设及防护做法",
                            semantic_text="一级测量控制点埋设及防护做法",
                        ),
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-SCAFFOLD"]
    assert build.summary["image_dedupe_summary"]["removed_incompatible_count"] == 1


def test_export_full_bid_docx_enriches_image_fingerprints(tmp_path: Path):
    image_path = tmp_path / "image1.png"
    Image.new("RGB", (16, 16), color="red").save(image_path)
    source_docx = tmp_path / "source.docx"
    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(image_path, "word/media/image1.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        json_dumps(
            {
                "sources": [{"source_id": "SRC1", "source_paths": [str(source_docx)]}],
                "image_assets": [
                    {
                        "image_asset_id": "ASSET-1",
                        "image_id": "IMG-1",
                        "source_id": "SRC1",
                        "part_name": "word/media/image1.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    chapter_inputs = {"packages": [_package("U1", "N1", ["主要施工方案与技术措施"], ["测量放线"])]}
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["主要施工方案与技术措施"],
                    "测量放线",
                    [
                        {
                            "type": "image_ref",
                            "image_id": "IMG-1",
                            "image_asset_id": "ASSET-1",
                            "caption": "测量放线示意图",
                            "source_bid_id": "SRC1",
                            "source_part_name": "word/media/image1.png",
                        }
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        material_library_json=library_path,
        raw_root=tmp_path,
    )
    image_ref = _all_image_refs(build.generation_result)[0]

    assert image_ref["sha256"]
    assert image_ref["canonical_image_id"] == f"sha256:{image_ref['sha256']}"
    assert image_ref["perceptual_hash"]
    assert build.summary["image_fingerprint_summary"]["enriched_count"] == 1


def test_build_full_bid_generation_result_dedupes_same_image_bytes_with_different_asset_ids(tmp_path: Path):
    image_path = tmp_path / "same.png"
    Image.new("RGB", (16, 16), color="blue").save(image_path)
    source_docx = tmp_path / "source.docx"
    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(image_path, "word/media/image1.png")
        archive.write(image_path, "word/media/image2.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        json_dumps(
            {
                "sources": [{"source_id": "SRC1", "source_paths": [str(source_docx)]}],
                "image_assets": [
                    {
                        "image_asset_id": "ASSET-1",
                        "image_id": "IMG-1",
                        "source_id": "SRC1",
                        "part_name": "word/media/image1.png",
                    },
                    {
                        "image_asset_id": "ASSET-2",
                        "image_id": "IMG-2",
                        "source_id": "SRC1",
                        "part_name": "word/media/image2.png",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    chapter_inputs = {"packages": [_package("U1", "N1", ["主要施工方案与技术措施"], ["测量放线"])]}
    generation_results = [
        {
            "chapters": [
                _generated_chapter(
                    "U1",
                    "N1",
                    ["主要施工方案与技术措施"],
                    "测量放线",
                    [
                        {
                            "type": "image_ref",
                            "image_id": "IMG-1",
                            "image_asset_id": "ASSET-1",
                            "caption": "测量放线示意图1",
                            "source_bid_id": "SRC1",
                            "source_part_name": "word/media/image1.png",
                        },
                        {
                            "type": "image_ref",
                            "image_id": "IMG-2",
                            "image_asset_id": "ASSET-2",
                            "caption": "测量放线示意图2",
                            "source_bid_id": "SRC1",
                            "source_part_name": "word/media/image2.png",
                        },
                    ],
                )
            ]
        }
    ]

    build = build_full_bid_generation_result(
        chapter_inputs,
        generation_results,
        apply_current_image_policy=False,
        material_library_json=library_path,
        raw_root=tmp_path,
    )
    image_refs = _all_image_refs(build.generation_result)

    assert [block["image_id"] for block in image_refs] == ["IMG-1"]
    assert build.summary["image_fingerprint_summary"]["enriched_count"] == 2
    assert build.summary["image_dedupe_summary"]["removed_duplicate_asset_count"] == 1


def test_export_full_bid_docx_writes_render_stats_to_output_json(tmp_path: Path):
    image_path = tmp_path / "image1.png"
    Image.new("RGB", (16, 16), color="red").save(image_path)
    source_docx = tmp_path / "source.docx"
    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(image_path, "word/media/image1.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        '{"sources":[{"source_id":"SRC1","source_paths":["' + str(source_docx).replace("\\", "\\\\") + '"]}]}',
        encoding="utf-8",
    )
    inputs_path = tmp_path / "inputs.json"
    inputs_path.write_text(
        json_dumps(
            {
                "packages": [
                    _package("U1", "N1", ["主要施工方案与技术措施", "土建施工方案与技术措施"], ["测量放线"])
                ]
            }
        ),
        encoding="utf-8",
    )
    generation_path = tmp_path / "generation.json"
    generation_path.write_text(
        json_dumps(
            {
                "provider": "test",
                "model": "test-model",
                "chapters": [
                    {
                        "unit_id": "U1",
                        "target_node_id": "N1",
                        "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                        "sections": [
                            {
                                "heading": "测量放线",
                                "level": 3,
                                "blocks": [
                                    {
                                        "type": "image_ref",
                                        "caption": "测量示意图",
                                        "source_bid_id": "SRC1",
                                        "source_part_name": "word/media/image1.png",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_docx = tmp_path / "out.docx"
    output_json = tmp_path / "out.json"

    summary = export_full_bid_docx_from_files(
        inputs_path,
        [generation_path],
        output_docx,
        output_json=output_json,
        material_library_json=library_path,
        raw_root=tmp_path,
        apply_current_image_policy=False,
    )

    written = json_loads(output_json)
    word_summary_path = tmp_path / "word_quality_summary.json"
    system_generated = tmp_path / "system_generated.docx"
    versions_dir = tmp_path / "versions"
    word_quality_summary = json_loads(word_summary_path)

    assert summary["render_stats"]["image_ref_count"] == 1
    assert summary["word_versions"]["system_generated_docx"] == str(system_generated)
    assert system_generated.exists()
    assert word_summary_path.exists()
    assert len(list(versions_dir.glob("v*_system_generated.docx"))) == 1
    assert word_quality_summary["latest_version"] == "system_generated"
    assert word_quality_summary["versions"]["system_generated"]["exists"] is True
    assert word_quality_summary["stats"]["image_count"] == 1
    assert word_quality_summary["stats"]["table_count"] >= 0
    assert word_quality_summary["outline_consistency"]["expected_heading_count"] >= 1
    assert written["full_bid_export_summary"]["render_stats"]["image_ref_count"] == 1
    assert written["full_bid_export_summary"]["render_stats"]["rendered_image_count"] == 1
    timing = written["full_bid_export_summary"]["word_refresh_timing"]
    stage_keys = {stage["key"] for stage in timing["stages"]}
    assert timing["llm_called"] is False
    assert timing["docx_size_bytes"] == output_docx.stat().st_size
    assert timing["json_size_bytes"] == output_json.stat().st_size
    assert timing["image_ref_count"] == 1
    assert timing["rendered_image_count"] == 1
    assert "load_inputs" in stage_keys
    assert "compose_full_bid_json" in stage_keys
    assert "write_docx" in stage_keys
    assert "write_full_bid_json" in stage_keys


def _package(unit_id, target_node_id, chapter_path, child_headings):
    return {
        "generation_unit": {
            "unit_id": unit_id,
            "target_node_id": target_node_id,
            "parent_level_1_node_id": target_node_id.split("_")[0],
            "unit_type": "level2_section_group" if len(chapter_path) > 1 else "level1_chapter",
            "chapter_path": chapter_path,
            "child_headings": child_headings,
        },
        "score_point": {
            "score_point_raw": chapter_path[0],
            "score_standard_raw": f"{chapter_path[0]}评分标准原文。",
        },
    }


def _generated_chapter(unit_id, target_node_id, chapter_path, heading, image_refs):
    return {
        "unit_id": unit_id,
        "target_node_id": target_node_id,
        "chapter_path": chapter_path,
        "sections": [
            {
                "heading": heading,
                "level": 3,
                "blocks": [
                    {"type": "paragraph", "text": f"{heading}正文。"},
                    *[dict(image) for image in image_refs],
                ],
            }
        ],
        "score_response_check": {"response_summary": "已覆盖评分要求。"},
    }


def _image_ref(
    image_id,
    asset_id,
    caption,
    *,
    semantic_text=None,
    group_id=None,
    group_title=None,
    member_index=None,
    member_count=None,
):
    return {
        "type": "image_ref",
        "image_id": image_id,
        "image_asset_id": asset_id,
        "caption": caption,
        "semantic_text": semantic_text or caption,
        "semantic_confidence": 0.9,
        "source_part_name": f"word/media/{image_id}.png",
        "image_group_id": group_id,
        "group_title": group_title or ("钢筋加工示意图" if group_id else None),
        "group_semantic_text": group_title or ("钢筋加工示意图" if group_id else None),
        "group_member_index": member_index,
        "group_member_count": member_count,
        "must_keep_with_group": bool(group_id),
    }


def _all_image_refs(result):
    return [
        block
        for chapter in result["chapters"]
        for section in chapter["sections"]
        for block in section.get("blocks") or []
        if block.get("type") == "image_ref"
    ]


def json_dumps(data):
    import json

    return json.dumps(data, ensure_ascii=False)


def json_loads(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))
