import json
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from construction_bidding_agent.chapter_generator.chapter_docx_renderer import (
    ImageLayoutProfile,
    ImageLayoutItem,
    _build_image_resolver,
    _column_widths,
    _fit_image_size_cm,
    _grid_row_image_max_height_cm,
    _grid_row_image_max_width_cm,
    _image_grid_column_count,
    _image_layout_rows,
    _image_max_columns,
    _load_image_layout_profile,
    _image_item_caption,
    _shared_image_group_caption,
    _resolve_source_path,
    _resolve_image_bytes,
    _take_image_group,
    _text_image_block_table_title,
    render_chapter_docx_from_file,
)


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def test_take_image_group_groups_consecutive_image_refs():
    blocks = [
        {"type": "paragraph", "text": "正文"},
        {"type": "image_ref", "image_id": "I1"},
        {"type": "image_ref", "image_id": "I2"},
        {"type": "rich_table"},
    ]

    group, next_index = _take_image_group(blocks, 1)

    assert [item["image_id"] for item in group] == ["I1", "I2"]
    assert next_index == 3


def test_take_image_group_splits_different_text_image_blocks():
    blocks = [
        {
            "type": "image_ref",
            "image_id": "I1",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
        },
        {
            "type": "image_ref",
            "image_id": "I2",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
        },
        {"type": "image_ref", "image_id": "I3", "image_group_id": "G1"},
    ]

    group, next_index = _take_image_group(blocks, 0)

    assert [item["image_id"] for item in group] == ["I1", "I2"]
    assert next_index == 2


def test_shared_image_group_caption_detects_repeated_caption():
    blocks = [
        {
            "type": "image_ref",
            "image_id": "I1",
            "caption": "剪刀撑搭设方法示意图",
            "image_group_id": "G1",
        },
        {
            "type": "image_ref",
            "image_id": "I2",
            "caption": "剪刀撑搭设方法示意图",
            "image_group_id": "G1",
        },
    ]

    assert _shared_image_group_caption(blocks) == "剪刀撑搭设方法示意图"


def test_shared_image_group_caption_hides_long_merged_caption():
    blocks = [
        {
            "type": "image_ref",
            "image_id": "I1",
            "caption": "剪刀撑搭设方法示意立杆的接长大横杆在转角处的接长",
            "image_group_id": "G1",
        },
        {
            "type": "image_ref",
            "image_id": "I2",
            "caption": "剪刀撑搭设方法示意立杆的接长大横杆在转角处的接长",
            "image_group_id": "G1",
        },
    ]

    assert _shared_image_group_caption(blocks) == ""


def test_repeated_long_image_item_caption_is_hidden():
    caption = "剪刀撑搭设方法示意立杆的接长大横杆在转角处的接长"

    assert _image_item_caption({"caption": caption}, repeated_caption=caption) == ""
    assert _image_item_caption({"caption": caption}) == caption


def test_text_image_block_table_title_uses_common_block_title():
    blocks = [
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "钢筋加工成型",
            "image_id": "I1",
        },
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "钢筋加工成型",
            "image_id": "I2",
        },
    ]

    assert _text_image_block_table_title(blocks) == "钢筋加工成型"


def test_text_image_block_table_title_rejects_mixed_blocks():
    blocks = [
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "钢筋加工成型",
        },
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-2",
            "text_image_block_title": "模板支设",
        },
    ]

    assert _text_image_block_table_title(blocks) == ""


def test_text_image_block_table_title_skips_weak_table_column_title():
    blocks = [
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "序号；设计说明",
            "group_title": "模板支设节点做法",
        },
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "序号；设计说明",
            "group_title": "模板支设节点做法",
        },
    ]

    assert _text_image_block_table_title(blocks) == "模板支设节点做法"


def test_text_image_block_table_title_hides_long_merged_caption_title():
    blocks = [
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "剪刀撑搭设方法示意立杆的接长大横杆在转角处的接长",
        },
        {
            "type": "image_ref",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "剪刀撑搭设方法示意立杆的接长大横杆在转角处的接长",
        },
    ]

    assert _text_image_block_table_title(blocks) == ""


def test_build_image_resolver_reads_docx_media(tmp_path: Path):
    image_path = tmp_path / "image1.png"
    Image.new("RGB", (16, 16), color="red").save(image_path)
    docx_path = tmp_path / "source.docx"
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.write(image_path, "word/media/image1.png")
    library = {
        "sources": [
            {
                "source_id": "SRC0001",
                "source_paths": [str(docx_path)],
            }
        ]
    }
    library_path = tmp_path / "library.json"
    library_path.write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")

    resolver = _build_image_resolver(library_path, tmp_path)

    assert ("SRC0001", "word/media/image1.png") in resolver
    assert ("", "word/media/image1.png") in resolver


def test_resolve_source_path_supports_windows_style_raw_path(tmp_path: Path):
    raw_root = tmp_path / "data" / "raw"
    docx_path = raw_root / "投标文件" / "总体施工方案.docx"
    docx_path.parent.mkdir(parents=True)
    docx_path.write_bytes(b"fake")

    resolved = _resolve_source_path(Path("data\\raw\\投标文件\\总体施工方案.docx"), raw_root)

    assert resolved == docx_path


def test_resolve_source_path_supports_local_raw_uri(tmp_path: Path):
    raw_root = tmp_path / "data" / "raw"
    docx_path = raw_root / "投标文件" / "总体施工方案.docx"
    docx_path.parent.mkdir(parents=True)
    docx_path.write_bytes(b"fake")

    resolved = _resolve_source_path(Path("local://raw/投标文件/总体施工方案.docx"), raw_root)

    assert resolved == docx_path


def test_resolve_image_bytes_requires_exact_source_when_source_id_present(tmp_path: Path):
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    Image.new("RGB", (16, 16), color="red").save(first_image)
    Image.new("RGB", (16, 16), color="blue").save(second_image)
    first_docx = tmp_path / "first.docx"
    second_docx = tmp_path / "second.docx"
    with zipfile.ZipFile(first_docx, "w") as archive:
        archive.write(first_image, "word/media/image1.png")
    with zipfile.ZipFile(second_docx, "w") as archive:
        archive.write(second_image, "word/media/image1.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        json.dumps(
            {
                "sources": [
                    {"source_id": "SRC0001", "source_paths": [str(first_docx)]},
                    {"source_id": "SRC0002", "source_paths": [str(second_docx)]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resolver = _build_image_resolver(library_path, tmp_path)

    resolved = _resolve_image_bytes(
        {"source_bid_id": "SRC0002", "source_part_name": "word/media/image1.png"},
        resolver,
    )
    missing = _resolve_image_bytes(
        {"source_bid_id": "SRC9999", "source_part_name": "word/media/image1.png"},
        resolver,
    )
    legacy = _resolve_image_bytes({"source_part_name": "word/media/image1.png"}, resolver)

    assert resolved == resolver[("SRC0002", "word/media/image1.png")]
    assert resolved != resolver[("SRC0001", "word/media/image1.png")]
    assert missing is None
    assert legacy == resolver[("", "word/media/image1.png")]


def test_fit_image_size_respects_width_and_height_caps():
    tall = BytesIO()
    Image.new("RGB", (300, 900), color="red").save(tall, format="PNG")
    width_cm, height_cm = _fit_image_size_cm(tall.getvalue(), max_width_cm=12.8, max_height_cm=8.5)

    assert round(height_cm, 1) == 8.5
    assert width_cm < 3

    wide = BytesIO()
    Image.new("RGB", (1600, 800), color="blue").save(wide, format="PNG")
    width_cm, height_cm = _fit_image_size_cm(wide.getvalue(), max_width_cm=12.8, max_height_cm=8.5)

    assert round(width_cm, 1) == 12.8
    assert height_cm < 7


def test_image_grid_column_count_uses_three_columns_for_large_photo_groups():
    assert _image_grid_column_count(2) == 2
    assert _image_grid_column_count(5) == 2
    assert _image_grid_column_count(6) == 3
    assert _image_grid_column_count(9) == 3


def test_image_max_columns_keeps_detail_drawings_readable():
    assert _image_max_columns({"caption": "剪力墙模板交叉杆平立剖面设计节点图"}) == 1
    assert _image_max_columns({"caption": "钢筋绑扎流程示意图"}) == 2
    assert _image_max_columns({"caption": "混凝土振捣现场照片"}) == 3


def test_image_layout_profile_can_override_keywords_and_sizes(tmp_path: Path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "image_layout": {
                    "single_image_max_width_cm": 11.2,
                    "two_column_max_height_cm": 6.1,
                    "high_detail_keywords": ["超清节点"],
                    "medium_detail_keywords": ["过程图"],
                    "photo_keywords": ["现场照"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profile = _load_image_layout_profile(profile_path)

    assert profile.single_image_max_width_cm == 11.2
    assert profile.two_column_max_height_cm == 6.1
    assert _image_max_columns({"caption": "超清节点"}, profile) == 1
    assert _image_max_columns({"caption": "过程图"}, profile) == 2
    assert _image_max_columns({"caption": "现场照"}, profile) == 3
    assert _grid_row_image_max_width_cm(1, profile) == 11.2
    assert _grid_row_image_max_height_cm(2, profile) == 6.1


def test_image_layout_rows_splits_mixed_detail_and_photo_groups():
    items = [
        ImageLayoutItem(block={"caption": "做法照片1"}, image_bytes=None, max_columns=3),
        ImageLayoutItem(block={"caption": "做法照片2"}, image_bytes=None, max_columns=3),
        ImageLayoutItem(block={"caption": "复杂节点图"}, image_bytes=None, max_columns=1),
        ImageLayoutItem(block={"caption": "做法照片3"}, image_bytes=None, max_columns=3),
        ImageLayoutItem(block={"caption": "做法照片4"}, image_bytes=None, max_columns=3),
        ImageLayoutItem(block={"caption": "做法照片5"}, image_bytes=None, max_columns=3),
    ]

    rows = _image_layout_rows(items)

    assert [len(row) for row in rows] == [2, 1, 3]


def test_single_column_grid_row_uses_full_image_width_cap():
    assert _grid_row_image_max_width_cm(1) == 12.8
    assert _grid_row_image_max_width_cm(2) == 7.55
    assert round(_grid_row_image_max_width_cm(3), 2) == 4.88
    assert _grid_row_image_max_height_cm(1) == 8.5
    assert _grid_row_image_max_height_cm(2) == 6.4
    assert _grid_row_image_max_height_cm(3) == 5.6


def test_render_chapter_docx_from_file_creates_docx_with_image(tmp_path: Path):
    image_path = tmp_path / "image1.png"
    Image.new("RGB", (64, 48), color="blue").save(image_path)
    source_docx = tmp_path / "source.docx"
    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(image_path, "word/media/image1.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        json.dumps({"sources": [{"source_id": "SRC0001", "source_paths": [str(source_docx)]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_generation_result(), ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "out.docx"

    stats = render_chapter_docx_from_file(result_path, output_docx, material_library_json=library_path, raw_root=tmp_path)

    assert output_docx.exists()
    assert stats["paragraph_count"] == 1
    assert stats["table_count"] == 1
    assert stats["rendered_image_count"] == 1
    with zipfile.ZipFile(output_docx) as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_text_image_block_images_render_as_one_table(tmp_path: Path):
    first_image = tmp_path / "image1.png"
    second_image = tmp_path / "image2.png"
    Image.new("RGB", (64, 48), color="blue").save(first_image)
    Image.new("RGB", (64, 48), color="green").save(second_image)
    source_docx = tmp_path / "source.docx"
    with zipfile.ZipFile(source_docx, "w") as archive:
        archive.write(first_image, "word/media/image1.png")
        archive.write(second_image, "word/media/image2.png")
    library_path = tmp_path / "library.json"
    library_path.write_text(
        json.dumps({"sources": [{"source_id": "SRC0001", "source_paths": [str(source_docx)]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = _generation_result()
    blocks = result["chapters"][0]["sections"][0]["blocks"]
    blocks[:] = [
        {"type": "paragraph", "text": "图文块前置说明。"},
        {
            "type": "image_ref",
            "caption": "钢筋加工成型图",
            "source_bid_id": "SRC0001",
            "source_part_name": "word/media/image1.png",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "钢筋加工成型",
            "row_scope": {"table_index": 1, "start_row_index": 2, "end_row_index": 2},
            "render_policy": {"row_level_context": True},
        },
        {
            "type": "image_ref",
            "caption": "箍筋检查模具图",
            "source_bid_id": "SRC0001",
            "source_part_name": "word/media/image2.png",
            "source_reuse_mode": "text_image_block",
            "text_image_block_id": "TIB-1",
            "text_image_block_title": "钢筋加工成型",
            "row_scope": {"table_index": 1, "start_row_index": 2, "end_row_index": 2},
            "render_policy": {"row_level_context": True},
        },
    ]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "text_image_block.docx"

    stats = render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=library_path,
        raw_root=tmp_path,
        output_mode="final",
    )

    assert stats["rendered_image_count"] == 2
    with zipfile.ZipFile(output_docx) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    table_texts = [
        "\n".join(_paragraph_text(paragraph) for paragraph in table.findall(".//w:p", NS))
        for table in root.findall(".//w:tbl", NS)
    ]
    matching_tables = [text for text in table_texts if "钢筋加工成型" in text]
    assert len(matching_tables) == 1
    assert "钢筋加工成型图" in matching_tables[0]
    assert "箍筋检查模具图" in matching_tables[0]


def test_final_output_mode_hides_review_artifacts_and_numbers_headings(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_generation_result(), ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "final.docx"

    render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=None,
        raw_root=tmp_path,
        output_mode="final",
    )

    with zipfile.ZipFile(output_docx) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    texts = [_paragraph_text(paragraph) for paragraph in root.findall(".//w:p", NS)]
    joined_text = "\n".join(texts)

    assert "评分点响应摘要" not in joined_text
    assert "人工复核清单" not in joined_text
    assert any(text.startswith("1.主要施工方案与技术措施") for text in texts)
    assert any(text.startswith("1.1.1.测量放线") for text in texts)


def test_heading_styles_follow_bid_font_color_rules(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_generation_result(), ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "styled.docx"

    render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=None,
        raw_root=tmp_path,
        output_mode="final",
    )

    with zipfile.ZipFile(output_docx) as archive:
        styles_xml = archive.read("word/styles.xml")
    root = ET.fromstring(styles_xml)

    assert _style_font(root, "Heading1") == "宋体"
    assert _style_color(root, "Heading1") == "C00000"
    assert _style_size(root, "Heading1") == "32"
    assert _style_font(root, "Heading2") == "宋体"
    assert _style_color(root, "Heading2") == "002060"
    assert _style_size(root, "Heading2") == "28"
    assert _style_font(root, "Heading3") == "宋体"
    assert _style_color(root, "Heading3") == "000000"
    assert _style_size(root, "Heading3") == "28"
    assert _style_font(root, "Normal") == "宋体"
    assert _style_size(root, "Normal") == "24"


def test_final_output_mode_adds_toc_and_restarts_body_page_numbers(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_generation_result(), ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "final_with_toc.docx"

    render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=None,
        raw_root=tmp_path,
        output_mode="final",
        word_export_profile={
            "toc": {
                "enabled": True,
                "title": "目录",
                "levels": 3,
                "body_page_number_restart": True,
                "body_page_number_start": 1,
            }
        },
    )

    with zipfile.ZipFile(output_docx) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "目录" in document_xml
    assert 'TOC \\o "1-3" \\h \\z \\u' in document_xml
    assert 'w:pgNumType w:start="1"' in document_xml


def test_final_output_mode_does_not_restart_page_numbers_per_chapter(tmp_path: Path):
    result = _generation_result()
    result["chapters"].append(
        {
            "chapter_path": ["质量管理体系与措施"],
            "title": "质量管理体系与措施",
            "sections": [
                {
                    "heading": "质量目标与承诺",
                    "level": 2,
                    "blocks": [{"type": "paragraph", "text": "建立质量目标分解和过程检查机制。"}],
                }
            ],
        }
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "final_multi_chapter.docx"

    render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=None,
        raw_root=tmp_path,
        output_mode="final",
        word_export_profile={
            "toc": {
                "enabled": True,
                "body_page_number_restart": True,
                "body_page_number_start": 1,
            }
        },
    )

    with zipfile.ZipFile(output_docx) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    sections = root.findall(".//w:sectPr", NS)
    page_number_starts = [
        pg_num_type.attrib.get(f"{{{NS['w']}}}start")
        for section in sections
        if (pg_num_type := section.find("w:pgNumType", NS)) is not None
    ]

    assert len(sections) == 2
    assert page_number_starts == ["1"]


def test_table_paragraphs_have_no_first_line_indent(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_generation_result(), ensure_ascii=False), encoding="utf-8")
    output_docx = tmp_path / "table_indent.docx"

    render_chapter_docx_from_file(
        result_path,
        output_docx,
        material_library_json=None,
        raw_root=tmp_path,
        output_mode="final",
    )

    with zipfile.ZipFile(output_docx) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    indents = []
    for cell in root.findall(".//w:tc", NS):
        for paragraph in cell.findall(".//w:p", NS):
            indent = paragraph.find("./w:pPr/w:ind", NS)
            if indent is not None:
                indents.append(indent.attrib.get(f"{{{NS['w']}}}firstLine"))

    assert indents
    assert set(indents) == {"0"}


def test_column_widths_prioritize_long_responsibility_columns():
    columns = [
        {"key": "role", "title": "岗位/部门"},
        {"key": "duty", "title": "主要职责"},
        {"key": "metric", "title": "考核指标"},
    ]
    rows = [
        {
            "cells": {
                "role": "项目经理",
                "duty": "全面负责项目技术创新资源的配置与协调，审批重大技术方案，保障创新实施投入。",
                "metric": "创新目标达成率100%",
            }
        },
        {
            "cells": {
                "role": "项目总工程师",
                "duty": "主持编制技术创新策划书，组织新技术、新工艺的论证与实施，解决关键技术难题。",
                "metric": "方案审批及时率100%，无重大技术事故。",
            }
        },
    ]

    widths = _column_widths(columns, rows)

    assert round(sum(widths), 2) == 16.0
    assert widths[0] <= 3.2
    assert widths[1] > widths[2] > widths[0]
    assert widths[1] >= 6.0


def test_column_widths_keep_material_spec_columns_readable():
    columns = [
        {"key": "col_1", "title": "物资类别"},
        {"key": "col_2", "title": "物资名称"},
        {"key": "col_3", "title": "规格型号/要求"},
        {"key": "col_4", "title": "储备数量"},
        {"key": "col_5", "title": "存放地点及管理责任人"},
    ]
    rows = [
        {
            "cells": {
                "col_1": "个人防护",
                "col_2": "正压式空气呼吸器",
                "col_3": "符合国标，气瓶压力正常，配套全面罩",
                "col_4": "4套",
                "col_5": "应急物资库/安全员",
            }
        },
        {
            "cells": {
                "col_1": "个人防护",
                "col_2": "防毒面具",
                "col_3": "过滤式，配相应滤毒盒（有机气体/酸性气体）",
                "col_4": "20个",
                "col_5": "应急物资库/安全员",
            }
        },
    ]

    widths = _column_widths(columns, rows)

    assert round(sum(widths), 2) == 16.0
    assert min(widths) > 1.0
    assert widths[2] >= 2.0
    assert widths[3] < widths[2]


def test_column_widths_never_create_negative_width_for_five_column_plan_table():
    columns = [
        {"key": "col_1", "title": "演练月份"},
        {"key": "col_2", "title": "演练类型"},
        {"key": "col_3", "title": "演练科目"},
        {"key": "col_4", "title": "参与人员"},
        {"key": "col_5", "title": "预期目标"},
    ]
    rows = [
        {
            "cells": {
                "col_1": "9月",
                "col_2": "综合演练",
                "col_3": "高处坠落急救",
                "col_4": "架子工、木工、钢筋工等高处作业人员",
                "col_5": "检验多部门协同作战能力，验证预案可行性",
            }
        }
    ]

    widths = _column_widths(columns, rows)

    assert round(sum(widths), 2) == 16.0
    assert min(widths) >= 1.1
    assert widths[4] >= 2.3


def test_column_widths_preserve_required_equipment_column_in_accident_table():
    columns = [
        {"key": "col_1", "title": "事故类型"},
        {"key": "col_2", "title": "关键处置措施"},
        {"key": "col_3", "title": "注意事项"},
        {"key": "col_4", "title": "所需物资/设备"},
    ]
    rows = [
        {
            "cells": {
                "col_1": "土方/模板坍塌",
                "col_2": "立即停止作业，撤离人员；切断电源、气源；清理坍塌物搜救；加固周边结构。",
                "col_3": "严禁盲目进入不稳定区域；注意观察周边裂缝发展；防止次生灾害；设立专人监护。",
                "col_4": "挖掘机、千斤顶、切割设备、担架、急救包、生命探测仪。",
            }
        }
    ]

    widths = _column_widths(columns, rows)

    assert round(sum(widths), 2) == 16.0
    assert widths[3] >= 2.2
    assert min(widths) >= 2.0


def _generation_result():
    return {
        "model": "test-model",
        "generated_at": "2026-05-05T00:00:00+08:00",
        "chapters": [
            {
                "chapter_path": ["主要施工方案与技术措施", "土建施工方案与技术措施"],
                "title": "土建施工方案与技术措施",
                "score_response_check": {"response_summary": "覆盖施工方案评分点。"},
                "sections": [
                    {
                        "heading": "测量放线",
                        "level": 3,
                        "blocks": [
                            {"type": "paragraph", "text": "项目部按控制网复核、轴线投测、标高传递和过程复核的流程组织测量放线。"},
                            {
                                "type": "rich_table",
                                "title": "测量控制表",
                                "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "控制内容"}],
                                "rows": [{"cells": {"col_1": "1", "col_2": "控制网复测"}}],
                            },
                            {
                                "type": "image_ref",
                                "image_id": "IMG1",
                                "caption": "测量控制示意图",
                                "source_bid_id": "SRC0001",
                                "source_part_name": "word/media/image1.png",
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _paragraph_text(paragraph):
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))


def _style(root, style_id: str):
    for item in root.findall(".//w:style", NS):
        if item.attrib.get(f"{{{NS['w']}}}styleId") == style_id:
            return item
    raise AssertionError(f"style not found: {style_id}")


def _style_font(root, style_id: str):
    style = _style(root, style_id)
    rfonts = style.find(".//w:rFonts", NS)
    return rfonts.attrib.get(f"{{{NS['w']}}}eastAsia")


def _style_color(root, style_id: str):
    style = _style(root, style_id)
    color = style.find(".//w:color", NS)
    return color.attrib.get(f"{{{NS['w']}}}val")


def _style_size(root, style_id: str):
    style = _style(root, style_id)
    size = style.find(".//w:sz", NS)
    return size.attrib.get(f"{{{NS['w']}}}val")
