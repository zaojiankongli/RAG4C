from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from core.enterprise_release_quality_evidence import canonical_quality_digest


STAGE17_TARGET_REVISION = "0027_enterprise_workspace_authorization"
STAGE17_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "playwright"
    / "enterprise-workspace-authorization-stage17"
)
STAGE17_MATRIX = [
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


STAGE18_TARGET_REVISION = "0028_enterprise_knowledge_base_registry"
STAGE18_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "playwright"
    / "enterprise-knowledge-base-registry-stage18"
)
STAGE18_MATRIX = [
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


class _MissingTool:
    HEAD_REVISION = "missing-enterprise-upgrade-tool"

    def __getattr__(self, name: str):  # pragma: no cover - intentional RED phase
        pytest.fail(f"enterprise upgrade tool is missing; cannot access {name}")


@pytest.fixture()
def tool():
    try:
        from scripts import enterprise_catalog_upgrade
    except (ImportError, ModuleNotFoundError):  # pragma: no cover - intentional RED phase
        return _MissingTool()
    return enterprise_catalog_upgrade


def test_url_safety_report_redacts_credentials_and_rejects_system_database(tool) -> None:
    report = tool.inspect_url_safety(
        "mysql+pymysql://catalog-user:super-secret@db.internal:3306/rag4c?charset=utf8mb4",
        target_label="production",
    )

    assert report.safe is True
    assert report.label == "mysql+pymysql://db.internal:3306/rag4c"
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "super-secret" not in serialized
    assert "catalog-user" not in serialized

    unsafe = tool.inspect_url_safety(
        "mysql+pymysql://root:secret@db.internal:3306/mysql",
        target_label="production",
    )
    assert unsafe.safe is False
    assert any("system" in reason.lower() for reason in unsafe.reasons)
    assert "secret" not in json.dumps(unsafe.to_dict())


def test_preflight_is_read_only_and_reports_revision_tables_roles_and_owner_gaps(
    tool, tmp_path: Path
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES ('0007_chunk_rev')")
            )
            connection.execute(
                text(
                    "CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("CREATE TABLE tenant_members (tenant_id VARCHAR(64), role VARCHAR(32))")
            )
            connection.execute(
                text(
                    "INSERT INTO tenants(id, status) VALUES ('tenant-1', 'active'), ('tenant-2', 'active')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_members(tenant_id, role) VALUES "
                    "('tenant-1', 'owner'), ('tenant-1', 'ADMIN'), ('tenant-2', 'mystery')"
                )
            )

        before_tables = set(tool.inspect(engine).get_table_names())
        report = tool.run_preflight(
            database_url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            require_mysql=False,
        )
        after_tables = set(tool.inspect(engine).get_table_names())

        assert report["url"]["safe"] is True
        assert report["revision"] == "0007_chunk_rev"
        assert report["head_revision"] == tool.HEAD_REVISION
        assert report["schema"]["status"] == "behind"
        assert "knowledge_audit_events" in report["schema"]["missing_tables"]
        assert report["roles"]["unknown"] == ["mystery"]
        assert report["owners"]["ownerless_active_tenants"] == 1
        assert report["safe_to_upgrade"] is False
        assert before_tables == after_tables
    finally:
        engine.dispose()


def test_disk_target_check_never_creates_target_directory(tool, tmp_path: Path) -> None:
    target = tmp_path / "future-backups"
    report = tool.check_disk_target(target, min_free_bytes=1)

    assert report["target_exists"] is False
    assert report["parent_exists"] is True
    assert target.exists() is False


def test_backup_command_uses_shell_quoting_and_never_prints_password(tool) -> None:
    command = tool.build_backup_command(
        "mysql+pymysql://backup-user:p@ss-word@db.internal:3307/rag4c?charset=utf8mb4",
        Path("C:/Program Files/RAG4C/backups/client & prod.sql"),
        shell="powershell",
    )

    assert "p@ss-word" not in command
    assert "mysql+pymysql://" not in command
    assert "MYSQL_PWD" in command
    assert "client & prod.sql" in command
    assert "--single-transaction" in command

    defaults_command = tool.build_backup_command(
        "mysql+pymysql://backup-user:secret@db.internal:3307/rag4c",
        Path("/var/backups/rag4c.sql"),
        shell="posix",
        defaults_extra_file=Path("/run/mysql-client.cnf"),
    )
    assert "secret" not in defaults_command
    assert "--defaults-extra-file=" in defaults_command
    assert "mysql-client.cnf" in defaults_command


def test_verify_backup_checks_dump_markers_and_sha256(tool, tmp_path: Path) -> None:
    backup = tmp_path / "rag4c.sql"
    backup.write_bytes(
        b"-- MySQL dump 10.13\n"
        b"SET NAMES utf8mb4;\n"
        b"CREATE TABLE `alembic_version` (`version_num` varchar(64));\n"
        b"-- Dump completed on 2026-08-26 10:42:00\n"
    )
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()

    report = tool.verify_backup(backup, digest)

    assert report["verified"] is True
    assert report["sha256"] == digest
    assert report["size_bytes"] > 0

    wrong_hash = tool.verify_backup(backup, "0" * 64)
    assert wrong_hash["verified"] is False
    assert wrong_hash["sha256_match"] is False


def test_migration_plan_lists_every_revision_and_manual_rollback_points(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0007_chunk_rev",
        target_revision=STAGE18_TARGET_REVISION,
    )

    revisions = [step["revision"] for step in plan["steps"]]
    assert revisions == [
        "0008_governance",
        "0009_content",
        "0010_dataset_profile",
        "0011_durable_delete",
        "0012_retrieval_experiments",
        "0013_source_control",
        "0014_source_schedules",
        "0015_document_catalog_indexes",
        "0016_enterprise_membership",
        "0017_enterprise_access_graph",
        "0018_organization_membership",
        "0019_dataset_acl_control",
        "0020_tenant_invitation_lifecycle",
        "0021_enterprise_identity_federation",
        "0022_scim_provisioning_data_plane",
        "0023_enterprise_audit_compliance",
        "0024_oidc_sso_runtime",
        "0025_enterprise_approval_control",
        "0026_enterprise_workspace_control",
        "0027_enterprise_workspace_authorization",
        "0028_enterprise_knowledge_base_registry",
    ]
    assert len(revisions) == 21
    assert plan["to_revision"] == STAGE18_TARGET_REVISION
    assert plan["head_revision"] == tool.HEAD_REVISION
    assert plan["maintenance_window_minutes"] == 183
    assert all(step["rollback_point"] for step in plan["steps"])
    assert plan["automatic_rollback"] is False
    assert plan["manual_rollback_only"] is True


def test_upgrade_defaults_to_dry_run_and_does_not_call_alembic(tool) -> None:
    called = False

    def runner(_config, _target):
        nonlocal called
        called = True

    report = tool.run_upgrade(
        "mysql+pymysql://user:secret@db.internal:3306/rag4c",
        execute=False,
        expected_current="0007_chunk_rev",
        alembic_runner=runner,
    )

    assert report["mode"] == "dry-run"
    assert report["executed"] is False
    assert called is False
    assert report["safety_gates"]["execute_required"] is True


def test_upgrade_execute_requires_backup_hash_and_expected_current(tool, tmp_path: Path) -> None:
    with pytest.raises(tool.SafetyGateError, match="backup-sha256"):
        tool.run_upgrade(
            "mysql+pymysql://user:secret@db.internal:3306/rag4c",
            execute=True,
            expected_current=None,
            backup_sha256=None,
            backup_path=tmp_path / "backup.sql",
        )


def test_upgrade_rechecks_before_and_after_and_calls_injected_alembic_runner(
    tool, tmp_path: Path
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    engine = create_engine(database_url)
    backup = tmp_path / "backup.sql"
    backup.write_bytes(
        b"-- MySQL dump\nCREATE TABLE `alembic_version` (x int);\n-- Dump completed\n"
    )
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('0007_chunk_rev')")
        )

    calls: list[str] = []

    def preflight_provider():
        calls.append("preflight")
        return {"safe_to_upgrade": True, "revision": "0007_chunk_rev"}

    def runner(_config, target):
        calls.append(f"alembic:{target}")
        with engine.begin() as connection:
            connection.execute(
                text(f"UPDATE alembic_version SET version_num='{tool.HEAD_REVISION}'")
            )

    try:
        report = tool.run_upgrade(
            database_url,
            execute=True,
            backup_path=backup,
            backup_sha256=digest,
            expected_current="0007_chunk_rev",
            target_label="test",
            engine=engine,
            preflight_provider=preflight_provider,
            alembic_runner=runner,
            require_mysql=False,
        )
    finally:
        engine.dispose()

    assert calls == ["preflight", "alembic:head"]
    assert report["executed"] is True
    assert report["before_revision"] == "0007_chunk_rev"
    assert report["after_revision"] == tool.HEAD_REVISION


def test_rollback_only_generates_steps_and_rejects_execute(tool, tmp_path: Path) -> None:
    plan = tool.run_rollback(
        "mysql+pymysql://user:secret@db.internal:3306/rag4c",
        current_revision=tool.HEAD_REVISION,
        target_revision="0007_chunk_rev",
        backup_path=tmp_path / "backup.sql",
        execute=False,
    )
    assert plan["executed"] is False
    assert plan["automatic_restore"] is False
    assert plan["steps"]
    assert all("manual" in step["operator_action"].lower() for step in plan["steps"])

    with pytest.raises(tool.SafetyGateError, match="rollback"):
        tool.run_rollback(
            "mysql+pymysql://user:secret@db.internal:3306/rag4c",
            current_revision=tool.HEAD_REVISION,
            target_revision="0007_chunk_rev",
            execute=True,
        )


def test_cli_dry_run_plan_and_backup_command_do_not_touch_database(
    tool, capsys, tmp_path: Path
) -> None:
    assert (
        tool.main(
            [
                "backup-command",
                "--url",
                "mysql+pymysql://user:secret@db.internal:3306/rag4c",
                "--output",
                str(tmp_path / "backup.sql"),
            ]
        )
        == 0
    )
    assert "secret" not in capsys.readouterr().out

    assert tool.main(["plan", "--from", "0007_chunk_rev", "--to", "head"]) == 0
    assert "0015_document_catalog_indexes" in capsys.readouterr().out

    assert (
        tool.main(
            [
                "upgrade",
                "--url",
                "mysql+pymysql://user:secret@db.internal:3306/rag4c",
                "--expected-current",
                "0007_chunk_rev",
            ]
        )
        == 0
    )
    assert "dry-run" in capsys.readouterr().out


def test_preflight_reports_duplicate_pending_invitations_without_writing(
    tool,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'duplicate-invitations.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES ('0019_dataset_acl_control')")
            )
            connection.execute(
                text(
                    "CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("CREATE TABLE tenant_members (tenant_id VARCHAR(64), role VARCHAR(32))")
            )
            connection.execute(
                text(
                    "CREATE TABLE tenant_invitations "
                    "(tenant_id VARCHAR(64), normalized_email VARCHAR(256), "
                    "status VARCHAR(16), token_hash VARCHAR(128))"
                )
            )
            connection.execute(
                text("INSERT INTO tenants(id, status) VALUES ('tenant-1', 'active')")
            )
            connection.execute(
                text("INSERT INTO tenant_members(tenant_id, role) VALUES ('tenant-1', 'owner')")
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_invitations "
                    "(tenant_id, normalized_email, status, token_hash) VALUES "
                    "('tenant-1', 'duplicate@example.test', 'pending', :token_a), "
                    "('tenant-1', 'duplicate@example.test', 'pending', :token_b)"
                ),
                {"token_a": "a" * 64, "token_b": "b" * 64},
            )

        before = (
            engine.connect().execute(text("SELECT COUNT(*) FROM tenant_invitations")).scalar_one()
        )
        report = tool.run_preflight(
            database_url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            require_mysql=False,
        )
        after = (
            engine.connect().execute(text("SELECT COUNT(*) FROM tenant_invitations")).scalar_one()
        )

        assert report["invitations"] == {
            "checked": True,
            "duplicate_pending": [
                {
                    "tenant_id": "tenant-1",
                    "normalized_email": "duplicate@example.test",
                    "count": 2,
                }
            ],
            "duplicate_token_hashes": [],
        }
        assert "duplicate pending tenant invitations were found" in report["reasons"]
        assert report["safe_to_upgrade"] is False
        assert report["read_only"] is True
        assert before == after == 2
    finally:
        engine.dispose()


def test_enterprise_upgrade_runbook_targets_0028_and_registry_preflight() -> None:
    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")

    assert "0020_tenant_invitation_lifecycle" in runbook
    assert "0021_enterprise_identity_federation" in runbook
    assert "0022_scim_provisioning_data_plane" in runbook
    assert "0023_enterprise_audit_compliance" in runbook
    assert "0024_oidc_sso_runtime" in runbook
    assert "0025_enterprise_approval_control" in runbook
    assert "0026_enterprise_workspace_control" in runbook
    assert "0027_enterprise_workspace_authorization" in runbook
    assert "二十一个预期 revision" in runbook
    assert "`183` 分钟" in runbook
    assert "15 + 8 × 21 = 183" in runbook
    assert (
        "从 `0007_chunk_rev` 到 `0028_enterprise_knowledge_base_registry` 有 21 个迁移步骤"
        in runbook
    )
    assert "至少 360 分钟" in runbook
    assert "duplicate pending" in runbook.casefold()
    assert "tenant_control_mutation_requests" in runbook
    assert "runtime_not_connected" in runbook
    assert "tenant_scim_user_links" in runbook
    assert "tenant_scim_group_links" in runbook
    assert "use_count" in runbook
    assert "tenant_audit_retention_policies" in runbook
    assert "tenant_audit_legal_holds" in runbook
    assert "tenant_audit_export_jobs" in runbook
    assert "manual_execution_only" in runbook
    assert "tenant_approval_policies" in runbook
    assert "tenant_approval_policy_approvers" in runbook
    assert "tenant_approval_requests" in runbook
    assert "tenant_approval_decisions" in runbook
    assert "execution authorization ticket" in runbook
    assert "tenant_workspaces" in runbook
    assert "tenant_workspace_members" in runbook
    assert "tenant_workspace_datasets" in runbook
    assert "workspace-default-{tenant_id}" in runbook
    assert "tenant_workspace_authorization_policies" in runbook
    assert "workspace_authorization_mode_change" in runbook
    assert "disabled -> shadow -> enforced" in runbook
    assert "permission_model_version" in runbook
    assert "permission_matrix_fingerprint" in runbook
    assert "禁止自动生产迁移" in runbook
    assert "禁止自动回滚" in runbook
    assert "禁止自动写库" in runbook
    assert "不执行真实 Workspace Authorization policy backfill" in runbook
    assert "不执行真实 Enforced 激活" in runbook
    assert "不执行真实 Workspace Authorization approval execution" in runbook


def test_stage17_runbook_uses_uv_and_describes_controlled_qa_blockers() -> None:
    runbook = (
        Path(__file__).resolve().parents[1] / "docs/operations/enterprise-catalog-upgrade.md"
    ).read_text(encoding="utf-8")

    assert "uv run python scripts/enterprise_catalog_upgrade.py" in runbook
    assert "run-stage17-acceptance.ps1" in runbook
    assert "stage17-browser-result.json" in runbook
    assert "direct/hash" in runbook
    assert "light/dark" in runbook
    assert "1440/375/280" in runbook
    assert "console_error_count" in runbook
    assert "page_error_count" in runbook
    assert "unknown_request_count" in runbook
    assert "all_no_horizontal_overflow" in runbook
    assert "raw_ticket_visible" in runbook
    assert "raw_ticket_persisted" in runbook
    assert "前端未就绪" in runbook or "blocked" in runbook.casefold()
    assert "application head" in runbook.casefold()


def test_stage17_controlled_playwright_result_is_matrix_complete_and_ticket_safe() -> None:
    output_dir = STAGE17_OUTPUT_DIR
    required_files = (
        "run-stage17-acceptance.ps1",
        "stage17-acceptance.py",
        "stage17-controlled-fixture.json",
        "stage17-browser-result.json",
        "stage17-artifact-manifest.json",
        "stage17_gate.py",
        "test_stage17_gate.py",
        "README.md",
    )
    for name in required_files:
        assert (output_dir / name).is_file(), f"missing Stage17 QA artifact: {name}"

    result = json.loads((output_dir / "stage17-browser-result.json").read_text(encoding="utf-8"))

    assert result["schema_version"] == "stage17-browser-result.v2"
    assert result["stage"] == 17
    assert result["target_revision"] == STAGE17_TARGET_REVISION
    assert result["status"] in {"blocked", "passed"}
    assert result["matrix"] == STAGE17_MATRIX
    assert result["raw_ticket_visible"] is False
    assert result["raw_ticket_persisted"] is False
    assert result["raw_ticket_result_json"] is False
    assert result["unexpected_failed_request_count"] == 0
    assert result["failed_request_count"] == result["allowlisted_failed_request_count"]

    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert "rag4c-approval-ticket-" not in serialized
    assert "opaque-ticket-" not in serialized

    if result["status"] == "passed":
        assert result["exit_code"] == 0
        assert result["console_error_count"] == 0
        assert result["page_error_count"] == 0
        assert result["unknown_request_count"] == 0
        assert result["all_no_horizontal_overflow"] is True
        assert result["permissions_rollout_visible"] is True
        assert result["impact_visible"] is True
        assert result["mode_dialog_visible"] is True
        assert result["keyboard_focus_all_passed"] is True
        assert result["screenshot_files"]
        assert all(not item["gate_failures"] for item in result["results"])
    else:
        assert result["exit_code"] == 2
        assert result["blocking_reasons"]
        assert any(item["gate_failures"] for item in result["results"])


def test_stage18_runbook_describes_registry_preflight_and_controlled_qa() -> None:
    runbook = (
        Path(__file__).resolve().parents[1] / "docs/operations/enterprise-catalog-upgrade.md"
    ).read_text(encoding="utf-8")

    for value in (
        "0028_enterprise_knowledge_base_registry",
        "dataset_workspace_ownerships",
        "app_dataset_references",
        "dataset_workspace_transfer",
        "ownership backfill",
        "Application reference",
        "run-stage18-acceptance.ps1",
        "stage18-browser-result.json",
        "禁止自动生产迁移",
        "不执行真实 Dataset ownership transfer",
        "不执行真实 Application reference mutation",
    ):
        assert value in runbook


def test_stage18_controlled_playwright_result_is_fail_closed_and_matrix_complete() -> None:
    required_files = (
        "run-stage18-acceptance.ps1",
        "stage18-acceptance.py",
        "stage18-controlled-fixture.json",
        "stage18-browser-result.json",
        "stage18-artifact-manifest.json",
        "stage18_gate.py",
        "test_stage18_gate.py",
        "README.md",
    )
    for name in required_files:
        assert (STAGE18_OUTPUT_DIR / name).is_file(), f"missing Stage18 QA artifact: {name}"

    result = json.loads(
        (STAGE18_OUTPUT_DIR / "stage18-browser-result.json").read_text(encoding="utf-8")
    )
    assert result["stage"] == 18
    assert result["target_revision"] == STAGE18_TARGET_REVISION
    assert result["matrix"] == STAGE18_MATRIX
    assert result["status"] in {"blocked", "passed"}
    assert result["raw_ticket_visible"] is False
    assert result["raw_secret_visible"] is False
    manifest = json.loads(
        (STAGE18_OUTPUT_DIR / "stage18-artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["all_required_screenshots_captured"] is (result["status"] == "passed")
    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert "rag4c-approval-ticket-" not in serialized
    assert "raw-password" not in serialized

    if result["status"] == "passed":
        assert result["exit_code"] == 0
        assert result["console_error_count"] == 0
        assert result["page_error_count"] == 0
        assert result["unknown_request_count"] == 0
        assert result["unexpected_failed_request_count"] == 0
        assert result["all_no_horizontal_overflow"] is True
        assert result["registry_visible"] is True
        assert result["dependency_rail_visible"] is True
        assert result["application_references_visible"] is True
        assert result["ownership_transfer_visible"] is True
        assert result["keyboard_focus_all_passed"] is True
        assert result["screenshot_files"]
    else:
        assert result["exit_code"] == 2
        assert result["blocking_reasons"]
        assert result["screenshot_files"] == []


def test_stage18_artifact_contract_requires_split_surfaces_responsive_filter_and_transfer_evidence() -> (
    None
):
    result = json.loads(
        (STAGE18_OUTPUT_DIR / "stage18-browser-result.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (STAGE18_OUTPUT_DIR / "stage18-artifact-manifest.json").read_text(encoding="utf-8")
    )

    assert result["schema_version"] == "stage18-browser-result.v2"
    assert result["registry_baseline_matrix"] == STAGE18_MATRIX
    assert result["deep_link_detail_matrix"] == STAGE18_MATRIX
    for item in result["results"]:
        baseline = item["registry_baseline"]
        detail = item["deep_link_detail"]
        assert baseline["surface"] == "registry_baseline"
        assert detail["surface"] == "deep_link_detail"
        if result["status"] == "passed":
            assert detail["opened_from_deep_link"] is True
        assert set(
            ("opened", "focus_inside", "escape_closed", "focus_return", "all_passed")
        ) <= set(item["drawer_focus"])
        if item["viewport"]["width"] <= 600 and result["status"] == "passed":
            assert baseline["cards_visible"] is True
            assert baseline["filter_drawer_visible"] is True

    for field in (
        "archive_blocker_visible",
        "registry_cards_visible",
        "filter_drawer_visible",
        "direct_transfer",
        "approval_transfer",
        "drawer_focus",
    ):
        assert field in result

    assert manifest["schema_version"] == "stage18-artifact-manifest.v2"
    assert manifest["run_id"]
    assert manifest["captured_at"]
    artifact_names = [item["file"] for item in manifest["artifacts"]]
    assert len(artifact_names) == len(set(artifact_names))
    capture_ids = [item["capture_id"] for item in manifest["artifacts"] if item["capture_id"]]
    assert len(capture_ids) == len(set(capture_ids))
    assert all("fresh" in item for item in manifest["artifacts"])

    if result["status"] == "passed":
        assert result["direct_transfer"] is True
        assert result["approval_transfer"] is True
        assert result["drawer_focus"] is True
        assert result["registry_cards_visible"] is True
        assert result["filter_drawer_visible"] is True
        assert manifest["all_required_screenshots_captured"] is True
    else:
        assert result["status"] == "blocked"
        assert result["exit_code"] == 2
        assert result["blocking_reasons"]
        assert isinstance(manifest["all_required_screenshots_captured"], bool)
        assert result["screenshot_files"] == []


def test_stage18_artifact_manifest_rejects_stale_or_duplicate_capture_contract() -> None:
    manifest = json.loads(
        (STAGE18_OUTPUT_DIR / "stage18-artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["fresh_artifacts"] is True
    assert manifest["distinct_artifacts"] is True
    assert manifest["duplicate_files"] == []
    assert manifest["duplicate_capture_ids"] == []


def test_stage18_preflight_reports_registry_ownership_and_reference_blockers(
    tool, tmp_path: Path
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version(version_num) VALUES "
                "('0027_enterprise_workspace_authorization')"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE datasets (id VARCHAR(64), tenant_id VARCHAR(64), status VARCHAR(16))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_workspace_datasets (dataset_id VARCHAR(64), tenant_id VARCHAR(64), "
                "workspace_id VARCHAR(128), status VARCHAR(16), binding_kind VARCHAR(16), "
                "active_primary_slot VARCHAR(16))"
            )
        )
        connection.execute(text("CREATE TABLE apps (id VARCHAR(64), tenant_id VARCHAR(64))"))
        connection.execute(
            text(
                "CREATE TABLE app_dataset_references (id VARCHAR(64), tenant_id VARCHAR(64), "
                "app_id VARCHAR(64), dataset_id VARCHAR(64), status VARCHAR(16), active_slot VARCHAR(16))"
            )
        )
        connection.execute(text("CREATE TABLE tenant_approval_policies (action_type VARCHAR(64))"))
        connection.execute(
            text(
                "INSERT INTO datasets VALUES "
                "('dataset-a','tenant-a','active'),('dataset-a','tenant-b','active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_workspace_datasets VALUES "
                "('dataset-a','tenant-a','workspace-shared','active','shared',NULL),"
                "('dataset-a','tenant-b','workspace-primary','active','primary','primary')"
            )
        )
        connection.execute(text("INSERT INTO apps VALUES ('app-a','tenant-a')"))
        connection.execute(
            text(
                "INSERT INTO app_dataset_references VALUES "
                "('ref-a','tenant-a','app-a','dataset-a','active','active')"
            )
        )
        connection.execute(
            text("INSERT INTO tenant_approval_policies VALUES ('dataset_workspace_transfer')")
        )
    report = tool.run_preflight(
        f"sqlite:///{(tmp_path / 'preflight.db').as_posix()}",
        engine=engine,
        target_label="test",
        backup_dir=tmp_path,
        require_mysql=False,
        expected_current="0027_enterprise_workspace_authorization",
    )
    registry = report["registry"]
    assert registry["checked"] is True
    assert registry["dataset_count"] == 2
    assert registry["primary_ownership_candidate_count"] == 1
    assert registry["missing_primary_dataset_ids"] == ["tenant-a:dataset-a"]
    assert registry["active_shared_association_count"] == 1
    assert registry["active_application_reference_count"] == 1
    assert registry["dataset_workspace_transfer_approval_count"] == 1
    assert "missing_primary_ownership" in registry["blockers"]
    assert "knowledge base registry preflight blockers were found" in report["reasons"]
    assert report["safe_to_upgrade"] is False
    assert report["read_only"] is True
    engine.dispose()


def _create_stage19_release_preflight_fixture(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version(version_num) VALUES "
                "('0029_enterprise_knowledge_base_releases')"
            )
        )
        connection.execute(
            text("CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text(
                "CREATE TABLE datasets ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "status VARCHAR(16) NOT NULL, profile_revision INTEGER NOT NULL, "
                "mutation_generation INTEGER NOT NULL, serving_generation INTEGER NOT NULL, "
                "graph_enabled INTEGER NOT NULL, serving_release_id VARCHAR(64), "
                "release_revision INTEGER NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_workspaces ("
                "id VARCHAR(128) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "status VARCHAR(16) NOT NULL, revision INTEGER NOT NULL, "
                "PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_workspace_ownerships ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, workspace_id VARCHAR(128) NOT NULL, "
                "revision INTEGER NOT NULL, PRIMARY KEY (tenant_id, dataset_id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE documents ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, lifecycle_state VARCHAR(24) NOT NULL, "
                "retrieval_enabled INTEGER NOT NULL, current_version_id VARCHAR(64), "
                "content_revision INTEGER NOT NULL, desired_index_revision INTEGER NOT NULL, "
                "indexed_revision INTEGER NOT NULL, graph_revision INTEGER NOT NULL, "
                "mutation_generation INTEGER NOT NULL, active_delete_operation_id VARCHAR(64), "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE document_versions ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, document_id VARCHAR(64) NOT NULL, "
                "revision INTEGER NOT NULL, source_hash VARCHAR(64), "
                "PRIMARY KEY (tenant_id, dataset_id, document_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE index_operations ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, document_id VARCHAR(64) NOT NULL, "
                "target_store VARCHAR(32) NOT NULL, operation VARCHAR(24) NOT NULL, "
                "target_revision INTEGER NOT NULL, document_generation INTEGER NOT NULL, "
                "status VARCHAR(24) NOT NULL, PRIMARY KEY (id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE data_sources ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, status VARCHAR(24) NOT NULL, "
                "mutation_generation INTEGER NOT NULL, PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE source_sync_runs ("
                "id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, "
                "tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "status VARCHAR(24) NOT NULL, execution_state VARCHAR(16) NOT NULL, "
                "source_generation INTEGER NOT NULL, dataset_generation INTEGER NOT NULL, "
                "PRIMARY KEY (id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_release_channels ("
                "id VARCHAR(128) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "code VARCHAR(64) NOT NULL, normalized_code VARCHAR(64) NOT NULL, "
                "status VARCHAR(16) NOT NULL, is_default_serving INTEGER NOT NULL, "
                "active_default_slot VARCHAR(16), revision INTEGER NOT NULL, "
                "PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_manifests ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, release_number INTEGER NOT NULL, "
                "profile_revision INTEGER NOT NULL, ownership_revision INTEGER NOT NULL, "
                "workspace_id VARCHAR(128) NOT NULL, workspace_revision INTEGER NOT NULL, "
                "mutation_generation INTEGER NOT NULL, serving_generation INTEGER NOT NULL, "
                "readiness_state VARCHAR(16) NOT NULL, entry_count INTEGER NOT NULL, "
                "blocker_count INTEGER NOT NULL, PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_entries ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, release_id VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_events ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, release_id VARCHAR(64) NOT NULL, "
                "event_type VARCHAR(24) NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_channel_releases ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, channel_id VARCHAR(128) NOT NULL, "
                "status VARCHAR(16) NOT NULL, active_release_id VARCHAR(64), "
                "revision INTEGER NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE apps (id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE app_dataset_references ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, app_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL, active_slot VARCHAR(16) NOT NULL, "
                "release_mode VARCHAR(16), release_channel_id VARCHAR(128), pinned_release_id VARCHAR(64), "
                "PRIMARY KEY (tenant_id, id))"
            )
        )

        connection.execute(
            text(
                "INSERT INTO tenants(id, status) VALUES "
                "('tenant-a', 'active'), ('tenant-b', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id,tenant_id,status,profile_revision,mutation_generation,serving_generation,graph_enabled,serving_release_id,release_revision) VALUES "
                "('dataset-1','tenant-a','active',2,3,4,0,'release-a',2),"
                "('dataset-1','tenant-b','active',1,1,1,1,'release-b',2),"
                "('dataset-archived','tenant-a','archived',1,0,0,0,NULL,1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_workspaces(id,tenant_id,status,revision) VALUES "
                "('workspace-a','tenant-a','active',6)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_workspace_ownerships "
                "(id,tenant_id,dataset_id,workspace_id,revision) VALUES "
                "('ownership-a','tenant-a','dataset-1','workspace-a',4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id,tenant_id,dataset_id,lifecycle_state,retrieval_enabled,current_version_id,content_revision,desired_index_revision,indexed_revision,graph_revision,mutation_generation,active_delete_operation_id) VALUES "
                "('doc-a','tenant-a','dataset-1','active',1,'version-a',1,2,2,0,1,NULL),"
                "('doc-b','tenant-b','dataset-1','active',1,NULL,0,3,1,1,1,NULL),"
                "('doc-disabled','tenant-a','dataset-1','deleted',0,NULL,0,0,0,0,0,NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id,tenant_id,dataset_id,document_id,revision,source_hash) VALUES "
                "('version-a','tenant-a','dataset-1','doc-a',2,:source_hash)"
            ),
            {"source_hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO index_operations "
                "(id,tenant_id,dataset_id,document_id,target_store,operation,target_revision,document_generation,status) VALUES "
                "('index-b','tenant-b','dataset-1','doc-b','milvus_chunks','upsert',3,1,'pending'),"
                "('index-a-done','tenant-a','dataset-1','doc-a','milvus_chunks','upsert',2,1,'completed')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO data_sources(id,tenant_id,dataset_id,status,mutation_generation) VALUES "
                "('source-a','tenant-a','dataset-1','active',2),"
                "('source-b','tenant-b','dataset-1','active',2)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO source_sync_runs "
                "(id,source_id,tenant_id,dataset_id,status,execution_state,source_generation,dataset_generation) VALUES "
                "('run-a','source-a','tenant-a','dataset-1','completed','completed',2,3),"
                "('run-b','source-b','tenant-b','dataset-1','running','executing',2,1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_release_channels "
                "(id,tenant_id,code,normalized_code,status,is_default_serving,active_default_slot,revision) VALUES "
                "('channel-a-dev','tenant-a','development','development','active',0,NULL,1),"
                "('channel-a-prod','tenant-a','production','production','active',1,'default',1),"
                "('channel-a-uat','tenant-a','uat','uat','active',0,NULL,1),"
                "('channel-b-prod','tenant-b','production','production','active',1,'default',1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_manifests "
                "(id,tenant_id,dataset_id,release_number,profile_revision,ownership_revision,workspace_id,workspace_revision,mutation_generation,serving_generation,readiness_state,entry_count,blocker_count) VALUES "
                "('release-a','tenant-a','dataset-1',1,2,4,'workspace-a',6,3,4,'ready',2,0),"
                "('release-b','tenant-b','dataset-1',1,1,1,'workspace-b',1,1,1,'blocked',1,1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_entries(id,tenant_id,dataset_id,release_id) VALUES "
                "('entry-a-1','tenant-a','dataset-1','release-a'),"
                "('entry-a-2','tenant-a','dataset-1','release-a'),"
                "('entry-b-1','tenant-b','dataset-1','release-b')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_events(id,tenant_id,dataset_id,release_id,event_type) VALUES "
                "('event-a','tenant-a','dataset-1','release-a','candidate_created'),"
                "('event-b','tenant-b','dataset-1','release-b','candidate_created')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_channel_releases(id,tenant_id,dataset_id,channel_id,status,active_release_id,revision) VALUES "
                "('binding-a','tenant-a','dataset-1','channel-a-prod','active','release-a',2),"
                "('binding-b','tenant-b','dataset-1','channel-b-prod','active','release-b',2)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO apps(id,tenant_id) VALUES ('app-a','tenant-a'),('app-b','tenant-b'),('app-c','tenant-b'),('app-d','tenant-b')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO app_dataset_references "
                "(id,tenant_id,app_id,dataset_id,status,active_slot,release_mode,release_channel_id,pinned_release_id) VALUES "
                "('reference-a','tenant-a','app-a','dataset-1','active','active','follow_channel','channel-a-prod',NULL),"
                "('reference-b','tenant-b','app-b','dataset-1','active','active','bogus',NULL,NULL),"
                "('reference-c','tenant-b','app-c','dataset-1','active','active','pinned',NULL,'missing-release'),"
                "('reference-d','tenant-b','app-d','dataset-1','active','active','pinned',NULL,'release-a')"
            )
        )


def test_stage19_release_preflight_is_tenant_scoped_and_read_only(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _create_stage19_release_preflight_fixture(engine)
        before = {
            table: engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in (
                "tenant_release_channels",
                "dataset_release_manifests",
                "dataset_release_entries",
                "dataset_release_events",
                "dataset_channel_releases",
                "app_dataset_references",
            )
        }

        report = tool.run_preflight(
            "sqlite+pysqlite:///:memory:",
            engine=engine,
            target_label="test",
            backup_dir=Path.cwd(),
            require_mysql=False,
            expected_current="0029_enterprise_knowledge_base_releases",
        )
        releases = report["knowledge_base_releases"]

        assert releases["checked"] is True
        assert releases["available"] is True
        assert releases["schema_status"] == "current"
        assert releases["target_revision"] == "0029_enterprise_knowledge_base_releases"
        assert releases["active_dataset_count"] == 2
        assert releases["active_dataset_ids"] == [
            "tenant-a:dataset-1",
            "tenant-b:dataset-1",
        ]
        assert releases["release_count"] == 2
        assert releases["release_entry_count"] == 3
        assert releases["release_event_count"] == 2
        assert releases["channel_count"] == 4
        assert releases["active_channel_count"] == 4
        assert releases["default_channel_count"] == 3
        assert releases["custom_channel_count"] == 1
        assert releases["channel_binding_count"] == 2
        assert releases["active_channel_binding_count"] == 2
        assert releases["application_reference_count"] == 4
        assert releases["active_application_reference_count"] == 4
        assert releases["malformed_app_release_mode_count"] == 1
        assert releases["malformed_app_release_pin_count"] == 2
        assert releases["custom_channels"] == [{"tenant_id": "tenant-a", "code": "uat"}]
        assert releases["missing_default_channels"] == [
            {
                "tenant_id": "tenant-a",
                "missing_codes": ["testing"],
            },
            {
                "tenant_id": "tenant-b",
                "missing_codes": ["development", "testing"],
            },
        ]
        assert releases["readiness"]["ownership"]["ready_dataset_count"] == 1
        assert releases["readiness"]["ownership"]["blocked_dataset_count"] == 1
        assert releases["readiness"]["ownership"]["missing_dataset_ids"] == ["tenant-b:dataset-1"]
        assert releases["readiness"]["version"]["ready_dataset_count"] == 1
        assert releases["readiness"]["version"]["missing_document_ids"] == [
            "tenant-b:dataset-1:doc-b"
        ]
        assert releases["readiness"]["index"]["ready_dataset_count"] == 1
        assert releases["readiness"]["index"]["drift_document_ids"] == ["tenant-b:dataset-1:doc-b"]
        assert releases["readiness"]["source_sync"]["ready_dataset_count"] == 1
        assert releases["readiness"]["source_sync"]["active_run_ids"] == [
            "tenant-b:dataset-1:source-b:run-b"
        ]
        assert releases["readiness"]["projection"]["ready_dataset_count"] == 1
        assert releases["readiness"]["projection"]["drift_document_ids"] == [
            "tenant-b:dataset-1:doc-b"
        ]
        assert releases["tenant_summaries"] == [
            {
                "tenant_id": "tenant-a",
                "active_dataset_count": 1,
                "release_count": 1,
                "channel_count": 3,
                "active_application_reference_count": 1,
            },
            {
                "tenant_id": "tenant-b",
                "active_dataset_count": 1,
                "release_count": 1,
                "channel_count": 1,
                "active_application_reference_count": 3,
            },
        ]
        assert {
            "missing_default_channels",
            "missing_ownership",
            "document_version_missing",
            "document_index_drift",
            "active_source_sync",
            "document_graph_drift",
            "malformed_app_release_mode",
            "malformed_app_release_pin",
            "release_not_ready",
        } <= set(releases["blockers"])
        assert releases["reasons"]
        assert "knowledge base releases preflight blockers were found" in report["reasons"]
        assert report["read_only"] is True

        after = {
            table: engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in before
        }
        assert after == before
    finally:
        engine.dispose()


def test_stage19_release_preflight_fails_closed_for_partial_0029_schema(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES "
                    "('0029_enterprise_knowledge_base_releases')"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE datasets (id VARCHAR(64), tenant_id VARCHAR(64), status VARCHAR(16))"
                )
            )
        report = tool.run_preflight(
            "sqlite+pysqlite:///:memory:",
            engine=engine,
            target_label="test",
            backup_dir=Path.cwd(),
            require_mysql=False,
            expected_current="0029_enterprise_knowledge_base_releases",
        )
        releases = report["knowledge_base_releases"]
        assert releases["checked"] is True
        assert releases["available"] is False
        assert releases["schema_status"] == "partial"
        assert set(releases["missing_tables"]) >= {
            "tenant_release_channels",
            "dataset_release_manifests",
            "dataset_release_entries",
            "dataset_release_events",
            "dataset_channel_releases",
        }
        assert releases["blockers"] == ["release_schema_incomplete"]
        assert "0029 release authority schema is incomplete" in releases["reasons"]
        assert "knowledge base releases preflight blockers were found" in report["reasons"]
        assert report["read_only"] is True
    finally:
        engine.dispose()


def test_stage19_release_preflight_does_not_block_before_0029_when_target_tables_are_absent(
    tool,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES "
                    "('0028_enterprise_knowledge_base_registry')"
                )
            )
        report = tool.run_preflight(
            "sqlite+pysqlite:///:memory:",
            engine=engine,
            target_label="test",
            backup_dir=Path.cwd(),
            require_mysql=False,
            expected_current="0028_enterprise_knowledge_base_registry",
        )
        releases = report["knowledge_base_releases"]
        assert releases["checked"] is False
        assert releases["available"] is False
        assert releases["schema_status"] == "not_available"
        assert releases["blockers"] == []
        assert releases["reasons"] == []
        assert "knowledge base releases preflight blockers were found" not in report["reasons"]
    finally:
        engine.dispose()


def test_stage19_runbook_documents_release_preflight_manual_steps_and_prohibitions() -> None:
    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")

    for value in (
        "0029_enterprise_knowledge_base_releases",
        "tenant_release_channels",
        "dataset_release_manifests",
        "dataset_release_entries",
        "dataset_release_events",
        "dataset_channel_releases",
        "knowledge_base_releases",
        "active datasets",
        "ownership/version/index/source-sync/projection readiness",
        "malformed App release modes/pins",
        "custom/default channels",
        "Release manifest capture",
        "manual Channel seed verification",
        "不执行真实 default-channel backfill",
        "不执行真实 Release capture",
        "不执行真实 Release promotion",
        "不执行真实 Release rollback",
        "不执行真实 Application pin",
    ):
        assert value in runbook


STAGE20_TARGET_REVISION = "0030_enterprise_release_quality_certification"
STAGE20_QUALITY_TABLES = (
    "tenant_release_quality_gate_policies",
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
)


def _create_stage20_quality_preflight_fixture(engine, *, approval_checks: bool = True) -> None:
    approval_check = (
        " CHECK(action_type IN ('knowledge_base_release_quality_waiver'))"
        if approval_checks
        else ""
    )
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY, status VARCHAR(16) NOT NULL)")
        )
        connection.execute(
            text(
                "CREATE TABLE datasets ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "status VARCHAR(16) NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_release_channels ("
                "id VARCHAR(128) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "status VARCHAR(16) NOT NULL, risk_tier VARCHAR(16) NOT NULL, "
                "is_default_serving INTEGER NOT NULL, active_default_slot VARCHAR(16), "
                "PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_manifests ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "dataset_id VARCHAR(64) NOT NULL, manifest_digest VARCHAR(64) NOT NULL, "
                "mutation_generation INTEGER NOT NULL, serving_generation INTEGER NOT NULL, "
                "readiness_state VARCHAR(16) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_approval_policies ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "action_type VARCHAR(64) NOT NULL" + approval_check + ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_approval_requests ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "action_type VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL"
                + approval_check
                + ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_release_quality_gate_policies ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, "
                "name VARCHAR(128) NOT NULL, scope_type VARCHAR(16) NOT NULL, "
                "scope_value VARCHAR(128) NOT NULL, channel_id VARCHAR(128), "
                "active_scope_key VARCHAR(192), status VARCHAR(16) NOT NULL, "
                "revision INTEGER NOT NULL, min_experiment_count INTEGER NOT NULL, "
                "min_judged_result_count INTEGER NOT NULL, min_judgment_coverage_bps INTEGER NOT NULL, "
                "min_exact_agreement_bps INTEGER NOT NULL, min_mean_score_milli INTEGER NOT NULL, "
                "max_conflicting_results INTEGER NOT NULL, require_all_experiments_completed INTEGER NOT NULL, "
                "require_no_degraded_results INTEGER NOT NULL, max_certification_age_minutes INTEGER NOT NULL, "
                "policy_digest VARCHAR(64) NOT NULL, updated_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_quality_baselines ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "name VARCHAR(128) NOT NULL, normalized_name VARCHAR(256) NOT NULL, "
                "baseline_revision INTEGER NOT NULL, parent_baseline_id VARCHAR(64), "
                "experiment_count INTEGER NOT NULL, query_count INTEGER NOT NULL, "
                "baseline_digest VARCHAR(64) NOT NULL, created_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_quality_baseline_items ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "baseline_id VARCHAR(64) NOT NULL, ordinal INTEGER NOT NULL, experiment_id VARCHAR(64) NOT NULL, "
                "experiment_sequence INTEGER NOT NULL, query_hash VARCHAR(64) NOT NULL, "
                "experiment_serving_generation INTEGER NOT NULL, strategy_digest VARCHAR(64) NOT NULL, "
                "result_digest VARCHAR(64) NOT NULL, evidence_digest VARCHAR(64) NOT NULL, "
                "judgment_digest VARCHAR(64) NOT NULL, created_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_quality_certifications ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "release_id VARCHAR(64) NOT NULL, baseline_id VARCHAR(64) NOT NULL, policy_id VARCHAR(64) NOT NULL, "
                "policy_revision INTEGER NOT NULL, release_manifest_digest VARCHAR(64) NOT NULL, "
                "release_mutation_generation INTEGER NOT NULL, release_serving_generation INTEGER NOT NULL, "
                "status VARCHAR(16) NOT NULL, experiment_count INTEGER NOT NULL, "
                "completed_experiment_count INTEGER NOT NULL, degraded_experiment_count INTEGER NOT NULL, "
                "query_count INTEGER NOT NULL, judged_result_count INTEGER NOT NULL, total_result_count INTEGER NOT NULL, "
                "judgment_count INTEGER NOT NULL, judgment_coverage_bps INTEGER, multi_judged_results INTEGER NOT NULL, "
                "unanimous_results INTEGER NOT NULL, conflicting_results INTEGER NOT NULL, exact_agreement_bps INTEGER, "
                "mean_score_milli INTEGER, failed_rule_count INTEGER NOT NULL, policy_snapshot_json TEXT NOT NULL, "
                "summary_json TEXT NOT NULL, evidence_digest VARCHAR(64) NOT NULL, "
                "certification_digest VARCHAR(64) NOT NULL, valid_until VARCHAR(64) NOT NULL, "
                "created_at VARCHAR(64) NOT NULL, PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_quality_certification_evidence ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "certification_id VARCHAR(64) NOT NULL, baseline_item_id VARCHAR(64) NOT NULL, "
                "experiment_id VARCHAR(64) NOT NULL, ordinal INTEGER NOT NULL, status VARCHAR(16) NOT NULL, "
                "result_count INTEGER NOT NULL, judged_result_count INTEGER NOT NULL, judgment_count INTEGER NOT NULL, "
                "multi_judged_results INTEGER NOT NULL, unanimous_results INTEGER NOT NULL, conflicting_results INTEGER NOT NULL, "
                "exact_agreement_bps INTEGER, mean_score_milli INTEGER, experiment_digest VARCHAR(64) NOT NULL, "
                "judgment_digest VARCHAR(64) NOT NULL, safe_facts_json TEXT NOT NULL, created_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_quality_waivers ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "release_id VARCHAR(64) NOT NULL, channel_id VARCHAR(128) NOT NULL, policy_id VARCHAR(64) NOT NULL, "
                "policy_revision INTEGER NOT NULL, release_manifest_digest VARCHAR(64) NOT NULL, "
                "approval_request_id VARCHAR(64) NOT NULL, approval_execution_id VARCHAR(128) NOT NULL, "
                "reason VARCHAR(512) NOT NULL, valid_from VARCHAR(64) NOT NULL, expires_at VARCHAR(64) NOT NULL, "
                "waiver_digest VARCHAR(64) NOT NULL, created_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, dataset_id, id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_quality_events ("
                "id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, dataset_id VARCHAR(64) NOT NULL, "
                "release_id VARCHAR(64) NOT NULL, channel_id VARCHAR(128) NOT NULL, certification_id VARCHAR(64), "
                "waiver_id VARCHAR(64), event_type VARCHAR(32) NOT NULL, event_sequence INTEGER NOT NULL, "
                "state VARCHAR(16), previous_event_digest VARCHAR(64), event_digest VARCHAR(64) NOT NULL, "
                "approval_request_id VARCHAR(64), approval_execution_id VARCHAR(128), reason VARCHAR(512) NOT NULL, "
                "safe_snapshot_json TEXT NOT NULL, occurred_at VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (tenant_id, id))"
            )
        )

        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        digest_a = "a" * 64
        digest_b = "b" * 64
        connection.execute(
            text(
                "INSERT INTO tenants(id,status) VALUES ('tenant-a','active'),('tenant-b','active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets(id,tenant_id,status) VALUES "
                "('dataset-a','tenant-a','active'),('dataset-b','tenant-b','active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_release_channels "
                "(id,tenant_id,status,risk_tier,is_default_serving,active_default_slot) VALUES "
                "('channel-a-default','tenant-a','active','high',1,'default'),"
                "('channel-b-high','tenant-b','active','high',0,NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_manifests "
                "(id,tenant_id,dataset_id,manifest_digest,mutation_generation,serving_generation,readiness_state) "
                "VALUES ('release-a','tenant-a','dataset-a',:manifest,3,4,'ready')"
            ),
            {"manifest": digest_b},
        )
        connection.execute(
            text(
                "INSERT INTO tenant_approval_policies(id,tenant_id,action_type) VALUES "
                "('approval-policy-a','tenant-a','knowledge_base_release_quality_waiver')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_approval_requests(id,tenant_id,action_type,status) VALUES "
                "('approval-request-a','tenant-a','knowledge_base_release_quality_waiver','executed')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_release_quality_gate_policies "
                "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
                "min_experiment_count,min_judged_result_count,min_judgment_coverage_bps,min_exact_agreement_bps,"
                "min_mean_score_milli,max_conflicting_results,require_all_experiments_completed,"
                "require_no_degraded_results,max_certification_age_minutes,policy_digest,updated_at) VALUES "
                "('policy-a-high','tenant-a','High quality','risk_tier','high',NULL,'risk_tier:high','active',1,"
                "1,0,0,0,0,0,1,1,60,:policy,:now)"
            ),
            {"policy": digest_a, "now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO dataset_quality_baselines "
                "(id,tenant_id,dataset_id,name,normalized_name,baseline_revision,parent_baseline_id,"
                "experiment_count,query_count,baseline_digest,created_at) VALUES "
                "('baseline-a','tenant-a','dataset-a','Baseline A','baseline a',1,NULL,1,1,:digest,:now)"
            ),
            {"digest": digest_a, "now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO dataset_quality_baseline_items "
                "(id,tenant_id,dataset_id,baseline_id,ordinal,experiment_id,experiment_sequence,query_hash,"
                "experiment_serving_generation,strategy_digest,result_digest,evidence_digest,judgment_digest,created_at) VALUES "
                "('item-a','tenant-a','dataset-a','baseline-a',1,'experiment-a',1,:query,4,:strategy,:result,:evidence,:judgment,:now)"
            ),
            {
                "query": digest_a,
                "strategy": digest_a,
                "result": digest_a,
                "evidence": digest_a,
                "judgment": digest_a,
                "now": now.isoformat(),
            },
        )
        policy_snapshot = json.dumps(
            {"id": "policy-a-high", "revision": 1, "policy_digest": digest_a},
            ensure_ascii=False,
            sort_keys=True,
        )
        summary_data = {"verdict": "passed", "experiment_count": 1, "query_count": 1}
        summary_snapshot = json.dumps(
            summary_data,
            ensure_ascii=False,
            sort_keys=True,
        )
        certification_digest = canonical_quality_digest(
            "certification",
            {
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "release_id": "release-a",
                "baseline_id": "baseline-a",
                "baseline_digest": digest_a,
                "policy_id": "policy-a-high",
                "policy_revision": 1,
                "policy_digest": digest_a,
                "release_manifest_digest": digest_b,
                "release_mutation_generation": 3,
                "release_serving_generation": 4,
                "status": "passed",
                "summary": summary_data,
                "evidence_digest": digest_a,
                "valid_until": datetime.fromisoformat(future)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
                .isoformat(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_certifications "
                "(id,tenant_id,dataset_id,release_id,baseline_id,policy_id,policy_revision,release_manifest_digest,"
                "release_mutation_generation,release_serving_generation,status,experiment_count,completed_experiment_count,"
                "degraded_experiment_count,query_count,judged_result_count,total_result_count,judgment_count,judgment_coverage_bps,"
                "multi_judged_results,unanimous_results,conflicting_results,exact_agreement_bps,mean_score_milli,failed_rule_count,"
                "policy_snapshot_json,summary_json,evidence_digest,certification_digest,valid_until,created_at) VALUES "
                "('cert-a','tenant-a','dataset-a','release-a','baseline-a','policy-a-high',1,:manifest,3,4,'passed',"
                "1,1,0,1,1,1,1,10000,0,0,0,10000,3000,0,:policy_snapshot,:summary_snapshot,:evidence,:certification,:future,:now)"
            ),
            {
                "manifest": digest_b,
                "policy_snapshot": policy_snapshot,
                "summary_snapshot": summary_snapshot,
                "evidence": digest_a,
                "certification": certification_digest,
                "future": future,
                "now": now.isoformat(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_certification_evidence "
                "(id,tenant_id,dataset_id,certification_id,baseline_item_id,experiment_id,ordinal,status,result_count,"
                "judged_result_count,judgment_count,multi_judged_results,unanimous_results,conflicting_results,"
                "exact_agreement_bps,mean_score_milli,experiment_digest,judgment_digest,safe_facts_json,created_at) VALUES "
                "('evidence-a','tenant-a','dataset-a','cert-a','item-a','experiment-a',1,'completed',1,1,1,0,0,0,10000,3000,:experiment,:judgment,:facts,:now)"
            ),
            {
                "experiment": digest_a,
                "judgment": digest_a,
                "facts": json.dumps({"query_hash": digest_a, "degraded": False}, sort_keys=True),
                "now": now.isoformat(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_waivers "
                "(id,tenant_id,dataset_id,release_id,channel_id,policy_id,policy_revision,release_manifest_digest,"
                "approval_request_id,approval_execution_id,reason,valid_from,expires_at,waiver_digest,created_at) VALUES "
                "('waiver-a','tenant-a','dataset-a','release-a','channel-a-default','policy-a-high',1,:manifest,"
                "'approval-request-a','execution-a','approved exception',:valid_from,:expires_at,:waiver,:now)"
            ),
            {
                "manifest": digest_b,
                "valid_from": now.isoformat(),
                "expires_at": future,
                "waiver": digest_a,
                "now": now.isoformat(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_events "
                "(id,tenant_id,dataset_id,release_id,channel_id,certification_id,waiver_id,event_type,event_sequence,state,"
                "previous_event_digest,event_digest,approval_request_id,approval_execution_id,reason,safe_snapshot_json,occurred_at) VALUES "
                "('event-a','tenant-a','dataset-a','release-a','channel-a-default','cert-a',NULL,'certification_created',1,'passed',"
                "NULL,:event_digest,NULL,NULL,'certified',:snapshot,:now)"
            ),
            {
                "event_digest": digest_a,
                "snapshot": json.dumps(
                    {"certification_id": "cert-a", "status": "passed"}, sort_keys=True
                ),
                "now": now.isoformat(),
            },
        )


def test_stage20_release_quality_preflight_reports_counts_policy_coverage_and_is_read_only(
    tool,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _create_stage20_quality_preflight_fixture(engine)
        before = {
            table: engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in STAGE20_QUALITY_TABLES
        }
        tables = set(tool.inspect(engine).get_table_names())
        report = tool._release_quality_preflight_report(
            engine, tables, revision=STAGE20_TARGET_REVISION
        )
        after = {
            table: engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in STAGE20_QUALITY_TABLES
        }

        assert report["checked"] is True
        assert report["available"] is True
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert report["schema_status"] == "current"
        assert report["target_revision"] == STAGE20_TARGET_REVISION
        assert report["missing_tables"] == []
        assert report["policy_count"] == 1
        assert report["active_policy_count"] == 1
        assert report["baseline_count"] == 1
        assert report["baseline_item_count"] == 1
        assert report["certification_count"] == 1
        assert report["certification_evidence_count"] == 1
        assert report["waiver_count"] == 1
        assert report["event_count"] == 1
        assert report["approval"]["action_type"] == "knowledge_base_release_quality_waiver"
        assert report["approval"]["supported"] is True
        assert report["channel_coverage"]["required_channel_ids"] == [
            "tenant-a:channel-a-default",
            "tenant-b:channel-b-high",
        ]
        assert report["channel_coverage"]["covered_channel_ids"] == ["tenant-a:channel-a-default"]
        assert report["channel_coverage"]["missing_policy_channel_ids"] == [
            "tenant-b:channel-b-high"
        ]
        assert "high_or_default_policy_missing" in report["blockers"]
        assert report["tenant_summaries"] == [
            {
                "tenant_id": "tenant-a",
                "policy_count": 1,
                "baseline_count": 1,
                "baseline_item_count": 1,
                "certification_count": 1,
                "certification_evidence_count": 1,
                "waiver_count": 1,
                "event_count": 1,
            },
            {
                "tenant_id": "tenant-b",
                "policy_count": 0,
                "baseline_count": 0,
                "baseline_item_count": 0,
                "certification_count": 0,
                "certification_evidence_count": 0,
                "waiver_count": 0,
                "event_count": 0,
            },
        ]
        assert before == after
    finally:
        engine.dispose()


def test_stage20_release_quality_preflight_fails_closed_for_integrity_and_lifecycle_blockers(
    tool,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _create_stage20_quality_preflight_fixture(engine)
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        older = (now - timedelta(hours=2)).isoformat()
        future = (now + timedelta(hours=1)).isoformat()
        stale_summary = {"verdict": "passed"}
        stale_certification = canonical_quality_digest(
            "certification",
            {
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "release_id": "release-a",
                "baseline_id": "baseline-a",
                "baseline_digest": "a" * 64,
                "policy_id": "policy-a-high",
                "policy_revision": 1,
                "policy_digest": "a" * 64,
                "release_manifest_digest": "f" * 64,
                "release_mutation_generation": 3,
                "release_serving_generation": 4,
                "status": "passed",
                "summary": stale_summary,
                "evidence_digest": "a" * 64,
                "valid_until": datetime.fromisoformat(future)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
                .isoformat(),
            },
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_release_quality_gate_policies "
                    "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
                    "min_experiment_count,min_judged_result_count,min_judgment_coverage_bps,min_exact_agreement_bps,"
                    "min_mean_score_milli,max_conflicting_results,require_all_experiments_completed,require_no_degraded_results,"
                    "max_certification_age_minutes,policy_digest,updated_at) VALUES "
                    "('policy-invalid','tenant-a','Invalid','channel','not-channel','channel-a-default','bad-scope','active',1,"
                    "1,0,0,0,0,0,1,1,60,:digest,:now),"
                    "('policy-duplicate','tenant-a','Duplicate','risk_tier','high',NULL,'risk_tier:high','active',2,"
                    "1,0,0,0,0,0,1,1,60,:digest,:now)"
                ),
                {"digest": "c" * 64, "now": now.isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_quality_baselines "
                    "(id,tenant_id,dataset_id,name,normalized_name,baseline_revision,parent_baseline_id,experiment_count,"
                    "query_count,baseline_digest,created_at) VALUES "
                    "('baseline-orphan','tenant-b','missing-dataset','Orphan','orphan',1,NULL,1,1,:digest,:now)"
                ),
                {"digest": "d" * 64, "now": now.isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_quality_baseline_items "
                    "(id,tenant_id,dataset_id,baseline_id,ordinal,experiment_id,experiment_sequence,query_hash,"
                    "experiment_serving_generation,strategy_digest,result_digest,evidence_digest,judgment_digest,created_at) VALUES "
                    "('item-cross','tenant-a','dataset-b','baseline-a',2,'experiment-cross',2,:query,4,:digest,:digest,:digest,:digest,:now)"
                ),
                {"query": "e" * 64, "digest": "e" * 64, "now": now.isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release_quality_certifications "
                    "(id,tenant_id,dataset_id,release_id,baseline_id,policy_id,policy_revision,release_manifest_digest,"
                    "release_mutation_generation,release_serving_generation,status,experiment_count,completed_experiment_count,"
                    "degraded_experiment_count,query_count,judged_result_count,total_result_count,judgment_count,judgment_coverage_bps,"
                    "multi_judged_results,unanimous_results,conflicting_results,exact_agreement_bps,mean_score_milli,failed_rule_count,"
                    "policy_snapshot_json,summary_json,evidence_digest,certification_digest,valid_until,created_at) VALUES "
                    "('cert-stale','tenant-a','dataset-a','release-a','baseline-a','policy-a-high',1,:stale_manifest,3,4,'passed',"
                    "1,1,0,1,1,1,1,10000,0,0,0,10000,3000,0,:policy,:summary,:evidence,:certification,:future,:now),"
                    "('cert-invalid','tenant-a','dataset-a','release-a','baseline-a','policy-a-high',1,:valid_manifest,3,4,'passed',"
                    "1,1,0,1,1,1,1,10000,0,0,0,10000,3000,0,:policy,:bad_summary,:evidence,:bad_certification,:future,:now)"
                ),
                {
                    "stale_manifest": "f" * 64,
                    "valid_manifest": "b" * 64,
                    "policy": json.dumps({"id": "policy-a-high", "revision": 1}, sort_keys=True),
                    "summary": json.dumps({"verdict": "passed"}, sort_keys=True),
                    "bad_summary": json.dumps(
                        {"verdict": "passed", "body": "secret"}, sort_keys=True
                    ),
                    "evidence": "a" * 64,
                    "certification": stale_certification,
                    "bad_certification": "invalid-digest",
                    "future": future,
                    "now": now.isoformat(),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release_quality_waivers "
                    "(id,tenant_id,dataset_id,release_id,channel_id,policy_id,policy_revision,release_manifest_digest,"
                    "approval_request_id,approval_execution_id,reason,valid_from,expires_at,waiver_digest,created_at) VALUES "
                    "('waiver-expired','tenant-a','dataset-a','release-a','channel-a-default','policy-a-high',1,:manifest,"
                    "'approval-request-a','execution-expired','expired',:valid_from,:expires_at,:digest,:now),"
                    "('waiver-revoked','tenant-a','dataset-a','release-a','channel-a-default','policy-a-high',1,:manifest,"
                    "'approval-request-a','execution-revoked','revoked',:valid_from,:future,:digest,:now)"
                ),
                {
                    "manifest": "b" * 64,
                    "valid_from": older,
                    "expires_at": past,
                    "future": future,
                    "digest": "a" * 64,
                    "now": now.isoformat(),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release_quality_events "
                    "(id,tenant_id,dataset_id,release_id,channel_id,certification_id,waiver_id,event_type,event_sequence,state,"
                    "previous_event_digest,event_digest,approval_request_id,approval_execution_id,reason,safe_snapshot_json,occurred_at) VALUES "
                    "('event-revoked','tenant-a','dataset-a','release-a','channel-a-default',NULL,'waiver-revoked','waiver_revoked',2,'revoked',"
                    "NULL,:digest,NULL,'execution-revoked','revoked',:snapshot,:now),"
                    "('event-cross','tenant-b','dataset-b','release-a','channel-a-default',NULL,NULL,'gate_blocked',1,'blocked',"
                    "NULL,:digest,NULL,NULL,'cross tenant',:snapshot,:now)"
                ),
                {
                    "digest": "a" * 64,
                    "snapshot": json.dumps({"safe": True}, sort_keys=True),
                    "now": now.isoformat(),
                },
            )

        report = tool._release_quality_preflight_report(
            engine, set(tool.inspect(engine).get_table_names()), revision=STAGE20_TARGET_REVISION
        )
        serialized = json.dumps(report, ensure_ascii=False)
        assert "secret" not in serialized
        assert "body" not in serialized
        assert report["policy_scope_invalid_ids"] == ["tenant-a:policy-invalid"]
        assert report["duplicate_active_policy_scope_ids"] == [
            "tenant-a:policy-a-high",
            "tenant-a:policy-duplicate",
        ]
        assert report["baseline_orphan_ids"] == ["tenant-b:baseline-orphan"]
        assert report["baseline_item_orphan_ids"] == ["tenant-a:dataset-b:item-cross"]
        assert report["stale_certification_ids"] == ["tenant-a:dataset-a:cert-stale"]
        assert report["invalid_envelope_ids"] == ["tenant-a:dataset-a:cert-invalid"]
        assert report["expired_waiver_ids"] == ["tenant-a:dataset-a:waiver-expired"]
        assert report["revoked_waiver_ids"] == ["tenant-a:dataset-a:waiver-revoked"]
        assert "invalid_policy_scope" in report["blockers"]
        assert "duplicate_active_policy_scope" in report["blockers"]
        assert "baseline_tenant_integrity" in report["blockers"]
        assert "certification_stale" in report["blockers"]
        assert "invalid_quality_envelope" in report["blockers"]
        assert "waiver_expired" in report["blockers"]
        assert "waiver_revoked" in report["blockers"]
        assert "event_tenant_integrity" in report["blockers"]
    finally:
        engine.dispose()


def test_stage20_release_quality_preflight_checks_schema_approval_action_and_pre_0030_compatibility(
    tool,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES ('0029_enterprise_knowledge_base_releases')"
                )
            )
        before_tables = set(tool.inspect(engine).get_table_names())
        before = tool._release_quality_preflight_report(
            engine, before_tables, revision="0029_enterprise_knowledge_base_releases"
        )
        assert before["checked"] is False
        assert before["blockers"] == []

        _create_stage20_quality_preflight_fixture(engine, approval_checks=False)
        report = tool._release_quality_preflight_report(
            engine, set(tool.inspect(engine).get_table_names()), revision=STAGE20_TARGET_REVISION
        )
        assert report["approval"]["supported"] is False
        assert report["approval"]["policy_count"] == 1
        assert report["approval"]["request_count"] == 1
        assert "approval_waiver_action_unsupported" in report["blockers"]
    finally:
        engine.dispose()


def test_stage20_release_quality_preflight_reports_missing_tables_fail_closed(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        report = tool._release_quality_preflight_report(
            engine, set(tool.inspect(engine).get_table_names()), revision=STAGE20_TARGET_REVISION
        )
        assert report["checked"] is True
        assert report["available"] is False
        assert set(report["missing_tables"]) == set(STAGE20_QUALITY_TABLES)
        assert report["blockers"] == ["release_quality_schema_incomplete"]
    finally:
        engine.dispose()


def test_run_preflight_exposes_stage20_quality_report_without_writing_before_0030(
    tool, tmp_path: Path
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'stage20-preflight.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES "
                    "('0029_enterprise_knowledge_base_releases')"
                )
            )
        before_tables = set(tool.inspect(engine).get_table_names())
        report = tool.run_preflight(
            database_url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            expected_current="0029_enterprise_knowledge_base_releases",
            require_mysql=False,
            head_revision=STAGE20_TARGET_REVISION,
        )
        assert report["read_only"] is True
        assert report["release_quality"]["checked"] is False
        assert report["release_quality"]["read_only"] is True
        assert report["release_quality"]["automatic_actions"] == []
        assert set(tool.inspect(engine).get_table_names()) == before_tables
    finally:
        engine.dispose()


def test_stage20_migration_plan_and_runbook_document_0030_manual_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0029_enterprise_knowledge_base_releases",
        target_revision=STAGE20_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE20_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0030_enterprise_release_quality_certification.py"
    assert plan["to_revision"] == STAGE20_TARGET_REVISION
    assert plan["head_revision"] == tool.HEAD_REVISION
    assert plan["manual_rollback_only"] is True

    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "0030_enterprise_release_quality_certification",
        "七张 Release Quality 表",
        "tenant_release_quality_gate_policies",
        "dataset_quality_baselines",
        "dataset_quality_baseline_items",
        "dataset_release_quality_certifications",
        "dataset_release_quality_certification_evidence",
        "dataset_release_quality_waivers",
        "dataset_release_quality_events",
        "canonical active policy scope",
        "missing/stale/expired/revoked/invalid envelope",
        "knowledge_base_release_quality_waiver",
        "high/default Channel policy coverage",
        "不自动创建 Policy/Baseline/Certification/Waiver/Event",
        "不执行真实 0030 migration",
        "0030 SQLite offline upgrade is unsupported",
        "alembic upgrade 0030_enterprise_release_quality_certification --sql",
    ):
        assert value in runbook


STAGE21_TARGET_REVISION = "0031_enterprise_release_quality_operations"
STAGE21_OPERATIONS_TABLES = (
    "tenant_release_quality_slo_policies",
    "tenant_release_quality_scan_schedules",
    "tenant_release_quality_scan_runs",
    "dataset_release_quality_observations",
    "dataset_release_quality_alerts",
    "dataset_release_recertification_jobs",
)
STAGE21_RAW_COLUMNS = {
    "tenant_release_quality_slo_policies": (
        "id",
        "tenant_id",
        "name",
        "scope_type",
        "scope_value",
        "channel_id",
        "active_scope_key",
        "status",
        "revision",
        "certification_warning_minutes",
        "certification_critical_minutes",
        "waiver_warning_minutes",
        "max_open_alerts",
        "auto_queue_recertification",
        "require_passing_certification",
        "allow_active_waiver",
        "policy_digest",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "disabled_at",
        "disabled_by",
    ),
    "tenant_release_quality_scan_schedules": (
        "id",
        "tenant_id",
        "dataset_id",
        "slo_policy_id",
        "status",
        "active_policy_slot",
        "revision",
        "interval_seconds",
        "next_run_at",
        "last_enqueued_at",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "paused_at",
        "paused_by",
        "archived_at",
        "archived_by",
    ),
    "tenant_release_quality_scan_runs": (
        "id",
        "tenant_id",
        "dataset_id",
        "schedule_id",
        "slo_policy_id",
        "slo_policy_revision",
        "status",
        "planned_at",
        "claim_owner",
        "claim_lease_until",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "started_at",
        "finished_at",
        "observation_count",
        "alert_count",
        "recertification_job_count",
        "idempotency_key_digest",
        "request_hash",
        "summary_digest",
        "safe_error_code",
        "safe_error",
        "created_at",
        "updated_at",
    ),
    "dataset_release_quality_observations": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "scan_run_id",
        "slo_policy_id",
        "slo_policy_revision",
        "release_role",
        "gate_state",
        "gate_reason",
        "certification_id",
        "certification_digest",
        "certification_valid_until",
        "waiver_id",
        "waiver_digest",
        "waiver_expires_at",
        "minutes_to_certification_expiry",
        "minutes_to_waiver_expiry",
        "severity",
        "observation_digest",
        "observed_at",
        "observed_by",
        "request_id",
    ),
    "dataset_release_quality_alerts": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "release_role",
        "alert_type",
        "severity",
        "status",
        "active_alert_key",
        "revision",
        "source_observation_id",
        "source_observation_digest",
        "occurrence_count",
        "opened_at",
        "last_observed_at",
        "acknowledged_at",
        "acknowledged_by",
        "acknowledged_comment",
        "resolved_at",
        "resolved_by",
        "resolved_comment",
        "suppressed_until",
        "suppressed_by",
        "suppressed_comment",
        "created_at",
        "updated_at",
    ),
    "dataset_release_recertification_jobs": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "release_role",
        "baseline_id",
        "policy_id",
        "policy_revision",
        "slo_policy_id",
        "slo_policy_revision",
        "trigger",
        "status",
        "active_job_key",
        "cycle_key",
        "expected_manifest_digest",
        "expected_evidence_digest",
        "expected_channel_revision",
        "claim_owner",
        "claim_lease_until",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "result_certification_id",
        "idempotency_key_digest",
        "request_hash",
        "safe_error_code",
        "safe_error",
        "created_at",
        "created_by",
        "updated_at",
        "completed_at",
        "cancelled_at",
        "cancelled_by",
        "request_id",
        "reason",
    ),
}


def _create_stage21_raw_preflight_fixture(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tenants (id VARCHAR(64), status VARCHAR(16))"))
        connection.execute(text("CREATE TABLE datasets (id VARCHAR(64), tenant_id VARCHAR(64))"))
        connection.execute(
            text("CREATE TABLE tenant_release_channels (id VARCHAR(128), tenant_id VARCHAR(64))")
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_manifests (id VARCHAR(64), tenant_id VARCHAR(64), dataset_id VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tenant_release_quality_gate_policies (id VARCHAR(64), tenant_id VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_quality_baselines (id VARCHAR(64), tenant_id VARCHAR(64), dataset_id VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_release_quality_certifications (id VARCHAR(64), tenant_id VARCHAR(64), dataset_id VARCHAR(64))"
            )
        )
        for table, columns in STAGE21_RAW_COLUMNS.items():
            definitions = ", ".join(f'"{column}" TEXT' for column in columns)
            connection.execute(text(f'CREATE TABLE "{table}" ({definitions})'))
        connection.execute(
            text(
                "INSERT INTO tenants(id,status) VALUES ('tenant-a','active'),('tenant-b','active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets(id,tenant_id) VALUES "
                "('dataset-a','tenant-a'),('dataset-b','tenant-b')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_release_channels(id,tenant_id) VALUES ('channel-a','tenant-a')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_manifests(id,tenant_id,dataset_id) "
                "VALUES ('release-a','tenant-a','dataset-a')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_release_quality_gate_policies(id,tenant_id) "
                "VALUES ('gate-a','tenant-a')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_quality_baselines(id,tenant_id,dataset_id) "
                "VALUES ('baseline-a','tenant-a','dataset-a')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_certifications(id,tenant_id,dataset_id) "
                "VALUES ('cert-a','tenant-a','dataset-a')"
            )
        )


def _insert_stage21_raw_row(connection, table: str, **values: object) -> None:
    columns = list(values)
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(f":{column}" for column in columns)
    connection.execute(
        text(f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'),
        values,
    )


def test_stage21_release_quality_operations_preflight_reports_six_tables_counts_and_is_read_only(
    tool, tmp_path: Path
) -> None:
    from tests.test_enterprise_knowledge_base_release_migration import engine_for, sqlite_url
    from tests.test_enterprise_release_quality_operations_migration import (
        _insert_run,
        _insert_schedule,
        _insert_slo_policy,
        _upgrade_fixture,
    )

    url = sqlite_url(tmp_path / "stage21-preflight-clean.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO dataset_workspace_ownerships "
                    "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,"
                    "updated_at,updated_by) VALUES "
                    "('ownership-a','tenant-a','dataset-a','workspace-a',1,CURRENT_TIMESTAMP,"
                    "'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                )
            )
            _insert_slo_policy(connection)
            _insert_schedule(connection)
            _insert_run(connection)
        before_tables = set(tool.inspect(engine).get_table_names())
        before_counts = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE21_OPERATIONS_TABLES
        }

        report = tool._release_quality_operations_preflight_report(
            engine,
            before_tables,
            revision=STAGE21_TARGET_REVISION,
        )

        after_tables = set(tool.inspect(engine).get_table_names())
        after_counts = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE21_OPERATIONS_TABLES
        }
        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE21_TARGET_REVISION
        assert report["table_counts"] == {
            "tenant_release_quality_slo_policies": 1,
            "tenant_release_quality_scan_schedules": 1,
            "tenant_release_quality_scan_runs": 1,
            "dataset_release_quality_observations": 0,
            "dataset_release_quality_alerts": 0,
            "dataset_release_recertification_jobs": 0,
        }
        assert report["blockers"] == []
        assert report["safe_to_upgrade"] is True
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert report["observation_immutable_guards"]["missing"] == []
        assert report["dataset_scope"]["schedule"]["invalid_ids"] == []
        assert report["dataset_scope"]["scan_run"]["invalid_ids"] == []
        assert before_tables == after_tables
        assert before_counts == after_counts
    finally:
        engine.dispose()


def test_stage21_preflight_reports_identity_lease_scope_and_immutable_blockers(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _create_stage21_raw_preflight_fixture(engine)
        with engine.begin() as connection:
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_slo_policies",
                id="slo-a",
                tenant_id="tenant-a",
                scope_type="global",
                scope_value="*",
                active_scope_key="global:*",
                status="active",
            )
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_slo_policies",
                id="slo-duplicate",
                tenant_id="tenant-a",
                scope_type="global",
                scope_value="*",
                active_scope_key="global:*",
                status="active",
            )
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_slo_policies",
                id="slo-invalid",
                tenant_id="tenant-a",
                scope_type="risk_tier",
                scope_value="high",
                active_scope_key="global:*",
                status="active",
            )
            for schedule_id, active_slot in (
                ("schedule-a", "slo-a"),
                ("schedule-duplicate", "slo-a"),
                ("schedule-invalid", "wrong-slo"),
            ):
                _insert_stage21_raw_row(
                    connection,
                    "tenant_release_quality_scan_schedules",
                    id=schedule_id,
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    slo_policy_id="slo-a",
                    status="active",
                    active_policy_slot=active_slot,
                )
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_scan_runs",
                id="run-terminal-bad",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                schedule_id="schedule-a",
                slo_policy_id="slo-a",
                status="completed",
                claim_owner="worker-a",
                claim_lease_until="2026-08-29 12:00:00",
                heartbeat_at="2026-08-29 12:00:00",
                finished_at=None,
            )
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_scan_runs",
                id="run-orphan",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                schedule_id="missing-schedule",
                slo_policy_id="slo-a",
                status="pending",
            )
            _insert_stage21_raw_row(
                connection,
                "tenant_release_quality_scan_runs",
                id="run-cross-tenant",
                tenant_id="tenant-a",
                dataset_id="dataset-b",
                schedule_id="schedule-a",
                slo_policy_id="slo-a",
                status="pending",
            )
            _insert_stage21_raw_row(
                connection,
                "dataset_release_quality_observations",
                id="observation-orphan",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id="release-a",
                channel_id="channel-a",
                scan_run_id="missing-run",
                slo_policy_id="slo-a",
                observation_digest="not-a-digest",
            )
            for alert_id in ("alert-a", "alert-duplicate"):
                _insert_stage21_raw_row(
                    connection,
                    "dataset_release_quality_alerts",
                    id=alert_id,
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    release_id="release-a",
                    channel_id="channel-a",
                    release_role="active",
                    alert_type="quality_gate_blocked",
                    status="open",
                    active_alert_key="dataset-a:release-a:channel-a:active:quality_gate_blocked",
                )
            _insert_stage21_raw_row(
                connection,
                "dataset_release_quality_alerts",
                id="alert-resolved-with-key",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id="release-a",
                channel_id="channel-a",
                release_role="active",
                alert_type="quality_gate_blocked",
                status="resolved",
                active_alert_key="dataset-a:release-a:channel-a:active:quality_gate_blocked",
            )
            _insert_stage21_raw_row(
                connection,
                "dataset_release_recertification_jobs",
                id="job-invalid-cycle",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id="release-a",
                channel_id="channel-a",
                release_role="active",
                policy_id="gate-a",
                status="pending",
                active_job_key="wrong-job-key",
                cycle_key="bad-cycle-key",
            )
            for job_id in ("job-a", "job-duplicate"):
                _insert_stage21_raw_row(
                    connection,
                    "dataset_release_recertification_jobs",
                    id=job_id,
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    release_id="release-a",
                    channel_id="channel-a",
                    release_role="active",
                    policy_id="gate-a",
                    status="pending",
                    active_job_key="dataset-a:release-a:channel-a:active:gate-a",
                    cycle_key="c" * 64,
                )

        report = tool._release_quality_operations_preflight_report(
            engine,
            set(tool.inspect(engine).get_table_names()),
            revision=STAGE21_TARGET_REVISION,
        )

        assert report["safe_to_upgrade"] is False
        assert report["blockers"]
        assert report["identities"]["slo_policy"]["duplicate_ids"]
        assert report["identities"]["slo_policy"]["invalid_ids"]
        assert report["identities"]["schedule"]["duplicate_ids"]
        assert report["identities"]["schedule"]["invalid_ids"]
        assert report["identities"]["alert"]["duplicate_ids"]
        assert report["identities"]["alert"]["invalid_ids"]
        assert report["identities"]["job"]["duplicate_ids"]
        assert report["identities"]["job"]["invalid_ids"]
        assert report["cycle_key"]["invalid_ids"]
        assert report["cycle_key"]["duplicate_ids"]
        assert report["lease_terminal_consistency"]["scan_run_ids"]
        assert report["observation_immutable_guards"]["missing"]
        assert report["orphan_ids"]["scan_runs"]
        assert report["cross_tenant_ids"]["scan_runs"]
        assert report["duplicates"]["active_alerts"]
        assert report["duplicates"]["non_terminal_jobs"]
    finally:
        engine.dispose()


def test_run_preflight_exposes_stage21_report_and_combines_its_blockers(
    tool, tmp_path: Path
) -> None:
    from tests.test_enterprise_knowledge_base_release_migration import engine_for, sqlite_url
    from tests.test_enterprise_release_quality_operations_migration import _upgrade_fixture

    url = sqlite_url(tmp_path / "stage21-preflight-integration.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER trg_dataset_release_quality_observations_no_update")
            )
        report = tool.run_preflight(
            url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            expected_current=STAGE21_TARGET_REVISION,
            require_mysql=False,
            head_revision=STAGE21_TARGET_REVISION,
        )
        assert report["release_quality_operations"]["checked"] is True
        assert report["release_quality_operations"]["observation_immutable_guards"]["missing"]
        assert "release quality operations preflight blockers were found" in report["reasons"]
        assert report["safe_to_upgrade"] is False
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
    finally:
        engine.dispose()


def test_stage21_migration_plan_and_runbook_document_manual_read_only_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0030_enterprise_release_quality_certification",
        target_revision=STAGE21_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE21_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0031_enterprise_release_quality_operations.py"
    assert plan["manual_rollback_only"] is True

    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "文档日期：2026-08-29",
        "0031_enterprise_release_quality_operations",
        "六张 Release Quality Operations 表",
        "tenant_release_quality_slo_policies",
        "tenant_release_quality_scan_schedules",
        "tenant_release_quality_scan_runs",
        "dataset_release_quality_observations",
        "dataset_release_quality_alerts",
        "dataset_release_recertification_jobs",
        "Dataset-scoped Schedule/Run",
        "canonical active SLO/Schedule/Alert/Job identities",
        "cycle_key",
        "lease/terminal consistency",
        "Observation immutable guards",
        "orphan/cross-Tenant/duplicate blockers",
        "read_only=true",
        "mutations_performed=false",
        "automatic_actions=[]",
        "不得创建 Policy/Schedule/Run/Observation/Alert/Job",
        "MySQL/PostgreSQL offline review",
        "SQLite online only",
        "nonempty downgrade blocker",
        "real container smoke",
        "cross-DB production-ready",
    ):
        assert value in runbook


STAGE22_NOTIFICATION_TABLES = {
    "tenant_notification_subscriptions",
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_receipts",
    "tenant_notification_events",
}
STAGE22_TARGET_REVISION = "0032_enterprise_notification_center"


def test_stage22_notification_center_preflight_is_read_only_and_reports_five_tables(
    tool, tmp_path: Path
) -> None:
    from core.catalog_schema import upgrade_catalog
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{(tmp_path / 'stage22-notification-preflight.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        before = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE22_NOTIFICATION_TABLES
        }
        report = tool._notification_center_preflight_report(
            engine, tables, revision=STAGE22_TARGET_REVISION
        )
        after = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE22_NOTIFICATION_TABLES
        }
        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE22_TARGET_REVISION
        assert report["table_counts"] == {table: 0 for table in sorted(STAGE22_NOTIFICATION_TABLES)}
        assert report["blockers"] == []
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert before == after
    finally:
        engine.dispose()


def test_stage22_preflight_detects_canonical_receipt_and_event_blockers(
    tool, tmp_path: Path
) -> None:
    from test_enterprise_knowledge_base_release_snapshot import _release_engine
    from sqlalchemy import inspect

    engine, now = _release_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "INSERT INTO tenant_notification_subscriptions "
                    "(id,tenant_id,account_id,category,status,preference,active_subscription_key,"
                    "revision,minimum_severity,created_at,created_by,updated_at,updated_by) VALUES "
                    "('bad-sub','tenant-a','owner-a','quality','active','subscribed','forged',1,"
                    "'warning',:now,'owner-a',:now,'owner-a')"
                ),
                {"now": now},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        report = tool._notification_center_preflight_report(
            engine, set(inspect(engine).get_table_names()), revision=STAGE22_TARGET_REVISION
        )
        assert report["blockers"]
        assert any(
            "identity" in blocker or "subscription" in blocker for blocker in report["blockers"]
        )
        assert report["safe"] is False
    finally:
        engine.dispose()


def test_stage22_plan_and_runbook_document_notification_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0031_enterprise_release_quality_operations",
        target_revision=STAGE22_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE22_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0032_enterprise_notification_center.py"
    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "0032_enterprise_notification_center",
        "tenant_notification_subscriptions",
        "tenant_notifications",
        "tenant_notification_recipients",
        "tenant_notification_receipts",
        "tenant_notification_events",
        "read_only=true",
        "Notification materialization",
        "SQLite online only",
        "nonempty downgrade blocker",
    ):
        assert value in runbook


STAGE23_RECOVERY_TABLES = {
    "tenant_content_retention_policies",
    "tenant_document_recycle_entries",
    "tenant_document_legal_holds",
    "tenant_document_purge_requests",
    "tenant_document_recovery_events",
}
STAGE23_TARGET_REVISION = "0033_enterprise_content_recovery"


def test_stage23_content_recovery_preflight_is_read_only_and_reports_five_tables(
    tool, tmp_path: Path
) -> None:
    from core.catalog_schema import upgrade_catalog
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{(tmp_path / 'stage23-recovery-preflight.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        before = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE23_RECOVERY_TABLES
        }
        report = tool._content_recovery_preflight_report(
            engine, tables, revision=STAGE23_TARGET_REVISION
        )
        after = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE23_RECOVERY_TABLES
        }
        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE23_TARGET_REVISION
        assert report["table_counts"] == {table: 0 for table in sorted(STAGE23_RECOVERY_TABLES)}
        assert report["blockers"] == []
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert before == after
    finally:
        engine.dispose()


def test_stage23_plan_and_runbook_document_recovery_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0032_enterprise_notification_center",
        target_revision=STAGE23_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE23_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0033_enterprise_content_recovery.py"
    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "0033_enterprise_content_recovery",
        "tenant_content_retention_policies",
        "tenant_document_recycle_entries",
        "tenant_document_legal_holds",
        "tenant_document_purge_requests",
        "tenant_document_recovery_events",
        "read_only=true",
        "no automatic recycle",
        "SQLite online only",
        "recycled Document downgrade blocker",
    ):
        assert value in runbook


STAGE24_TASK_TABLES = {
    "tenant_task_projections",
    "tenant_task_operator_actions",
    "tenant_task_events",
    "tenant_task_saved_views",
    "tenant_task_reconciliation_runs",
}
STAGE24_TARGET_REVISION = "0034_enterprise_task_operations"


def test_stage24_task_operations_preflight_is_read_only_and_reports_five_tables(
    tool, tmp_path: Path
) -> None:
    from core.catalog_schema import upgrade_catalog
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{(tmp_path / 'stage24-task-preflight.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        before = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE24_TASK_TABLES
        }
        report = tool._task_operations_preflight_report(
            engine, tables, revision=STAGE24_TARGET_REVISION
        )
        after = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE24_TASK_TABLES
        }
        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE24_TARGET_REVISION
        assert report["table_counts"] == {table: 0 for table in sorted(STAGE24_TASK_TABLES)}
        assert report["blockers"] == []
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert report["reconcile_performed"] is False
        assert report["source_actions_dispatched"] == []
        assert before == after
    finally:
        engine.dispose()


def test_stage24_task_operations_preflight_fails_closed_for_partial_schema(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES "
                    "('0034_enterprise_task_operations')"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE tenant_task_projections "
                    "(id VARCHAR(64), tenant_id VARCHAR(64), source_kind VARCHAR(64))"
                )
            )
        tables = set(inspect(engine).get_table_names())
        report = tool._task_operations_preflight_report(
            engine, tables, revision=STAGE24_TARGET_REVISION
        )
        assert report["checked"] is True
        assert report["available"] is True
        assert report["missing_tables"] == sorted(STAGE24_TASK_TABLES - {"tenant_task_projections"})
        assert report["blockers"] == ["task_operations_schema_incomplete"]
        assert report["safe_to_upgrade"] is False
        assert report["read_only"] is True
        assert report["reconcile_performed"] is False
        assert report["source_actions_dispatched"] == []
    finally:
        engine.dispose()


def test_stage24_plan_and_runbook_document_task_operations_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0033_enterprise_content_recovery",
        target_revision=STAGE24_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE24_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0034_enterprise_task_operations.py"
    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "0034_enterprise_task_operations",
        "tenant_task_projections",
        "tenant_task_operator_actions",
        "tenant_task_events",
        "tenant_task_saved_views",
        "tenant_task_reconciliation_runs",
        "read_only=true",
        "no automatic reconcile",
        "no automatic retry or cancel",
        "SQLite online only",
        "nonempty downgrade blocker",
    ):
        assert value in runbook


STAGE25_AUTOMATION_TABLES = {
    "tenant_automation_rules",
    "tenant_automation_rule_revisions",
    "tenant_automation_source_cursors",
    "tenant_automation_runs",
    "tenant_automation_action_requests",
    "tenant_automation_events",
}
STAGE25_TARGET_REVISION = "0035_enterprise_automation_workflows"


def test_stage25_automation_preflight_is_read_only_and_reports_six_table_counts(
    tool, tmp_path: Path
) -> None:
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'stage25-automation-preflight.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        before = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE25_AUTOMATION_TABLES
        }
        report = tool._automation_workflows_preflight_report(
            engine, tables, revision=STAGE25_TARGET_REVISION
        )
        after = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE25_AUTOMATION_TABLES
        }

        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE25_TARGET_REVISION
        assert report["schema_status"] == "current"
        assert report["required_tables"] == sorted(STAGE25_AUTOMATION_TABLES)
        assert report["missing_tables"] == []
        assert report["table_counts"] == {table: 0 for table in sorted(STAGE25_AUTOMATION_TABLES)}
        assert report["counts"] == report["table_counts"]
        assert report["schema_capability_state"] == "ready"
        assert report["schema_capability_issues"] == []
        assert report["blockers"] == []
        assert report["safe"] is True
        assert report["safe_to_upgrade"] is True
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert report["source_observation_performed"] is False
        assert report["cursor_advanced"] is False
        assert report["action_dispatches"] == []
        assert before == after
    finally:
        engine.dispose()


def test_stage25_automation_preflight_fails_closed_for_partial_0035_schema(tool) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) VALUES "
                    "('0035_enterprise_automation_workflows')"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE tenant_automation_rules "
                    "(id VARCHAR(64), tenant_id VARCHAR(64), name VARCHAR(128))"
                )
            )
        tables = set(inspect(engine).get_table_names())
        report = tool._automation_workflows_preflight_report(
            engine, tables, revision=STAGE25_TARGET_REVISION
        )

        assert report["checked"] is True
        assert report["available"] is True
        assert report["schema_status"] == "partial"
        assert report["missing_tables"] == sorted(
            STAGE25_AUTOMATION_TABLES - {"tenant_automation_rules"}
        )
        assert report["blockers"] == ["automation_workflows_schema_incomplete"]
        assert report["safe"] is False
        assert report["safe_to_upgrade"] is False
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["automatic_actions"] == []
        assert report["source_observation_performed"] is False
        assert report["cursor_advanced"] is False
        assert report["action_dispatches"] == []
    finally:
        engine.dispose()


def test_run_preflight_exposes_stage25_automation_report_without_side_effects(
    tool, tmp_path: Path
) -> None:
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'stage25-automation-integration.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    try:
        report = tool.run_preflight(
            url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            require_mysql=False,
        )
        automation = report["automation_workflows"]

        assert automation["target_revision"] == STAGE25_TARGET_REVISION
        assert automation["counts"] == {table: 0 for table in sorted(STAGE25_AUTOMATION_TABLES)}
        assert automation["schema_capability_state"] == "ready"
        assert automation["read_only"] is True
        assert automation["mutations_performed"] is False
        assert automation["automatic_actions"] == []
        assert automation["source_observation_performed"] is False
        assert automation["cursor_advanced"] is False
        assert automation["action_dispatches"] == []
    finally:
        engine.dispose()



def test_stage25_preflight_does_not_create_missing_sqlite_database(tool, tmp_path: Path) -> None:
    database_path = tmp_path / "missing-stage25-preflight.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    assert not database_path.exists()

    report = tool.run_preflight(
        database_url,
        target_label="test",
        backup_dir=tmp_path,
        require_mysql=False,
    )

    assert not database_path.exists()
    assert report["read_only"] is True
    assert report["mutations_performed"] is False
    assert report["safe_to_upgrade"] is False


def test_stage25_plan_and_runbook_document_automation_read_only_boundary(tool) -> None:
    plan = tool.build_migration_plan(
        start_revision="0034_enterprise_task_operations",
        target_revision=STAGE25_TARGET_REVISION,
    )
    assert [step["revision"] for step in plan["steps"]] == [STAGE25_TARGET_REVISION]
    assert plan["steps"][0]["file"] == "0035_enterprise_automation_workflows.py"

    runbook = Path("docs/operations/enterprise-catalog-upgrade.md").read_text(encoding="utf-8")
    for value in (
        "0035_enterprise_automation_workflows",
        "tenant_automation_rules",
        "tenant_automation_rule_revisions",
        "tenant_automation_source_cursors",
        "tenant_automation_runs",
        "tenant_automation_action_requests",
        "tenant_automation_events",
        "read_only=true",
        "mutations_performed=false",
        "automatic_actions=[]",
        "source_observation_performed=false",
        "cursor_advanced=false",
        "action_dispatches=[]",
        "no automatic Run",
        "no automatic Action",
        "no automatic Rule pause",
        "SQLite online only",
        "MySQL/PostgreSQL offline DDL review",
        "nonempty downgrade blocker",
    ):
        assert value in runbook

STAGE26_TARGET_REVISION = "0036_enterprise_knowledge_serving_reliability"
STAGE26_KNOWLEDGE_SERVING_TABLES = {
    "tenant_knowledge_serving_profiles",
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
}


def test_non_sqlite_preflight_engine_configures_physical_read_only_sessions(
    tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class Sentinel:
        pass

    sentinel = Sentinel()

    def fake_create_engine(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return sentinel

    monkeypatch.setattr(tool, "create_engine", fake_create_engine)

    postgres = tool._create_read_only_preflight_engine(
        "postgresql+psycopg://catalog:secret@db.internal:5432/rag4c"
    )
    mysql = tool._create_read_only_preflight_engine(
        "mysql+pymysql://readonly:secret@db.internal:3306/rag4c"
    )

    assert postgres is sentinel
    assert mysql is sentinel
    assert calls == [
        (
            "postgresql+psycopg://catalog:secret@db.internal:5432/rag4c",
            {
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "connect_args": {"options": "-c default_transaction_read_only=on"},
            },
        ),
        (
            "mysql+pymysql://readonly:secret@db.internal:3306/rag4c",
            {
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "connect_args": {"init_command": "SET SESSION TRANSACTION READ ONLY"},
            },
        ),
    ]


def test_non_sqlite_preflight_rejects_dialects_without_a_read_only_proof(
    tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tool,
        "create_engine",
        lambda *_args, **_kwargs: pytest.fail("unsupported dialect must fail before engine creation"),
    )

    with pytest.raises(tool.SafetyGateError, match="read-only"):
        tool._create_read_only_preflight_engine("oracle+oracledb://catalog:secret@db.internal/rag4c")


def test_preflight_fails_closed_for_unproven_non_sqlite_engine(
    tool, tmp_path: Path
) -> None:
    class Dialect:
        name = "postgresql"

    class UnprovenEngine:
        dialect = Dialect()

    report = tool.run_preflight(
        "postgresql+psycopg://catalog:secret@db.internal:5432/rag4c",
        engine=UnprovenEngine(),
        target_label="test",
        backup_dir=tmp_path,
        require_mysql=False,
    )

    assert report["safe_to_upgrade"] is False
    assert any("physical read-only" in reason for reason in report["reasons"])


def test_stage26_knowledge_serving_preflight_is_read_only_and_reports_six_tables(
    tool, tmp_path: Path
) -> None:
    from tests.test_enterprise_knowledge_serving_migration import upgrade_0036

    url = f"sqlite:///{(tmp_path / 'stage26-serving-preflight.db').as_posix()}"
    upgrade_0036(url)
    engine = create_engine(url)
    try:
        before = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE26_KNOWLEDGE_SERVING_TABLES
        }
        report = tool._knowledge_serving_preflight_report(
            engine, set(inspect(engine).get_table_names()), revision=STAGE26_TARGET_REVISION
        )
        after = {
            table: int(engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in STAGE26_KNOWLEDGE_SERVING_TABLES
        }
        assert report["checked"] is True
        assert report["available"] is True
        assert report["target_revision"] == STAGE26_TARGET_REVISION
        assert report["required_tables"] == sorted(STAGE26_KNOWLEDGE_SERVING_TABLES)
        assert report["missing_tables"] == []
        assert report["counts"] == {table: 0 for table in sorted(STAGE26_KNOWLEDGE_SERVING_TABLES)}
        assert report["schema_capability_state"] == "ready"
        assert report["schema_capability_issues"] == []
        assert report["blockers"] == []
        assert report["safe"] is True
        assert report["safe_to_upgrade"] is True
        assert report["read_only"] is True
        assert report["mutations_performed"] is False
        assert report["snapshot_observation_performed"] is False
        assert report["source_state_advanced"] is False
        assert before == after
    finally:
        engine.dispose()


def test_stage26_run_preflight_exposes_serving_report_without_observation(tool, tmp_path: Path) -> None:
    from tests.test_enterprise_knowledge_serving_migration import upgrade_0036

    url = f"sqlite:///{(tmp_path / 'stage26-serving-integration.db').as_posix()}"
    upgrade_0036(url)
    engine = create_engine(url)
    try:
        report = tool.run_preflight(
            url,
            engine=engine,
            target_label="test",
            backup_dir=tmp_path,
            require_mysql=False,
            head_revision=STAGE26_TARGET_REVISION,
        )
        serving = report["knowledge_serving_reliability"]
        assert serving["target_revision"] == STAGE26_TARGET_REVISION
        assert serving["counts"] == {table: 0 for table in sorted(STAGE26_KNOWLEDGE_SERVING_TABLES)}
        assert serving["read_only"] is True
        assert serving["mutations_performed"] is False
        assert serving["snapshot_observation_performed"] is False
        assert serving["source_state_advanced"] is False
    finally:
        engine.dispose()


def test_stage26_preflight_does_not_create_missing_sqlite_database(tool, tmp_path: Path) -> None:
    database_path = tmp_path / "missing-stage26-serving-preflight.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    assert not database_path.exists()
    report = tool.run_preflight(
        database_url,
        target_label="test",
        backup_dir=tmp_path,
        require_mysql=False,
        head_revision=STAGE26_TARGET_REVISION,
    )
    assert not database_path.exists()
    assert report["read_only"] is True
    assert report["mutations_performed"] is False
    assert report["safe_to_upgrade"] is False
