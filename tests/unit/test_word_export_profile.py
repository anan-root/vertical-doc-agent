import json

from construction_bidding_agent.chapter_generator.word_export_profile import (
    SCHEMA_VERSION,
    default_word_export_profile,
    load_word_export_profile,
    merge_word_export_profile,
    reset_word_export_profile,
    save_word_export_profile,
)


def test_default_word_export_profile_contains_required_sections():
    profile = default_word_export_profile()

    assert profile["schema_version"] == SCHEMA_VERSION
    assert profile["toc"]["title"] == "目录"
    assert profile["toc"]["levels"] == 3
    assert profile["heading_1"]["page_break_before"] is True
    assert profile["body"]["first_line_indent_chars"] == 2
    assert profile["table"]["first_line_indent_chars"] == 0


def test_merge_word_export_profile_keeps_defaults_and_applies_overrides():
    merged = merge_word_export_profile(
        {
            "page": {"top_margin_cm": 3.0},
            "heading_2": {"color": "123456"},
            "body": {"line_spacing": 1.5},
        }
    )

    assert merged["page"]["top_margin_cm"] == 3.0
    assert merged["page"]["bottom_margin_cm"] == 2.54
    assert merged["heading_2"]["color"] == "123456"
    assert merged["body"]["line_spacing"] == 1.5
    assert merged["toc"]["title"] == "目录"


def test_word_export_profile_validation_clamps_invalid_values():
    merged = merge_word_export_profile(
        {
            "page": {"top_margin_cm": 100, "paper_size": "Letter"},
            "toc": {"levels": 9},
            "image": {"multi_image_layout": {"default_columns": 99}},
        }
    )

    assert merged["page"]["top_margin_cm"] == 8.0
    assert merged["page"]["paper_size"] == "A4"
    assert merged["toc"]["levels"] == 3
    assert merged["image"]["multi_image_layout"]["default_columns"] == 4


def test_save_load_and_reset_word_export_profile(tmp_path):
    path = tmp_path / "word_export_profile.json"

    saved = save_word_export_profile(path, {"body": {"font_size_pt": 10.5}})
    loaded = load_word_export_profile(path)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["body"]["font_size_pt"] == 10.5
    assert saved["body"]["font_size_pt"] == 10.5
    assert loaded["body"]["font_size_pt"] == 10.5

    reset = reset_word_export_profile(path)

    assert reset["body"]["font_size_pt"] == 12
    assert load_word_export_profile(path)["body"]["font_size_pt"] == 12
