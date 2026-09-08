from __future__ import annotations

import importlib.util
import json
from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "playwright"
    / "enterprise-knowledge-base-release-stage19"
)
MATRIX = [
    {
        "route": route,
        "theme": theme,
        "viewport": {"width": width, "height": height},
        "name": f"{route}-{theme}-{width}",
    }
    for route in ("direct", "hash")
    for theme in ("light", "dark")
    for width, height in ((1440, 900), (375, 812), (280, 720))
]


def test_stage19_controlled_playwright_evidence_is_complete_and_secret_safe() -> None:
    required = (
        "run-stage19-acceptance.ps1",
        "stage19-acceptance.py",
        "stage19-controlled-fixture.json",
        "stage19_gate.py",
        "test_stage19_gate.py",
        "stage19-browser-result.json",
        "stage19-artifact-manifest.json",
        "README.md",
    )
    for name in required:
        assert (OUT / name).is_file(), name
    result = json.loads((OUT / "stage19-browser-result.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == "stage19-browser-result.v1"
    assert result["stage"] == 19
    assert result["target_revision"] == "0029_enterprise_knowledge_base_releases"
    assert result["status"] == "passed"
    assert result["exit_code"] == 0
    assert result["matrix"] == MATRIX
    assert result["required_flows"] == {
        "capture": True,
        "approval_promote": True,
        "rollback": True,
        "lazy_tabs": True,
    }
    assert result["release_center_visible"] is True
    assert result["desktop_table_visible"] is True
    assert result["mobile_cards_visible"] is True
    assert result["keyboard_focus_all_passed"] is True
    assert result["all_no_horizontal_overflow"] is True
    assert result["console_error_count"] == 0
    assert result["page_error_count"] == 0
    assert result["unknown_request_count"] == 0
    assert result["unexpected_failed_request_count"] == 0
    assert result["raw_ticket_visible"] is False
    assert result["raw_secret_visible"] is False
    assert result["raw_idempotency_key_visible"] is False
    assert result["raw_ticket_result_json"] is False
    assert result["raw_secret_result_json"] is False
    assert result["raw_idempotency_key_result_json"] is False
    assert result["production_actions_performed"] is False
    assert result["blocking_reasons"] == []
    assert all(not item["gate_failures"] for item in result["results"])


def test_stage19_artifact_manifest_is_fresh_distinct_and_complete() -> None:
    manifest = json.loads((OUT / "stage19-artifact-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "stage19-artifact-manifest.v1"
    assert manifest["stage"] == 19
    assert manifest["target_revision"] == "0029_enterprise_knowledge_base_releases"
    assert manifest["status"] == "passed"
    assert manifest["run_id"]
    assert manifest["captured_at"]
    assert manifest["all_required_screenshots_captured"] is True
    assert manifest["duplicate_files"] == []
    assert manifest["duplicate_capture_ids"] == []
    source_binding = manifest["source_binding"]
    assert source_binding["source_tree_sha256"]
    assert len(source_binding["source_tree_sha256"]) == 64
    assert source_binding["source_file_count"] > 0
    assert set(source_binding["lockfile_sha256"]) == {"uv.lock", "frontend/package-lock.json"}
    assert all(len(value) == 64 for value in source_binding["lockfile_sha256"].values())
    spec = importlib.util.spec_from_file_location(
        "stage19_acceptance_binding", OUT / "stage19-acceptance.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert source_binding == module._source_binding()
    assert len(manifest["artifacts"]) == 35
    assert len({item["file"] for item in manifest["artifacts"]}) == 35
    assert len({item["capture_id"] for item in manifest["artifacts"]}) == 35
    assert all(
        item["exists"]
        and item["captured_this_run"]
        and item["fresh"]
        and item["bytes"] > 0
        and len(item["sha256"]) == 64
        for item in manifest["artifacts"]
    )
