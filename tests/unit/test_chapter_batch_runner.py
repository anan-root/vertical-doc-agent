import json

from construction_bidding_agent.chapter_generator.chapter_batch_runner import (
    BATCH_ARTIFACT_SCHEMA_VERSION,
    run_chapter_generation_batch,
)
from construction_bidding_agent.llm_config import LlmClientConfig


def test_batch_runner_writes_per_chapter_artifacts_and_combined_result(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        if llm_input["task_type"] == "technical_bid_chapter_generation":
            calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1"), _package("GU-2")],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    artifacts = sorted((tmp_path / "chapters").glob("*.json"))
    assert calls == ["GU-1", "GU-2"]
    assert result.completed_count == 2
    assert result.failed_count == 0
    assert [chapter["unit_id"] for chapter in result.chapters] == ["GU-1", "GU-2"]
    assert len(artifacts) == 2
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["schema_version"] == BATCH_ARTIFACT_SCHEMA_VERSION
    assert artifact["status"] == "completed"
    assert artifact["chapter"]["unit_id"] == "GU-1"
    assert artifact["package_hash"]
    assert artifact["cache_key"]
    assert artifact["cache_status"] == "miss"
    assert artifact["resume_action"] == "generate"
    assert "未找到章节状态文件" in artifact["resume_reason"]
    assert artifact["task"]["llm_input_profile"] == "slim_v3"
    assert artifact["task"]["llm_input_char_count"] > 0
    assert artifact["task"]["full_package_char_count"] > 0


def test_batch_runner_uses_configured_max_workers_by_default(tmp_path):
    def fake_llm(llm_input, _config):
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1"), _package("GU-2")],
        state_dir=tmp_path,
        llm_config_override=_config(max_workers=3),
        llm_callable=fake_llm,
    )

    assert result.max_workers == 3
    assert result.execution_mode == "parallel_resumable"


def test_batch_runner_allows_max_workers_argument_to_override_config(tmp_path):
    def fake_llm(llm_input, _config):
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1"), _package("GU-2")],
        state_dir=tmp_path,
        max_workers=1,
        llm_config_override=_config(max_workers=3),
        llm_callable=fake_llm,
    )

    assert result.max_workers == 1
    assert result.execution_mode == "serial_resumable"


def test_batch_runner_skips_completed_artifacts_on_resume(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        if llm_input["task_type"] == "technical_bid_chapter_generation":
            calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    packages = [_package("GU-1"), _package("GU-2")]
    first = run_chapter_generation_batch(
        packages,
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )
    second = run_chapter_generation_batch(
        packages,
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert first.completed_count == 2
    assert second.completed_count == 2
    assert second.skipped_count == 0
    assert calls == ["GU-1", "GU-2"]
    assert [chapter["unit_id"] for chapter in second.chapters] == ["GU-1", "GU-2"]
    assert all(task.cache_status == "hit" for task in second.tasks)
    assert all(task.resume_action == "skip" for task in second.tasks)


def test_batch_runner_force_regenerates_completed_artifacts(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    packages = [_package("GU-1")]
    run_chapter_generation_batch(
        packages,
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )
    run_chapter_generation_batch(
        packages,
        state_dir=tmp_path,
        force=True,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == ["GU-1", "GU-1"]


def test_batch_runner_regenerates_when_package_hash_changes(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    package = _package("GU-1")
    changed = _package("GU-1")
    changed["technical_requirements"] = [{"requirement_id": "R1", "raw_clause": "新增技术要求"}]

    run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )
    run_chapter_generation_batch(
        [changed],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == ["GU-1", "GU-1"]


def test_batch_runner_regenerates_when_model_cache_key_changes(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append((_config.model, llm_input["generation_unit"]["unit_id"]))
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    package = _package("GU-1")

    run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        llm_config_override=_config(model="model-a"),
        llm_callable=fake_llm,
    )
    second = run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        llm_config_override=_config(model="model-b"),
        llm_callable=fake_llm,
    )

    assert calls == [("model-a", "GU-1"), ("model-b", "GU-1")]
    assert second.tasks[0].cache_status == "miss"
    assert second.tasks[0].resume_action == "generate"
    assert "模型、提示词或生成参数变化" in str(second.tasks[0].resume_reason)


def test_batch_runner_failed_artifact_can_be_skipped_or_retried(tmp_path):
    calls = []

    def broken_llm(llm_input, _config):
        if llm_input.get("task_type") == "technical_bid_chapter_generation":
            calls.append(llm_input["generation_unit"]["unit_id"])
        return '{"title": "未闭合"'

    package = _package("GU-1")
    first = run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=broken_llm,
    )
    skipped = run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        retry_failed=False,
        llm_config_override=_config(),
        llm_callable=broken_llm,
    )
    retried = run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        retry_failed=True,
        llm_config_override=_config(),
        llm_callable=broken_llm,
    )

    assert first.failed_count == 1
    assert skipped.tasks[0].status == "failed"
    assert skipped.tasks[0].cache_status == "hit"
    assert skipped.tasks[0].resume_action == "skip"
    assert retried.failed_count == 1
    assert retried.tasks[0].cache_status == "miss"
    assert calls == ["GU-1", "GU-1"]


def test_batch_runner_auto_retries_transient_json_failure(tmp_path):
    calls = []

    def flaky_llm(llm_input, _config):
        calls.append(llm_input.get("task_type"))
        if calls.count("technical_bid_chapter_generation") == 1:
            raise ValueError("LLM response content is empty.")
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1")],
        state_dir=tmp_path,
        llm_config_override=_config(max_retries=1),
        llm_callable=flaky_llm,
    )

    artifact = json.loads((tmp_path / "chapters" / "GU-1.json").read_text(encoding="utf-8"))
    assert result.completed_count == 1
    assert result.failed_count == 0
    assert result.tasks[0].retry_attempt_count == 1
    assert artifact["task"]["retry_attempt_count"] == 1
    assert artifact["task"]["retry_summary"]["attempts"][0]["status"] == "failed"
    assert artifact["task"]["retry_summary"]["attempts"][1]["status"] == "completed"


def test_batch_runner_state_filename_is_stable_when_filtering_subset(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    package = _package("GU-2")
    first = run_chapter_generation_batch(
        [package],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )
    second = run_chapter_generation_batch(
        [_package("GU-1"), package],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    artifact_names = sorted(path.name for path in (tmp_path / "chapters").glob("*.json"))
    assert first.completed_count == 1
    assert second.completed_count == 2
    assert calls == ["GU-2", "GU-1"]
    assert artifact_names == ["GU-1.json", "GU-2.json"]


def test_batch_runner_dry_run_does_not_write_artifacts(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1"), _package("GU-2")],
        state_dir=tmp_path,
        dry_run=True,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == []
    assert result.completed_count == 0
    assert result.skipped_count == 2
    assert result.warnings[0].startswith("dry-run")
    assert not (tmp_path / "chapters").exists()


def test_batch_runner_schedules_heavy_pending_items_first_but_returns_original_order(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        if llm_input["task_type"] == "technical_bid_chapter_generation":
            calls.append(llm_input["generation_unit"]["unit_id"])
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    light = _package("GU-1")
    heavy = _package("GU-2")
    heavy["expanded_generation_policy"]["targets"]["min_paragraphs_total"] = 30
    heavy["expanded_generation_policy"]["targets"]["min_rich_tables"] = 6
    heavy["table_references"] = [{"table_id": f"T-{index}"} for index in range(8)]
    heavy["generation_unit"]["chapter_path"] = ["主要施工方案与技术措施", "合理化建议"]

    result = run_chapter_generation_batch(
        [light, heavy],
        state_dir=tmp_path,
        max_workers=1,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert calls == ["GU-2", "GU-1"]
    assert [task.unit_id for task in result.tasks] == ["GU-1", "GU-2"]


def test_batch_runner_dedupes_cross_chapter_images_and_rewrites_artifacts(tmp_path):
    def fake_llm(llm_input, _config):
        output = _valid_output(llm_input["generation_unit"])
        output["sections"][0]["heading"] = "模板支设"
        output["sections"][0]["blocks"].append(
            {
                "type": "image_ref",
                "image_id": "IMG-SAME",
                "image_asset_id": "ASSET-SAME",
                "caption": "模板支设做法示意图",
            }
        )
        return json.dumps(output, ensure_ascii=False)

    package_1 = _package("GU-1")
    package_2 = _package("GU-2")
    for package in [package_1, package_2]:
        package["image_candidate_pool"] = [
            {
                "image_id": "IMG-SAME",
                "image_asset_id": "ASSET-SAME",
                "source_id": "SRC0001",
                "part_name": "word/media/image1.png",
                "caption": "模板支设做法示意图",
                "semantic_text": "模板支设做法示意图",
                "semantic_confidence": 0.9,
                "semantic_sources": [
                    {"source_type": "same_cell_caption", "text": "模板支设做法示意图", "confidence": 0.9}
                ],
                "bound_section": "模板支设",
                "reuse_level": "candidate_reuse",
                "risk_level": "low",
            }
        ]

    result = run_chapter_generation_batch(
        [package_1, package_2],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert "跨章节图片去重：移除 1 个重复图片引用。" in result.warnings
    assert _image_ids(result.chapters[0]) == ["IMG-SAME"]
    assert _image_ids(result.chapters[1]) == []
    artifact = json.loads((tmp_path / "chapters" / "GU-2.json").read_text(encoding="utf-8"))
    assert _image_ids(artifact["chapter"]) == []
    assert artifact["chapter"]["cross_chapter_image_dedup"]["removed_count"] == 1


def test_batch_runner_reports_per_chapter_progress(tmp_path):
    events = []

    def fake_llm(llm_input, _config):
        return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)

    result = run_chapter_generation_batch(
        [_package("GU-1"), _package("GU-2")],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
        progress_callback=events.append,
    )

    assert result.completed_count == 2
    assert [event["finished"] for event in events] == [1, 2]
    assert events[-1]["completed"] == 2
    assert events[-1]["total"] == 2


def test_batch_runner_repairs_invalid_json_with_second_llm_call(tmp_path):
    calls = []

    def fake_llm(llm_input, _config):
        calls.append(llm_input["task_type"])
        if llm_input["task_type"] == "repair_json_syntax_only":
            return json.dumps(_valid_output(llm_input["generation_unit"]), ensure_ascii=False)
        output = _valid_output(llm_input["generation_unit"])
        return json.dumps(output, ensure_ascii=False).replace('", "target_node_id"', '" "target_node_id"', 1)

    result = run_chapter_generation_batch(
        [_package("GU-1")],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert result.completed_count == 1
    assert calls == ["technical_bid_chapter_generation", "repair_json_syntax_only"]
    artifact = json.loads((tmp_path / "chapters" / "GU-1.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "completed"
    issue_types = [issue["type"] for issue in artifact["task"]["validation"]["issues"]]
    assert "json_repair_applied" in issue_types


def test_batch_runner_preserves_raw_output_when_json_repair_fails(tmp_path):
    broken = '{"title": "未闭合"'

    def fake_llm(_llm_input, _config):
        return broken

    result = run_chapter_generation_batch(
        [_package("GU-1")],
        state_dir=tmp_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    assert result.failed_count == 1
    artifact = json.loads((tmp_path / "chapters" / "GU-1.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "failed"
    assert artifact["task"]["output_text"] == broken
    assert artifact["task"]["error"]


def _package(unit_id: str) -> dict:
    return {
        "task_type": "technical_bid_chapter_generation",
        "schema_version": "chapter_generation_input_v0.1",
        "project_info": {"project_name": "测试项目"},
        "generation_unit": {
            "unit_id": unit_id,
            "target_node_id": unit_id.replace("GU", "NODE"),
            "chapter_path": ["主要施工方案与技术措施", f"测试章节{unit_id[-1]}"],
            "unit_type": "level2_section_group",
            "child_headings": ["施工准备", "工艺流程"],
        },
        "score_point": {
            "score_point_raw": "主要施工方案与技术措施",
            "score_standard_raw": "施工方案完整、合理。",
        },
        "technical_requirements": [],
        "excellent_bid_references": [],
        "table_references": [],
        "image_candidates": [],
        "expanded_generation_policy": {
            "mode": "expanded",
            "targets": {
                "min_sections": 1,
                "min_paragraphs_total": 1,
                "min_paragraphs_per_section": 1,
                "min_rich_tables": 0,
                "min_rows_per_rich_table": 0,
                "min_image_refs": 0,
            },
        },
        "generation_constraints": {"generation_mode": "expanded"},
    }


def _valid_output(unit: dict) -> dict:
    return {
        "schema_version": "technical_bid_chapter_draft_v1",
        "unit_id": unit["unit_id"],
        "target_node_id": unit["target_node_id"],
        "chapter_path": unit["chapter_path"],
        "title": unit["chapter_path"][-1],
        "sections": [
            {
                "heading": "施工准备",
                "level": 3,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "本节围绕施工准备、资源组织、技术交底和现场条件复核展开，形成可执行的章节正文。",
                    }
                ],
            }
        ],
        "score_response_check": {
            "score_point_raw": "主要施工方案与技术措施",
            "response_summary": "已响应施工方案完整性要求。",
            "covered": True,
            "evidence_headings": ["施工准备"],
        },
        "source_usage": [],
        "review_items": [],
    }


def _image_ids(chapter: dict) -> list[str]:
    return [
        block.get("image_id")
        for section in chapter["sections"]
        for block in section.get("blocks") or []
        if block.get("type") == "image_ref"
    ]


def _config(
    api_key: str | None = "test-key",
    *,
    max_workers: int = 1,
    max_retries: int = 0,
    model: str = "fake-model",
) -> LlmClientConfig:
    return LlmClientConfig(
        provider="test",
        api_key=api_key,
        base_url="https://example.test/v1",
        model=model,
        temperature=0,
        top_p=1,
        max_tokens=None,
        timeout_seconds=30,
        max_retries=max_retries,
        max_workers=max_workers,
    )
