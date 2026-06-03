import json
import time

from construction_bidding_agent.llm_config import LlmClientConfig
from construction_bidding_agent.outline_generator.refinement_runner import (
    run_outline_refinement_from_files,
    run_outline_refinement,
)


def test_run_outline_refinement_applies_valid_llm_output():
    outline = _outline()
    package = _package()

    def fake_llm(_package, _config):
        return json.dumps(
            {
                "schema_version": "outline_refinement_v1",
                "target_node_id": "N1",
                "level_1_title": "安全管理体系与措施",
                "level_1_title_unchanged": True,
                "domain": "construction",
                "category": "安全管理",
                "refined_children": [
                    {
                        "level": 2,
                        "title": "安全管理目标",
                        "title_source": "generated",
                        "reason": "补充安全目标",
                        "requires_review": False,
                        "children": [
                            {"level": 3, "title": "安全目标分解", "title_source": "generated"},
                        ],
                    },
                    {"level": 2, "title": "安全管理体系", "title_source": "generated", "children": []},
                    {"level": 2, "title": "安全生产责任制", "title_source": "generated", "children": []},
                    {"level": 2, "title": "危险源辨识与控制", "title_source": "generated", "children": []},
                    {"level": 2, "title": "安全防护措施", "title_source": "generated", "children": []},
                    {"level": 2, "title": "应急管理措施", "title_source": "generated", "children": []},
                ],
                "quality_self_check": {"needs_human_review": False},
            },
            ensure_ascii=False,
        )

    result = run_outline_refinement(
        outline,
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    node = result.outline["nodes"][0]
    assert result.applied_count == 1
    assert result.failed_count == 0
    assert node["template_source"] == "llm_refined"
    assert node["children"][0]["title"] == "安全管理目标"
    assert node["children"][0]["children"][0]["title"] == "安全目标分解"
    assert node["children"][0]["children"][0]["confirmation_state"]["review_status"] == "auto_checked"
    assert node["confirmation_state"]["review_status"] == "auto_checked"
    assert result.outline["confirmation"]["review_queue"] == []
    assert any(item["level"] == 3 for item in result.outline["confirmation"]["flat_nodes"])
    assert result.outline["refinement"]["status"] == "completed"


def test_run_outline_refinement_rejects_modified_level_1_and_keeps_rule_outline():
    outline = _outline()
    package = _package()

    def fake_llm(_package, _config):
        return json.dumps(
            {
                "schema_version": "outline_refinement_v1",
                "target_node_id": "N1",
                "level_1_title": "安全管理措施",
                "level_1_title_unchanged": False,
                "domain": "construction",
                "category": "安全管理",
                "refined_children": [],
                "quality_self_check": {},
            },
            ensure_ascii=False,
        )

    result = run_outline_refinement(
        outline,
        [package],
        llm_config_override=_config(),
        llm_callable=fake_llm,
    )

    node = result.outline["nodes"][0]
    assert result.applied_count == 0
    assert result.failed_count == 1
    assert node["template_source"] == "generated_from_requirement"
    assert [child["title"] for child in node["children"]] == ["安全保证措施"]
    assert any(issue["type"] == "level_1_modified" for issue in result.tasks[0].validation["issues"])
    assert result.outline["refinement"]["status"] == "failed"


def test_run_outline_refinement_skips_without_api_key_and_callable():
    result = run_outline_refinement(
        _outline(),
        [_package()],
        llm_config_override=_config(api_key=None),
    )

    assert result.skipped_count == 1
    assert result.applied_count == 0
    assert result.failed_count == 0
    assert result.outline["nodes"][0]["children"][0]["title"] == "安全保证措施"
    assert result.outline["refinement"]["status"] == "skipped"
    assert "API_KEY 未配置" in result.warnings[0]


def test_run_outline_refinement_parallel_preserves_order_and_applies_all():
    outline = _outline_with_two_nodes()
    packages = [_package("N1", "安全管理体系与措施"), _package("N2", "质量管理体系与措施")]

    def fake_llm(package, _config):
        target = package["target_outline_node"]
        if target["node_id"] == "N1":
            time.sleep(0.03)
        return _valid_response(target["node_id"], target["level_1_title"])

    result = run_outline_refinement(
        outline,
        packages,
        llm_config_override=_config(),
        llm_callable=fake_llm,
        max_workers=2,
    )

    assert [task.target_node_id for task in result.tasks] == ["N1", "N2"]
    assert result.applied_count == 2
    assert result.failed_count == 0
    assert result.outline["nodes"][0]["template_source"] == "llm_refined"
    assert result.outline["nodes"][1]["template_source"] == "llm_refined"


def test_run_outline_refinement_uses_configured_max_workers_by_default():
    outline = _outline_with_two_nodes()
    packages = [_package("N1", "安全管理体系与措施"), _package("N2", "质量管理体系与措施")]

    def fake_llm(package, _config):
        target = package["target_outline_node"]
        return _valid_response(target["node_id"], target["level_1_title"])

    result = run_outline_refinement(
        outline,
        packages,
        llm_config_override=_config(max_workers=3),
        llm_callable=fake_llm,
    )

    assert result.max_workers == 3
    assert result.execution_mode == "parallel"


def test_run_outline_refinement_parallel_is_faster_than_serial_for_slow_calls():
    packages = [
        _package("N1", "安全管理体系与措施"),
        _package("N2", "质量管理体系与措施"),
        _package("N3", "文明施工措施"),
        _package("N4", "工期保证措施"),
    ]

    def fake_llm(package, _config):
        time.sleep(0.04)
        target = package["target_outline_node"]
        return _valid_response(target["node_id"], target["level_1_title"])

    serial_start = time.perf_counter()
    run_outline_refinement(
        _outline_with_many_nodes(4),
        packages,
        llm_config_override=_config(),
        llm_callable=fake_llm,
        max_workers=1,
    )
    serial_duration = time.perf_counter() - serial_start

    parallel_start = time.perf_counter()
    run_outline_refinement(
        _outline_with_many_nodes(4),
        packages,
        llm_config_override=_config(),
        llm_callable=fake_llm,
        max_workers=4,
    )
    parallel_duration = time.perf_counter() - parallel_start

    assert parallel_duration < serial_duration * 0.75


def test_run_outline_refinement_uses_task_cache(tmp_path):
    call_count = {"count": 0}

    def fake_llm(package, _config):
        call_count["count"] += 1
        target = package["target_outline_node"]
        return _valid_response(target["node_id"], target["level_1_title"])

    first = run_outline_refinement(
        _outline(),
        [_package()],
        llm_config_override=_config(),
        llm_callable=fake_llm,
        cache_dir=tmp_path / "cache",
    )
    second = run_outline_refinement(
        _outline(),
        [_package()],
        llm_config_override=_config(),
        llm_callable=fake_llm,
        cache_dir=tmp_path / "cache",
    )
    refreshed = run_outline_refinement(
        _outline(),
        [_package()],
        llm_config_override=_config(),
        llm_callable=fake_llm,
        cache_dir=tmp_path / "cache",
        force_refresh=True,
    )

    assert call_count["count"] == 2
    assert first.tasks[0].cache_status == "miss"
    assert second.tasks[0].cache_status == "hit"
    assert refreshed.tasks[0].cache_status == "miss"
    assert second.applied_count == 1
    assert second.outline["nodes"][0]["template_source"] == "llm_refined"


def test_run_outline_refinement_from_files_filters_target_node_ids(tmp_path):
    outline_path = tmp_path / "outline.json"
    inputs_path = tmp_path / "inputs.json"
    outline_path.write_text(json.dumps(_outline_with_two_nodes(), ensure_ascii=False), encoding="utf-8")
    inputs_path.write_text(
        json.dumps(
            {
                "packages": [
                    _package("N1", "安全管理体系与措施"),
                    _package("N2", "质量管理体系与措施"),
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_llm(package, _config):
        target = package["target_outline_node"]
        return _valid_response(target["node_id"], target["level_1_title"])

    result = run_outline_refinement_from_files(
        outline_path,
        inputs_path,
        llm_config_override=_config(),
        llm_callable=fake_llm,
        cache_dir=tmp_path / "cache",
        target_node_ids=["N2"],
    )

    assert result.task_count == 1
    assert result.tasks[0].target_node_id == "N2"
    assert result.outline["nodes"][0]["template_source"] == "generated_from_requirement"
    assert result.outline["nodes"][1]["template_source"] == "llm_refined"


def _outline():
    return {
        "schema_version": "technical_bid_outline_v0.1",
        "generator_version": "stage0_rule_based",
        "outline_id": "outline_test",
        "status": "completed_with_warnings",
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "number": "1",
                "title": "安全管理体系与措施",
                "title_source": "score_point_raw",
                "score_rule": "安全措施完善。",
                "domain": "construction",
                "category": "安全管理",
                "template_source": "generated_from_requirement",
                "template_refs": [],
                "children": [
                    {
                        "node_id": "N1_001",
                        "level": 2,
                        "number": "1.1",
                        "title": "安全保证措施",
                        "title_source": "generated_from_requirement",
                        "domain": "construction",
                        "category": "安全管理",
                        "children": [],
                        "requires_review": True,
                        "review_reason": "二级目录由系统根据评分标准生成，需人工确认。",
                        "generation_status": "construction_ready",
                    }
                ],
                "requires_review": True,
                "review_reason": "二级目录由系统根据评分标准生成，需人工确认。",
                "generation_status": "construction_ready",
            }
        ],
        "review_items": [],
        "quality_checks": [],
    }


def _package_for(node_id, title):
    return {
        "target_outline_node": {
            "node_id": node_id,
            "level_1_title": title,
        },
        "granularity_rule": {
            "min_level_2_count": 6,
            "level_3_required": True,
        },
        "trigger_reasons": ["目录过薄", "核心章节缺少三级目录"],
    }


def _package(node_id="N1", title="安全管理体系与措施"):
    return _package_for(node_id, title)


def _outline_with_two_nodes():
    outline = _outline()
    second = json.loads(json.dumps(outline["nodes"][0], ensure_ascii=False))
    second["node_id"] = "N2"
    second["number"] = "2"
    second["title"] = "质量管理体系与措施"
    second["category"] = "质量管理"
    second["children"][0]["node_id"] = "N2_001"
    second["children"][0]["number"] = "2.1"
    second["children"][0]["title"] = "质量保证措施"
    outline["nodes"].append(second)
    return outline


def _outline_with_many_nodes(count):
    titles = ["安全管理体系与措施", "质量管理体系与措施", "文明施工措施", "工期保证措施"]
    outline = _outline()
    outline["nodes"] = []
    for index in range(count):
        node = json.loads(json.dumps(_outline()["nodes"][0], ensure_ascii=False))
        node["node_id"] = f"N{index + 1}"
        node["number"] = str(index + 1)
        node["title"] = titles[index]
        node["children"][0]["node_id"] = f"N{index + 1}_001"
        node["children"][0]["number"] = f"{index + 1}.1"
        outline["nodes"].append(node)
    return outline


def _valid_response(node_id, title):
    return json.dumps(
        {
            "schema_version": "outline_refinement_v1",
            "target_node_id": node_id,
            "level_1_title": title,
            "level_1_title_unchanged": True,
            "domain": "construction",
            "category": "安全管理",
            "refined_children": [
                {"level": 2, "title": "管理目标", "title_source": "generated", "children": [{"level": 3, "title": "目标分解", "title_source": "generated"}]},
                {"level": 2, "title": "组织体系", "title_source": "generated", "children": []},
                {"level": 2, "title": "责任制度", "title_source": "generated", "children": []},
                {"level": 2, "title": "过程控制", "title_source": "generated", "children": []},
                {"level": 2, "title": "检查整改", "title_source": "generated", "children": []},
                {"level": 2, "title": "应急处置", "title_source": "generated", "children": []},
            ],
            "quality_self_check": {"needs_human_review": False},
        },
        ensure_ascii=False,
    )


def _config(api_key="key", *, max_workers=1):
    return LlmClientConfig(
        provider="test",
        api_key=api_key,
        base_url="https://compatible.example.com/v1",
        model="test-model",
        temperature=0.2,
        top_p=0.9,
        max_tokens=4096,
        timeout_seconds=120,
        max_retries=2,
        api_type="responses",
        structured_output_type="json_object",
        enable_thinking=False,
        reasoning_effort="none",
        store_response=False,
        max_workers=max_workers,
    )
